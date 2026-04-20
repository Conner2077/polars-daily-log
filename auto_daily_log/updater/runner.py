"""Orchestrate an end-to-end upgrade: backup → pip install → restart.

This module is invoked detached from the live server process by
``python -m auto_daily_log.updater apply --target-version X``. By the
time it does anything destructive the API has already returned 202 to
the browser, so the user sees a clean "upgrading…" UI.

All subprocess calls are **cross-platform**:
  * killing the server uses ``taskkill`` on Windows and ``signal`` elsewhere
  * detached spawn uses ``DETACHED_PROCESS`` on Windows and
    ``start_new_session=True`` on POSIX

Pip itself is invoked through the **current interpreter** so we never
care which venv we're in — ``sys.executable -m pip`` always targets
the right environment.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import __version__
from .backup import create_backup, restore_backup
from .paths import data_dir
from .state import UpdateStatus, advance, write_status

# Override pip with a fake executable in tests; otherwise use this interp's pip.
PIP_CMD_ENV = "PDL_UPDATER_PIP_CMD"


@dataclass
class RestartSpec:
    """Describes how to bring the server back up after the wheel is installed.

    The server writes this to disk before it dies so the updater (a fresh
    process) doesn't need to guess at argv. Encoded as JSON in the state file.
    """
    argv: list[str]
    cwd: str
    log_path: str
    pidfile: str
    health_url: str = "http://127.0.0.1:8888/api/dashboard/today"
    wait_seconds: int = 30


def _pip_argv() -> list[str]:
    """Legacy helper kept for backward-compat; prefer _installer_command."""
    override = os.environ.get(PIP_CMD_ENV)
    if override:
        return override.split()
    return [sys.executable, "-m", "pip"]


def _probe(cmd: list[str]) -> int:
    """Run cmd silently; return exit code. 127 if binary missing."""
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
        return proc.returncode
    except (OSError, FileNotFoundError):
        return 127


def _installer_command(target: str) -> list[str]:
    """Build the command that installs/upgrades ``target`` into the current venv.

    Probe chain — first hit wins:
      1. ``python -m pip`` if pip is already importable
      2. ``python -m ensurepip --upgrade`` to bootstrap pip (stdlib fallback)
         then re-probe pip
      3. ``uv pip install --python <python>`` if uv is on PATH

    uv venvs (``uv venv``) ship without pip by default and also strip
    ensurepip; step 3 catches those installs. Raises RuntimeError if
    nothing works so apply_update can log a clear message + rollback.
    """
    override = os.environ.get(PIP_CMD_ENV)
    if override:
        return [*override.split(), "install", "--upgrade", target]

    python = sys.executable

    # 1. pip already there
    if _probe([python, "-m", "pip", "--version"]) == 0:
        return [python, "-m", "pip", "install", "--upgrade", target]

    # 2. bootstrap pip via ensurepip (works for stdlib-based venvs)
    if _probe([python, "-m", "ensurepip", "--upgrade"]) == 0:
        if _probe([python, "-m", "pip", "--version"]) == 0:
            return [python, "-m", "pip", "install", "--upgrade", target]

    # 3. fall back to uv (covers `uv venv` installs that skipped ensurepip)
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", python, "--upgrade", target]

    raise RuntimeError(
        "Cannot install update: this venv has no pip and `uv` is not on PATH. "
        f"Run `{python} -m ensurepip --upgrade` or `uv pip install pip` then retry."
    )


def kill_server(pid: int, *, timeout: float = 10.0) -> bool:
    """Stop the running server. Returns True if the PID was reaped."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.2)

    # Last resort
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    return not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        )
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def spawn_detached(argv: list[str], log_path: Path, *, cwd: Optional[Path] = None) -> int:
    """Launch a long-lived process detached from this one. Returns child PID."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = open(log_path, "ab", buffering=0)
    kwargs: dict = {
        "stdout": log_fd,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "cwd": str(cwd) if cwd else None,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    return proc.pid


def run_pip_install(wheel_url: str, *, log_path: Path) -> int:
    """Install/upgrade the package into the current venv. Returns rc.

    Picks pip or uv automatically — see _installer_command. Returns 127
    (shell "command not found") when no installer is available, which
    apply_update treats as a pip failure and triggers rollback.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cmd = _installer_command(wheel_url)
    except RuntimeError as exc:
        with log_path.open("ab", buffering=0) as f:
            f.write(f"\nupdater error: {exc}\n".encode())
        return 127
    with log_path.open("ab", buffering=0) as f:
        f.write(f"\n$ {' '.join(cmd)}\n".encode())
        proc = subprocess.run(
            cmd,
            stdout=f,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return proc.returncode


def wait_for_health(url: str, *, timeout: int) -> bool:
    """Poll the new server until it answers, or the timeout elapses."""
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def apply_update(
    *,
    target_version: str,
    wheel_url: str,
    restart: RestartSpec,
    config_paths: list[Path],
    server_pid: Optional[int] = None,
) -> UpdateStatus:
    """End-to-end upgrade. Errors are caught and reported via the state file
    so the browser always sees a final phase (completed | failed)."""
    status = UpdateStatus(
        target_version=target_version,
        from_version=__version__,
    )
    write_status(status)
    log_dir = Path(restart.log_path).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    update_log = log_dir / "update.log"

    try:
        status = advance(phase="starting", progress_pct=5, message=f"upgrading to {target_version}", base=status)

        if server_pid is not None:
            status = advance(phase="stopping_server", progress_pct=10, message=f"stopping pid {server_pid}", base=status)
            kill_server(server_pid)

        status = advance(phase="backing_up", progress_pct=25, message="snapshotting database + config", base=status)
        manifest = create_backup(
            old_version=__version__,
            new_version=target_version,
            config_paths=config_paths,
        )
        status.backup_id = manifest.id
        write_status(status)

        status = advance(phase="downloading", progress_pct=40, message=f"fetching {wheel_url}", base=status)
        status = advance(phase="installing", progress_pct=55, message="pip install --upgrade", base=status)
        rc = run_pip_install(wheel_url, log_path=update_log)
        if rc != 0:
            status = advance(phase="failed", progress_pct=55, message=f"pip exited with code {rc}", base=status)
            return _try_rollback(status, manifest.id)

        status = advance(phase="migrating", progress_pct=80, message="schema migrations run on next server start", base=status)

        status = advance(phase="restarting", progress_pct=90, message="launching new server", base=status)
        spawn_detached(restart.argv, Path(restart.log_path), cwd=Path(restart.cwd))

        if wait_for_health(restart.health_url, timeout=restart.wait_seconds):
            status = advance(phase="completed", progress_pct=100, message=f"now running {target_version}", base=status)
        else:
            status = advance(
                phase="failed",
                progress_pct=90,
                message=f"new server did not answer {restart.health_url} within {restart.wait_seconds}s",
                base=status,
            )
            return _try_rollback(status, manifest.id)
        return status

    except Exception as exc:  # pragma: no cover - defensive
        return advance(phase="failed", progress_pct=status.progress_pct, message=f"{type(exc).__name__}: {exc}", base=status)


def _try_rollback(status: UpdateStatus, backup_id: str) -> UpdateStatus:
    try:
        restore_backup(backup_id)
        status.message = f"rolled back to {status.from_version} from backup {backup_id}"
    except Exception as exc:  # pragma: no cover
        status.error = f"rollback failed: {exc}"
    write_status(status)
    return status


def rollback(backup_id: str, *, restart: RestartSpec) -> UpdateStatus:
    """Reinstall the wheel-version recorded in the backup and restore the DB."""
    from .backup import list_backups
    target = next((b for b in list_backups() if b.id == backup_id), None)
    if target is None:
        raise FileNotFoundError(f"backup {backup_id} not found")

    status = UpdateStatus(
        target_version=target.old_version,
        from_version=target.new_version,
    )
    write_status(status)
    update_log = Path(restart.log_path).parent / "update.log"

    status = advance(phase="starting", progress_pct=10, message=f"rolling back to {target.old_version}", base=status)
    status = advance(phase="installing", progress_pct=40, message=f"pip install auto-daily-log=={target.old_version}", base=status)
    rc = run_pip_install(f"auto-daily-log=={target.old_version}", log_path=update_log)
    if rc != 0:
        return advance(phase="failed", progress_pct=40, message=f"pip exited with code {rc}", base=status)

    status = advance(phase="backing_up", progress_pct=70, message=f"restoring database from backup {backup_id}", base=status)
    restore_backup(backup_id)

    status = advance(phase="restarting", progress_pct=90, message="launching restored server", base=status)
    spawn_detached(restart.argv, Path(restart.log_path), cwd=Path(restart.cwd))
    if wait_for_health(restart.health_url, timeout=restart.wait_seconds):
        return advance(phase="completed", progress_pct=100, message=f"rolled back to {target.old_version}", base=status)
    return advance(phase="failed", progress_pct=90, message="server did not answer health check", base=status)
