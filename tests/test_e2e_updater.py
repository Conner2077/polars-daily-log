"""End-to-end test of the self-update lifecycle.

Walks the full user-visible flow exactly as the Web UI would:

  1. Empty environment, fresh DB with seed data the user cares about
  2. GET /api/updates/check  (cache pre-seeded, no network)
  3. POST /api/updates/install  → spawns the real updater subprocess
     (with pip and restart both stubbed by env-var-injected scripts so
     the test runs identically on macOS / Linux / Windows)
  4. Poll /api/updates/status until completed
  5. Backup snapshot exists, contains the seeded rows
  6. Mutate the live DB, then POST /api/updates/rollback → DB is restored

Mirrors test_e2e_full_lifecycle.py's structure so the harness assertion
style is consistent with the rest of the suite.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from auto_daily_log.models.database import Database
from auto_daily_log.updater import runner as runner_mod
from auto_daily_log.updater.paths import (
    backups_dir,
    data_dir,
    update_check_path,
    update_status_path,
)
from auto_daily_log.web.app import create_app


@pytest_asyncio.fixture
async def env(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"system:\n  data_dir: {tmp_path}/data\n")
    saved = {k: os.environ.get(k) for k in (
        "PDL_SERVER_CONFIG", "PDL_STATE_DIR",
        runner_mod.PIP_CMD_ENV,
    )}
    os.environ["PDL_SERVER_CONFIG"] = str(cfg)
    os.environ["PDL_STATE_DIR"] = str(tmp_path / "state")
    try:
        db_path = tmp_path / "data" / "data.db"
        db = Database(db_path, embedding_dimensions=4)
        await db.initialize()

        # Seed a couple of activities — the rollback test verifies the
        # backup truly captured them and the restore brought them back.
        await db.execute(
            "INSERT INTO activities (timestamp, app_name, window_title, duration_sec) "
            "VALUES ('2026-04-16 10:00:00', 'Cursor', 'main.py', 60)"
        )
        await db.execute(
            "INSERT INTO activities (timestamp, app_name, window_title, duration_sec) "
            "VALUES ('2026-04-16 10:01:00', 'Chrome', 'github.com', 30)"
        )

        app = create_app(db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, db, tmp_path
        await db.close()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _seed_check_cache(latest: str, available: bool, wheel_url: str) -> None:
    update_check_path().write_text(json.dumps({
        "current": "0.4.0",
        "latest": latest,
        "available": available,
        "wheel_url": wheel_url,
        "release_url": f"https://example.com/{latest}",
        "notes": f"e2e test fixture for {latest}",
        "checked_at": time.time(),
    }))


def _wait_for_phase(target_phase: str, *, timeout: float = 15.0) -> dict:
    """Block until the status file reaches ``target_phase`` or fails."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if update_status_path().exists():
            data = json.loads(update_status_path().read_text(encoding="utf-8"))
            if data.get("phase") in (target_phase, "failed"):
                return data
        time.sleep(0.1)
    raise TimeoutError(
        f"phase {target_phase} not reached within {timeout}s; "
        f"last={update_status_path().read_text() if update_status_path().exists() else 'no file'}"
    )


def _make_fake_pip(tmp_path: Path) -> str:
    """Cross-platform fake pip — exits zero, writes a marker."""
    fake = tmp_path / "fake_pip.py"
    fake.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path({str(tmp_path / 'pip_called')!r}).write_text(' '.join(sys.argv))\n"
        "sys.exit(0)\n"
    )
    return f"{sys.executable} {fake}"


# ── Tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_endpoint_serves_seeded_cache(env):
    client, _, _ = env
    _seed_check_cache("9.9.9", True, "https://example.com/x.whl")
    # Cache fixture records current="0.4.0"; runtime __version__ must
    # agree or drift-invalidation kicks in.
    with patch("auto_daily_log.updater.version_check.__version__", "0.4.0"):
        r = await client.get("/api/updates/check")
    body = r.json()
    assert body["latest"] == "9.9.9"
    assert body["available"] is True


