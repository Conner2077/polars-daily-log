"""MCP server implementation for Polars Daily Log.

Uses the official ``mcp`` Python SDK (FastMCP) with stdio transport.
Each tool opens its own Database connection to ``~/.auto_daily_log/data.db``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..models.database import Database

mcp = FastMCP("polars-daily-log")

DB_PATH = Path.home() / ".auto_daily_log" / "data.db"


async def _get_db(db_path: Optional[Path] = None) -> Database:
    """Open (and initialise) a Database connection."""
    path = db_path or DB_PATH
    db = Database(path, embedding_dimensions=4)
    await db.initialize()
    return db


# ── Tool 1: query_activities ─────────────────────────────────────────

@mcp.tool(
    name="query_activities",
    description="Query activities for a given date. Returns timestamped list of app usage with LLM summaries.",
)
async def query_activities(
    date: str,
    keyword: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Query activities for *date* (YYYY-MM-DD), optionally filtered by *keyword*."""
    db = await _get_db()
    try:
        params: list = [date]
        keyword_clause = ""
        if keyword:
            keyword_clause = " AND (app_name LIKE ? OR window_title LIKE ? OR llm_summary LIKE ?)"
            like = f"%{keyword}%"
            params.extend([like, like, like])
        params.append(limit)

        rows = await db.fetch_all(
            "SELECT timestamp, app_name, window_title, llm_summary, duration_sec "
            "FROM activities "
            "WHERE date(timestamp) = ? AND deleted_at IS NULL"
            f"{keyword_clause} "
            "ORDER BY timestamp ASC LIMIT ?",
            tuple(params),
        )
        if not rows:
            return f"No activities found for {date}."

        lines = [f"Found {len(rows)} activities on {date}:"]
        for r in rows:
            ts = r.get("timestamp") or ""
            try:
                hhmm = datetime.fromisoformat(ts).strftime("%H:%M")
            except (ValueError, TypeError):
                hhmm = ts[:16] if len(ts) >= 16 else ts
            app = r.get("app_name") or ""
            summary = r.get("llm_summary") or r.get("window_title") or ""
            dur = r.get("duration_sec") or 0
            mins = round(dur / 60, 1)
            lines.append(f"- {hhmm} {app} — {summary} ({mins}min)")
        return "\n".join(lines)
    finally:
        await db.close()


# ── Tool 2: query_worklogs ───────────────────────────────────────────

