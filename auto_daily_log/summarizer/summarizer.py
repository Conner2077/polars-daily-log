import json
import re
from typing import Optional

from ..models.database import Database
from .engine import LLMEngine
from .prompt import (
    DEFAULT_AUTO_APPROVE_PROMPT,
    DEFAULT_SUMMARIZE_PROMPT,
    render_prompt,
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

        activities_text = self._compress_activities(activities)
        commits_text = self._format_commits(commits)

        # ─── Step 1: full activity summary (raw) ─────────────────────
        summarize_template = prompt_template or await self._get_template(
            "summarize_prompt", DEFAULT_SUMMARIZE_PROMPT, summary_type=summary_type,
        )
        summarize_prompt = render_prompt(
            summarize_template,
            date=target_date,
            git_commits=commits_text,
            activities=activities_text,
        )
        print(f"[Summarizer] Step 1 (full summary) prompt length: {len(summarize_prompt)}")
        full_summary = (await self._engine.generate(summarize_prompt)).strip()
        if not full_summary:
            print("[Summarizer] Step 1 returned empty, skipping")
            return []
        print(f"[Summarizer] Step 1 done, full_summary length: {len(full_summary)}")

        # ─── Step 2: per-issue JSON for Jira ─────────────────────────
        issues_text = "\n".join(
            f"- {i['issue_key']}: {i['summary']} ({i['description'] or ''})"
            for i in issues
        ) or "无（将所有工作汇总为一条，issue_key 使用 ALL）"

        refine_template = await self._get_template(
            "auto_approve_prompt", DEFAULT_AUTO_APPROVE_PROMPT, summary_type=summary_type,
        )
        refine_prompt = render_prompt(
            refine_template,
            date=target_date,
            jira_issues=issues_text,
            full_summary=full_summary,
            git_commits=commits_text,
        )
        print(f"[Summarizer] Step 2 (refine) prompt length: {len(refine_prompt)}")
        refine_response = await self._engine.generate(refine_prompt)
        parsed = self._parse_json_array(refine_response)
        print(f"[Summarizer] Step 2 done, parsed {len(parsed)} issue entries")

        # ─── Assemble and persist ────────────────────────────────────
        # Delete stale pending drafts only after confirming we have new content
        await self._db.execute(
            "DELETE FROM worklog_drafts WHERE date = ? AND status = 'pending_review' AND tag = 'daily'",
            (target_date,),
        )

        # LLM sometimes ignores "同一 issue_key 合并为一条"; enforce in code.
        merged: dict[str, dict] = {}
        for item in parsed:
            try:
                hours = float(item.get("time_spent_hours", 0))
            except (TypeError, ValueError):
                continue
            key = item.get("issue_key") or ""
            if not key or key == "OTHER":
                continue  # Discard unmapped entries — already in full_summary
            summary_text = (item.get("summary") or "").strip()
            if key in merged:
                merged[key]["time_spent_hours"] = round(merged[key]["time_spent_hours"] + hours, 2)
                if summary_text:
                    existing = merged[key]["summary"]
                    merged[key]["summary"] = f"{existing}；{summary_text}" if existing else summary_text
            else:
                merged[key] = {
                    "issue_key": key,
                    "time_spent_hours": round(hours, 2),
                    "summary": summary_text,
                    "jira_worklog_id": None,
                }

        issue_entries = list(merged.values())
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

    def _format_commits(self, commits: list[dict]) -> str:
        if not commits:
            return "无"
        return "\n".join(
            f"- {c['committed_at'][:16]} {c['message']} ({c.get('files_changed', '')})"
            for c in commits
        )

    def _compress_activities(self, activities: list[dict]) -> str:
        """Compress raw activities into a text summary for LLM prompt.

        Groups by (category, app_name), aggregates duration, keeps window
        titles. For activity content it prefers llm_summary (dense, ≤100
        chars, written by ActivitySummarizer) and falls back to raw OCR
        truncation only when llm_summary is NULL or '(failed)'.
        """
        if not activities:
            return "无"

        from collections import defaultdict

        groups = defaultdict(lambda: {
            "duration": 0,
            "titles": set(),
            "llm_summaries": [],
            "ocr_fallback": [],
        })
        for a in activities:
            key = (a.get("category", "other"), a.get("app_name", "Unknown"))
            groups[key]["duration"] += a.get("duration_sec", 0)
            title = a.get("window_title")
            if title:
                groups[key]["titles"].add(title[:60])

            llm_sum = a.get("llm_summary")
            if llm_sum and llm_sum != "(failed)":
                # Dedup — several consecutive activities often share the
                # same app and produce similar summaries; collapse them
                # so the prompt doesn't repeat.
                if llm_sum not in groups[key]["llm_summaries"]:
                    groups[key]["llm_summaries"].append(llm_sum)
            else:
                # Fallback: old OCR truncation. Only used when the LLM
                # worker hasn't reached this row yet or gave up on it.
                if a.get("signals"):
                    try:
                        signals = json.loads(a["signals"])
                        ocr = (signals.get("ocr_text") or "")[:100]
                        if ocr and len(groups[key]["ocr_fallback"]) < 3:
                            groups[key]["ocr_fallback"].append(ocr)
                    except (json.JSONDecodeError, TypeError):
                        pass

        lines = []
        for (cat, app), info in sorted(groups.items(), key=lambda x: -x[1]["duration"]):
            hours = round(info["duration"] / 3600, 1)
            if hours < 0.1:
                continue
            titles = list(info["titles"])[:5]
            title_str = ", ".join(titles) if titles else ""
            line = f"- [{cat}] {app} ({hours}h): {title_str}"
            if info["llm_summaries"]:
                # Cap at 8 joined summaries so a chatty day doesn't blow
                # up the step-1 prompt budget.
                summaries = "；".join(info["llm_summaries"][:8])
                line += f" | 内容: {summaries}"
            elif info["ocr_fallback"]:
                line += f" | OCR: {'; '.join(info['ocr_fallback'][:2])}"
            lines.append(line)

        return "\n".join(lines) or "无"

    def _parse_json_array(self, response: str) -> list[dict]:
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return []

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
