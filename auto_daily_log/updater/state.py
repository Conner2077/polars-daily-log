"""Atomic, file-based progress reporting between the updater and the UI.

The updater owns the file. The Web UI polls it via GET /api/updates/status.
Writes are atomic (tmp + rename) so the UI never reads a half-written JSON.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from .paths import update_status_path

PHASES = (
    "idle",
    "starting",
    "stopping_server",
    "backing_up",
    "downloading",
    "installing",
    "migrating",
    "restarting",
    "completed",
    "failed",
)

# Terminal phases — state is frozen here; the updater will never write
# again until a new upgrade starts.
_TERMINAL_PHASES = frozenset({"completed", "failed"})

# How long a terminal status lingers before read_status() treats it as
# stale and returns a fresh idle. 1 hour: long enough for the user to
# see the toast/banner after reloading post-upgrade; short enough that
# a failed run yesterday doesn't make every subsequent page load claim
# "升级失败".
_TERMINAL_TTL_SEC = 3600


@dataclass
class UpdateStatus:
    phase: str = "idle"
    target_version: str = ""
    from_version: str = ""
    backup_id: str = ""
    progress_pct: int = 0
    message: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    error: str = ""
    log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def read_status() -> UpdateStatus:
    path = update_status_path()
    if not path.exists():
        return UpdateStatus()
    try:
        status = UpdateStatus(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError):
        return UpdateStatus(phase="failed", error="status file corrupted")
    # Stale-terminal cleanup: without this, a persisted failed/completed
    # record makes the UI show "升级失败" or "升级完成" forever on every
    # subsequent page load. Drop the file and return fresh idle once TTL
    # elapses. In-progress states are kept untouched — a stuck upgrade
    # is its own signal the user should see.
    if status.phase in _TERMINAL_PHASES and status.updated_at:
        if time.time() - status.updated_at > _TERMINAL_TTL_SEC:
            try:
                path.unlink()
            except OSError:
                pass
            return UpdateStatus()
    return status


def write_status(status: UpdateStatus) -> None:
    status.updated_at = time.time()
    path = update_status_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(status.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def advance(
    *,
    phase: str,
    progress_pct: int,
    message: str,
    base: Optional[UpdateStatus] = None,
) -> UpdateStatus:
    """Convenience: load current state, advance one step, persist, return it."""
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase}")
    status = base or read_status()
    status.phase = phase
    status.progress_pct = progress_pct
    status.message = message
    status.log.append(f"[{phase}] {message}")
    if phase == "starting" and not status.started_at:
        status.started_at = time.time()
    if phase == "failed":
        status.error = message
    write_status(status)
    return status
