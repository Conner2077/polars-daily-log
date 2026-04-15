from pathlib import Path
import sys
import types


class _FakeBaseModel:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _fake_field(default=None, **kwargs):
    return default


sys.modules.setdefault(
    "pydantic",
    types.SimpleNamespace(BaseModel=_FakeBaseModel, Field=_fake_field),
)

from auto_daily_log_collector.platforms.linux import LinuxWaylandAdapter
from shared.schemas import CAPABILITY_WINDOW_TITLE


def test_wayland_adapter_reads_gnome_state_file(monkeypatch, tmp_path):
    state_file = tmp_path / "wayland-state.json"
    state_file.write_text(
        '{"app_name":"Google Chrome","window_title":"Jira - Chrome","browser_url":null}',
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTO_DAILY_LOG_WAYLAND_STATE_FILE", str(state_file))

    adapter = LinuxWaylandAdapter()

    assert adapter.get_frontmost_app() == "Google Chrome"
    assert adapter.get_window_title("Google Chrome") == "Jira - Chrome"
    assert adapter.get_browser_tab("Google Chrome") == ("Jira - Chrome", None)


def test_wayland_adapter_capabilities_include_window_title_when_wayland_introspection_available(monkeypatch):
    monkeypatch.setattr(
        "auto_daily_log_collector.platforms.linux._wayland_window_introspection_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "auto_daily_log_collector.platforms.linux._wayland_screenshot_available",
        lambda: False,
    )

    adapter = LinuxWaylandAdapter()

    assert adapter.capabilities() == {CAPABILITY_WINDOW_TITLE}


def test_wayland_adapter_capture_prefers_portal_backend(monkeypatch, tmp_path):
    output = tmp_path / "shot.png"

    class FakePortalBackend:
        def capture_to_file(self, filepath: Path):
            filepath.write_bytes(b"portal")
            return filepath

    monkeypatch.setattr(
        "auto_daily_log_collector.platforms.linux._create_wayland_portal_backend",
        lambda state_dir: FakePortalBackend(),
    )
    monkeypatch.setattr(
        "auto_daily_log_collector.platforms.linux.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy fallback should not run")),
    )

    adapter = LinuxWaylandAdapter()

    assert adapter.capture_screenshot(output) is True
    assert output.read_bytes() == b"portal"


def test_wayland_adapter_capture_falls_back_to_tool_when_portal_fails(monkeypatch, tmp_path):
    output = tmp_path / "shot.png"

    class FakePortalBackend:
        def capture_to_file(self, filepath: Path):
            return None

    def fake_run(cmd, timeout, capture_output, pass_fds=()):
        assert cmd == ["grim", str(output)]
        output.write_bytes(b"grim")

    monkeypatch.setattr(
        "auto_daily_log_collector.platforms.linux._create_wayland_portal_backend",
        lambda state_dir: FakePortalBackend(),
    )
    monkeypatch.setattr(
        "auto_daily_log_collector.platforms.linux.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "auto_daily_log_collector.platforms.linux.shutil.which",
        lambda name: "/usr/bin/grim" if name == "grim" else None,
    )

    adapter = LinuxWaylandAdapter()

    assert adapter.capture_screenshot(output) is True
    assert output.read_bytes() == b"grim"


def test_collector_linux_module_does_not_import_legacy_monitor_platforms():
    content = Path("auto_daily_log_collector/platforms/linux.py").read_text(encoding="utf-8")
    assert "auto_daily_log.monitor.platforms" not in content
