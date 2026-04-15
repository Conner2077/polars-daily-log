import os
import platform
from typing import Literal
from .base import PlatformAPI

PlatformType = Literal["macos", "windows", "linux"]

def get_current_platform() -> PlatformType:
    system = platform.system().lower()
    if system == "darwin": return "macos"
    elif system == "windows": return "windows"
    return "linux"

def _is_gnome_wayland_session() -> bool:
    if not os.environ.get("WAYLAND_DISPLAY"):
        return False
    if os.environ.get("GNOME_SHELL_SESSION_MODE"):
        return True
    desktop = (os.environ.get("DESKTOP_SESSION", "") + " " + os.environ.get("XDG_CURRENT_DESKTOP", "")).lower()
    return any(token in desktop for token in ("gnome", "zorin"))


def get_platform_module() -> PlatformAPI:
    current = get_current_platform()
    if current == "macos":
        from .macos import MacOSAPI
        return MacOSAPI()
    elif current == "windows":
        from .windows import WindowsAPI
        return WindowsAPI()
    else:
        if _is_gnome_wayland_session():
            from .gnome_wayland import GnomeWaylandAPI
            return GnomeWaylandAPI()
        from .linux import LinuxAPI
        return LinuxAPI()
