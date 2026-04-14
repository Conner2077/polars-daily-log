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

    def __init__(self, db: Database, engine: LLMEngine):
        self._db = db
        self._engine = engine

    async def generate_drafts(
        self, target_date: str, prompt_template: Optional[str] = None
    ) -> list[dict]:
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
        summarize_template = prompt_template or await self._get_template("summarize_prompt", DEFAULT_SUMMARIZE_PROMPT)
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

        refine_template = await self._get_template("auto_approve_prompt", DEFAULT_AUTO_APPROVE_PROMPT)
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

        issue_entries = []
        total_time_sec = 0
        for item in parsed:
            try:
                hours = float(item.get("time_spent_hours", 0))
            except (TypeError, ValueError):
                continue
            time_sec = int(hours * 3600)
            total_time_sec += time_sec
            issue_entries.append({
                "issue_key": item.get("issue_key", "OTHER"),
                "time_spent_hours": hours,
                "summary": item.get("summary", ""),
                "jira_worklog_id": None,
            })

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
        Groups by app+category, aggregates duration, keeps window titles + OCR."""
        if not activities:
            return "无"

        from collections import defaultdict

        groups = defaultdict(lambda: {"duration": 0, "titles": set(), "ocr_snippets": []})
        for a in activities:
            key = (a.get("category", "other"), a.get("app_name", "Unknown"))
            groups[key]["duration"] += a.get("duration_sec", 0)
            title = a.get("window_title")
            if title:
                groups[key]["titles"].add(title[:60])
            if a.get("signals"):
                try:
                    signals = json.loads(a["signals"])
                    ocr = signals.get("ocr_text", "")
                    if ocr and len(groups[key]["ocr_snippets"]) < 3:
                        groups[key]["ocr_snippets"].append(ocr[:100])
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
            if info["ocr_snippets"]:
                line += f" | OCR: {'; '.join(info['ocr_snippets'][:2])}"
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

    async def _get_template(self, setting_key: str, default: str) -> str:
        setting = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key = ?", (setting_key,)
        )
        if setting and setting["value"] and setting["value"].strip():
            return setting["value"]
        return default
