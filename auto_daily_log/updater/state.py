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
        return UpdateStatus(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError):
        return UpdateStatus(phase="failed", error="status file corrupted")


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
