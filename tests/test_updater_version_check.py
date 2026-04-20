"""Unit tests for the GitHub Releases version checker."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from auto_daily_log.updater import version_check
from auto_daily_log.updater.paths import update_check_path


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"system:\n  data_dir: {tmp_path}/data\n")
    monkeypatch.setenv("PDL_SERVER_CONFIG", str(cfg))
    yield tmp_path


def _release_payload(tag: str, *, with_asset: bool = True) -> dict:
    assets = []
    if with_asset:
        assets = [{
            "name": f"auto_daily_log-{tag}-py3-none-any.whl",
            "browser_download_url": f"https://example.com/auto_daily_log-{tag}-py3-none-any.whl",
        }]
    return {
        "tag_name": f"v{tag}",
        "html_url": f"https://github.com/Conner2077/polars-daily-log/releases/tag/v{tag}",
        "body": f"Release {tag}",
        "assets": assets,
    }


def _mock_httpx(payload: dict):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return patch("auto_daily_log.updater.version_check.httpx.get", return_value=resp)


def test_check_reports_update_available_when_remote_is_newer():
    with _mock_httpx(_release_payload("9.9.9")):
        result = version_check.check(force=True, current="0.4.0")
    assert result.current == "0.4.0"
    assert result.latest == "9.9.9"
    assert result.available is True
    assert result.wheel_url == "https://example.com/auto_daily_log-9.9.9-py3-none-any.whl"


def test_check_reports_no_update_when_remote_is_same():
    with _mock_httpx(_release_payload("0.4.0")):
        result = version_check.check(force=True, current="0.4.0")
    assert result.available is False
    assert result.latest == "0.4.0"


def test_check_reports_no_update_when_remote_is_older():
    with _mock_httpx(_release_payload("0.1.0")):
        result = version_check.check(force=True, current="0.4.0")
    assert result.available is False


def test_check_falls_back_to_conventional_url_when_asset_missing():
    with _mock_httpx(_release_payload("0.5.0", with_asset=False)):
        result = version_check.check(force=True, current="0.4.0")
    assert result.wheel_url == (
        "https://github.com/Conner2077/polars-daily-log/releases/download/"
        "v0.5.0/auto_daily_log-0.5.0-py3-none-any.whl"
    )


def test_check_uses_cache_within_ttl(isolated_data_dir):
    cached = {
        "current": "0.4.0",
        "latest": "0.5.0",
        "available": True,
        "wheel_url": "https://example.com/x.whl",
        "release_url": "https://example.com",
        "notes": "from cache",
        "checked_at": time.time(),
    }
    update_check_path().write_text(json.dumps(cached))
    # Cache is only honored when cached current matches runtime __version__.
    # Patch __version__ to the cached value so the TTL path is exercised.
    with patch.object(version_check, "__version__", "0.4.0"), \
         patch("auto_daily_log.updater.version_check.httpx.get") as net:
        result = version_check.check(force=False, current="0.4.0")
    assert net.call_count == 0
    assert result.notes == "from cache"


def test_check_ignores_stale_cache(isolated_data_dir):
    stale = {
        "current": "0.4.0", "latest": "0.5.0", "available": True,
        "wheel_url": "", "release_url": "", "notes": "stale",
        "checked_at": time.time() - version_check.CACHE_TTL_SEC - 60,
    }
    update_check_path().write_text(json.dumps(stale))
    with patch.object(version_check, "__version__", "0.4.0"), \
         _mock_httpx(_release_payload("0.6.0")):
        result = version_check.check(force=False, current="0.4.0")
    assert result.latest == "0.6.0"


def test_cache_invalidated_when_runtime_version_drifted(isolated_data_dir):
    """Regression: user manually `git pull + pip install` upgrades from
    0.7.0 → 0.7.1 while the cache still says current=0.7.0. Without this
    check the UI keeps offering "upgrade to 0.7.1", the install endpoint
    then 409s because target == __version__. The cache must be treated as
    stale when runtime __version__ drifts from what was recorded."""
    stale_but_recent = {
        "current": "0.7.0",          # recorded when we were on 0.7.0
        "latest": "0.7.1",
        "available": True,
        "wheel_url": "https://example.com/auto_daily_log-0.7.1-py3-none-any.whl",
        "release_url": "",
        "notes": "should be ignored",
        "checked_at": time.time(),    # within 24h TTL — old gate would return cache
    }
    update_check_path().write_text(json.dumps(stale_but_recent))

    # Runtime __version__ has moved on; check() must re-query GitHub.
    with patch.object(version_check, "__version__", "0.7.1"), \
         _mock_httpx(_release_payload("0.7.1")):
        result = version_check.check(force=False)

    assert result.current == "0.7.1"
    assert result.latest == "0.7.1"
    assert result.available is False  # now actually up to date
    assert result.notes != "should be ignored"  # stale cache was dropped


def test_cache_honored_when_runtime_version_matches(isolated_data_dir):
    """Sanity: the TTL-window cache should still be used when the runtime
    version matches the cached `current`. We don't want to tear down the
    cache on every call — only when drift is detected."""
    cached = {
        "current": "0.7.1",
        "latest": "0.7.1",
        "available": False,
        "wheel_url": "",
        "release_url": "",
        "notes": "fresh",
        "checked_at": time.time(),
    }
    update_check_path().write_text(json.dumps(cached))

    with patch.object(version_check, "__version__", "0.7.1"), \
         patch("auto_daily_log.updater.version_check.httpx.get") as net:
        result = version_check.check(force=False)

    assert net.call_count == 0
    assert result.notes == "fresh"


def test_check_swallows_network_error_and_returns_no_update():
    import httpx as _httpx
    with patch(
        "auto_daily_log.updater.version_check.httpx.get",
        side_effect=_httpx.ConnectError("offline"),
    ):
        result = version_check.check(force=True, current="0.4.0")
    assert result.available is False
    assert result.current == "0.4.0"
    assert "check failed" in result.notes
