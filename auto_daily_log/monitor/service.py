import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import MonitorConfig
from ..models.database import Database
from .classifier import classify_activity
from .platforms.detect import get_platform_module
from .screenshot import capture_screenshot
from .ocr import ocr_image


class MonitorService:
    def __init__(self, db: Database, config: MonitorConfig, screenshot_dir: Path):
        self._db = db
        self._config = config
        self._screenshot_dir = screenshot_dir
        self._platform = get_platform_module()
        self._last_app: Optional[str] = None
        self._last_title: Optional[str] = None
        self._last_id: Optional[int] = None
        self._running = False

    def _capture_raw(self) -> dict:
        app_name = self._platform.get_frontmost_app()
        window_title = self._platform.get_window_title(app_name) if app_name else None
        tab_title, url = (
            self._platform.get_browser_tab(app_name) if app_name else (None, None)
        )
        wecom_group = self._platform.get_wecom_chat_name(app_name) if app_name else None

        screenshot_path = None
        ocr_text = None
        if self._config.ocr_enabled:
            today_dir = self._screenshot_dir / datetime.now().strftime("%Y-%m-%d")
            screenshot_path = capture_screenshot(today_dir)
            if screenshot_path:
                ocr_text = ocr_image(screenshot_path, self._config.ocr_engine)

        return {
            "app_name": app_name,
            "window_title": tab_title or window_title,
            "url": url,
            "wecom_group": wecom_group,
            "screenshot_path": str(screenshot_path) if screenshot_path else None,
            "ocr_text": ocr_text,
        }

    def _is_blocked(self, raw: dict) -> bool:
        app = raw.get("app_name") or ""
        url = raw.get("url") or ""
        for blocked in self._config.privacy.blocked_apps:
            if blocked.lower() in app.lower():
                return True
        for blocked in self._config.privacy.blocked_urls:
            if blocked.lower() in url.lower():
                return True
        return False

    async def sample_once(self) -> None:
        raw = self._capture_raw()
        if not raw["app_name"] or self._is_blocked(raw):
            return

        app_name = raw["app_name"]
        window_title = raw["window_title"]

        # Merge consecutive same activity
        if app_name == self._last_app and window_title == self._last_title and self._last_id:
            await self._db.execute(
                "UPDATE activities SET duration_sec = duration_sec + ? WHERE id = ?",
                (self._config.interval_sec, self._last_id),
            )
            return

        category, confidence, hints = classify_activity(app_name, window_title, raw["url"])

        signals = {
            "browser_url": raw["url"],
            "wecom_group_name": raw["wecom_group"],
            "screenshot_path": raw["screenshot_path"],
            "ocr_text": raw["ocr_text"],
            "hints": hints,
        }

        row_id = await self._db.execute(
            """INSERT INTO activities
               (timestamp, app_name, window_title, category, confidence, url, signals, duration_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                app_name,
                window_title,
                category,
                confidence,
                raw["url"],
                json.dumps(signals, ensure_ascii=False),
                self._config.interval_sec,
            ),
        )

        self._last_app = app_name
        self._last_title = window_title
        self._last_id = row_id

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.sample_once()
            except Exception as e:
                print(f"[Monitor] Error: {e}")
            await asyncio.sleep(self._config.interval_sec)

    def stop(self) -> None:
        self._running = False
