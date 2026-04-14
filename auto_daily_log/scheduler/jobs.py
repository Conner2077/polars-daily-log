import json
import re
from datetime import date, datetime
from typing import Optional

from ..config import AutoApproveConfig, JiraConfig
from ..models.database import Database
from ..summarizer.engine import LLMEngine
from ..summarizer.prompt import DEFAULT_AUTO_APPROVE_PROMPT, render_prompt


class DailyWorkflow:
    def __init__(self, db: Database, engine: LLMEngine, auto_approve_config: AutoApproveConfig):
        self._db = db
        self._engine = engine
        self._auto_approve_config = auto_approve_config

    async def run_daily_summary(self, target_date: Optional[str] = None) -> list[dict]:
        from ..collector.git_collector import GitCollector
        from ..summarizer.summarizer import WorklogSummarizer

        target = target_date or date.today().isoformat()
        collector = GitCollector(self._db)
        await collector.collect_today()
        summarizer = WorklogSummarizer(self._db, self._engine)
        drafts = await summarizer.generate_drafts(target)
        return drafts

    async def auto_approve_and_submit(self, target_date: str) -> None:
        """Auto-approve pending daily drafts via LLM, then submit approved ones to Jira."""
        await self.auto_approve_pending(target_date)
        await self._submit_approved(target_date)

    async def auto_approve_pending(self, target_date: str) -> None:
        """Auto-approve pass: the AUTO_APPROVE_PROMPT already ran at
        generation time (18:00) and produced per-issue JSON. At 21:30
        we just mark leftover pending drafts as auto_approved so the
        submit pass picks them up. No additional LLM call.

        Drafts with empty per-issue JSON (LLM found no work content)
        are NOT auto-approved — they stay pending for the user to
        handle manually.
        """
        if not self._auto_approve_config.enabled:
            return

        drafts = await self._db.fetch_all(
            "SELECT * FROM worklog_drafts WHERE date = ? AND status = 'pending_review' AND tag = 'daily'",
            (target_date,),
        )

        for draft in drafts:
            try:
                issue_entries = json.loads(draft["summary"])
            except (json.JSONDecodeError, TypeError):
                issue_entries = []

            if not issue_entries:
                # Nothing to submit — keep pending, write audit note
                await self._db.execute(
                    "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'auto_skipped', ?)",
                    (draft["id"], json.dumps({"reason": "no issue entries"}, ensure_ascii=False)),
                )
                continue

            await self._db.execute(
                "UPDATE worklog_drafts SET status = 'auto_approved', updated_at = datetime('now') WHERE id = ?",
                (draft["id"],),
            )
            await self._db.execute(
                "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'auto_approved', ?)",
                (draft["id"], json.dumps({"issue_count": len(issue_entries)}, ensure_ascii=False)),
            )

    async def _submit_approved(self, target_date: str) -> None:
        """Submit all approved/auto_approved daily drafts to Jira."""
        drafts = await self._db.fetch_all(
            "SELECT * FROM worklog_drafts WHERE date = ? AND status IN ('approved', 'auto_approved') AND tag = 'daily'",
            (target_date,),
        )
        if not drafts:
            return

        jira_url = await self._db.fetch_one("SELECT value FROM settings WHERE key = 'jira_server_url'")
        jira_pat = await self._db.fetch_one("SELECT value FROM settings WHERE key = 'jira_pat'")
        jira_cookie = await self._db.fetch_one("SELECT value FROM settings WHERE key = 'jira_cookie'")
        jira_auth_mode = await self._db.fetch_one("SELECT value FROM settings WHERE key = 'jira_auth_mode'")

        url_val = (jira_url or {}).get("value", "")
        pat_val = (jira_pat or {}).get("value", "")
        cookie_val = (jira_cookie or {}).get("value", "")
        auth_mode_val = (jira_auth_mode or {}).get("value", "cookie")

        if not url_val:
            return

        from ..jira_client.client import JiraClient
        jira_config = JiraConfig(server_url=url_val, pat=pat_val, auth_mode=auth_mode_val, cookie=cookie_val)
        jira = JiraClient(jira_config)

        for draft in drafts:
            try:
                # Jira started = {draft_date}T21:00 — record against the
                # day the work happened, even for historical submissions.
                started = f"{draft['date']}T21:00:00.000+0800"

                # Parse issue entries from summary JSON
                try:
                    issues = json.loads(draft["summary"])
                except (json.JSONDecodeError, TypeError):
                    continue

                _SKIP_KEYS = {"OTHER", "ALL", "DAILY"}
                results = []
                for i, issue in enumerate(issues):
                    if issue.get("jira_worklog_id"):
                        continue
                    if issue["issue_key"] in _SKIP_KEYS:
                        continue
                    time_sec = int(issue["time_spent_hours"] * 3600)
                    result = await jira.submit_worklog(
                        issue_key=issue["issue_key"],
                        time_spent_sec=time_sec,
                        comment=issue["summary"],
                        started=started,
                    )
                    issues[i]["jira_worklog_id"] = str(result.get("id", ""))
                    results.append(result)

                await self._db.execute(
                    "UPDATE worklog_drafts SET summary = ?, status = 'submitted', updated_at = datetime('now') WHERE id = ?",
                    (json.dumps(issues, ensure_ascii=False), draft["id"]),
                )
                await self._db.execute(
                    "INSERT INTO audit_logs (draft_id, action, jira_response) VALUES (?, 'submitted', ?)",
                    (draft["id"], json.dumps(results, ensure_ascii=False)),
                )
            except Exception as e:
                await self._db.execute(
                    "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'submit_failed', ?)",
                    (draft["id"], str(e)),
                )

    def _parse_approval(self, response: str) -> dict:
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {"approved": False, "reason": "Failed to parse LLM response"}

    async def _get_auto_approve_prompt(self) -> str:
        setting = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key = 'auto_approve_prompt'"
        )
        if setting and setting["value"] and setting["value"].strip():
            return setting["value"]
        return DEFAULT_AUTO_APPROVE_PROMPT
