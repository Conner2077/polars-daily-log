"""Tests for scheduler catch-up, misfire, and daily_generate edge cases.

Covers the scenarios where daily_generate or auto_approve might silently
fail or not produce output despite the server running and collector active.
"""
import json
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from auto_daily_log.scheduler.jobs import DailyWorkflow
from auto_daily_log.config import AutoApproveConfig
from auto_daily_log.models.database import Database


# ─── Helpers ─────────────────────────────────────────────────────────

class FakeDB:
    """In-memory store that mimics Database.fetch_all / fetch_one / execute."""

    def __init__(self):
        self.tables = {"activities": [], "git_commits": [], "worklog_drafts": [],
                       "audit_logs": [], "jira_issues": [], "settings": [],
                       "collectors": [], "time_scopes": [], "summaries": []}
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
            for row in self.tables.get(table, []):
                if "status" in sql.lower() and params:
                    pass
        if sql.strip().upper().startswith("DELETE"):
            table = self._guess_table(sql)
            if params:
                self.tables[table] = [r for r in self.tables.get(table, [])
                                       if not any(r.get(k) == p for k in r for p in (params if isinstance(params, (list, tuple)) else [params]))]
        return None

    def _guess_table(self, sql):
        sql_up = sql.upper()
        for t in self.tables:
            if t.upper() in sql_up:
                return t
        return "unknown"

    def _filter(self, sql, params, rows):
        if not params:
            return rows
        params_list = list(params if isinstance(params, (list, tuple)) else [params])
        result = []
        for r in rows:
            match = True
            # Match all string params against any matching field value
            for p in params_list:
                if isinstance(p, str):
                    found_in_any_field = any(r.get(k) == p for k in r if isinstance(r.get(k), str))
                    if not found_in_any_field:
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

    def add_time_scope(self, name, schedule_rule, scope_type="day", enabled=1):
        self.tables["time_scopes"].append({
            "name": name, "schedule_rule": schedule_rule,
            "scope_type": scope_type, "enabled": enabled,
        })

    def add_summary(self, scope_name, date):
        self._id += 1
        self.tables["summaries"].append({
            "id": self._id, "scope_name": scope_name, "date": date,
        })
        return self._id


class FakeEngine:
    """Fake LLM engine that returns deterministic responses."""

    async def generate(self, prompt, **kwargs):
        return json.dumps([{
            "issue_key": "TEST-1",
            "time_spent_hours": 1.0,
            "summary": "Test work"
        }])


# ─── Tests: run_daily_summary ────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_summary_no_activities_returns_empty():
    db = FakeDB()
    engine = FakeEngine()
    config = AutoApproveConfig(enabled=False, trigger_time="21:30")
    workflow = DailyWorkflow(db, engine, config)

    with patch("auto_daily_log.collector.git_collector.GitCollector") as mock_gc:
        mock_gc.return_value.collect_today = AsyncMock()
        drafts = await workflow.run_daily_summary("2026-04-15")

    assert drafts == []
    assert len(db.tables["worklog_drafts"]) == 0


@pytest.mark.asyncio
async def test_daily_summary_llm_exception_propagates():
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
    db = FakeDB()
    db.add_activity("2026-04-14T23:30:00", "VS Code")

    config = AutoApproveConfig(enabled=False, trigger_time="21:30")
    engine = FakeEngine()
    workflow = DailyWorkflow(db, engine, config)

    with patch("auto_daily_log.collector.git_collector.GitCollector") as mock_gc:
        mock_gc.return_value.collect_today = AsyncMock()
        drafts = await workflow.run_daily_summary("2026-04-15")

    assert drafts == []


# ─── Tests: auto_approve_pending ─────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_approve_disabled_is_noop():
    db = FakeDB()
    db.add_draft("2026-04-15", status="pending_review")
    config = AutoApproveConfig(enabled=False, trigger_time="21:30")
    workflow = DailyWorkflow(db, FakeEngine(), config)

    await workflow.auto_approve_pending("2026-04-15")
    draft = db.tables["worklog_drafts"][0]
    assert draft["status"] == "pending_review"


