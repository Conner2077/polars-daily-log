"""CLI query interface — `pdl query activities|worklogs|commits|issues`.

Outputs JSON to stdout so any agent (or script) can parse it.
Human-readable table mode via --format table.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from .config import resolve_db_path


async def _query(args: argparse.Namespace) -> list[dict]:
    from .models.database import Database

    db = Database(resolve_db_path(args.db), embedding_dimensions=4)
    await db.initialize()
    try:
        return await _dispatch(db, args)
    finally:
        await db.close()


async def _dispatch(db, args: argparse.Namespace) -> list[dict]:
    target = args.target

    if target == "activities":
        sql = (
            "SELECT id, timestamp, app_name, window_title, llm_summary, duration_sec "
            "FROM activities WHERE deleted_at IS NULL"
        )
        params: list = []
        if args.date:
            sql += " AND date(timestamp) = ?"
            params.append(args.date)
        if args.keyword:
            sql += " AND (app_name LIKE ? OR window_title LIKE ? OR llm_summary LIKE ?)"
            k = f"%{args.keyword}%"
            params.extend([k, k, k])
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(args.limit)
        return await db.fetch_all(sql, tuple(params))

    if target == "worklogs":
        sql = (
            "SELECT id, date, issue_key, summary, time_spent_sec, status, tag "
            "FROM worklog_drafts WHERE 1=1"
        )
        params = []
        if args.date:
            sql += " AND date = ?"
            params.append(args.date)
        if args.issue:
            sql += " AND issue_key = ?"
            params.append(args.issue)
        if args.status:
            sql += " AND status = ?"
            params.append(args.status)
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(args.limit)
        return await db.fetch_all(sql, tuple(params))

    if target == "commits":
        sql = (
            "SELECT hash, message, author, committed_at, insertions, deletions "
            "FROM git_commits WHERE 1=1"
        )
        params = []
        if args.date:
            sql += " AND date = ?"
            params.append(args.date)
        sql += " ORDER BY committed_at DESC LIMIT ?"
        params.append(args.limit)
        return await db.fetch_all(sql, tuple(params))

    if target == "issues":
        sql = "SELECT issue_key, summary, description, is_active FROM jira_issues"
        params = []
        if args.active:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY issue_key"
        return await db.fetch_all(sql, tuple(params))

    print(f"Unknown target: {target}", file=sys.stderr)
    sys.exit(2)


def _format_table(rows: list[dict]) -> str:
    if not rows:
        return "(no results)"
    keys = list(rows[0].keys())
    widths = {k: max(len(k), *(len(str(r.get(k, ""))) for r in rows)) for k in keys}
    # Cap column width for readability
    for k in widths:
        widths[k] = min(widths[k], 60)
    header = "  ".join(k.ljust(widths[k]) for k in keys)
    sep = "  ".join("-" * widths[k] for k in keys)
    lines = [header, sep]
    for r in rows:
        lines.append("  ".join(str(r.get(k, ""))[:widths[k]].ljust(widths[k]) for k in keys))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pdl query", description="Query Polars Daily Log data")
    parser.add_argument("target", choices=["activities", "worklogs", "commits", "issues"])
    parser.add_argument("--date", help="YYYY-MM-DD (default: today for activities/commits)")
    parser.add_argument("--keyword", "-k", help="Filter by keyword (activities only)")
    parser.add_argument("--issue", help="Filter by Jira issue key (worklogs only)")
    parser.add_argument("--status", help="Filter by status (worklogs only)")
    parser.add_argument("--active", action="store_true", default=True, help="Active issues only (default)")
    parser.add_argument("--all-issues", action="store_true", help="Include inactive issues")
    parser.add_argument("--limit", "-n", type=int, default=20, help="Max rows (default: 20)")
    parser.add_argument("--format", "-f", choices=["json", "table"], default="json", help="Output format")
    parser.add_argument("--db", help="Override DB path (default: from PDL_SERVER_CONFIG → system.data_dir, else ~/.auto_daily_log/data.db)")

    args = parser.parse_args(argv)
    if args.all_issues:
        args.active = False

    rows = asyncio.run(_query(args))

    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(_format_table(rows))


if __name__ == "__main__":
    main()
