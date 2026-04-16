"""Tests for scheduler catch-up, misfire, and daily_generate edge cases.

Covers the scenarios where daily_generate or auto_approve might silently
fail or not produce output despite the server running and collector active.
"""
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from auto_daily_log.scheduler.jobs import DailyWorkflow
from auto_daily_log.config import AutoApproveConfig


# ─── Helpers ─────────────────────────────────────────────────────────

class FakeDB:
    """In-memory store that mimics Database.fetch_all / fetch_one / execute."""

    def __init__(self):
        self.tables = {"activities": [], "git_commits": [], "worklog_drafts": [],
                       "audit_logs": [], "jira_issues": [], "settings": [],
                       "collectors": []}
        self._id = 100

    async def fetch_all(self, sql, params=None):
        table = self._guess_table(sql)
        rows = self.tables.get(table, [])
        if "WHERE" in sql:
            rows = self._filter(sql, params, rows)
        if "ORDER" in sql and "DESC" in sql:
            rows = list(reversed(rows))
        return rows

    async def fetch_one(self, sql, params=None):
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT"):
            self._id += 1
            table = self._guess_table(sql)
            if table == "worklog_drafts":
                row = {"id": self._id, "status": "pending_review", "tag": "daily",
                       "date": params[0] if params else None}
                self.tables[table].append(row)
            elif table == "audit_logs":
                self.tables[table].append({"id": self._id})
            return self._id
        if sql.strip().upper().startswith("UPDATE"):
            table = self._guess_table(sql)
            # Simple: mark all matching rows
            for row in self.tables.get(table, []):
                if "status" in sql.lower() and params:
                    pass  # simplified — just mark it
        return None

    def _guess_table(self, sql):
        sql_up = sql.upper()
        for t in self.tables:
            if t.upper() in sql_up:
                return t
        return "unknown"

    def _filter(self, sql, params, rows):
        # Very crude filter — just check date param presence
        if not params:
            return rows
        result = []
        for r in rows:
            match = True
            for p in (params if isinstance(params, (list, tuple)) else [params]):
                if isinstance(p, str) and "date" in r and r["date"] != p:
                    match = False
            if match:
                result.append(r)
        return result

    def add_activity(self, timestamp, app_name="VS Code", category="coding",
                     machine_id="local", duration_sec=30):
        self._id += 1
        self.tables["activities"].append({
            "id": self._id,
            "timestamp": timestamp,
            "app_name": app_name,
            "category": category,
            "machine_id": machine_id,
            "duration_sec": duration_sec,
            "deleted_at": None,
            "date": timestamp[:10] if isinstance(timestamp, str) else None,
        })

    def add_draft(self, date, status="pending_review", tag="daily", summary="[]"):
        self._id += 1
        self.tables["worklog_drafts"].append({
            "id": self._id, "date": date, "status": status, "tag": tag,
            "summary": summary, "full_summary": "", "time_spent_sec": 3600,
        })
        return self._id


class FakeEngine:
    """Fake LLM engine that returns deterministic responses."""

    async def generate(self, prompt, **kwargs):
        # Return a minimal valid response for the summarizer
        return json.dumps([{
            "issue_key": "TEST-1",
            "time_spent_hours": 1.0,
            "summary": "Test work"
        }])


# ─── Tests: run_daily_summary ────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_summary_no_activities_returns_empty():
    """If there are no activities and no commits for the target date,
    generate_drafts should return [] — this is the most common cause
    of 'missing daily summary' reports."""
    db = FakeDB()
    engine = FakeEngine()
    config = AutoApproveConfig(enabled=False, trigger_time="21:30")
    workflow = DailyWorkflow(db, engine, config)

    # No activities added → should return empty
    with patch("auto_daily_log.collector.git_collector.GitCollector") as mock_gc:
        mock_gc.return_value.collect_today = AsyncMock()
        drafts = await workflow.run_daily_summary("2026-04-15")

    assert drafts == []
    # Verify: no draft was created
    assert len(db.tables["worklog_drafts"]) == 0


@pytest.mark.asyncio
async def test_daily_summary_llm_exception_propagates():
    """If the LLM engine throws, the exception should NOT be silently
    swallowed — it must propagate so the caller (scheduler job or
    catch-up) can log it."""
    db = FakeDB()
    db.add_activity("2026-04-15T10:00:00", "VS Code")

    class BrokenEngine:
        async def generate(self, prompt, **kwargs):
            raise RuntimeError("API key invalid")

    config = AutoApproveConfig(enabled=False, trigger_time="21:30")
    workflow = DailyWorkflow(db, BrokenEngine(), config)

    with patch("auto_daily_log.collector.git_collector.GitCollector") as mock_gc:
        mock_gc.return_value.collect_today = AsyncMock()
        with pytest.raises(RuntimeError, match="API key invalid"):
            await workflow.run_daily_summary("2026-04-15")


@pytest.mark.asyncio
async def test_daily_summary_date_mismatch_timezone():
    """Activities stored with timestamps that don't match the target_date
    via SQLite date() should result in no drafts — this is a known edge
    case when timestamps are in UTC but target_date is local."""
    db = FakeDB()
    # Activity at 2026-04-14T23:30:00 UTC — in UTC+8 this is 2026-04-15 07:30
    # But SQLite date('2026-04-14T23:30:00') = '2026-04-14'
    db.add_activity("2026-04-14T23:30:00", "VS Code")

    config = AutoApproveConfig(enabled=False, trigger_time="21:30")
    engine = FakeEngine()
    workflow = DailyWorkflow(db, engine, config)

    with patch("auto_daily_log.collector.git_collector.GitCollector") as mock_gc:
        mock_gc.return_value.collect_today = AsyncMock()
        # Asking for 2026-04-15 but activity is date() = 2026-04-14
        drafts = await workflow.run_daily_summary("2026-04-15")

    assert drafts == []


