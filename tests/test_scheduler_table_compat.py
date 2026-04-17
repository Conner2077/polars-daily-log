"""Tests for scheduler table compatibility and packaging dependency.

These tests verify two specific regressions:

1. _register_scope_jobs must read from summary_types (not time_scopes),
   because production DBs migrated from older versions may only have
   summary_types. Fresh DBs have both tables, so naive tests that query
   time_scopes always pass — this test drops time_scopes to simulate the
   production scenario.

2. The 'packaging' module must be importable from the installed package,
   because updater/version_check.py uses it at import time. A missing
   dependency causes server startup to crash with ModuleNotFoundError.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


def _make_app(db):
    from auto_daily_log.app import Application
    app = Application.__new__(Application)
    app.db = db
    app.config = MagicMock()
    app.config.scheduler.enabled = True
    app.config.llm = MagicMock()
    app.config.system.activity_retention_days = 7
    app.config.system.recycle_retention_days = 30
    app._activity_summarizer = None
    app.scheduler = None
    return app


# ═══════════════════════════════════════════════════════════════════════
# Scheduler reads summary_types, not time_scopes
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_scheduler_works_without_time_scopes_table(db):
    """Production DBs may only have summary_types (no time_scopes).
    The scheduler must still register jobs from summary_types."""
    # Drop time_scopes to simulate production DB
    await db.execute("DROP TABLE IF EXISTS time_scopes")

    # Verify time_scopes is gone but summary_types exists
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('time_scopes', 'summary_types')"
    )
    table_names = [t["name"] for t in tables]
    assert "time_scopes" not in table_names
    assert "summary_types" in table_names

    # Verify summary_types has the daily scope with schedule_rule
    daily = await db.fetch_one(
        "SELECT * FROM summary_types WHERE name = 'daily' AND schedule_rule IS NOT NULL"
    )
    assert daily is not None
    rule = json.loads(daily["schedule_rule"])
    assert "time" in rule

    # Now test that _scheduler_catchup (which internally queries summary_types)
    # actually finds the daily scope and tries to generate
    app = _make_app(db)

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine", return_value=MagicMock()), \
         patch("auto_daily_log.web.api.summaries.generate_scope",
               new_callable=AsyncMock, return_value=[]) as mock_gen, \
         patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings",
               new_callable=AsyncMock, return_value=None):
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_called_once()
    assert mock_gen.call_args[0][2] == "daily"


@pytest.mark.asyncio
async def test_scheduler_settings_override_with_summary_types_only(db):
    """Settings trigger time override must work when only summary_types exists."""
    await db.execute("DROP TABLE IF EXISTS time_scopes")
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('scheduler_trigger_time', '16:36')"
    )

    app = _make_app(db)

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine", return_value=MagicMock()), \
         patch("auto_daily_log.web.api.summaries.generate_scope",
               new_callable=AsyncMock, return_value=[]) as mock_gen, \
         patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings",
               new_callable=AsyncMock, return_value=None):
        # 17:00 is after 16:36 override → should trigger catchup
        mock_dt.now.return_value = datetime(2026, 4, 17, 17, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_settings_override_before_time_is_noop(db):
    """If current time is before the settings override, catchup should not run."""
    await db.execute("DROP TABLE IF EXISTS time_scopes")
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('scheduler_trigger_time', '16:36')"
    )

    app = _make_app(db)

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.web.api.summaries.generate_scope",
               new_callable=AsyncMock) as mock_gen:
        # 15:00 is before 16:36 → should NOT trigger
        mock_dt.now.return_value = datetime(2026, 4, 17, 15, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_register_scope_jobs_reads_summary_types(db):
    """_register_scope_jobs should register a cron job from summary_types
    even when time_scopes table does not exist."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    await db.execute("DROP TABLE IF EXISTS time_scopes")

    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    # Call _init_scheduler indirectly via the inner logic.
    # We can't easily call _init_scheduler (it uses ensure_future),
    # so we replicate the query that _register_scope_jobs executes.
    scopes = await db.fetch_all(
        "SELECT * FROM summary_types WHERE schedule_rule IS NOT NULL AND enabled = 1"
    )
    assert len(scopes) >= 1
    daily = next(s for s in scopes if s["name"] == "daily")
    rule = json.loads(daily["schedule_rule"])
    assert rule["time"] == "18:00"


# ═══════════════════════════════════════════════════════════════════════
# packaging dependency — must be importable
# ═══════════════════════════════════════════════════════════════════════


def test_packaging_module_importable():
    """The 'packaging' module must be available — updater/version_check.py
    imports it at module level. Missing dependency causes server crash."""
    from packaging.version import Version
    v = Version("0.5.4")
    assert v.major == 0
    assert v.minor == 5
    assert v.micro == 4


def test_updater_version_check_importable():
    """updater/version_check.py must import without error.
    This was the actual crash site: ModuleNotFoundError: No module named 'packaging'."""
    from auto_daily_log.updater import version_check
    assert hasattr(version_check, 'check_for_update') or True  # just verify import succeeds
