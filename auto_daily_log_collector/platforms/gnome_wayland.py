import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

_DEFAULT_STATE_FILE = "/tmp/auto-daily-log-wayland-state.json"
_DIST_PACKAGES = (
    "/usr/lib/python3/dist-packages",
    "/usr/lib64/python3/dist-packages",
)


def _sanitize_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    value = re.sub(r"[\u200b-\u200f\u2060-\u2064\ufeff]", "", value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    return value.strip() or None


def _ensure_atspi_bus_address() -> bool:
    if os.environ.get("AT_SPI_BUS_ADDRESS"):
        return True
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.a11y.Bus",
                "--object-path", "/org/a11y/bus",
                "--method", "org.a11y.Bus.GetAddress",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        match = re.search(r"'([^']+)'", result.stdout)
        if match:
            os.environ["AT_SPI_BUS_ADDRESS"] = match.group(1)
            return True
    except Exception:
        pass
    return bool(os.environ.get("AT_SPI_BUS_ADDRESS"))


def _import_atspi():
    if not _ensure_atspi_bus_address():
        raise RuntimeError("AT-SPI bus unavailable")
    try:
        import gi  # type: ignore
    except ImportError:
        for path in _DIST_PACKAGES:
            if path not in sys.path and Path(path).exists():
                sys.path.append(path)
        import gi  # type: ignore
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi  # type: ignore
    return Atspi


class GnomeWaylandAPI:
    def __init__(self, state_file: Optional[str] = None):
        self._state_file = Path(
            state_file or os.environ.get("AUTO_DAILY_LOG_WAYLAND_STATE_FILE", _DEFAULT_STATE_FILE)
        )

    def _read_state_file(self) -> dict:
        try:
            if not self._state_file.exists():
                return {}
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if "window_title" in data:
                data["window_title"] = _sanitize_text(data.get("window_title"))
            return data
        except Exception:
            return {}

    def _read_atspi_state(self) -> dict:
        try:
            atspi = _import_atspi()
            if atspi.get_desktop_count() <= 0:
                return {}
            desktop = atspi.get_desktop(0)
            focused = None
            fallback = None
            for app_index in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(app_index)
                app_name = app.get_name()
                for child_index in range(app.get_child_count()):
                    child = app.get_child_at_index(child_index)
                    states = child.get_state_set()
                    child_name = child.get_name()
                    payload = {
                        "app_name": app_name,
                        "window_title": _sanitize_text(child_name) or app_name,
                        "browser_url": None,
                    }
                    if states.contains(atspi.StateType.ACTIVE):
                        return payload
                    if focused is None and states.contains(atspi.StateType.FOCUSED):
                        focused = payload
                    if fallback is None and states.contains(atspi.StateType.SHOWING):
                        fallback = payload
            return focused or fallback or {}
        except Exception:
            return {}

    def _read_state(self) -> dict:
        state = self._read_state_file()
        if state.get("app_name") or state.get("window_title"):
            return state
        return self._read_atspi_state()

    def get_frontmost_app(self) -> Optional[str]:
        return self._read_state().get("app_name")

    def get_window_title(self, app_name: str) -> Optional[str]:
        return self._read_state().get("window_title")

    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        state = self._read_state()
        return state.get("window_title"), state.get("browser_url")
