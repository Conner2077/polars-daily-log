import pytest
from unittest.mock import patch
from auto_daily_log_collector.monitor_internals.platforms.detect import get_current_platform, get_platform_module
from auto_daily_log_collector.monitor_internals.platforms.base import PlatformAPI


def test_get_current_platform():
    platform = get_current_platform()
    assert platform in ("macos", "windows", "linux")


def test_get_platform_module_returns_platform_api():
    module = get_platform_module()
    assert isinstance(module, PlatformAPI)


def test_platform_api_has_required_methods():
    module = get_platform_module()
    assert hasattr(module, "get_frontmost_app")
    assert hasattr(module, "get_window_title")
    assert hasattr(module, "get_browser_tab")
    assert hasattr(module, "get_wecom_chat_name")
