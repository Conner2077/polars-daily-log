"""macOS adapter — reuses legacy monitor/platforms/macos via wrapping."""
import platform as _platform
import subprocess
from pathlib import Path
from typing import Optional

from auto_daily_log.monitor.platforms.macos import MacOSAPI
from auto_daily_log.monitor.idle import get_idle_seconds as _get_idle
from auto_daily_log.monitor.screenshot import capture_screenshot as _capture
from shared.schemas import (
    CAPABILITY_BROWSER_TAB,
    CAPABILITY_IDLE,
    CAPABILITY_OCR,
    CAPABILITY_SCREENSHOT,
    CAPABILITY_WINDOW_TITLE,
    PLATFORM_MACOS,
)

from .base import PlatformAdapter


class MacOSAdapter(PlatformAdapter):
    def __init__(self):
        self._api = MacOSAPI()

    def platform_id(self) -> str:
        return PLATFORM_MACOS

    def platform_detail(self) -> str:
        return f"macOS {_platform.mac_ver()[0]}"

    def capabilities(self) -> set[str]:
        # OCR via Vision framework (if pyobjc-Vision installed)
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

    def get_browser_tab(self, app_name: str):
        return self._api.get_browser_tab(app_name)

    def capture_screenshot(self, output_path) -> bool:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        result = _capture(p.parent)
        if result and result.exists():
            if str(result) != str(p):
                result.rename(p)
            return True
        return False

    def get_idle_seconds(self) -> float:
        return _get_idle()
