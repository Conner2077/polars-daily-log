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
from .phash import compute_phash, is_similar
from .idle import get_idle_seconds
from .watchdog import MonitorTrace


class MonitorService:
    def __init__(self, db: Database, config: MonitorConfig, screenshot_dir: Path, trace: Optional[MonitorTrace] = None):
        self._db = db
        self._config = config
        self._screenshot_dir = screenshot_dir
        self._platform = get_platform_module()
        self._trace = trace or MonitorTrace()
        self._last_app: Optional[str] = None
        self._last_title: Optional[str] = None
        self._last_id: Optional[int] = None
        self._last_phash = None
        self._last_ocr_text: Optional[str] = None
        self._last_was_idle: bool = False
        self._running = False

    @property
    def trace(self) -> MonitorTrace:
        return self._trace

    async def _get_runtime_config(self) -> dict:
        """Read latest monitor settings from DB, fallback to config.yaml values."""
        rows = await self._db.fetch_all(
            "SELECT key, value FROM settings WHERE key IN "
            "('monitor_ocr_enabled', 'monitor_ocr_engine', 'monitor_interval_sec')"
        )
        s = {r["key"]: r["value"] for r in rows}

        def _bool(val, default):
            if val is None:
                return default
            return str(val).lower() in ("true", "1", "yes", "on")

        return {
            "ocr_enabled": _bool(s.get("monitor_ocr_enabled"), self._config.ocr_enabled),
            "ocr_engine": s.get("monitor_ocr_engine") or self._config.ocr_engine,
            "interval_sec": int(s.get("monitor_interval_sec") or self._config.interval_sec),
        }

    def _capture_raw_inner(self) -> dict:
        self._trace.log("get_frontmost_app")
        app_name = self._platform.get_frontmost_app()
        self._trace.log("got_frontmost", app=app_name)

        # Apps whose internals we don't probe via AppleScript/UI APIs
        # (they self-exit when probed). Configurable via config.monitor.
        hostile_as = {s.lower() for s in self._config.hostile_apps_applescript}
        app_lower = (app_name or "").lower()
        is_hostile = app_lower in hostile_as

        window_title = None
        if app_name and not is_hostile:
            self._trace.log("get_window_title", app=app_name)
            window_title = self._platform.get_window_title(app_name)
            self._trace.log("got_window_title", app=app_name, title=window_title)
        elif is_hostile:
            self._trace.log("skip_window_title_hostile", app=app_name)

        tab_title, url = (None, None)
        if app_name and not is_hostile:
            self._trace.log("get_browser_tab", app=app_name)
            tab_title, url = self._platform.get_browser_tab(app_name)

        wecom_group = None
        if app_name and not is_hostile:
            self._trace.log("get_wecom_chat_name", app=app_name)
            wecom_group = self._platform.get_wecom_chat_name(app_name)

        return {
            "app_name": app_name,
            "window_title": tab_title or window_title,
            "url": url,
            "wecom_group": wecom_group,
        }

    def _capture_raw(self, ocr_enabled: bool, ocr_engine: str) -> dict:
        raw = self._capture_raw_inner()

        screenshot_path = None
        ocr_text = None

        # Skip screenshot+OCR if app and title haven't changed (biggest resource saver)
        app = raw.get("app_name")
        title = raw.get("window_title")
        same_window = (app == self._last_app and title == self._last_title
                       and self._last_app is not None)

        # Skip screenshot if frontmost app is hostile to screen capture
        # DEBUG MODE: set to False to disable protection and reproduce crash
        import os
        _debug_no_skip = os.environ.get("PDL_DEBUG_NO_SKIP") == "1"
        app_lower = (app or "").lower()
        hostile_ss = {s.lower() for s in self._config.hostile_apps_screenshot}
        skip_screenshot = (not _debug_no_skip) and (app_lower in hostile_ss)

        if ocr_enabled and not same_window and not skip_screenshot:
            today_dir = self._screenshot_dir / datetime.now().strftime("%Y-%m-%d")
            self._trace.log("capture_screenshot", app=app, title=title)
            screenshot_path = capture_screenshot(today_dir)
            if screenshot_path:
                if self._config.phash_enabled:
                    current_hash = compute_phash(screenshot_path)
                    if is_similar(current_hash, self._last_phash, self._config.phash_threshold):
                        # Screenshot visually similar — reuse last OCR, delete file
                        ocr_text = self._last_ocr_text
                        try:
                            screenshot_path.unlink()
                        except OSError:
                            pass
                        screenshot_path = None
                    else:
                        ocr_text = ocr_image(screenshot_path, ocr_engine)
                        self._last_phash = current_hash
                        self._last_ocr_text = ocr_text
                else:
                    ocr_text = ocr_image(screenshot_path, ocr_engine)
        elif skip_screenshot and ocr_enabled:
            # Hostile app frontmost — skip screenshot entirely
            self._trace.log("skip_screenshot_hostile", app=app)
        elif same_window:
            # Same window — reuse last OCR text, no screenshot
            ocr_text = self._last_ocr_text

        raw["screenshot_path"] = str(screenshot_path) if screenshot_path else None
        raw["ocr_text"] = ocr_text
        return raw

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
        rt = await self._get_runtime_config()
        interval_sec = rt["interval_sec"]

        idle_sec = get_idle_seconds()
        is_idle = idle_sec >= self._config.idle_threshold_sec

        if is_idle:
            if self._last_was_idle and self._last_id:
                await self._db.execute(
                    "UPDATE activities SET duration_sec = duration_sec + ? WHERE id = ?",
                    (interval_sec, self._last_id),
                )
                return

            row_id = await self._db.execute(
                """INSERT INTO activities
                   (timestamp, app_name, window_title, category, confidence,
                    duration_sec, machine_id)
                   VALUES (?, ?, ?, 'idle', 0.99, ?, ?)""",
                (datetime.now().isoformat(), "System", "Idle", interval_sec, "local"),
            )
            self._last_app = None
            self._last_title = None
            self._last_id = row_id
            self._last_was_idle = True
            return

        self._last_was_idle = False

        raw = self._capture_raw(rt["ocr_enabled"], rt["ocr_engine"])
        if not raw["app_name"] or self._is_blocked(raw):
            return

        app_name = raw["app_name"]
        window_title = raw["window_title"]

        if app_name == self._last_app and window_title == self._last_title and self._last_id:
            await self._db.execute(
                "UPDATE activities SET duration_sec = duration_sec + ? WHERE id = ?",
                (interval_sec, self._last_id),
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
               (timestamp, app_name, window_title, category, confidence,
                url, signals, duration_sec, machine_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                app_name,
                window_title,
                category,
                confidence,
                raw["url"],
                json.dumps(signals, ensure_ascii=False),
                interval_sec,
                "local",  # built-in collector uses fixed machine_id
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
            # Read interval dynamically so settings UI changes take effect
            rt = await self._get_runtime_config()
            await asyncio.sleep(rt["interval_sec"])

    def stop(self) -> None:
        self._running = False
