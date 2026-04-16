"""Integration tests for /api/updates/* endpoints.

Covers the FastAPI surface end-to-end with the updater subprocess fully
mocked — the spawned child is never actually launched, but the request
flow through ``check`` → ``install`` → ``status`` is exercised.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from auto_daily_log.models.database import Database
from auto_daily_log.updater import state as state_mod
from auto_daily_log.updater.paths import backups_dir, update_check_path
from auto_daily_log.web.app import create_app


@pytest_asyncio.fixture
async def env(tmp_path):
    import os
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"system:\n  data_dir: {tmp_path}/data\n")
    saved = {k: os.environ.get(k) for k in ("PDL_SERVER_CONFIG", "PDL_STATE_DIR")}
    os.environ["PDL_SERVER_CONFIG"] = str(cfg)
    os.environ["PDL_STATE_DIR"] = str(tmp_path / "state")
    try:
        db = Database(tmp_path / "data" / "data.db", embedding_dimensions=4)
        await db.initialize()
        app = create_app(db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        await db.close()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _seed_check_cache(latest: str, available: bool, wheel: str = "") -> None:
    update_check_path().write_text(json.dumps({
        "current": "0.4.0",
        "latest": latest,
        "available": available,
        "wheel_url": wheel or f"https://example.com/auto_daily_log-{latest}-py3-none-any.whl",
        "release_url": "https://example.com",
        "notes": "test fixture",
        "checked_at": time.time(),
    }))


# ── /check ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_returns_cached_payload(env):
    _seed_check_cache("9.9.9", True)
    r = await env.get("/api/updates/check")
    assert r.status_code == 200
    body = r.json()
    assert body["latest"] == "9.9.9"
    assert body["available"] is True


@pytest.mark.asyncio
async def test_check_force_bypasses_cache(env):
    _seed_check_cache("0.4.0", False)
    payload = {
        "tag_name": "v9.9.9",
        "html_url": "https://example.com/v9.9.9",
        "body": "notes",
        "assets": [{
            "name": "auto_daily_log-9.9.9-py3-none-any.whl",
            "browser_download_url": "https://example.com/x.whl",
        }],
    }
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    with patch("auto_daily_log.updater.version_check.httpx.get", return_value=resp):
        r = await env.get("/api/updates/check?force=true")
    assert r.json()["latest"] == "9.9.9"


# ── /install ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_install_returns_409_when_already_on_latest(env):
    _seed_check_cache("0.4.0", False)
    with patch("auto_daily_log.web.api.updates.__version__", "0.4.0"):
        r = await env.post("/api/updates/install", json={})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_install_spawns_updater_and_returns_202(env):
    _seed_check_cache("9.9.9", True, "https://example.com/x.whl")
    with patch("auto_daily_log.web.api.updates._spawn_updater", return_value=4242) as spawned, \
         patch("auto_daily_log.web.api.updates.__version__", "0.4.0"):
        r = await env.post("/api/updates/install", json={})
    assert r.status_code == 202
    body = r.json()
    assert body["updater_pid"] == 4242
    assert body["target"] == "9.9.9"
    assert spawned.call_count == 1
    sub_args = spawned.call_args[0][0]
    assert sub_args[0] == "apply"
    assert "--target-version" in sub_args
    assert "9.9.9" in sub_args


@pytest.mark.asyncio
async def test_install_rejects_when_no_wheel_url(env):
    _seed_check_cache("9.9.9", True, wheel="")
    update_check_path().write_text(json.dumps({
        "current": "0.4.0", "latest": "9.9.9", "available": True,
        "wheel_url": "", "release_url": "", "notes": "",
        "checked_at": time.time(),
    }))
    with patch("auto_daily_log.web.api.updates.__version__", "0.4.0"):
        r = await env.post("/api/updates/install", json={})
    assert r.status_code == 400


# ── /status ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_reflects_state_file(env):
    state_mod.write_status(state_mod.UpdateStatus(
        phase="installing", progress_pct=55, target_version="9.9.9",
        from_version="0.4.0", message="pip running",
    ))
    r = await env.get("/api/updates/status")
    body = r.json()
    assert body["phase"] == "installing"
    assert body["progress_pct"] == 55
    assert body["target_version"] == "9.9.9"


@pytest.mark.asyncio
async def test_status_idle_when_no_state_file(env):
    r = await env.get("/api/updates/status")
    body = r.json()
    assert body["phase"] == "idle"
    assert body["progress_pct"] == 0


# ── /backups + /rollback + /prune ──────────────────────────────────────

@pytest.mark.asyncio
async def test_backups_endpoint_lists_backups(env):
    from auto_daily_log.updater.backup import create_backup
    create_backup(old_version="0.4.0", new_version="0.5.0", config_paths=[])
    r = await env.get("/api/updates/backups")
    body = r.json()
    assert len(body) == 1
    assert body[0]["old_version"] == "0.4.0"
    assert body[0]["new_version"] == "0.5.0"


@pytest.mark.asyncio
async def test_rollback_404_on_unknown_backup(env):
    r = await env.post("/api/updates/rollback", json={"backup_id": "nope"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rollback_spawns_updater(env):
    from auto_daily_log.updater.backup import create_backup
    m = create_backup(old_version="0.4.0", new_version="0.5.0", config_paths=[])
    with patch("auto_daily_log.web.api.updates._spawn_updater", return_value=999):
        r = await env.post("/api/updates/rollback", json={"backup_id": m.id})
    assert r.status_code == 202
    assert r.json()["backup_id"] == m.id


@pytest.mark.asyncio
async def test_prune_removes_old_backups(env):
    from datetime import datetime, timedelta, timezone
    from auto_daily_log.updater.backup import create_backup
    base = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
    for offset in range(5):
        create_backup(
            old_version="0.4.0", new_version="0.5.0",
            config_paths=[], now=base + timedelta(seconds=offset),
        )
    r = await env.post("/api/updates/prune", json={"keep": 2})
    body = r.json()
    assert len(body["removed"]) == 3
    assert len(body["kept"]) == 2
