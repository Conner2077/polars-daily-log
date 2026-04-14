"""Platform factory — picks the right adapter based on OS + session."""
import os
import platform as _platform

from shared.schemas import (
    PLATFORM_LINUX_HEADLESS,
    PLATFORM_LINUX_WAYLAND,
    PLATFORM_LINUX_X11,
    PLATFORM_MACOS,
    PLATFORM_WINDOWS,
)

from .base import PlatformAdapter


def detect_platform_id() -> str:
    """Return the PLATFORM_* constant for the current environment."""
    system = _platform.system().lower()
    if system == "darwin":
        return PLATFORM_MACOS
    if system == "windows":
        return PLATFORM_WINDOWS
    # Linux branches
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland":
        return PLATFORM_LINUX_WAYLAND
    if os.environ.get("DISPLAY"):
        return PLATFORM_LINUX_X11
    return PLATFORM_LINUX_HEADLESS


def create_adapter(platform_id: str | None = None) -> PlatformAdapter:
    """Instantiate the adapter for the given platform_id (or auto-detect)."""
    pid = platform_id or detect_platform_id()
    if pid == PLATFORM_MACOS:
        from .macos import MacOSAdapter
        return MacOSAdapter()
    if pid == PLATFORM_WINDOWS:
        from .windows import WindowsAdapter
        return WindowsAdapter()
    if pid == PLATFORM_LINUX_X11:
        from .linux import LinuxX11Adapter
        return LinuxX11Adapter()
    if pid == PLATFORM_LINUX_WAYLAND:
        from .linux import LinuxWaylandAdapter
        return LinuxWaylandAdapter()
    if pid == PLATFORM_LINUX_HEADLESS:
        from .linux import LinuxHeadlessAdapter
        return LinuxHeadlessAdapter()
    raise ValueError(f"Unknown platform_id: {pid}")
