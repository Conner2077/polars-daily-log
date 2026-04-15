"""Tests for the built-in collector — CollectorRuntime + LocalSQLiteBackend.

Mirrors the classic MonitorService behaviour: inserts into the real DB,
aggregates same-window samples, and picks up settings-table overrides
via the LocalSQLiteBackend.heartbeat() path.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_daily_log.models.backends import LocalSQLiteBackend
from auto_daily_log.models.database import Database
from auto_daily_log_collector.config import CollectorConfig
from auto_daily_log_collector.enricher import ActivityEnricher
from auto_daily_log_collector.runner import CollectorRuntime


def _make_adapter(app="Visual Studio Code", title="main.py", url=None, idle=0.0):
    adapter = MagicMock()
    adapter.platform_id.return_value = "macos"
    adapter.platform_detail.return_value = "macOS test"
    adapter.capabilities.return_value = {"screenshot", "idle"}
    adapter.get_frontmost_app.return_value = app
    adapter.get_window_title.return_value = title
    adapter.get_browser_tab.return_value = (None, url)
    adapter.get_wecom_chat_name.return_value = None
    adapter.get_idle_seconds.return_value = idle
    return adapter


async def _make_builtin(tmp_path, adapter, **cfg_overrides):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    backend = LocalSQLiteBackend(db)
    config = CollectorConfig(
        server_url="http://builtin.local",
        name="Built-in (this machine)",
        interval_sec=30,
        idle_threshold_sec=180,
        ocr_enabled=False,
        ocr_engine="tesseract",
        phash_enabled=False,
        data_dir=str(tmp_path / "cdata"),
        **cfg_overrides,
    )
    enricher = ActivityEnricher(
        screenshot_dir=tmp_path / "ss",
        hostile_apps_applescript=["WeCom"],
        hostile_apps_screenshot=[],
        phash_enabled=False,
    )
    runtime = CollectorRuntime(
        config=config,
        backend=backend,
        adapter=adapter,
        enricher=enricher,
        machine_id="local",
        skip_http_register=True,
    )
    await runtime.ensure_registered()
    return runtime, db


@pytest.mark.asyncio
async def test_builtin_inserts_activity_with_local_machine_id(tmp_path):
    adapter = _make_adapter()
    runtime, db = await _make_builtin(tmp_path, adapter)
    try:
        await runtime.sample_once()
        rows = await db.fetch_all("SELECT * FROM activities")
        assert len(rows) == 1
        assert rows[0]["machine_id"] == "local"
        assert rows[0]["app_name"] == "Visual Studio Code"
        assert rows[0]["category"] == "coding"
        assert rows[0]["duration_sec"] == 30
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_builtin_same_window_accumulates_into_one_row(tmp_path):
    adapter = _make_adapter()
    runtime, db = await _make_builtin(tmp_path, adapter)
    try:
        await runtime.sample_once()
        await runtime.sample_once()
        await runtime.sample_once()

        rows = await db.fetch_all("SELECT * FROM activities")
        assert len(rows) == 1
        # First sample sets 30. _pending_extend_sec holds the next 60 until a
        # window change or idle transition — that's how network calls stay
        # bounded in the HTTP path. Force a flush by switching window.
        adapter.get_frontmost_app.return_value = "Safari"
        await runtime.sample_once()

        rows = await db.fetch_all("SELECT * FROM activities ORDER BY id")
        assert len(rows) == 2
        assert rows[0]["app_name"] == "Visual Studio Code"
        assert rows[0]["duration_sec"] == 90  # 30 + 60 flushed on switch
        assert rows[1]["app_name"] == "Safari"
        assert rows[1]["duration_sec"] == 30
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_builtin_idle_creates_single_row_and_aggregates(tmp_path):
    adapter = _make_adapter(idle=300.0)
    runtime, db = await _make_builtin(tmp_path, adapter)
    try:
        await runtime.sample_once()  # first idle insert
        await runtime.sample_once()
        await runtime.sample_once()

        rows = await db.fetch_all("SELECT * FROM activities")
        assert len(rows) == 1
        assert rows[0]["category"] == "idle"
        assert rows[0]["app_name"] == "System"
        assert rows[0]["duration_sec"] == 90  # 30 + 30 + 30
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_builtin_heartbeat_picks_up_settings_override(tmp_path):
    adapter = _make_adapter()
    runtime, db = await _make_builtin(tmp_path, adapter)
    try:
        # Simulate the UI flipping ocr_enabled = true + interval = 45
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("monitor_ocr_enabled", "true"),
        )
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("monitor_interval_sec", "45"),
        )

        assert runtime.config.ocr_enabled is False
        assert runtime.config.interval_sec == 30

        await runtime.heartbeat()

        assert runtime.config.ocr_enabled is True
        assert runtime.config.interval_sec == 45
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_builtin_blocked_app_not_recorded(tmp_path):
    adapter = _make_adapter(app="1Password", title="Vault")
    runtime, db = await _make_builtin(tmp_path, adapter, blocked_apps=["1Password"])
    try:
        result = await runtime.sample_once()
        assert result is None
        rows = await db.fetch_all("SELECT * FROM activities")
        assert rows == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_builtin_hostile_app_skips_title_probe_and_still_inserts(tmp_path):
    adapter = _make_adapter(app="WeCom", title="group-chat")
    runtime, db = await _make_builtin(tmp_path, adapter)
    try:
        await runtime.sample_once()

        adapter.get_window_title.assert_not_called()
        adapter.get_browser_tab.assert_not_called()

        rows = await db.fetch_all("SELECT * FROM activities")
        assert len(rows) == 1
        assert rows[0]["app_name"] == "WeCom"
        assert rows[0]["window_title"] is None
    finally:
        await db.close()
