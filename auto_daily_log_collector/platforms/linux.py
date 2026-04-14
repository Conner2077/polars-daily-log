"""Linux adapters — X11, Wayland, and headless variants."""
import os
import platform as _platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from auto_daily_log.monitor.platforms.linux import LinuxAPI
from auto_daily_log.monitor.idle import get_idle_seconds as _get_idle
from auto_daily_log.monitor.screenshot import capture_screenshot as _capture
from shared.schemas import (
    CAPABILITY_BROWSER_TAB,
    CAPABILITY_IDLE,
    CAPABILITY_OCR,
    CAPABILITY_SCREENSHOT,
    CAPABILITY_WINDOW_TITLE,
    PLATFORM_LINUX_HEADLESS,
    PLATFORM_LINUX_WAYLAND,
    PLATFORM_LINUX_X11,
)

from .base import PlatformAdapter


def _linux_distro() -> str:
    """Detect /etc/os-release PRETTY_NAME."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f"Linux {_platform.release()}"


class LinuxX11Adapter(PlatformAdapter):
    """Linux with X11 session — full feature set if tools installed."""

    def __init__(self):
        self._api = LinuxAPI()

    def platform_id(self) -> str:
        return PLATFORM_LINUX_X11

    def platform_detail(self) -> str:
        return f"{_linux_distro()} (X11)"

    def capabilities(self) -> set[str]:
        caps = {CAPABILITY_IDLE}
        if shutil.which("xdotool"):
            caps.add(CAPABILITY_WINDOW_TITLE)
        if any(shutil.which(t) for t in ("gnome-screenshot", "scrot", "maim", "import")):
            caps.add(CAPABILITY_SCREENSHOT)
        if shutil.which("tesseract"):
            caps.add(CAPABILITY_OCR)
        return caps

    def get_frontmost_app(self) -> Optional[str]:
        return self._api.get_frontmost_app()

    def get_window_title(self, app_name: str) -> Optional[str]:
        return self._api.get_window_title(app_name)

    def get_browser_tab(self, app_name: str):
        # X11 can't introspect browser tabs reliably
        return None, None

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


class LinuxWaylandAdapter(PlatformAdapter):
    """Linux with Wayland session — limited window/screen access.

    Most Wayland compositors prohibit arbitrary screenshots and window
    introspection for security. We gracefully degrade: idle detection
    works via GNOME Idle D-Bus (if available), but window tracking
    often returns None.
    """

    def platform_id(self) -> str:
        return PLATFORM_LINUX_WAYLAND

    def platform_detail(self) -> str:
        return f"{_linux_distro()} (Wayland)"

    def capabilities(self) -> set[str]:
        caps = set()
        # Screenshot via grim (wlroots) or gnome-screenshot
        if shutil.which("grim") or shutil.which("gnome-screenshot"):
            caps.add(CAPABILITY_SCREENSHOT)
        if shutil.which("tesseract") and CAPABILITY_SCREENSHOT in caps:
            caps.add(CAPABILITY_OCR)
        return caps

    def get_frontmost_app(self) -> Optional[str]:
        # Wayland doesn't allow arbitrary introspection; best-effort via
        # swaymsg (Sway) or gdbus (GNOME). Return None for unsupported.
        if shutil.which("swaymsg"):
            try:
                result = subprocess.run(
                    ["swaymsg", "-t", "get_tree"],
                    capture_output=True, text=True, timeout=3,
                )
                import json
                tree = json.loads(result.stdout)
                # Walk tree for focused node
                def find_focused(node):
                    if node.get("focused"):
                        return node.get("app_id") or node.get("name")
                    for child in node.get("nodes", []) + node.get("floating_nodes", []):
                        r = find_focused(child)
                        if r:
                            return r
                    return None
                return find_focused(tree)
            except (subprocess.TimeoutExpired, Exception):
                return None
        return None

    def get_window_title(self, app_name: str) -> Optional[str]:
        return None  # Wayland: not accessible

    def get_browser_tab(self, app_name: str):
        return None, None

    def capture_screenshot(self, output_path) -> bool:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        for tool_cmd in [
            ["grim", str(p)],
            ["gnome-screenshot", "-f", str(p)],
        ]:
            try:
                subprocess.run(tool_cmd, timeout=10, capture_output=True)
                if p.exists() and p.stat().st_size > 0:
                    return True
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                continue
        return False

    def get_idle_seconds(self) -> float:
        # xprintidle doesn't work on Wayland; use GNOME D-Bus if available
        # Fallback: return 0 (never idle) if unknown
        return _get_idle()  # Will return 0 on Wayland


class LinuxHeadlessAdapter(PlatformAdapter):
    """No GUI — server, SSH-only Docker container, etc.

    Only useful for git-commit collection + time-based logging. All
    window/screenshot/idle calls return None/False.
    """

    def platform_id(self) -> str:
        return PLATFORM_LINUX_HEADLESS

    def platform_detail(self) -> str:
        return f"{_linux_distro()} (headless)"

    def capabilities(self) -> set[str]:
        # Only git on headless; no UI signals at all
        return set()

    def get_frontmost_app(self) -> Optional[str]:
        return None

    def get_window_title(self, app_name: str) -> Optional[str]:
        return None

    def get_browser_tab(self, app_name: str):
        return None, None

    def capture_screenshot(self, output_path) -> bool:
        return False

    def get_idle_seconds(self) -> float:
        return 0.0
