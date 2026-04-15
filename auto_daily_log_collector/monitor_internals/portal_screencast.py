import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
SESSION_IFACE = "org.freedesktop.portal.Session"
REQUEST_IFACE = "org.freedesktop.portal.Request"
MONITOR_SOURCE = 1
CURSOR_HIDDEN = 1
PERSIST_MODE_PERSISTENT = 2


class PortalScreenshotBackend:
    def __init__(self, state_dir: Path):
        self._state_dir = state_dir
        if not shutil.which("gst-launch-1.0"):
            raise RuntimeError("gst-launch-1.0 is required for portal screenshots")
        if not shutil.which("python3"):
            raise RuntimeError("python3 is required for portal screenshots")

    def capture_to_file(self, filepath: Path) -> Optional[Path]:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        try:
            return _capture_portal_frame(filepath, self._state_dir)
        except ModuleNotFoundError:
            return self._capture_via_helper(filepath)
        except Exception:
            return None

    def _capture_via_helper(self, filepath: Path) -> Optional[Path]:
        command = [
            shutil.which("python3") or "python3",
            str(Path(__file__).resolve()),
            "--capture",
            str(filepath),
            str(self._state_dir),
        ]
        try:
            result = subprocess.run(command, timeout=240, capture_output=True, text=True)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0 and filepath.exists():
            return filepath
        return None


def _capture_portal_frame(filepath: Path, state_dir: Path) -> Optional[Path]:
    restore_token = _load_restore_token(state_dir)
    path = _capture_portal_frame_once(filepath, state_dir, restore_token)
    if path or not restore_token:
        return path
    _clear_restore_token(state_dir)
    return _capture_portal_frame_once(filepath, state_dir, None)


def _capture_portal_frame_once(filepath: Path, state_dir: Path, restore_token: Optional[str]) -> Optional[Path]:
    from gi.repository import Gio, GLib

    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    sender_token = connection.get_unique_name().lstrip(":").replace(".", "_")

    def request_path(token: str) -> str:
        return f"/org/freedesktop/portal/desktop/request/{sender_token}/{token}"

    def call_sync(method: str, parameters: GLib.Variant):
        return connection.call_sync(
            PORTAL_BUS,
            PORTAL_OBJECT,
            SCREENCAST_IFACE,
            method,
            parameters,
            None,
            Gio.DBusCallFlags.NONE,
            15000,
            None,
        )

    def call_request(path: str, invoke, timeout_ms: int) -> tuple[int, dict]:
        loop = GLib.MainLoop()
        state: dict = {}

        def on_response(_conn, _sender, _object_path, _interface_name, _signal_name, params, _user_data=None):
            response, results = params.unpack()
            state["response"] = int(response)
            state["results"] = dict(results)
            loop.quit()

        subscription = connection.signal_subscribe(
            PORTAL_BUS,
            REQUEST_IFACE,
            "Response",
            path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )
        try:
            invoke()
            GLib.timeout_add(timeout_ms, lambda: (state.setdefault("timeout", True), loop.quit(), False)[-1])
            loop.run()
        finally:
            connection.signal_unsubscribe(subscription)

        if state.get("timeout"):
            raise TimeoutError(path)
        return state.get("response", 2), state.get("results", {})

    session_handle = None
    pipewire_fd = None
    try:
        create_token = _new_token("create")
        response, results = call_request(
            request_path(create_token),
            lambda: call_sync(
                "CreateSession",
                GLib.Variant(
                    "(a{sv})",
                    ({
                        "handle_token": GLib.Variant("s", create_token),
                        "session_handle_token": GLib.Variant("s", _new_token("session")),
                    },),
                ),
            ),
            timeout_ms=15000,
        )
        if response != 0:
            return None
        session_handle = results.get("session_handle")
        if not session_handle:
            return None

        select_token = _new_token("select")
        select_options = {
            "handle_token": GLib.Variant("s", select_token),
            "types": GLib.Variant("u", MONITOR_SOURCE),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", CURSOR_HIDDEN),
            "persist_mode": GLib.Variant("u", PERSIST_MODE_PERSISTENT),
        }
        if restore_token:
            select_options["restore_token"] = GLib.Variant("s", restore_token)

        response, _ = call_request(
            request_path(select_token),
            lambda: call_sync("SelectSources", GLib.Variant("(oa{sv})", (session_handle, select_options))),
            timeout_ms=15000,
        )
        if response != 0:
            return None

        start_token = _new_token("start")
        response, results = call_request(
            request_path(start_token),
            lambda: call_sync(
                "Start",
                GLib.Variant(
                    "(osa{sv})",
                    (session_handle, "", {"handle_token": GLib.Variant("s", start_token)}),
                ),
            ),
            timeout_ms=180000,
        )
        if response != 0:
            return None

        streams = results.get("streams") or []
        if not streams:
            return None

        result, fd_list = connection.call_with_unix_fd_list_sync(
            PORTAL_BUS,
            PORTAL_OBJECT,
            SCREENCAST_IFACE,
            "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (session_handle, {})),
            None,
            Gio.DBusCallFlags.NONE,
            15000,
            None,
            None,
        )
        fd_index = result.unpack()[0]
        pipewire_fd = fd_list.get(fd_index)
        os.set_inheritable(pipewire_fd, True)

        restore = results.get("restore_token")
        if restore:
            _save_restore_token(state_dir, str(restore))

        node_id = int(streams[0][0])
        command = [
            "gst-launch-1.0",
            "-q",
            "pipewiresrc",
            f"fd={pipewire_fd}",
            f"path={node_id}",
            "num-buffers=1",
            "!",
            "videoconvert",
            "!",
            "pngenc",
            "snapshot=true",
            "!",
            "filesink",
            f"location={filepath}",
        ]
        proc = subprocess.run(command, timeout=30, capture_output=True, pass_fds=(pipewire_fd,))
        if proc.returncode != 0 or not filepath.exists():
            return None
        return filepath
    finally:
        if pipewire_fd is not None:
            try:
                os.close(pipewire_fd)
            except OSError:
                pass
        if session_handle:
            try:
                connection.call_sync(
                    PORTAL_BUS,
                    session_handle,
                    SESSION_IFACE,
                    "Close",
                    None,
                    None,
                    Gio.DBusCallFlags.NONE,
                    5000,
                    None,
                )
            except Exception:
                pass


def _new_token(prefix: str) -> str:
    return f"adl{prefix}{secrets.token_hex(4)}"


def _state_file(state_dir: Path) -> Path:
    return state_dir / ".portal_screencast.json"


def _load_restore_token(state_dir: Path) -> Optional[str]:
    try:
        data = json.loads(_state_file(state_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    token = data.get("restore_token")
    return str(token) if token else None


def _save_restore_token(state_dir: Path, token: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _state_file(state_dir).write_text(json.dumps({"restore_token": token}, ensure_ascii=False), encoding="utf-8")


def _clear_restore_token(state_dir: Path) -> None:
    try:
        _state_file(state_dir).unlink()
    except FileNotFoundError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--capture":
        filepath = Path(argv[2])
        state_dir = Path(argv[3])
        return 0 if _capture_portal_frame(filepath, state_dir) else 1
    print("usage: portal_screencast.py --capture <output_png> <state_dir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
