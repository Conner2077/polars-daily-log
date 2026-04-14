"""Credentials storage — machine_id + token persisted locally.

Kept out of yaml config so the yaml can be checked in without leaking
secrets. Written on first registration, read on every startup.
"""
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class StoredCredentials(BaseModel):
    machine_id: str
    token: str


def load_credentials(path: Path) -> Optional[StoredCredentials]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StoredCredentials(**data)
    except (json.JSONDecodeError, ValueError):
        return None


def save_credentials(path: Path, machine_id: str, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"machine_id": machine_id, "token": token}, indent=2),
        encoding="utf-8",
    )
    # Restrict permissions on unix-like systems
    try:
        path.chmod(0o600)
    except OSError:
        pass


def clear_credentials(path: Path) -> None:
    if path.exists():
        path.unlink()
