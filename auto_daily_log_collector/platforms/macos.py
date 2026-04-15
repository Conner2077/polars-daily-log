"""macOS adapter."""
import platform as _platform
import subprocess
from pathlib import Path
from typing import Optional

from auto_daily_log_collector.monitor_internals.idle import get_idle_seconds as _get_idle
from auto_daily_log_collector.monitor_internals.screenshot import capture_screenshot as _capture
from shared.schemas import (
    CAPABILITY_BROWSER_TAB,
    CAPABILITY_IDLE,
    CAPABILITY_OCR,
    CAPABILITY_SCREENSHOT,
    CAPABILITY_WINDOW_TITLE,
    PLATFORM_MACOS,
)

from .base import PlatformAdapter

_CHROMIUM = {"google chrome", "microsoft edge", "brave browser", "arc"}


def _run_osascript(script: str) -> Optional[str]:
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        output = result.stdout.strip()
        return output if output and output != "missing value" else None
    except (subprocess.TimeoutExpired, OSError):
        return None


class MacOSAPI:
    def get_frontmost_app(self) -> Optional[str]:
        return _run_osascript(
            'tell application "System Events" to get name of first application process whose frontmost is true'
        )

    def get_window_title(self, app_name: str) -> Optional[str]:
        return _run_osascript(
            f'tell application "System Events" to tell process "{app_name}" to get name of front window'
        )

    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        if not app_name:
            return None, None
        app_lower = app_name.lower()
        if app_lower in _CHROMIUM:
            title = _run_osascript(f'tell application "{app_name}" to get title of active tab of front window')
            url = _run_osascript(f'tell application "{app_name}" to get URL of active tab of front window')
            return title, url
        if app_lower == "safari":
            title = _run_osascript('tell application "Safari" to get name of current tab of front window')
            url = _run_osascript('tell application "Safari" to get URL of current tab of front window')
            return title, url
        return None, None


class MacOSAdapter(PlatformAdapter):
    def __init__(self):
        self._api = MacOSAPI()

    def platform_id(self) -> str:
        return PLATFORM_MACOS

    def platform_detail(self) -> str:
        return f"macOS {_platform.mac_ver()[0]}"

    def capabilities(self) -> set[str]:
        caps = {
            CAPABILITY_WINDOW_TITLE,
            CAPABILITY_BROWSER_TAB,
            CAPABILITY_SCREENSHOT,
            CAPABILITY_IDLE,
        }
        try:
            import Vision  # noqa: F401
            caps.add(CAPABILITY_OCR)
        except ImportError:
            pass
        return caps

    def get_frontmost_app(self) -> Optional[str]:
        return self._api.get_frontmost_app()

    def get_window_title(self, app_name: str) -> Optional[str]:
        return self._api.get_window_title(app_name)

    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        return self._api.get_browser_tab(app_name)

    def capture_screenshot(self, output_path) -> bool:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result = _capture(path.parent)
        if result and result.exists():
            if str(result) != str(path):
                result.rename(path)
            return True
        return False

    def get_idle_seconds(self) -> float:
        return _get_idle()