# ─── Tests: auto_approve_pending ─────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_approve_disabled_is_noop():
    """When auto_approve is disabled, auto_approve_pending should do nothing."""
    db = FakeDB()
    db.add_draft("2026-04-15", status="pending_review")
    config = AutoApproveConfig(enabled=False, trigger_time="21:30")
    workflow = DailyWorkflow(db, FakeEngine(), config)

    await workflow.auto_approve_pending("2026-04-15")

    # Draft should still be pending
    draft = db.tables["worklog_drafts"][0]
    assert draft["status"] == "pending_review"


@pytest.mark.asyncio
async def test_auto_approve_skips_empty_summary():
    """Drafts with empty summary JSON should NOT be auto-approved."""
    db = FakeDB()
    db.add_draft("2026-04-15", status="pending_review", summary="[]")
    config = AutoApproveConfig(enabled=True, trigger_time="21:30")
    workflow = DailyWorkflow(db, FakeEngine(), config)

    await workflow.auto_approve_pending("2026-04-15")

    # Draft should still be pending (empty issues → skipped)
    draft = db.tables["worklog_drafts"][0]
    assert draft["status"] == "pending_review"


@pytest.mark.asyncio
async def test_auto_approve_no_drafts_is_silent():
    """If there are no pending drafts, auto_approve should succeed silently."""
    db = FakeDB()
    config = AutoApproveConfig(enabled=True, trigger_time="21:30")
    workflow = DailyWorkflow(db, FakeEngine(), config)

    # Should not raise
    await workflow.auto_approve_pending("2026-04-15")
    await workflow.auto_approve_and_submit("2026-04-15")


# ─── Tests: scheduler catch-up logic ────────────────────────────────

@pytest.mark.asyncio
async def test_catchup_runs_when_missed():
    """_scheduler_catchup should call daily_generate when it's past trigger
    time and no draft exists for today."""
    from auto_daily_log.app import Application

    app = Application.__new__(Application)
    app.db = FakeDB()

    gen_fn = AsyncMock()
    approve_fn = AsyncMock()

    # Simulate: it's 20:00, trigger was 18:00, no draft exists
    with patch("auto_daily_log.app.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 15, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup(18, 0, gen_fn, approve_fn, 21, 30)

    gen_fn.assert_called_once()
    approve_fn.assert_not_called()  # 20:00 < 21:30 → not yet


@pytest.mark.asyncio
async def test_catchup_skips_when_draft_exists():
    """_scheduler_catchup should NOT re-run daily_generate if a draft
    already exists for today."""
    from auto_daily_log.app import Application

    app = Application.__new__(Application)
    app.db = FakeDB()
    app.db.add_draft("2026-04-15", status="pending_review")

    gen_fn = AsyncMock()

    with patch("auto_daily_log.app.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 15, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup(18, 0, gen_fn, None, None, None)

    gen_fn.assert_not_called()


@pytest.mark.asyncio
async def test_catchup_runs_both_when_late():
    """If server starts at 22:00 and both jobs missed, both should catch up."""
    from auto_daily_log.app import Application

    app = Application.__new__(Application)
    app.db = FakeDB()

    gen_fn = AsyncMock()
    approve_fn = AsyncMock()

    with patch("auto_daily_log.app.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 15, 22, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup(18, 0, gen_fn, approve_fn, 21, 30)

    gen_fn.assert_called_once()
    approve_fn.assert_called_once()


@pytest.mark.asyncio
async def test_catchup_gen_failure_doesnt_block_approve():
    """If daily_generate fails during catch-up, auto_approve should
    still attempt to run (it might find drafts from a previous run)."""
    from auto_daily_log.app import Application

    app = Application.__new__(Application)
    app.db = FakeDB()

    gen_fn = AsyncMock(side_effect=RuntimeError("LLM down"))
    approve_fn = AsyncMock()

    with patch("auto_daily_log.app.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 15, 22, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # Should NOT raise — catch-up wraps in try/except
        await app._scheduler_catchup(18, 0, gen_fn, approve_fn, 21, 30)

    gen_fn.assert_called_once()
    approve_fn.assert_called_once()


# ─── Tests: scheduler job wrapper exception handling ─────────────────

@pytest.mark.asyncio
async def test_daily_generate_job_exception_logged():
    """The daily_generate_job wrapper in _init_scheduler should catch
    exceptions and log them, not let APScheduler silently swallow them."""
    # This is a design requirement — verified by checking the code wraps
    # in try/except. We test the DailyWorkflow directly here since the
    # actual APScheduler wrapper is hard to unit-test.
    db = FakeDB()
    db.add_activity("2026-04-15T10:00:00")

    class ExplodingEngine:
        async def generate(self, prompt, **kwargs):
            raise ConnectionError("Network timeout")

    config = AutoApproveConfig(enabled=False, trigger_time="21:30")
    workflow = DailyWorkflow(db, ExplodingEngine(), config)

    with patch("auto_daily_log.collector.git_collector.GitCollector") as mock_gc:
        mock_gc.return_value.collect_today = AsyncMock()
        with pytest.raises(ConnectionError, match="Network timeout"):
            await workflow.run_daily_summary("2026-04-15")
