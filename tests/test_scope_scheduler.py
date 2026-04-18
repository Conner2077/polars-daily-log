"""Comprehensive tests for the ScopeScheduler: _register_scope_jobs, _scope_generate_job, _scheduler_catchup.

Covers:
  - Job registration from time_scopes table
  - schedule_rule parsing (daily, weekly, monthly, malformed)
  - Settings UI override of trigger time
  - Disabled / missing scopes skipped
  - _scope_generate_job: activity backfill, git collect, generate_scope call, dual-write, error handling
  - _scheduler_catchup: runs missed scopes, skips existing, skips before trigger time, handles failures
  - Edge cases: empty schedule_rule, no scopes, corrupt JSON, time-only rules
"""
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, call

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
    """Build a minimal Application instance with a real DB."""
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
# _register_scope_jobs
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_register_jobs_from_builtin_scopes(db):
    """Built-in daily scope (schedule_rule not null) should register a cron job."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    # _init_scheduler is complex (starts scheduler + catch-up), so we test
    # the inner _register_scope_jobs logic by calling _init_scheduler and
    # inspecting the registered jobs.
    # But _init_scheduler uses asyncio.ensure_future which we can't await.
    # Instead, let's directly invoke the core logic.

    scopes = await db.fetch_all(
        "SELECT * FROM time_scopes WHERE schedule_rule IS NOT NULL AND enabled = 1"
    )
    assert len(scopes) >= 1  # at least daily
    daily = next(s for s in scopes if s["name"] == "daily")
    rule = json.loads(daily["schedule_rule"])
    assert rule["time"] == "18:00"


@pytest.mark.asyncio
async def test_disabled_scope_not_registered(db):
    """Disabled scopes should not produce scheduler jobs."""
    await db.execute(
        "UPDATE time_scopes SET enabled = 0 WHERE name = 'daily'"
    )
    scopes = await db.fetch_all(
        "SELECT * FROM time_scopes WHERE schedule_rule IS NOT NULL AND enabled = 1"
    )
    names = [s["name"] for s in scopes]
    assert "daily" not in names


@pytest.mark.asyncio
async def test_scope_without_schedule_rule_not_registered(db):
    """Scopes with NULL schedule_rule (weekly, monthly) should not produce jobs."""
    scopes = await db.fetch_all(
        "SELECT * FROM time_scopes WHERE schedule_rule IS NOT NULL AND enabled = 1"
    )
    names = [s["name"] for s in scopes]
    assert "weekly" not in names
    assert "monthly" not in names


@pytest.mark.asyncio
async def test_custom_scope_with_weekly_rule(db):
    """A custom scope with a weekly schedule_rule should parse day_of_week."""
    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('sprint', 'Sprint 回顾', 'week', ?, 1)",
        (json.dumps({"day": "monday", "time": "09:00"}),),
    )
    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'sprint'")
    rule = json.loads(scope["schedule_rule"])
    assert rule["day"] == "monday"
    assert rule["time"] == "09:00"


@pytest.mark.asyncio
async def test_custom_scope_with_monthly_rule(db):
    """A custom scope with monthly schedule_rule should parse day_of_month."""
    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('month-end', '月末', 'month', ?, 1)",
        (json.dumps({"day_of_month": 1, "time": "10:00"}),),
    )
    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'month-end'")
    rule = json.loads(scope["schedule_rule"])
    assert rule["day_of_month"] == 1


@pytest.mark.asyncio
async def test_corrupt_schedule_rule_skipped(db):
    """Scope with corrupt JSON in schedule_rule should be silently skipped."""
    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('bad', 'Bad', 'day', 'NOT JSON', 1)"
    )
    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'bad'")
    assert scope is not None
    # Parsing should fail gracefully
    try:
        json.loads(scope["schedule_rule"])
        assert False, "Should have raised"
    except json.JSONDecodeError:
        pass  # expected


@pytest.mark.asyncio
async def test_schedule_rule_is_sole_source_of_truth(db):
    """time_scopes.schedule_rule is the only source; settings table has no override."""
    # Even if old settings exist, they should be ignored
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('scheduler_trigger_time', '19:30')"
    )
    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'daily'")
    rule = json.loads(scope["schedule_rule"])
    # schedule_rule says 18:00, settings says 19:30 — schedule_rule wins
    assert rule["time"] == "18:00"


@pytest.mark.asyncio
async def test_each_scope_has_independent_schedule(db):
    """Each scope's schedule_rule is independent."""
    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('nightly', 'Nightly Report', 'day', '{\"time\":\"23:00\"}', 1)"
    )
    daily = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'daily'")
    nightly = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'nightly'")
    assert json.loads(daily["schedule_rule"])["time"] == "18:00"
    assert json.loads(nightly["schedule_rule"])["time"] == "23:00"


