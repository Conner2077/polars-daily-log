"""Linux adapters — X11, Wayland, and headless variants."""
import json
import os
import platform as _platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from shared.schemas import (
    CAPABILITY_OCR,
    CAPABILITY_SCREENSHOT,
    CAPABILITY_WINDOW_TITLE,
    PLATFORM_LINUX_HEADLESS,
    PLATFORM_LINUX_WAYLAND,
    PLATFORM_LINUX_X11,
)

from .base import PlatformAdapter
from .gnome_wayland import GnomeWaylandAPI
from .portal_screencast import PortalScreenshotBackend

_DEFAULT_WAYLAND_STATE_FILE = "/tmp/auto-daily-log-wayland-state.json"


def _linux_distro() -> str:
    try:
        with open("/etc/os-release") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f"Linux {_platform.release()}"


def _run_command(cmd: list[str]) -> Optional[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout.strip()
        return output if output else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _get_idle_seconds() -> float:
    raw = _run_command(["xprintidle"])
    if raw is None:
        return 0.0
    try:
        return int(raw) / 1000.0
    except ValueError:
        return 0.0


def _capture_x11_screenshot(output_path: Path) -> bool:
    for tool_cmd in (
        ["gnome-screenshot", "-f", str(output_path)],
        ["import", "-window", "root", str(output_path)],
        ["scrot", str(output_path)],
        ["maim", str(output_path)],
    ):
        try:
            subprocess.run(tool_cmd, timeout=10, capture_output=True)
            if output_path.exists() and output_path.stat().st_size > 0:
                return True
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            continue
    return False


def _wayland_state_file() -> Path:
    return Path(os.environ.get("AUTO_DAILY_LOG_WAYLAND_STATE_FILE", _DEFAULT_WAYLAND_STATE_FILE))


def _wayland_window_introspection_available() -> bool:
    if shutil.which("swaymsg"):
        return True
    if _wayland_state_file().exists():
        return True
    return bool(shutil.which("gdbus") and os.environ.get("WAYLAND_DISPLAY"))


_WAYLAND_PORTAL_BACKEND: Optional[PortalScreenshotBackend] = None
_WAYLAND_PORTAL_STATE_DIR: Optional[Path] = None
_WAYLAND_PORTAL_DISABLED = False


def _create_wayland_portal_backend(state_dir: Path) -> Optional[PortalScreenshotBackend]:
    global _WAYLAND_PORTAL_BACKEND, _WAYLAND_PORTAL_STATE_DIR, _WAYLAND_PORTAL_DISABLED
    if _WAYLAND_PORTAL_DISABLED:
        return None
    if _WAYLAND_PORTAL_BACKEND is not None and _WAYLAND_PORTAL_STATE_DIR == state_dir:
        return _WAYLAND_PORTAL_BACKEND
    try:
        backend = PortalScreenshotBackend(state_dir)
    except Exception:
        _WAYLAND_PORTAL_DISABLED = True
        return None
    _WAYLAND_PORTAL_BACKEND = backend
    _WAYLAND_PORTAL_STATE_DIR = state_dir
    return backend


def _wayland_screenshot_available() -> bool:
    if _create_wayland_portal_backend(Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))) is not None:
        return True
    return any(shutil.which(tool) for tool in ("grim", "gnome-screenshot", "spectacle"))