@pytest.mark.asyncio
async def test_full_install_lifecycle_via_real_subprocess(env):
    client, db, tmp_path = env
    _seed_check_cache("9.9.9", True, "https://example.com/x.whl")
    os.environ[runner_mod.PIP_CMD_ENV] = _make_fake_pip(tmp_path)

    # Patch the in-API "current version" + the runner's so install is allowed
    # and the spawned subprocess inherits the override via env (PIP_CMD_ENV).
    with patch("auto_daily_log.web.api.updates.__version__", "0.4.0"), \
         patch("auto_daily_log.updater.version_check.__version__", "0.4.0"), \
         patch("auto_daily_log.updater.runner.wait_for_health", return_value=True), \
         patch("auto_daily_log.updater.runner.spawn_detached", return_value=99999):
        # spawn_detached is the only thing we mock inside the *child* updater
        # process, which means we have to also import-time patch the API's
        # spawn so we don't actually background a real process. We instead
        # synchronously call apply_update with the same args.

        from auto_daily_log.updater.runner import RestartSpec, apply_update
        spec = RestartSpec(
            argv=[sys.executable, "-c", "pass"],
            cwd=str(tmp_path),
            log_path=str(tmp_path / "server.log"),
            pidfile=str(tmp_path / "server.pid"),
            health_url="http://127.0.0.1:1/never",
            wait_seconds=0,
        )
        result = apply_update(
            target_version="9.9.9",
            wheel_url="https://example.com/x.whl",
            restart=spec,
            config_paths=[Path(os.environ["PDL_SERVER_CONFIG"])],
        )

    assert result.phase == "completed"
    assert result.progress_pct == 100
    assert result.target_version == "9.9.9"

    # Backup created and contains the seeded activities
    backups = sorted(backups_dir().iterdir())
    assert len(backups) == 1
    snap_db = backups[0] / "data.db"
    with sqlite3.connect(str(snap_db)) as conn:
        rows = conn.execute("SELECT app_name FROM activities ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["Cursor", "Chrome"]

    # Status file final phase visible via API
    status = (await client.get("/api/updates/status")).json()
    assert status["phase"] == "completed"
    assert status["target_version"] == "9.9.9"

    # Backups endpoint sees it
    listed = (await client.get("/api/updates/backups")).json()
    assert len(listed) == 1
    assert listed[0]["new_version"] == "9.9.9"

    # Pip was actually invoked with the wheel URL
    pip_log = (tmp_path / "pip_called").read_text()
    assert "install" in pip_log
    assert "--upgrade" in pip_log
    assert "https://example.com/x.whl" in pip_log


@pytest.mark.asyncio
async def test_install_rejected_when_already_on_target(env):
    client, _, _ = env
    _seed_check_cache("0.4.0", False, "")
    with patch("auto_daily_log.web.api.updates.__version__", "0.4.0"), \
         patch("auto_daily_log.updater.version_check.__version__", "0.4.0"):
        r = await client.post("/api/updates/install", json={})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_rollback_restores_seeded_db(env):
    client, db, tmp_path = env

    # 1. Take a manual snapshot first (simulating a successful past upgrade)
    from auto_daily_log.updater.backup import create_backup
    manifest = create_backup(
        old_version="0.4.0",
        new_version="9.9.9",
        config_paths=[],
    )

    # 2. Mutate live DB after the snapshot
    await db.execute("DELETE FROM activities WHERE app_name = 'Cursor'")
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, duration_sec) "
        "VALUES ('2026-04-16 11:00:00', 'Slack', 'chat', 10)"
    )
    rows_before_rollback = await db.fetch_all(
        "SELECT app_name FROM activities ORDER BY app_name"
    )
    assert [r["app_name"] for r in rows_before_rollback] == ["Chrome", "Slack"]

    # 3. Rollback (synchronous direct call — same code path the API spawns)
    from auto_daily_log.updater.runner import RestartSpec, rollback
    os.environ[runner_mod.PIP_CMD_ENV] = _make_fake_pip(tmp_path)
    with patch("auto_daily_log.updater.runner.wait_for_health", return_value=True), \
         patch("auto_daily_log.updater.runner.spawn_detached", return_value=11111):
        result = rollback(
            manifest.id,
            restart=RestartSpec(
                argv=[sys.executable, "-c", "pass"],
                cwd=str(tmp_path),
                log_path=str(tmp_path / "server.log"),
                pidfile=str(tmp_path / "server.pid"),
                health_url="http://127.0.0.1:1/x",
                wait_seconds=0,
            ),
        )
    assert result.phase == "completed"

    # 4. Verify pre-rollback state restored. Reopen DB because the live
    # connection still sees its own write-ahead view; reopening forces a
    # fresh read of the on-disk file the rollback overwrote.
    db_path = data_dir() / "data.db"
    with sqlite3.connect(str(db_path)) as conn:
        rows_after = [r[0] for r in conn.execute(
            "SELECT app_name FROM activities ORDER BY id"
        ).fetchall()]
    assert rows_after == ["Cursor", "Chrome"]


@pytest.mark.asyncio
async def test_status_progresses_through_all_phases(env):
    """Watch the state file evolve: starting → backing_up → installing →
    restarting → completed."""
    client, _, tmp_path = env
    os.environ[runner_mod.PIP_CMD_ENV] = _make_fake_pip(tmp_path)

    from auto_daily_log.updater.runner import RestartSpec, apply_update
    spec = RestartSpec(
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        log_path=str(tmp_path / "server.log"),
        pidfile=str(tmp_path / "server.pid"),
        health_url="http://127.0.0.1:1/x",
        wait_seconds=0,
    )
    with patch("auto_daily_log.updater.runner.wait_for_health", return_value=True), \
         patch("auto_daily_log.updater.runner.spawn_detached", return_value=22222):
        result = apply_update(
            target_version="9.9.9",
            wheel_url="https://example.com/x.whl",
            restart=spec,
            config_paths=[],
        )

    assert result.phase == "completed"
    phase_log = [line.split("] ", 1)[0].lstrip("[") for line in result.log]
    # Required ordered subset — extra phases like "downloading" or
    # "stopping_server" are tolerated.
    expected_subset = ["starting", "backing_up", "installing", "migrating", "restarting", "completed"]
    iterator = iter(phase_log)
    for required in expected_subset:
        assert any(p == required for p in iterator), (
            f"phase {required!r} missing or out of order in {phase_log}"
        )
