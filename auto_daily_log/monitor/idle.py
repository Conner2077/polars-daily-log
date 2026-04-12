import subprocess
import re
from .platforms.detect import get_current_platform


def get_idle_seconds() -> float:
    platform = get_current_platform()
    if platform == "macos":
        return _get_idle_macos()
    elif platform == "windows":
        return _get_idle_windows()
    else:
        return _get_idle_linux()


def _get_idle_macos() -> float:
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=5,
        )
        match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', result.stdout)
        if match:
            nanoseconds = int(match.group(1))
            return nanoseconds / 1_000_000_000
    except Exception:
        pass
    return 0.0


def _get_idle_windows() -> float:
    try:
        ps_script = (
            "Add-Type @'\n"
            "using System;\n"
            "using System.Runtime.InteropServices;\n"
            "public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }\n"
            "public class IdleTime {\n"
            "    [DllImport(\"user32.dll\")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);\n"
            "    public static uint Get() {\n"
            "        LASTINPUTINFO lii = new LASTINPUTINFO();\n"
            "        lii.cbSize = (uint)Marshal.SizeOf(lii);\n"
            "        GetLastInputInfo(ref lii);\n"
            "        return (uint)Environment.TickCount - lii.dwTime;\n"
            "    }\n"
            "}\n"
            "'@\n"
            "[IdleTime]::Get()"
        )
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=10,
        )
        ms = int(result.stdout.strip())
        return ms / 1000.0
    except Exception:
        pass
    return 0.0


def _get_idle_linux() -> float:
    try:
        result = subprocess.run(
            ["xprintidle"],
            capture_output=True, text=True, timeout=5,
        )
        ms = int(result.stdout.strip())
        return ms / 1000.0
    except (FileNotFoundError, ValueError, Exception):
        pass
    return 0.0