@pytest.mark.asyncio
async def test_auto_approve_skips_empty_summary():
    db = FakeDB()
    db.add_draft("2026-04-15", status="pending_review", summary="[]")
    config = AutoApproveConfig(enabled=True, trigger_time="21:30")
    workflow = DailyWorkflow(db, FakeEngine(), config)

    await workflow.auto_approve_pending("2026-04-15")
    draft = db.tables["worklog_drafts"][0]
    assert draft["status"] == "pending_review"


@pytest.mark.asyncio
async def test_auto_approve_no_drafts_is_silent():
    db = FakeDB()
    config = AutoApproveConfig(enabled=True, trigger_time="21:30")
    workflow = DailyWorkflow(db, FakeEngine(), config)

    await workflow.auto_approve_pending("2026-04-15")
    await workflow.auto_approve_and_submit("2026-04-15")


# ─── Tests: ScopeScheduler catch-up logic ────────────────────────────

@pytest.mark.asyncio
async def test_catchup_runs_when_missed():
    """_scheduler_catchup should generate when past trigger time and no summaries exist."""
    from auto_daily_log.app import Application

    app = Application.__new__(Application)
    app.db = FakeDB()
    app.config = MagicMock()
    app.config.llm = MagicMock()
    app.db.add_time_scope("daily", '{"time":"18:00"}')

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine") as mock_llm, \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock, return_value=[]) as mock_gen, \
         patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings", new_callable=AsyncMock, return_value=None):
        mock_dt.now.return_value = datetime(2026, 4, 15, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_llm.return_value = FakeEngine()
        await app._scheduler_catchup()

    mock_gen.assert_called_once()


@pytest.mark.asyncio
async def test_catchup_skips_when_draft_exists():
    """_scheduler_catchup should NOT re-run if summaries already exist for today."""
    from auto_daily_log.app import Application

    app = Application.__new__(Application)
    app.db = FakeDB()
    app.config = MagicMock()
    app.db.add_time_scope("daily", '{"time":"18:00"}')
    app.db.add_summary("daily", "2026-04-15")

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock) as mock_gen:
        mock_dt.now.return_value = datetime(2026, 4, 15, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_catchup_runs_both_when_late():
    """If two scopes both missed, both should catch up."""
    from auto_daily_log.app import Application

    app = Application.__new__(Application)
    app.db = FakeDB()
    app.config = MagicMock()
    app.config.llm = MagicMock()
    app.db.add_time_scope("daily", '{"time":"18:00"}')
    app.db.add_time_scope("nightly", '{"time":"21:00"}', scope_type="day")

    call_count = 0

    async def _mock_gen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return []

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine") as mock_llm, \
         patch("auto_daily_log.web.api.summaries.generate_scope", side_effect=_mock_gen), \
         patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings", new_callable=AsyncMock, return_value=None):
        mock_dt.now.return_value = datetime(2026, 4, 15, 22, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_llm.return_value = FakeEngine()
        await app._scheduler_catchup()

    assert call_count == 2


@pytest.mark.asyncio
async def test_catchup_gen_failure_doesnt_block_approve():
    """If one scope fails during catch-up, others should still run."""
    from auto_daily_log.app import Application

    app = Application.__new__(Application)
    app.db = FakeDB()
    app.config = MagicMock()
    app.config.llm = MagicMock()
    app.db.add_time_scope("daily", '{"time":"18:00"}')
    app.db.add_time_scope("nightly", '{"time":"21:00"}', scope_type="day")

    calls = []

    async def _mock_gen(db, engine, scope_name, *args, **kwargs):
        if scope_name == "daily":
            raise RuntimeError("LLM down")
        calls.append(scope_name)
        return []

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine") as mock_llm, \
         patch("auto_daily_log.web.api.summaries.generate_scope", side_effect=_mock_gen), \
         patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings", new_callable=AsyncMock, return_value=None):
        mock_dt.now.return_value = datetime(2026, 4, 15, 22, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_llm.return_value = FakeEngine()
        # Should NOT raise — catch-up wraps in try/except
        await app._scheduler_catchup()

    # nightly should still have run despite daily failing
    assert "nightly" in calls


# ─── Tests: scheduler job wrapper exception handling ─────────────────

@pytest.mark.asyncio
async def test_daily_generate_job_exception_logged():
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
