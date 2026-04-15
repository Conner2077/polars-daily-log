import subprocess
from typing import Optional, Tuple
from .base import PlatformAPI

def _run_powershell(cmd: str) -> Optional[str]:
    try:
        result = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True, timeout=10)
        output = result.stdout.strip()
        return output if output else None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None

class WindowsAPI(PlatformAPI):
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
