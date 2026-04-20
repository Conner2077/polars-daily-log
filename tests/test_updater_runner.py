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


def test_installer_command_uses_pip_when_available(monkeypatch):
    """Default path: `python -m pip` works → use it directly."""
    monkeypatch.delenv(runner.PIP_CMD_ENV, raising=False)
    monkeypatch.setattr(runner, "_probe", lambda cmd: 0 if "pip" in cmd else 1)
    monkeypatch.setattr("shutil.which", lambda name: None)
    cmd = runner._installer_command("x.whl")
    assert cmd[0] == sys.executable
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert cmd[-1] == "x.whl"


def test_installer_command_bootstraps_via_ensurepip(monkeypatch):
    """uv venvs that exclude pip but include ensurepip should self-repair:
    first pip probe fails → ensurepip succeeds → second pip probe passes."""
    monkeypatch.delenv(runner.PIP_CMD_ENV, raising=False)
    calls = []

    def fake_probe(cmd):
        calls.append(cmd)
        is_ensurepip = "ensurepip" in cmd
        if is_ensurepip:
            return 0  # ensurepip succeeds
        # pip probes: first fails, subsequent (after ensurepip) succeeds
        prior_pip_probes = sum(1 for c in calls[:-1] if "pip" in c and "ensurepip" not in c)
        return 1 if prior_pip_probes == 0 else 0

    monkeypatch.setattr(runner, "_probe", fake_probe)
    monkeypatch.setattr("shutil.which", lambda name: None)

    cmd = runner._installer_command("x.whl")
    assert cmd[0] == sys.executable
    assert cmd[1:4] == ["-m", "pip", "install"]
    # ensurepip must have been attempted in the probe chain
    assert any("ensurepip" in c for c in calls)


def test_installer_command_falls_back_to_uv_when_pip_unavailable(monkeypatch, tmp_path):
    """Pure uv venv: neither pip nor ensurepip works → use `uv pip install`."""
    monkeypatch.delenv(runner.PIP_CMD_ENV, raising=False)
    monkeypatch.setattr(runner, "_probe", lambda cmd: 1)  # every probe fails

    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 0\n")
    fake_uv.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(fake_uv) if name == "uv" else None)

    cmd = runner._installer_command("x.whl")
    assert cmd[0] == str(fake_uv)
    assert cmd[1:3] == ["pip", "install"]
    assert "--python" in cmd and sys.executable in cmd
    assert cmd[-2:] == ["--upgrade", "x.whl"]


def test_installer_command_raises_when_neither_pip_nor_uv(monkeypatch):
    """No pip, no ensurepip, no uv → raise a clear error so upstream can log + rollback."""
    monkeypatch.delenv(runner.PIP_CMD_ENV, raising=False)
    monkeypatch.setattr(runner, "_probe", lambda cmd: 1)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="pip"):
        runner._installer_command("x.whl")


def test_run_pip_install_logs_and_returns_127_when_installer_unavailable(monkeypatch, tmp_path):
    """If no installer can be found, run_pip_install returns 127 (command
    not found) and writes a diagnostic to the log, so apply_update triggers
    a clean rollback instead of crashing."""
    monkeypatch.delenv(runner.PIP_CMD_ENV, raising=False)
    monkeypatch.setattr(runner, "_probe", lambda cmd: 1)
    monkeypatch.setattr("shutil.which", lambda name: None)

    log = tmp_path / "u.log"
    rc = runner.run_pip_install("x.whl", log_path=log)
    assert rc == 127
    assert log.exists()
    content = log.read_text(encoding="utf-8")
    assert "pip" in content  # diagnostic mentions pip


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
