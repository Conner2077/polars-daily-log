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
            "SELECT * FROM activities WHERE date(timestamp) = ?", (target_date,)
        )
        commits = await self._db.fetch_all(
            "SELECT * FROM git_commits WHERE date = ?", (target_date,)
        )

        issues_text = "\n".join(
            f"- {i['issue_key']}: {i['summary']} ({i['description'] or ''})"
            for i in issues
        ) or "无"

        commits_text = "\n".join(
            f"- {c['committed_at'][:16]} {c['message']} ({c.get('files_changed', '')})"
            for c in commits
        ) or "无"

        activities_text = "\n".join(
            f"- {a['timestamp'][:16]} {a['app_name']} ({a['window_title']}) "
            f"{a['category']} {a['duration_sec']}s"
            for a in activities
        ) or "无"

        prompt = render_prompt(
            template,
            date=target_date,
            jira_issues=issues_text,
            git_commits=commits_text,
            activities=activities_text,
        )

        raw_response = await self._engine.generate(prompt)
        parsed = self._parse_response(raw_response)

        await self._db.execute(
            "DELETE FROM worklog_drafts WHERE date = ? AND status = 'pending_review'",
            (target_date,),
        )

        drafts = []
        for item in parsed:
            time_spent_sec = int(item["time_spent_hours"] * 3600)

            activity_ids = [
                a["id"] for a in activities
                if self._activity_matches_issue(a, item["issue_key"], issues)
            ]
            commit_ids = [c["id"] for c in commits]

            draft_id = await self._db.execute(
                """INSERT INTO worklog_drafts
                   (date, issue_key, time_spent_sec, summary, raw_activities, raw_commits, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending_review')""",
                (
                    target_date,
                    item["issue_key"],
                    time_spent_sec,
                    item["summary"],
                    json.dumps(activity_ids),
                    json.dumps(commit_ids),
                ),
            )

            await self._db.execute(
                """INSERT INTO audit_logs (draft_id, action, after_snapshot)
                   VALUES (?, 'created', ?)""",
                (draft_id, json.dumps(item, ensure_ascii=False)),
            )

            drafts.append({
                "id": draft_id,
                "issue_key": item["issue_key"],
                "time_spent_sec": time_spent_sec,
                "summary": item["summary"],
            })

        return drafts

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
        if setting:
            return setting["value"]
        return DEFAULT_SUMMARIZE_PROMPT
