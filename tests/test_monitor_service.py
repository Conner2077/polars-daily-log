import json
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock
from pathlib import Path
from auto_daily_log.monitor.service import MonitorService
from auto_daily_log.models.database import Database
from auto_daily_log.config import MonitorConfig


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_sample_once_stores_activity(db, tmp_path):
    config = MonitorConfig(ocr_enabled=False, interval_sec=30)
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    with patch.object(service, "_capture_raw") as mock_capture:
        mock_capture.return_value = {
            "app_name": "IntelliJ IDEA",
            "window_title": "Main.java — project",
            "url": None,
            "wecom_group": None,
            "screenshot_path": None,
            "ocr_text": None,
        }
        await service.sample_once()

    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 1
    assert rows[0]["app_name"] == "IntelliJ IDEA"
    assert rows[0]["category"] == "coding"


@pytest.mark.asyncio
async def test_privacy_blocklist_skips_app(db, tmp_path):
    config = MonitorConfig(
        ocr_enabled=False,
        privacy={"blocked_apps": ["WeChat"], "blocked_urls": []},
    )
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    with patch.object(service, "_capture_raw") as mock_capture:
        mock_capture.return_value = {
            "app_name": "WeChat",
            "window_title": "Chat",
            "url": None,
            "wecom_group": None,
            "screenshot_path": None,
            "ocr_text": None,
        }
        await service.sample_once()

    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_merge_consecutive_same_activity(db, tmp_path):
    config = MonitorConfig(ocr_enabled=False, interval_sec=30)
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    raw = {
        "app_name": "IntelliJ IDEA",
        "window_title": "Main.java",
        "url": None,
        "wecom_group": None,
        "screenshot_path": None,
        "ocr_text": None,
    }
    with patch.object(service, "_capture_raw", return_value=raw):
        await service.sample_once()
        await service.sample_once()

    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 1
    assert rows[0]["duration_sec"] == 60
