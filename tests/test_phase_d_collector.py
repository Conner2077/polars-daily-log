"""Phase D tests — collector package: config, platform factory, credentials."""
import json
import os
import platform as _platform
from pathlib import Path

import pytest

from auto_daily_log_collector.config import CollectorConfig, load_config
from auto_daily_log_collector.credentials import (
    clear_credentials,
    load_credentials,
    save_credentials,
)
from auto_daily_log_collector.platforms import create_adapter
from auto_daily_log_collector.platforms.factory import detect_platform_id
from shared.schemas import (
    ALL_CAPABILITIES,
    PLATFORM_LINUX_HEADLESS,
    PLATFORM_LINUX_WAYLAND,
    PLATFORM_LINUX_X11,
    PLATFORM_MACOS,
    PLATFORM_WINDOWS,
)


# ─── Config loading ──────────────────────────────────────────────────

def test_load_config_parses_yaml(tmp_path):
    yml = tmp_path / "c.yaml"
    yml.write_text(
        "server_url: http://host:9000\n"
        "name: Lab-Mac\n"
        "interval_sec: 15\n"
        "ocr_enabled: true\n"
        "blocked_apps: ['WeChat', 'Messages']\n"
    )
    c = load_config(str(yml))
    assert c.server_url == "http://host:9000"
    assert c.name == "Lab-Mac"
    assert c.interval_sec == 15
    assert c.ocr_enabled is True
    assert c.blocked_apps == ["WeChat", "Messages"]


def test_load_config_requires_server_url(tmp_path):
    yml = tmp_path / "c.yaml"
    yml.write_text("name: foo\n")  # server_url is required
    with pytest.raises(Exception) as exc_info:
        load_config(str(yml))
    assert "server_url" in str(exc_info.value).lower()


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "nope.yaml"))


def test_resolved_data_dir_creates_dir(tmp_path):
    c = CollectorConfig(server_url="http://x", data_dir=str(tmp_path / "new_dir"))
    path = c.resolved_data_dir
    assert path.exists()
    assert path.is_dir()


def test_credentials_file_under_data_dir(tmp_path):
    c = CollectorConfig(server_url="http://x", data_dir=str(tmp_path / "d"))
    assert c.credentials_file == tmp_path / "d" / "credentials.json"


# ─── Credentials ─────────────────────────────────────────────────────

def test_save_and_load_credentials_roundtrip(tmp_path):
    path = tmp_path / "cred.json"
    save_credentials(path, "m-1234567890abcdef", "my-token-" + "x" * 24)

    loaded = load_credentials(path)
    assert loaded is not None
    assert loaded.machine_id == "m-1234567890abcdef"
    assert loaded.token == "my-token-" + "x" * 24


def test_load_credentials_missing_returns_none(tmp_path):
    assert load_credentials(tmp_path / "nope.json") is None


def test_load_credentials_corrupt_returns_none(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("not valid json {")
    assert load_credentials(path) is None


def test_save_credentials_chmod_0o600(tmp_path):
    path = tmp_path / "c.json"
    save_credentials(path, "m-abc", "t" * 32)
    # On unix systems, file should be user-only
    if os.name == "posix":
        mode = oct(path.stat().st_mode)[-3:]
        assert mode == "600", f"expected 600 permissions, got {mode}"


def test_clear_credentials_removes_file(tmp_path):
    path = tmp_path / "c.json"
    save_credentials(path, "m-1", "t" * 32)
    assert path.exists()
    clear_credentials(path)
    assert not path.exists()
    # Clearing non-existent is safe
    clear_credentials(path)


# ─── Platform factory ───────────────────────────────────────────────

def test_detect_platform_id_returns_valid_constant():
    pid = detect_platform_id()
    assert pid in {
        PLATFORM_MACOS, PLATFORM_WINDOWS,
        PLATFORM_LINUX_X11, PLATFORM_LINUX_WAYLAND, PLATFORM_LINUX_HEADLESS,
    }, f"unknown platform: {pid}"


def test_detect_platform_on_macos():
    if _platform.system() != "Darwin":
        pytest.skip("macOS only")
    assert detect_platform_id() == PLATFORM_MACOS


def test_create_adapter_auto_detects():
    adapter = create_adapter()
    assert adapter.platform_id() == detect_platform_id()


def test_create_adapter_with_explicit_platform_id():
    """Force linux-headless on macOS host — should still work (no import errors)."""
    adapter = create_adapter(PLATFORM_LINUX_HEADLESS)
    assert adapter.platform_id() == PLATFORM_LINUX_HEADLESS
    assert adapter.capabilities() == set()  # headless has no capabilities
    assert adapter.get_frontmost_app() is None
    assert adapter.get_window_title("anything") is None
    assert adapter.capture_screenshot("/tmp/x.png") is False


def test_create_adapter_unknown_raises():
    with pytest.raises(ValueError) as exc_info:
        create_adapter("windows-2000-time-warp")
    assert "windows-2000-time-warp" in str(exc_info.value)


def test_adapter_capabilities_subset_of_schema():
    """All declared capabilities must be in the shared schema constants."""
    adapter = create_adapter()
    caps = adapter.capabilities()
    unknown = caps - ALL_CAPABILITIES
    assert not unknown, f"adapter returns unknown caps: {unknown}"


def test_adapter_platform_detail_non_empty():
    adapter = create_adapter()
    detail = adapter.platform_detail()
    assert detail, "platform_detail must not be empty"
    assert isinstance(detail, str)


# ─── Smoke test on macOS adapter (only runs on macOS) ────────────────

def test_macos_adapter_frontmost_app_returns_string():
    if _platform.system() != "Darwin":
        pytest.skip("macOS only")
    adapter = create_adapter(PLATFORM_MACOS)
    # Running this test ITSELF means pytest is frontmost → should return a name
    app = adapter.get_frontmost_app()
    # May be None if test runs headless, but if set, it's a non-empty string
    if app is not None:
        assert isinstance(app, str)
        assert len(app) > 0