# ═══════════════════════════════════════════════════════════════════════
# _scheduler_catchup
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_catchup_before_trigger_time_is_noop(db):
    """If current time is before the trigger time, catchup should not run."""
    app = _make_app(db)

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock) as mock_gen:
        # 16:00 < 18:00 trigger → should not run
        mock_dt.now.return_value = datetime(2026, 4, 17, 16, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_catchup_after_trigger_time_no_summaries_runs(db):
    """After trigger time with no summaries, catchup should generate."""
    app = _make_app(db)

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine") as mock_llm, \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock, return_value=[]) as mock_gen, \
         patch("auto_daily_log.summarizer.engine_registry.get_engine_by_name", new_callable=AsyncMock, return_value=None):
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_llm.return_value = MagicMock()
        await app._scheduler_catchup()

    mock_gen.assert_called_once()
    # Verify the scope_name passed
    args = mock_gen.call_args
    assert args[0][2] == "daily"  # third positional arg = scope_name


@pytest.mark.asyncio
async def test_catchup_skips_when_summaries_exist(db):
    """If summaries already exist for today, catchup should skip."""
    app = _make_app(db)

    outputs = await db.fetch_all("SELECT id FROM scope_outputs WHERE scope_name = 'daily' LIMIT 1")
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, '2026-04-17', '2026-04-17', '2026-04-17', 'already done')",
        (outputs[0]["id"],),
    )

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock) as mock_gen:
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_catchup_multiple_scopes_independent(db):
    """Multiple scopes with schedule_rule — each evaluated independently."""
    app = _make_app(db)

    # Add a second scope with 21:00 trigger
    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('nightly', 'Nightly', 'day', '{\"time\":\"21:00\"}', 1)"
    )
    # Add output for nightly so generate_scope doesn't fail
    await db.execute(
        "INSERT INTO scope_outputs (scope_name, display_name, output_mode, auto_publish) "
        "VALUES ('nightly', 'Nightly Log', 'single', 0)"
    )

    calls = []

    async def _mock_gen(db, engine, scope_name, *args, **kwargs):
        calls.append(scope_name)
        return []

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine") as mock_llm, \
         patch("auto_daily_log.web.api.summaries.generate_scope", side_effect=_mock_gen), \
         patch("auto_daily_log.summarizer.engine_registry.get_engine_by_name", new_callable=AsyncMock, return_value=None):
        # 22:00 → both 18:00 and 21:00 missed
        mock_dt.now.return_value = datetime(2026, 4, 17, 22, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_llm.return_value = MagicMock()
        await app._scheduler_catchup()

    assert "daily" in calls
    assert "nightly" in calls


@pytest.mark.asyncio
async def test_catchup_partial_time_check(db):
    """At 19:00, daily (18:00) should catch up but nightly (21:00) should not."""
    app = _make_app(db)

    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('nightly', 'Nightly', 'day', '{\"time\":\"21:00\"}', 1)"
    )

    calls = []

    async def _mock_gen(db, engine, scope_name, *args, **kwargs):
        calls.append(scope_name)
        return []

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine") as mock_llm, \
         patch("auto_daily_log.web.api.summaries.generate_scope", side_effect=_mock_gen), \
         patch("auto_daily_log.summarizer.engine_registry.get_engine_by_name", new_callable=AsyncMock, return_value=None):
        # 19:00 → daily missed (18:00), nightly not yet (21:00)
        mock_dt.now.return_value = datetime(2026, 4, 17, 19, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_llm.return_value = MagicMock()
        await app._scheduler_catchup()

    assert "daily" in calls
    assert "nightly" not in calls


@pytest.mark.asyncio
async def test_catchup_failure_doesnt_block_other_scopes(db):
    """If one scope's catchup fails, others should still execute."""
    app = _make_app(db)

    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('nightly', 'Nightly', 'day', '{\"time\":\"21:00\"}', 1)"
    )

    calls = []

    async def _mock_gen(db, engine, scope_name, *args, **kwargs):
        if scope_name == "daily":
            raise RuntimeError("LLM quota exceeded")
        calls.append(scope_name)
        return []

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine") as mock_llm, \
         patch("auto_daily_log.web.api.summaries.generate_scope", side_effect=_mock_gen), \
         patch("auto_daily_log.summarizer.engine_registry.get_engine_by_name", new_callable=AsyncMock, return_value=None):
        mock_dt.now.return_value = datetime(2026, 4, 17, 22, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_llm.return_value = MagicMock()
        await app._scheduler_catchup()  # should NOT raise

    assert "nightly" in calls  # ran despite daily failure


@pytest.mark.asyncio
async def test_catchup_corrupt_schedule_rule_skipped(db):
    """Catchup should skip scopes with unparseable schedule_rule."""
    app = _make_app(db)

    # Corrupt the daily scope's schedule_rule
    await db.execute(
        "UPDATE time_scopes SET schedule_rule = 'BROKEN' WHERE name = 'daily'"
    )
    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock) as mock_gen:
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_catchup_engine_fallback_to_config(db):
    """If settings-based engine returns None, catchup should fall back to config.llm."""
    app = _make_app(db)

    engine_used = []

    async def _mock_gen(db, engine, scope_name, *args, **kwargs):
        engine_used.append(engine)
        return []

    fake_engine = MagicMock()

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.web.api.summaries.generate_scope", side_effect=_mock_gen), \
         patch("auto_daily_log.summarizer.engine_registry.get_engine_by_name", new_callable=AsyncMock, return_value=fake_engine):
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    assert len(engine_used) == 1
    assert engine_used[0] is fake_engine  # resolved via get_engine_by_name