def _read_sway_tree() -> Optional[dict]:
    if not shutil.which("swaymsg"):
        return None
    try:
        result = subprocess.run(
            ["swaymsg", "-t", "get_tree"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _find_focused_sway_node(node: dict) -> Optional[dict]:
    if node.get("focused"):
        return node
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        found = _find_focused_sway_node(child)
        if found is not None:
            return found
    return None


class LinuxX11API:
    def get_frontmost_app(self) -> Optional[str]:
        window_id = _run_command(["xdotool", "getactivewindow"])
        if not window_id:
            return None
        wm_class = _run_command(["xprop", "-id", window_id, "WM_CLASS"])
        if wm_class and "=" in wm_class:
            parts = wm_class.split("=", 1)[1].strip().strip('"').split('", "')
            return parts[-1] if parts else None
        return None

    def get_window_title(self, app_name: str) -> Optional[str]:
        window_id = _run_command(["xdotool", "getactivewindow"])
        if not window_id:
            return None
        return _run_command(["xdotool", "getwindowname", window_id])

    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        title = self.get_window_title(app_name)
        return title, None


class LinuxX11Adapter(PlatformAdapter):
    def __init__(self):
        self._api = LinuxX11API()

    def platform_id(self) -> str:
        return PLATFORM_LINUX_X11

    def platform_detail(self) -> str:
        return f"{_linux_distro()} (X11)"

    def capabilities(self) -> set[str]:
        caps = set()
        if shutil.which("xdotool"):
            caps.add(CAPABILITY_WINDOW_TITLE)
        if any(shutil.which(tool) for tool in ("gnome-screenshot", "scrot", "maim", "import")):
            caps.add(CAPABILITY_SCREENSHOT)
        if shutil.which("tesseract") and CAPABILITY_SCREENSHOT in caps:
            caps.add(CAPABILITY_OCR)
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
        return _capture_x11_screenshot(path)

    def get_idle_seconds(self) -> float:
        return _get_idle_seconds()


class LinuxWaylandAdapter(PlatformAdapter):
    def __init__(self):
        self._gnome_api = GnomeWaylandAPI()

    def platform_id(self) -> str:
        return PLATFORM_LINUX_WAYLAND

    def platform_detail(self) -> str:
        return f"{_linux_distro()} (Wayland)"

    def capabilities(self) -> set[str]:
        caps = set()
        if _wayland_window_introspection_available():
            caps.add(CAPABILITY_WINDOW_TITLE)
        if _wayland_screenshot_available():
            caps.add(CAPABILITY_SCREENSHOT)
        if shutil.which("tesseract") and CAPABILITY_SCREENSHOT in caps:
            caps.add(CAPABILITY_OCR)
        return caps

    def _sway_payload(self) -> Optional[dict]:
        tree = _read_sway_tree()
        if tree is None:
            return None
        focused = _find_focused_sway_node(tree)
        if focused is None:
            return None
        return {
            "app_name": focused.get("app_id") or focused.get("name"),
            "window_title": focused.get("name"),
            "browser_url": None,
        }

    def _read_state(self) -> dict:
        sway_state = self._sway_payload()
        if sway_state and (sway_state.get("app_name") or sway_state.get("window_title")):
            return sway_state
        tab_title, browser_url = self._gnome_api.get_browser_tab("")
        return {
            "app_name": self._gnome_api.get_frontmost_app(),
            "window_title": self._gnome_api.get_window_title("") or tab_title,
            "browser_url": browser_url,
        }

    def get_frontmost_app(self) -> Optional[str]:
        return self._read_state().get("app_name")

    def get_window_title(self, app_name: str) -> Optional[str]:
        return self._read_state().get("window_title")

    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        state = self._read_state()
        return state.get("window_title"), state.get("browser_url")

    def capture_screenshot(self, output_path) -> bool:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        portal_backend = _create_wayland_portal_backend(path.parent.parent)
        if portal_backend is not None:
            portal_path = portal_backend.capture_to_file(path)
            if portal_path and portal_path.exists():
                return True
        for tool_cmd in (
            ["grim", str(path)],
            ["gnome-screenshot", "-f", str(path)],
            ["spectacle", "-b", "-n", "-o", str(path)],
        ):
            try:
                subprocess.run(tool_cmd, timeout=10, capture_output=True)
                if path.exists() and path.stat().st_size > 0:
                    return True
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                continue
        return False

    def get_idle_seconds(self) -> float:
        return _get_idle_seconds()


class LinuxHeadlessAdapter(PlatformAdapter):
    def platform_id(self) -> str:
        return PLATFORM_LINUX_HEADLESS

    def platform_detail(self) -> str:
        return f"{_linux_distro()} (headless)"

    def capabilities(self) -> set[str]:
        return set()

    def get_frontmost_app(self) -> Optional[str]:
        return None

    def get_window_title(self, app_name: str) -> Optional[str]:
        return None

    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        return None, None

    def capture_screenshot(self, output_path) -> bool:
        return False

    def get_idle_seconds(self) -> float:
        return 0.0
