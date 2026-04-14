import json
import re
from datetime import date
from typing import Optional

from ..models.database import Database
from .engine import LLMEngine
from .prompt import DEFAULT_SUMMARIZE_PROMPT, render_prompt


class WorklogSummarizer:
    def __init__(self, db: Database, engine: LLMEngine):
        self._db = db
        self._engine = engine

    async def generate_drafts(
        self, target_date: str, prompt_template: Optional[str] = None
    ) -> list[dict]:
        template = prompt_template or await self._get_prompt_template()

        issues = await self._db.fetch_all(
            "SELECT * FROM jira_issues WHERE is_active = 1"
        )
        activities = await self._db.fetch_all(
            "SELECT * FROM activities WHERE date(timestamp) = ? AND deleted_at IS NULL", (target_date,)
        )
        commits = await self._db.fetch_all(
            "SELECT * FROM git_commits WHERE date = ?", (target_date,)
        )

        issues_text = "\n".join(
            f"- {i['issue_key']}: {i['summary']} ({i['description'] or ''})"
            for i in issues
        ) or "无（请将所有工作汇总为一条，issue_key 使用 ALL）"

        commits_text = "\n".join(
            f"- {c['committed_at'][:16]} {c['message']} ({c.get('files_changed', '')})"
            for c in commits
        ) or "无"

        activities_text = self._compress_activities(activities)

        prompt = render_prompt(
            template,
            date=target_date,
            jira_issues=issues_text,
            git_commits=commits_text,
            activities=activities_text,
        )

        print(f"[Summarizer] Prompt length: {len(prompt)}, first 100: {prompt[:100]}")
        raw_response = await self._engine.generate(prompt)
        print(f"[Summarizer] Response length: {len(raw_response)}, first 100: {raw_response[:100]}")
        parsed = self._parse_response(raw_response)

        if not parsed:
            # LLM returned nothing useful, don't delete existing drafts
            return []

        # Only delete old pending drafts after confirming we have new ones
        await self._db.execute(
            "DELETE FROM worklog_drafts WHERE date = ? AND status = 'pending_review' AND tag = 'daily'",
            (target_date,),
        )

        # Build JSON array of all issue entries
        issue_entries = []
        total_time_sec = 0
        for item in parsed:
            time_spent_sec = int(item["time_spent_hours"] * 3600)
            total_time_sec += time_spent_sec
            issue_entries.append({
                "issue_key": item["issue_key"],
                "time_spent_hours": item["time_spent_hours"],
                "summary": item["summary"],
                "jira_worklog_id": None,
            })

        activity_ids = [a["id"] for a in activities]
        commit_ids = [c["id"] for c in commits]

        # Insert ONE record per day with all issues in summary JSON
        summary_json = json.dumps(issue_entries, ensure_ascii=False)
        draft_id = await self._db.execute(
            """INSERT INTO worklog_drafts
               (date, issue_key, time_spent_sec, summary, raw_activities, raw_commits, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending_review')""",
            (
                target_date,
                "DAILY",
                total_time_sec,
                summary_json,
                json.dumps(activity_ids),
                json.dumps(commit_ids),
            ),
        )

        await self._db.execute(
            """INSERT INTO audit_logs (draft_id, action, after_snapshot)
               VALUES (?, 'created', ?)""",
            (draft_id, summary_json),
        )

        return [{"id": draft_id, "issue_key": "DAILY", "time_spent_sec": total_time_sec, "summary": summary_json}]

    def _compress_activities(self, activities: list[dict]) -> str:
        """Compress raw activities into a concise summary for LLM prompt.
        Groups by app+category, aggregates duration, keeps key details."""
        if not activities:
            return "无"

        from collections import defaultdict

        # Group by (category, app_name) and aggregate
        groups = defaultdict(lambda: {"duration": 0, "titles": set(), "ocr_snippets": []})
        for a in activities:
            key = (a.get("category", "other"), a.get("app_name", "Unknown"))
            groups[key]["duration"] += a.get("duration_sec", 0)
            title = a.get("window_title")
            if title:
                groups[key]["titles"].add(title[:60])
            # Extract OCR text if available
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

    def _parse_response(self, response: str) -> list[dict]:
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return []

    def _activity_matches_issue(
        self, activity: dict, issue_key: str, issues: list[dict]
    ) -> bool:
        issue = next((i for i in issues if i["issue_key"] == issue_key), None)
        if not issue:
            return False
        keywords = (issue.get("summary") or "").lower().split()
        window = (activity.get("window_title") or "").lower()
        return any(k in window for k in keywords if len(k) > 2)

    async def _get_prompt_template(self) -> str:
        setting = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key = 'summarize_prompt'"
        )
        if setting and setting["value"] and setting["value"].strip():
            return setting["value"]
        return DEFAULT_SUMMARIZE_PROMPT