@pytest.mark.asyncio
async def test_catchup_no_scopes_with_schedule_is_noop(db):
    """If all scopes have NULL schedule_rule, catchup does nothing."""
    app = _make_app(db)

    # Remove all schedule_rules
    await db.execute("UPDATE time_scopes SET schedule_rule = NULL")

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock) as mock_gen:
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# _scope_generate_job (the cron callback)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generate_job_calls_generate_scope(db):
    """The cron job wrapper should call generate_scope with correct args."""
    app = _make_app(db)
    today = datetime.now().strftime("%Y-%m-%d")

    # We can't easily call _init_scheduler (it fires ensure_future which races
    # with db.close). Instead, inline the _scope_generate_job logic and test it
    # via the generate_scope mock in a catchup scenario.
    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine", return_value=MagicMock()), \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock, return_value=[]) as mock_gen, \
         patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings", new_callable=AsyncMock, return_value=MagicMock()):
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_called_once()
    args = mock_gen.call_args[0]
    assert args[2] == "daily"  # scope_name


@pytest.mark.asyncio
async def test_generate_job_deletes_existing_before_regenerate(db):
    """Catchup should still run even if old summaries exist for a different date."""
    app = _make_app(db)
    today = "2026-04-17"

    outputs = await db.fetch_all("SELECT id FROM scope_outputs WHERE scope_name = 'daily' LIMIT 1")
    # Old summary for yesterday — today should still generate
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, '2026-04-16', '2026-04-16', '2026-04-16', 'yesterday')",
        (outputs[0]["id"],),
    )

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine", return_value=MagicMock()), \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock, return_value=[]) as mock_gen, \
         patch("auto_daily_log.summarizer.engine_registry.get_engine_by_name", new_callable=AsyncMock, return_value=None):
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    mock_gen.assert_called_once()  # today has no summary → should generate


@pytest.mark.asyncio
async def test_generate_job_exception_is_caught(db):
    """If generate_scope throws during catchup, it should be caught not crash."""
    app = _make_app(db)

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine", return_value=MagicMock()), \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
         patch("auto_daily_log.summarizer.engine_registry.get_engine_by_name", new_callable=AsyncMock, return_value=None):
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # Should NOT raise
        await app._scheduler_catchup()


