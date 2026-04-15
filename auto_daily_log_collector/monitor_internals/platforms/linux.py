import subprocess
from typing import Optional, Tuple
from .base import PlatformAPI

def _run_command(cmd: list[str]) -> Optional[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout.strip()
        return output if output else None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None

class LinuxAPI(PlatformAPI):
    def get_frontmost_app(self) -> Optional[str]:
        window_id = _run_command(["xdotool", "getactivewindow"])
        if not window_id: return None
        wm_class = _run_command(["xprop", "-id", window_id, "WM_CLASS"])
        if wm_class and "=" in wm_class:
            parts = wm_class.split("=", 1)[1].strip().strip('"').split('", "')
            return parts[-1] if parts else None
        return None

    def get_window_title(self, app_name: str) -> Optional[str]:
        window_id = _run_command(["xdotool", "getactivewindow"])
        if not window_id: return None
        return _run_command(["xdotool", "getwindowname", window_id])

    def get_browser_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        title = self.get_window_title(app_name)
        return title, None

    def get_wecom_chat_name(self, app_name: str) -> Optional[str]:
        if not app_name: return None
        lower = app_name.lower()
        if lower not in ("wechat", "wecom", "企业微信", "微信"): return None
        title = self.get_window_title(app_name)
        if title:
            parts = title.rsplit(" - ", 1)
            if len(parts) == 2: return parts[0].strip()
        return None
