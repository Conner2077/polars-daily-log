from pathlib import Path
from unittest.mock import MagicMock, patch

from auto_daily_log_collector.monitor_internals import screenshot


def test_capture_screenshot_prefers_portal_backend_on_wayland(tmp_path, monkeypatch):
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')
    monkeypatch.setattr(screenshot, 'get_current_platform', lambda: 'linux')

    backend = MagicMock()

    def portal_capture(path: Path):
        path.write_bytes(b'portal')
        return path

    backend.capture_to_file.side_effect = portal_capture
    monkeypatch.setattr(screenshot, '_get_wayland_portal_backend', lambda output_dir: backend)

    with patch('auto_daily_log_collector.monitor_internals.screenshot.subprocess.run') as mock_run:
        path = screenshot.capture_screenshot(tmp_path)

    assert path == tmp_path / path.name
    assert path.read_bytes() == b'portal'
    mock_run.assert_not_called()
    backend.capture_to_file.assert_called_once()



def test_capture_screenshot_falls_back_to_legacy_when_portal_capture_fails(tmp_path, monkeypatch):
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')
    monkeypatch.setattr(screenshot, 'get_current_platform', lambda: 'linux')

    backend = MagicMock()
    backend.capture_to_file.return_value = None
    monkeypatch.setattr(screenshot, '_get_wayland_portal_backend', lambda output_dir: backend)

    def fake_run(cmd, timeout, capture_output, pass_fds=()):
        output = Path(cmd[-1])
        output.write_bytes(b'legacy')

    with patch('auto_daily_log_collector.monitor_internals.screenshot.subprocess.run', side_effect=fake_run) as mock_run:
        path = screenshot.capture_screenshot(tmp_path)

    assert path == tmp_path / path.name
    assert path.read_bytes() == b'legacy'
    assert mock_run.call_args[0][0][0] == 'gnome-screenshot'
    backend.capture_to_file.assert_called_once()