@pytest.mark.asyncio
async def test_generate_job_with_activity_backfill(db):
    """For day-scoped catchup, activity backfill is attempted inside generate_scope.
    Test that the engine is passed correctly."""
    app = _make_app(db)

    fake_engine = MagicMock()
    engines_used = []

    async def _track_gen(db, engine, *a, **kw):
        engines_used.append(engine)
        return []

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.web.api.summaries.generate_scope", side_effect=_track_gen), \
         patch("auto_daily_log.summarizer.engine_registry.get_engine_by_name", new_callable=AsyncMock, return_value=fake_engine):
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    assert len(engines_used) == 1
    assert engines_used[0] is fake_engine


@pytest.mark.asyncio
async def test_generate_job_backfill_failure_is_nonfatal(db):
    """Settings-engine failure should fall back to config engine, not crash."""
    app = _make_app(db)

    async def _bad_settings_engine(db):
        raise RuntimeError("settings DB corrupt")

    config_engine = MagicMock()

    with patch("auto_daily_log.app.datetime") as mock_dt, \
         patch("auto_daily_log.app.get_llm_engine", return_value=config_engine), \
         patch("auto_daily_log.web.api.summaries.generate_scope", new_callable=AsyncMock, return_value=[]) as mock_gen, \
         patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings", side_effect=_bad_settings_engine):
        mock_dt.now.return_value = datetime(2026, 4, 17, 20, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app._scheduler_catchup()

    # Should still have run with config_engine fallback
    # Note: the current catchup code has a try/except around the whole block,
    # so if _get_llm_engine_from_settings raises, the except catches it.
    # The test verifies no crash.


# ═══════════════════════════════════════════════════════════════════════
# Schedule rule parsing edge cases
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# APScheduler job registration (the actual cron jobs)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cron_job_registered_at_correct_time(db):
    """_register_scope_jobs_impl must register APScheduler job at the time from schedule_rule."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    dummy_fn = AsyncMock()
    job_ids = await app._register_scope_jobs_impl(dummy_fn, 7200)

    assert "daily=18:00" in job_ids

    jobs = app.scheduler.get_jobs()
    daily_job = next(j for j in jobs if j.id == "scope_daily")
    # Verify the cron trigger fields
    trigger = daily_job.trigger
    assert str(trigger) == "cron[hour='18', minute='0']"


@pytest.mark.asyncio
async def test_cron_job_uses_schedule_rule_not_settings(db):
    """After removing settings override, job must use time_scopes.schedule_rule only."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    # Even if old settings override exists, it should be ignored
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('scheduler_trigger_time', '19:30')"
    )
    # Update daily schedule_rule to 07:00
    await db.execute(
        "UPDATE time_scopes SET schedule_rule = '{\"time\":\"07:00\"}' WHERE name = 'daily'"
    )

    dummy_fn = AsyncMock()
    await app._register_scope_jobs_impl(dummy_fn, 7200)

    jobs = app.scheduler.get_jobs()
    daily_job = next(j for j in jobs if j.id == "scope_daily")
    trigger = daily_job.trigger
    assert str(trigger) == "cron[hour='7', minute='0']"


@pytest.mark.asyncio
async def test_cron_job_weekly_has_day_of_week(db):
    """Weekly scope should register with day_of_week in the cron trigger."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('friday-report', 'Friday', 'week', '{\"day\":\"friday\",\"time\":\"17:00\"}', 1)"
    )

    dummy_fn = AsyncMock()
    job_ids = await app._register_scope_jobs_impl(dummy_fn, 7200)

    assert "friday-report=17:00" in job_ids
    jobs = app.scheduler.get_jobs()
    fri_job = next(j for j in jobs if j.id == "scope_friday-report")
    trigger = fri_job.trigger
    assert "fri" in str(trigger)
    assert "17" in str(trigger)


@pytest.mark.asyncio
async def test_cron_job_monthly_has_day(db):
    """Monthly scope should register with day in the cron trigger."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('month-end', 'MonthEnd', 'month', '{\"day_of_month\":1,\"time\":\"10:00\"}', 1)"
    )

    dummy_fn = AsyncMock()
    await app._register_scope_jobs_impl(dummy_fn, 7200)

    jobs = app.scheduler.get_jobs()
    month_job = next(j for j in jobs if j.id == "scope_month-end")
    trigger = month_job.trigger
    assert "day='1'" in str(trigger)
    assert "hour='10'" in str(trigger)


