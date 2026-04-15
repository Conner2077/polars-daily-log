import subprocess
from typing import Optional, Tuple
from .base import PlatformAPI

_BROWSERS = {"google chrome", "microsoft edge", "brave browser", "arc", "safari"}
_CHROMIUM = {"google chrome", "microsoft edge", "brave browser", "arc"}
# NOTE: Hostile-app filtering is now applied at MonitorService level using
# config.monitor.hostile_apps_applescript (configurable).

def _run_osascript(script: str) -> Optional[str]:
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        output = result.stdout.strip()
        return output if output and output != "missing value" else None
    except (subprocess.TimeoutExpired, Exception):
        return None

class MacOSAPI(PlatformAPI):
    def get_frontmost_app(self) -> Optional[str]:
        return _run_osascript('tell application "System Events" to get name of first application process whose frontmost is true')

    def get_window_title(self, app_name: str) -> Optional[str]:
        return _run_osascript(f'tell application "System Events" to tell process "{app_name}" to get name of front window')

    def get_browser_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        if not app_name: return None, None
        app_lower = app_name.lower()
        if app_lower in _CHROMIUM: return self._get_chromium_tab(app_name)
        if app_lower == "safari": return self._get_safari_tab()
        return None, None

    def get_wecom_chat_name(self, app_name: str) -> Optional[str]:
        # Intentionally do NOT probe WeChat/企业微信 window contents —
        # they detect `tell process` automation and self-exit.
        # Chat name extraction is disabled on macOS for safety.
        return None

    def _get_chromium_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        title = _run_osascript(f'tell application "{app_name}" to get title of active tab of front window')
        url = _run_osascript(f'tell application "{app_name}" to get URL of active tab of front window')
        return title, url

    def _get_safari_tab(self) -> Tuple[Optional[str], Optional[str]]:
        title = _run_osascript('tell application "Safari" to get name of current tab of front window')
        url = _run_osascript('tell application "Safari" to get URL of current tab of front window')
        return title, url
