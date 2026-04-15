import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from .platforms.detect import get_current_platform
from .portal_screencast import PortalScreenshotBackend

_WAYLAND_PORTAL_BACKEND: Optional[PortalScreenshotBackend] = None
_WAYLAND_PORTAL_STATE_DIR: Optional[Path] = None
_WAYLAND_PORTAL_DISABLED = False


def _get_wayland_portal_backend(output_dir: Path) -> Optional[PortalScreenshotBackend]:
    global _WAYLAND_PORTAL_BACKEND, _WAYLAND_PORTAL_STATE_DIR, _WAYLAND_PORTAL_DISABLED
    if _WAYLAND_PORTAL_DISABLED:
        return None
    if get_current_platform() != "linux" or not os.environ.get("WAYLAND_DISPLAY"):
        return None

    state_dir = output_dir.parent
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


def _capture_legacy(filepath: Path, platform: str) -> Optional[Path]:
    try:
        if platform == "macos":
            subprocess.run(["screencapture", "-x", str(filepath)], timeout=10, capture_output=True)
        elif platform == "windows":
            ps_script = (
                f"Add-Type -AssemblyName System.Windows.Forms;"
                f"$bmp = New-Object System.Drawing.Bitmap("
                f"[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width,"
                f"[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height);"
                f"$g = [System.Drawing.Graphics]::FromImage($bmp);"
                f"$g.CopyFromScreen(0,0,0,0,$bmp.Size);"
                f'$bmp.Save("{filepath}")'
            )
            subprocess.run(["powershell", "-Command", ps_script], timeout=30, capture_output=True)
        else:
            for tool_cmd in [
                ["gnome-screenshot", "-f", str(filepath)],
                ["import", "-window", "root", str(filepath)],
                ["scrot", str(filepath)],
                ["maim", str(filepath)],
            ]:
                try:
                    subprocess.run(tool_cmd, timeout=10, capture_output=True)
                    if filepath.exists():
                        break
                except FileNotFoundError:
                    continue
        return filepath if filepath.exists() else None
    except (subprocess.TimeoutExpired, Exception):
        return None


def capture_screenshot(output_dir: Path) -> Optional[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    filepath = output_dir / filename
    platform = get_current_platform()

    portal_backend = _get_wayland_portal_backend(output_dir)
    if portal_backend:
        portal_path = portal_backend.capture_to_file(filepath)
        if portal_path and portal_path.exists():
            return portal_path

    return _capture_legacy(filepath, platform)