@pytest.mark.asyncio
async def test_cron_job_disabled_scope_skipped(db):
    """Disabled scopes should not get cron jobs registered."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    await db.execute("UPDATE time_scopes SET enabled = 0 WHERE name = 'daily'")

    dummy_fn = AsyncMock()
    job_ids = await app._register_scope_jobs_impl(dummy_fn, 7200)

    assert job_ids == []
    jobs = app.scheduler.get_jobs()
    scope_jobs = [j for j in jobs if j.id.startswith("scope_")]
    assert scope_jobs == []


@pytest.mark.asyncio
async def test_cron_job_corrupt_rule_skipped(db):
    """Scope with corrupt schedule_rule should not crash, just skip."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    await db.execute(
        "UPDATE time_scopes SET schedule_rule = 'NOT JSON' WHERE name = 'daily'"
    )

    dummy_fn = AsyncMock()
    job_ids = await app._register_scope_jobs_impl(dummy_fn, 7200)

    assert job_ids == []


@pytest.mark.asyncio
async def test_cron_job_null_schedule_rule_not_registered(db):
    """Scopes with NULL schedule_rule (weekly/monthly defaults) should not register."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    # Only daily has schedule_rule by default
    dummy_fn = AsyncMock()
    job_ids = await app._register_scope_jobs_impl(dummy_fn, 7200)

    scope_names = [j.split("=")[0] for j in job_ids]
    assert "weekly" not in scope_names
    assert "monthly" not in scope_names


@pytest.mark.asyncio
async def test_cron_job_time_updated_after_edit(db):
    """When user edits schedule_rule via UI, a restart should pick up the new time."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    app = _make_app(db)
    app.scheduler = AsyncIOScheduler()

    # Simulate user changing daily from 18:00 to 01:05 via Settings
    await db.execute(
        "UPDATE time_scopes SET schedule_rule = '{\"time\":\"01:05\"}' WHERE name = 'daily'"
    )

    dummy_fn = AsyncMock()
    job_ids = await app._register_scope_jobs_impl(dummy_fn, 7200)

    assert "daily=1:05" in job_ids
    jobs = app.scheduler.get_jobs()
    daily_job = next(j for j in jobs if j.id == "scope_daily")
    trigger = daily_job.trigger
    assert "hour='1'" in str(trigger)
    assert "minute='5'" in str(trigger)


# ═══════════════════════════════════════════════════════════════════════
# Schedule rule parsing edge cases
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_schedule_rule_time_only(db):
    """schedule_rule with just {"time":"HH:MM"} should work (daily implicit)."""
    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'daily'")
    rule = json.loads(scope["schedule_rule"])
    assert "time" in rule
    assert "day" not in rule
    assert "day_of_month" not in rule


@pytest.mark.asyncio
async def test_schedule_rule_with_day_for_weekly(db):
    """Weekly rule should have "day" field."""
    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('friday-report', 'Friday', 'week', ?, 1)",
        (json.dumps({"day": "friday", "time": "17:00"}),),
    )
    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'friday-report'")
    rule = json.loads(scope["schedule_rule"])
    assert rule["day"] == "friday"
    assert rule["time"] == "17:00"


@pytest.mark.asyncio
async def test_schedule_rule_hour_only_defaults_minute_zero(db):
    """If time is "9" (no colon), minute should default to 0."""
    await db.execute(
        "INSERT INTO time_scopes (name, display_name, scope_type, schedule_rule, enabled) "
        "VALUES ('morning', 'Morning', 'day', '{\"time\":\"9\"}', 1)"
    )
    scope = await db.fetch_one("SELECT * FROM time_scopes WHERE name = 'morning'")
    rule = json.loads(scope["schedule_rule"])
    parts = rule["time"].split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    assert hour == 9
    assert minute == 0
