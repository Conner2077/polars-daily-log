import pytest
from unittest.mock import patch
from auto_daily_log.monitor.idle import get_idle_seconds

def test_get_idle_seconds_returns_number():
    result = get_idle_seconds()
    assert isinstance(result, (int, float))
    assert result >= 0

@patch("auto_daily_log.monitor.idle.get_current_platform", return_value="macos")
@patch("auto_daily_log.monitor.idle._get_idle_macos", return_value=120.0)
def test_idle_dispatches_to_macos(mock_idle, mock_platform):
    result = get_idle_seconds()
    assert result == 120.0
    mock_idle.assert_called_once()

@patch("auto_daily_log.monitor.idle.get_current_platform", return_value="windows")
@patch("auto_daily_log.monitor.idle._get_idle_windows", return_value=60.0)
def test_idle_dispatches_to_windows(mock_idle, mock_platform):
    result = get_idle_seconds()
    assert result == 60.0

@patch("auto_daily_log.monitor.idle.get_current_platform", return_value="linux")
@patch("auto_daily_log.monitor.idle._get_idle_linux", return_value=30.0)
def test_idle_dispatches_to_linux(mock_idle, mock_platform):
    result = get_idle_seconds()
    assert result == 30.0
