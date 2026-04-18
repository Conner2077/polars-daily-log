"""Tests for Phase 1 pipeline migration: time_scopes + scope_outputs + summaries."""
import json

import pytest
import pytest_asyncio
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


# ── New tables exist ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_time_scopes_table_created(db):
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = [t["name"] for t in tables]
    assert "time_scopes" in table_names
    assert "scope_outputs" in table_names
    assert "summaries" in table_names


@pytest.mark.asyncio
async def test_audit_logs_has_summary_id_column(db):
    cols = await db.fetch_all("PRAGMA table_info(audit_logs)")
    col_names = {c["name"] for c in cols}
    assert "summary_id" in col_names


# ── Seed data ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_time_scopes_seeded_from_summary_types(db):
    scopes = await db.fetch_all("SELECT * FROM time_scopes ORDER BY name")
    names = [s["name"] for s in scopes]
    assert "daily" in names
    assert "weekly" in names
    assert "monthly" in names

    daily = next(s for s in scopes if s["name"] == "daily")
    assert daily["scope_type"] == "day"
    assert daily["is_builtin"] == 1
    assert daily["enabled"] == 1
    # schedule_rule should have {"time":"18:00"} (type stripped)
    sched = json.loads(daily["schedule_rule"])
    assert sched["time"] == "18:00"
    assert "type" not in sched

    weekly = next(s for s in scopes if s["name"] == "weekly")
    assert weekly["scope_type"] == "week"
    assert weekly["schedule_rule"] is None


@pytest.mark.asyncio
async def test_scope_outputs_seeded(db):
    outputs = await db.fetch_all("SELECT * FROM scope_outputs ORDER BY id")
    assert len(outputs) >= 4  # daily(2) + weekly + monthly + optional quarterly

    daily_single = next(o for o in outputs if o["scope_name"] == "daily" and o["output_mode"] == "single")
    assert daily_single["display_name"] == "原汁原味日志"
    assert daily_single["publisher_name"] is None
    assert daily_single["auto_publish"] == 0

    daily_issue = next(o for o in outputs if o["scope_name"] == "daily" and o["output_mode"] == "per_issue")
    assert daily_issue["display_name"] == "Jira 工时日志"
    assert daily_issue["publisher_name"] == "jira"
    assert daily_issue["issue_source"] == "jira"
    assert daily_issue["auto_publish"] == 1

    weekly = next(o for o in outputs if o["scope_name"] == "weekly")
    assert weekly["output_mode"] == "single"
    assert weekly["publisher_name"] is None

    monthly = next(o for o in outputs if o["scope_name"] == "monthly")
    assert monthly["output_mode"] == "single"


# ── Idempotency ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_idempotent(tmp_path):
    """Running initialize() twice must not duplicate rows."""
    db = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db.initialize()
    # First run — seeds
    scopes_1 = await db.fetch_all("SELECT COUNT(*) AS n FROM time_scopes")
    outputs_1 = await db.fetch_all("SELECT COUNT(*) AS n FROM scope_outputs")
    await db.close()

    # Second run — must be same counts
    db2 = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db2.initialize()
    scopes_2 = await db2.fetch_all("SELECT COUNT(*) AS n FROM time_scopes")
    outputs_2 = await db2.fetch_all("SELECT COUNT(*) AS n FROM scope_outputs")
    await db2.close()

    assert scopes_1[0]["n"] == scopes_2[0]["n"]
    assert outputs_1[0]["n"] == outputs_2[0]["n"]


# ── worklog_drafts → summaries migration ─────────────────────────────


