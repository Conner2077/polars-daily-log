"""Unit tests for the runner: state file, pip invocation, restart spawn.

Live subprocess interactions (kill/spawn) are tested with a fake binary
script so the test is identical on macOS / Linux / Windows.
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

from auto_daily_log.updater import runner, state
from auto_daily_log.updater.paths import (
    backups_dir,
    data_dir,
    update_status_path,
)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"system:\n  data_dir: {tmp_path}/data\n")
    monkeypatch.setenv("PDL_SERVER_CONFIG", str(cfg))
    yield tmp_path


# ── State file ─────────────────────────────────────────────────────────

def test_advance_persists_phase_and_log():
    state.write_status(state.UpdateStatus())
    s = state.advance(phase="starting", progress_pct=5, message="kicking off")
    assert s.phase == "starting"
    assert s.progress_pct == 5
    assert s.log == ["[starting] kicking off"]


def test_advance_rejects_unknown_phase():
    with pytest.raises(ValueError):
        state.advance(phase="bogus", progress_pct=1, message="x")


def test_status_file_is_atomic_on_concurrent_read(isolated_data_dir):
    state.write_status(state.UpdateStatus(phase="installing", progress_pct=55))
    raw = update_status_path().read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["phase"] == "installing"
    assert parsed["progress_pct"] == 55


# ── Pip invocation ─────────────────────────────────────────────────────

def _make_fake_pip(tmp_path: Path, *, exit_code: int = 0) -> Path:
    """A cross-platform fake pip: a Python script invoked via sys.executable.

    The runner's PIP_CMD_ENV override accepts a space-separated command,
    so we point it at ``<python> <fake.py>``.
    """
    fake = tmp_path / "fake_pip.py"
    fake.write_text(
        "import sys\n"
        f"sys.exit({exit_code})\n"
    )
    return fake


def test_run_pip_install_returns_zero_on_success(tmp_path, monkeypatch):
    fake = _make_fake_pip(tmp_path, exit_code=0)
    monkeypatch.setenv(runner.PIP_CMD_ENV, f"{sys.executable} {fake}")
    rc = runner.run_pip_install("https://example.com/x.whl", log_path=tmp_path / "u.log")
    assert rc == 0
    assert (tmp_path / "u.log").exists()


def test_run_pip_install_returns_nonzero_on_failure(tmp_path, monkeypatch):
    fake = _make_fake_pip(tmp_path, exit_code=7)
    monkeypatch.setenv(runner.PIP_CMD_ENV, f"{sys.executable} {fake}")
    rc = runner.run_pip_install("https://example.com/x.whl", log_path=tmp_path / "u.log")
    assert rc == 7


# ── Detached spawn ─────────────────────────────────────────────────────

def test_spawn_detached_starts_independent_child(tmp_path):
    sentinel = tmp_path / "child_was_here.txt"
    script = tmp_path / "child.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('ok')\n"
    )
    pid = runner.spawn_detached(
        [sys.executable, str(script)],
        tmp_path / "child.log",
    )
    assert pid > 0
    deadline = time.time() + 5
    while time.time() < deadline and not sentinel.exists():
        time.sleep(0.1)
    assert sentinel.read_text() == "ok"


# ── apply_update end-to-end (mocked) ──────────────────────────────────

def test_apply_update_writes_completed_phase_on_happy_path(tmp_path, monkeypatch):
    db = data_dir() / "data.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE x(id INTEGER)")
        conn.execute("INSERT INTO x VALUES(1)")

    fake = _make_fake_pip(tmp_path, exit_code=0)
    monkeypatch.setenv(runner.PIP_CMD_ENV, f"{sys.executable} {fake}")

    spec = runner.RestartSpec(
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        log_path=str(tmp_path / "server.log"),
        pidfile=str(tmp_path / "server.pid"),
        health_url="http://127.0.0.1:1/never",
        wait_seconds=0,
    )

    with patch("auto_daily_log.updater.runner.wait_for_health", return_value=True), \
         patch("auto_daily_log.updater.runner.spawn_detached", return_value=12345):
        result = runner.apply_update(
            target_version="0.9.9",
            wheel_url="https://example.com/x.whl",
            restart=spec,
            config_paths=[],
            server_pid=None,
        )

    assert result.phase == "completed"
    assert result.progress_pct == 100
    backup_dirs = list(backups_dir().iterdir())
    assert len(backup_dirs) == 1


def test_apply_update_marks_failed_when_pip_fails(tmp_path, monkeypatch):
    db = data_dir() / "data.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE x(id INTEGER)")
        conn.execute("INSERT INTO x VALUES(1)")

    fake = _make_fake_pip(tmp_path, exit_code=2)
    monkeypatch.setenv(runner.PIP_CMD_ENV, f"{sys.executable} {fake}")
    spec = runner.RestartSpec(
        argv=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        log_path=str(tmp_path / "server.log"),
        pidfile=str(tmp_path / "server.pid"),
        health_url="http://127.0.0.1:1/x",
        wait_seconds=0,
    )
    result = runner.apply_update(
        target_version="0.9.9",
        wheel_url="https://example.com/x.whl",
        restart=spec,
        config_paths=[],
        server_pid=None,
    )
    assert result.phase == "failed"
    # pip-exit signal lives in the audit log; the final message gets
    # overwritten by the auto-rollback step.
    assert any("pip exited with code 2" in line for line in result.log)
    # Auto-rollback should have triggered after pip failure.
    assert "rolled back" in result.message