@mcp.tool(
    name="query_worklogs",
    description="Query worklog drafts. Filter by date, issue_key, or status (pending_review/approved/submitted).",
)
async def query_worklogs(
    date: Optional[str] = None,
    issue_key: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Query worklog drafts with optional filters."""
    db = await _get_db()
    try:
        conditions = ["1=1"]
        params: list = []
        if date:
            conditions.append("date = ?")
            params.append(date)
        if issue_key:
            conditions.append("issue_key = ?")
            params.append(issue_key)
        if status:
            conditions.append("status = ?")
            params.append(status)
        params.append(limit)

        rows = await db.fetch_all(
            "SELECT date, issue_key, summary, time_spent_sec, status, full_summary "
            "FROM worklog_drafts "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY date DESC LIMIT ?",
            tuple(params),
        )
        if not rows:
            return "No worklogs found."

        lines = [f"Found {len(rows)} worklogs:"]
        for r in rows:
            d = r.get("date") or ""
            key = r.get("issue_key") or ""
            sec = r.get("time_spent_sec") or 0
            hours = round(sec / 3600, 1)
            summary = (r.get("summary") or "")[:120]
            st = r.get("status") or ""
            lines.append(f"- [{d}] [{key}] {hours}h — {summary} ({st})")
        return "\n".join(lines)
    finally:
        await db.close()


# ── Tool 3: get_jira_issues ──────────────────────────────────────────

@mcp.tool(
    name="get_jira_issues",
    description="List Jira issues tracked in the system. Optionally filter to active-only.",
)
async def get_jira_issues(active_only: bool = True) -> str:
    """List Jira issues, optionally active-only."""
    db = await _get_db()
    try:
        if active_only:
            rows = await db.fetch_all(
                "SELECT issue_key, summary, description, is_active FROM jira_issues "
                "WHERE is_active = 1 ORDER BY issue_key"
            )
        else:
            rows = await db.fetch_all(
                "SELECT issue_key, summary, description, is_active FROM jira_issues ORDER BY issue_key"
            )
        if not rows:
            return "No Jira issues found."

        lines = [f"{len(rows)} Jira issues:"]
        for r in rows:
            key = r.get("issue_key") or ""
            title = r.get("summary") or ""
            desc = (r.get("description") or "")[:80]
            lines.append(f"- [{key}] {title} — {desc}")
        return "\n".join(lines)
    finally:
        await db.close()


# ── Tool 4: submit_worklog ───────────────────────────────────────────

@mcp.tool(
    name="submit_worklog",
    description="Submit a worklog entry to Jira. Creates an audit trail in worklog_drafts.",
)
async def submit_worklog(
    issue_key: str,
    hours: float,
    summary: str,
    date: str,
) -> str:
    """Submit *hours* of work on *issue_key* to Jira for *date* (YYYY-MM-DD)."""
    db = await _get_db()
    try:
        from ..jira_client.client import build_jira_client_from_db, MissingJiraConfig

        time_spent_sec = int(hours * 3600)
        started = f"{date}T09:00:00.000+0800"

        # Insert audit row first
        draft_id = await db.execute(
            "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag) "
            "VALUES (?, ?, ?, ?, 'approved', 'daily')",
            (date, issue_key, time_spent_sec, summary),
        )

        try:
            jira = await build_jira_client_from_db(db)
            result = await jira.submit_worklog(
                issue_key=issue_key,
                time_spent_sec=time_spent_sec,
                comment=summary,
                started=started,
            )
            worklog_id = result.get("id", "")
            await db.execute(
                "UPDATE worklog_drafts SET status = 'submitted', jira_worklog_id = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (str(worklog_id), draft_id),
            )
            return f"Submitted {hours} hours to {issue_key} on {date}. Jira worklog ID: {worklog_id}"
        except MissingJiraConfig as e:
            return f"Failed to submit: {e}"
        except Exception as e:
            return f"Failed to submit: {e}"
    finally:
        await db.close()


# ── Tool 5: generate_daily_summary ───────────────────────────────────

@mcp.tool(
    name="generate_daily_summary",
    description="Get or generate daily worklog summary for a date. Returns existing drafts if available.",
)
async def generate_daily_summary(date: str) -> str:
    """Return existing worklog drafts for *date* (YYYY-MM-DD).

    A simplified version that reads existing drafts rather than invoking
    the full LLM pipeline (which requires app-state config).
    """
    db = await _get_db()
    try:
        rows = await db.fetch_all(
            "SELECT issue_key, time_spent_sec, summary, status, full_summary "
            "FROM worklog_drafts WHERE date = ? ORDER BY created_at DESC",
            (date,),
        )
        if not rows:
            return f"No worklog drafts found for {date}. Use the web UI to generate them first."

        lines = [f"Daily summary for {date}:"]
        for r in rows:
            key = r.get("issue_key") or ""
            sec = r.get("time_spent_sec") or 0
            hours = round(sec / 3600, 1)
            summary = r.get("full_summary") or r.get("summary") or ""
            lines.append(f"## [{key}]\n{hours} hours — {summary}")
        return "\n".join(lines)
    finally:
        await db.close()


# ── Tool 6: search_activities ────────────────────────────────────────

@mcp.tool(
    name="search_activities",
    description="Full-text search across activity LLM summaries. Returns matching activities across all dates.",
)
async def search_activities(query: str, limit: int = 10) -> str:
    """Search activities by *query* in llm_summary (LIKE match)."""
    db = await _get_db()
    try:
        like = f"%{query}%"
        rows = await db.fetch_all(
            "SELECT timestamp, app_name, llm_summary FROM activities "
            "WHERE llm_summary LIKE ? AND deleted_at IS NULL "
            "ORDER BY timestamp DESC LIMIT ?",
            (like, limit),
        )
        if not rows:
            return f"No activities matching '{query}'."

        lines = [f"Found {len(rows)} matches:"]
        for r in rows:
            ts = r.get("timestamp") or ""
            app = r.get("app_name") or ""
            summary = r.get("llm_summary") or ""
            lines.append(f"- [{ts}] {app} — {summary}")
        return "\n".join(lines)
    finally:
        await db.close()


# ── Tool 7: get_git_commits ──────────────────────────────────────────

@mcp.tool(
    name="get_git_commits",
    description="List git commits for a given date, with file change stats.",
)
async def get_git_commits(date: str, repo_path: Optional[str] = None) -> str:
    """List commits on *date* (YYYY-MM-DD)."""
    db = await _get_db()
    try:
        rows = await db.fetch_all(
            "SELECT hash, message, author, committed_at, files_changed, insertions, deletions "
            "FROM git_commits WHERE date = ? ORDER BY committed_at ASC",
            (date,),
        )
        if not rows:
            return f"No commits found for {date}."

        lines = [f"{len(rows)} commits on {date}:"]
        for r in rows:
            h = (r.get("hash") or "")[:7]
            msg = r.get("message") or ""
            ins = r.get("insertions") or 0
            dels = r.get("deletions") or 0
            lines.append(f"- [{h}] {msg} (+{ins}/-{dels})")
        return "\n".join(lines)
    finally:
        await db.close()