@pytest.mark.asyncio
async def test_worklog_drafts_migrated_to_summaries(tmp_path):
    """Pre-populate worklog_drafts, then initialize a fresh Database to trigger migration."""
    import aiosqlite
    import sqlite_vec

    db_path = tmp_path / "test.db"

    # Step 1: create schema + old data using a first Database instance
    db = Database(db_path, embedding_dimensions=4)
    await db.initialize()

    # Insert a daily draft with full_summary + per-issue JSON
    issue_json = json.dumps([
        {"issue_key": "PLS-100", "time_spent_hours": 3.5, "summary": "UI refactoring", "jira_worklog_id": "wl-001"},
        {"issue_key": "PLS-101", "time_spent_hours": 1.0, "summary": "Bug fix", "jira_worklog_id": None},
    ], ensure_ascii=False)
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, full_summary, status, tag) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-17", "DAILY", 16200, issue_json,
         "今天做了 UI 重构和 bug 修复", "submitted", "daily"),
    )
    # Insert a weekly draft (summary is plain text, not JSON)
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-14", "WEEKLY", 0, "本周完成了 UI 重构", "archived", "weekly",
         "2026-04-07", "2026-04-13"),
    )
    await db.close()

    # Step 2: Wipe summaries to simulate fresh migration
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("DELETE FROM summaries")
    await conn.commit()
    await conn.close()

    # Step 3: Re-initialize — triggers migration
    db2 = Database(db_path, embedding_dimensions=4)
    await db2.initialize()

    summaries = await db2.fetch_all("SELECT * FROM summaries ORDER BY id")

    # daily full_summary → 1 single row
    single_rows = [s for s in summaries if s["issue_key"] is None and s["scope_name"] == "daily"]
    assert len(single_rows) == 1
    assert single_rows[0]["content"] == "今天做了 UI 重构和 bug 修复"

    # daily per-issue → 2 rows
    issue_rows = [s for s in summaries if s["issue_key"] is not None and s["scope_name"] == "daily"]
    assert len(issue_rows) == 2
    keys = {r["issue_key"] for r in issue_rows}
    assert keys == {"PLS-100", "PLS-101"}

    pls100 = next(r for r in issue_rows if r["issue_key"] == "PLS-100")
    assert pls100["time_spent_sec"] == 12600  # 3.5 * 3600
    assert pls100["published_id"] == "wl-001"
    assert pls100["publisher_name"] == "jira"
    assert pls100["content"] == "UI refactoring"

    pls101 = next(r for r in issue_rows if r["issue_key"] == "PLS-101")
    assert pls101["time_spent_sec"] == 3600  # 1.0 * 3600
    assert pls101["published_id"] is None
    assert pls101["publisher_name"] is None

    # weekly → 1 single row (summary text, not full_summary)
    weekly_rows = [s for s in summaries if s["scope_name"] == "weekly"]
    assert len(weekly_rows) == 1
    assert weekly_rows[0]["content"] == "本周完成了 UI 重构"
    assert weekly_rows[0]["period_start"] == "2026-04-07"
    assert weekly_rows[0]["period_end"] == "2026-04-13"

    await db2.close()


@pytest.mark.asyncio
async def test_migration_skips_corrupt_json(tmp_path):
    """Drafts with broken JSON in summary column should be skipped, not crash."""
    db = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db.initialize()

    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, full_summary, status, tag) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-17", "DAILY", 0, "NOT VALID JSON{{{",
         "原汁原味日志", "pending_review", "daily"),
    )
    await db.close()

    # Wipe summaries, re-migrate
    import aiosqlite
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.execute("DELETE FROM summaries")
    await conn.commit()
    await conn.close()

    db2 = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db2.initialize()  # should not raise

    summaries = await db2.fetch_all("SELECT * FROM summaries")
    # Should have the full_summary row but no per-issue rows (corrupt JSON skipped)
    assert len(summaries) == 1
    assert summaries[0]["content"] == "原汁原味日志"
    assert summaries[0]["issue_key"] is None

    await db2.close()


@pytest.mark.asyncio
async def test_summaries_not_remigrated_if_already_present(tmp_path):
    """If summaries table already has data, migration must not re-run."""
    db = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db.initialize()

    # Insert a draft
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, full_summary, status, tag) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-17", "DAILY", 0, "[]", "日志内容", "pending_review", "daily"),
    )
    await db.close()

    # Wipe summaries, trigger first migration
    import aiosqlite
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.execute("DELETE FROM summaries")
    await conn.commit()
    await conn.close()

    db2 = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db2.initialize()
    count_1 = await db2.fetch_all("SELECT COUNT(*) AS n FROM summaries")
    await db2.close()

    # Add another draft — should NOT get migrated on second init
    db3 = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db3.initialize()
    await db3.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, full_summary, status, tag) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-18", "DAILY", 0, "[]", "新一天", "pending_review", "daily"),
    )
    await db3.close()

    db4 = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db4.initialize()
    count_2 = await db4.fetch_all("SELECT COUNT(*) AS n FROM summaries")
    await db4.close()

    assert count_1[0]["n"] == count_2[0]["n"]
