import json
from typing import Optional

from ..models.database import Database
from . import core
from .engine import LLMEngine
from .prompt import (
    DEFAULT_AUTO_APPROVE_PROMPT,
    DEFAULT_SUMMARIZE_PROMPT,
)


class WorklogSummarizer:
    """Two-step daily log pipeline:

    1. SUMMARIZE_PROMPT → full_summary (plain text, all activities, unfiltered)
    2. AUTO_APPROVE_PROMPT → per-issue JSON array (filtered + polished for Jira)

    Both results are stored in the same worklog_drafts row:
    - full_summary: column `full_summary`
    - per-issue JSON: column `summary` (same as before)
    """

    def __init__(self, db: Database, engine: LLMEngine, activity_summarizer=None):
        self._db = db
        self._engine = engine
        self._activity_summarizer = activity_summarizer

    async def generate_drafts(
        self, target_date: str, prompt_template: Optional[str] = None,
        *, summary_type: str = "daily",
    ) -> list[dict]:
        # Catch-up: synchronously process any pending activity rows so
        # _compress_activities sees llm_summary values instead of the
        # OCR-truncation fallback. Bounded at 60s; anything still pending
        # falls through and uses the OCR fallback branch.
        if self._activity_summarizer is not None:
            try:
                processed = await self._activity_summarizer.backfill_for_date(
                    target_date, timeout_sec=60
                )
                print(f"[Summarizer] activity backfill processed {processed} row(s) for {target_date}")
            except Exception as e:
                print(f"[Summarizer] activity backfill failed (non-fatal): {e}")

        issues = await self._db.fetch_all(
            "SELECT * FROM jira_issues WHERE is_active = 1"
        )
        activities = await self._db.fetch_all(
            "SELECT * FROM activities WHERE date(timestamp) = ? AND deleted_at IS NULL",
            (target_date,),
        )
        commits = await self._db.fetch_all(
            "SELECT * FROM git_commits WHERE date = ?", (target_date,)
        )

        if not activities and not commits:
            print(f"[Summarizer] No data for {target_date}, skipping generation")
            return []

        activities_text = core.compress_activities(activities)
        commits_text = core.format_commits(commits)

        async def engine_call(prompt: str) -> str:
            return await self._engine.generate(prompt)

        # ─── Step 1: full activity summary (raw) ─────────────────────
        summarize_template = prompt_template or await self._get_template(
            "summarize_prompt", DEFAULT_SUMMARIZE_PROMPT, summary_type=summary_type,
        )
        full_summary = await core.generate_full_summary(
            target_date, activities_text, commits_text, engine_call, summarize_template,
        )
        if not full_summary:
            print("[Summarizer] Step 1 returned empty, skipping")
            return []
        print(f"[Summarizer] Step 1 done, full_summary length: {len(full_summary)}")

        # ─── Step 2: per-issue JSON for Jira ─────────────────────────
        issues_text = core.format_jira_issues(issues)
        refine_template = await self._get_template(
            "auto_approve_prompt", DEFAULT_AUTO_APPROVE_PROMPT, summary_type=summary_type,
        )
        issue_entries = await core.generate_issue_entries(
            target_date, full_summary, commits_text, issues_text,
            engine_call, refine_template,
        )
        print(f"[Summarizer] Step 2 done, parsed {len(issue_entries)} issue entries")

        # ─── Assemble and persist ────────────────────────────────────
        # Delete stale pending drafts only after confirming we have new content
        await self._db.execute(
            "DELETE FROM worklog_drafts WHERE date = ? AND status = 'pending_review' AND tag = 'daily'",
            (target_date,),
        )

        total_time_sec = int(sum(e["time_spent_hours"] for e in issue_entries) * 3600)

        activity_ids = [a["id"] for a in activities]
        commit_ids = [c["id"] for c in commits]
        summary_json = json.dumps(issue_entries, ensure_ascii=False)

        draft_id = await self._db.execute(
            """INSERT INTO worklog_drafts
               (date, issue_key, time_spent_sec, summary, full_summary,
                raw_activities, raw_commits, status, tag)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_review', 'daily')""",
            (
                target_date,
                "DAILY",
                total_time_sec,
                summary_json,
                full_summary,
                json.dumps(activity_ids),
                json.dumps(commit_ids),
            ),
        )
        await self._db.execute(
            """INSERT INTO audit_logs (draft_id, action, after_snapshot)
               VALUES (?, 'created', ?)""",
            (draft_id, json.dumps({
                "full_summary_length": len(full_summary),
                "issue_count": len(issue_entries),
            }, ensure_ascii=False)),
        )

        return [{
            "id": draft_id,
            "issue_key": "DAILY",
            "time_spent_sec": total_time_sec,
            "summary": summary_json,
            "full_summary": full_summary,
        }]

    # ─── Helpers ──────────────────────────────────────────────────────
    # Thin wrappers around summarizer.core — kept on the class only so
    # existing tests can call them via WorklogSummarizer.__new__().

    def _format_commits(self, commits: list[dict]) -> str:
        return core.format_commits(commits)

    def _compress_activities(self, activities: list[dict]) -> str:
        return core.compress_activities(activities)

    def _parse_json_array(self, response: str) -> list[dict]:
        return core.parse_issue_entries(response)

    async def _get_template(
        self, setting_key: str, default: str, *, summary_type: str | None = None,
    ) -> str:
        """Resolve prompt template with 3-level fallback:
          1. summary_types.prompt_template for the specific type
          2. settings table global override (key = setting_key)
          3. hardcoded DEFAULT_*_PROMPT
        """
        # Level 1: per-type custom prompt
        if summary_type:
            row = await self._db.fetch_one(
                "SELECT prompt_template FROM summary_types WHERE name = ?",
                (summary_type,),
            )
            if row and row.get("prompt_template") and row["prompt_template"].strip():
                return row["prompt_template"]

        # Level 2: global settings override
        setting = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key = ?", (setting_key,)
        )
        if setting and setting["value"] and setting["value"].strip():
            return setting["value"]

        # Level 3: hardcoded default
        return default
