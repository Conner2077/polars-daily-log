"""Windows adapter."""
import platform as _platform
import subprocess
from pathlib import Path
from typing import Optional

from auto_daily_log.monitor.idle import get_idle_seconds as _get_idle
from auto_daily_log.monitor.screenshot import capture_screenshot as _capture
from shared.schemas import (
    CAPABILITY_BROWSER_TAB,
    CAPABILITY_IDLE,
    CAPABILITY_OCR,
    CAPABILITY_SCREENSHOT,
    CAPABILITY_WINDOW_TITLE,
    PLATFORM_WINDOWS,
)

from .base import PlatformAdapter


def _run_powershell(cmd: str) -> Optional[str]:
    try:
        result = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=10)
        output = result.stdout.strip()
        return output if output else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


class WindowsAPI:
    def get_frontmost_app(self) -> Optional[str]:
        return _run_powershell(
            "(Get-Process | Where-Object {$_.MainWindowHandle -eq "
            "(Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] "
            "public static extern IntPtr GetForegroundWindow();' "
            "-Name Win32 -PassThru)::GetForegroundWindow()}).ProcessName"
        )

    def get_window_title(self, app_name: str) -> Optional[str]:
        return _run_powershell(
            "(Get-Process | Where-Object {$_.MainWindowHandle -eq "
            "(Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] "
            "public static extern IntPtr GetForegroundWindow();' "
            "-Name Win32 -PassThru)::GetForegroundWindow()}).MainWindowTitle"
        )

    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        title = self.get_window_title(app_name)
        return title, None


class WindowsAdapter(PlatformAdapter):
    def __init__(self):
        self._api = WindowsAPI()

    def platform_id(self) -> str:
        return PLATFORM_WINDOWS

    def platform_detail(self) -> str:
        return f"Windows {_platform.release()} ({_platform.version()})"

    def capabilities(self) -> set[str]:
        caps = {
            CAPABILITY_WINDOW_TITLE,
            CAPABILITY_BROWSER_TAB,
            CAPABILITY_SCREENSHOT,
            CAPABILITY_IDLE,
        }
        try:
            import winocr  # noqa: F401
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
