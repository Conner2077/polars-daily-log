"""HTTP surface for the self-update flow.

Endpoints:
  GET  /api/updates/check        — current + latest version, cached 24h
  POST /api/updates/install      — kick off detached upgrade, returns 202
  GET  /api/updates/status       — phase/progress for the currently running run
  GET  /api/updates/backups      — list of snapshots
  POST /api/updates/rollback     — restore a backup, kicks off detached
  POST /api/updates/prune        — drop old backups beyond ``keep``

The install/rollback endpoints **return immediately** because the work
happens in a child process that survives the server's death. The UI
polls /status to draw a progress bar and reloads when phase=completed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import __version__
from ...updater import backup as backup_mod
from ...updater import state as state_mod
from ...updater import version_check
from ...updater.runner import RestartSpec, spawn_detached

router = APIRouter(tags=["updates"], prefix="/updates")


class InstallRequest(BaseModel):
    target_version: Optional[str] = None  # default: latest from check
    wheel_url: Optional[str] = None       # default: from check result


class RollbackRequest(BaseModel):
    backup_id: str


class PruneRequest(BaseModel):
    keep: int = backup_mod.KEEP_RECENT


def _restart_argv() -> list[str]:
    """How to bring the server back up after the wheel install.

    Mirrors how `pdl server start` invokes us, but uses sys.executable so
    it works without the bash wrapper (Windows path)."""
    config = os.environ.get("PDL_SERVER_CONFIG", "")
    port = os.environ.get("PDL_SERVER_PORT", "8888")
    argv = [sys.executable, "-u", "-m", "auto_daily_log"]
    if config:
        argv += ["--config", config]
    argv += ["--port", port]
    return argv


def _state_dir_for_logs() -> Path:
    state_root = os.environ.get("PDL_STATE_DIR")
    if state_root:
        return Path(state_root).expanduser() / "logs"
    return Path.home() / ".auto_daily_log" / "logs"


def _server_pidfile() -> Path:
    state_root = os.environ.get("PDL_STATE_DIR")
    if state_root:
        return Path(state_root).expanduser() / "pids" / "server.pid"
    return Path.home() / ".auto_daily_log" / "pids" / "server.pid"


def _read_server_pid() -> Optional[int]:
    f = _server_pidfile()
    if not f.exists():
        return None
    try:
        return int(f.read_text().strip())
    except (ValueError, OSError):
        return None


def _config_paths_arg() -> str:
    """Pack the server + collector config files for the updater to back up."""
    paths: list[str] = []
    for env in ("PDL_SERVER_CONFIG", "PDL_COLLECTOR_CONFIG"):
        v = os.environ.get(env)
        if v and Path(v).exists():
            paths.append(v)
    return os.pathsep.join(paths)


def _spawn_updater(extra_args: list[str]) -> int:
    log_path = _state_dir_for_logs() / "updater.log"
    argv = [sys.executable, "-u", "-m", "auto_daily_log.updater", *extra_args]
    return spawn_detached(argv, log_path)


@router.get("/check")
def check_for_update(force: bool = False):
    return version_check.check(force=force).to_dict()


@router.get("/status")
def get_status():
    return state_mod.read_status().to_dict()


@router.get("/backups")
def list_backups():
    return [b.to_dict() for b in backup_mod.list_backups()]


@router.post("/install", status_code=202)
def install_update(req: InstallRequest):
    info = version_check.check()
    target = req.target_version or info.latest
    wheel = req.wheel_url or info.wheel_url
    if not target or target == __version__:
        raise HTTPException(409, f"already on {__version__}; nothing to install")
    if not wheel:
        raise HTTPException(400, "no wheel_url available; check release assets")

    pid = _read_server_pid()
    state_mod.write_status(state_mod.UpdateStatus(
        phase="starting",
        target_version=target,
        from_version=__version__,
        progress_pct=1,
        message="updater spawning",
    ))

    extra = [
        "apply",
        "--target-version", target,
        "--wheel-url", wheel,
        "--restart-argv", "\x1f".join(_restart_argv()),
        "--restart-cwd", os.getcwd(),
        "--restart-log", str(_state_dir_for_logs() / "server.log"),
        "--restart-pidfile", str(_server_pidfile()),
        "--health-url", f"http://127.0.0.1:{os.environ.get('PDL_SERVER_PORT', '8888')}/api/dashboard/today",
        "--config-paths", _config_paths_arg(),
    ]
    if pid:
        extra += ["--server-pid", str(pid)]
    updater_pid = _spawn_updater(extra)
    return {"status": "spawned", "updater_pid": updater_pid, "target": target}


@router.post("/rollback", status_code=202)
def rollback(req: RollbackRequest):
    target = next((b for b in backup_mod.list_backups() if b.id == req.backup_id), None)
    if target is None:
        raise HTTPException(404, f"backup {req.backup_id} not found")

    state_mod.write_status(state_mod.UpdateStatus(
        phase="starting",
        target_version=target.old_version,
        from_version=__version__,
        progress_pct=1,
        message=f"rolling back to {target.old_version}",
    ))
    extra = [
        "rollback",
        "--backup", req.backup_id,
        "--restart-argv", "\x1f".join(_restart_argv()),
        "--restart-cwd", os.getcwd(),
        "--restart-log", str(_state_dir_for_logs() / "server.log"),
        "--restart-pidfile", str(_server_pidfile()),
        "--health-url", f"http://127.0.0.1:{os.environ.get('PDL_SERVER_PORT', '8888')}/api/dashboard/today",
    ]
    updater_pid = _spawn_updater(extra)
    return {"status": "spawned", "updater_pid": updater_pid, "backup_id": req.backup_id}


@router.post("/prune")
def prune(req: PruneRequest):
    removed = backup_mod.prune_backups(keep_recent=req.keep)
    return {"removed": removed, "kept": [b.id for b in backup_mod.list_backups()]}
