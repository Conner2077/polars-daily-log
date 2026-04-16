"""Pre-upgrade snapshots: copy the DB atomically and tar config files.

A backup is a directory under ``<data_dir>/backups/<id>/`` containing:
  data.db        — full snapshot via SQLite ``VACUUM INTO`` (atomic)
  config.tar.gz  — config.yaml + collector.yaml if they live next to pdl
  manifest.json  — old/new version, timestamps, sha256s, file list

Backups are listed/pruned in reverse chronological order, never silently
overwriting the **first-install** snapshot (the one that lets a user
recover from any future disaster).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import backups_dir, data_dir

KEEP_RECENT = 3                      # plus the first-install snapshot, always
FIRST_INSTALL_TAG = "first-install"  # marker filename in the backup dir


@dataclass
class BackupManifest:
    id: str
    old_version: str
    new_version: str
    created_at: str
    db_path: str
    db_sha256: str
    db_size_bytes: int
    config_archive: str
    is_first_install: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _vacuum_into(src: Path, dst: Path) -> None:
    """Atomic SQLite snapshot. Safe even with the server's connection open."""
    if not src.exists():
        # Fresh install with no DB yet — write an empty placeholder so
        # rollback's restore step still has something to copy back.
        dst.write_bytes(b"")
        return
    with sqlite3.connect(str(src)) as conn:
        conn.execute("VACUUM INTO ?", (str(dst),))


def _tar_config(dst: Path, config_paths: list[Path]) -> None:
    """Bundle config files into a single tar.gz; missing files are skipped."""
    with tarfile.open(dst, "w:gz") as tar:
        for p in config_paths:
            if p.exists() and p.is_file():
                tar.add(p, arcname=p.name)


def _new_id(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


def create_backup(
    *,
    old_version: str,
    new_version: str,
    db_path: Optional[Path] = None,
    config_paths: Optional[list[Path]] = None,
    is_first_install: bool = False,
    now: Optional[datetime] = None,
) -> BackupManifest:
    """Snapshot the DB + config files, write a manifest, and return it."""
    now = now or datetime.now(timezone.utc)
    backup_id = _new_id(now)
    backup_dir = backups_dir() / backup_id
    backup_dir.mkdir(parents=True, exist_ok=False)

    src_db = db_path or (data_dir() / "data.db")
    dst_db = backup_dir / "data.db"
    _vacuum_into(src_db, dst_db)

    archive = backup_dir / "config.tar.gz"
    _tar_config(archive, config_paths or [])

    manifest = BackupManifest(
        id=backup_id,
        old_version=old_version,
        new_version=new_version,
        created_at=now.isoformat(),
        db_path=str(src_db),
        db_sha256=_sha256(dst_db) if dst_db.stat().st_size > 0 else "",
        db_size_bytes=dst_db.stat().st_size,
        config_archive=str(archive.relative_to(backup_dir)),
        is_first_install=is_first_install,
    )
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if is_first_install:
        (backup_dir / FIRST_INSTALL_TAG).touch()
    return manifest


def list_backups() -> list[BackupManifest]:
    """Return all backups, newest first."""
    out: list[BackupManifest] = []
    for d in sorted(backups_dir().iterdir(), reverse=True):
        manifest = d / "manifest.json"
        if not manifest.exists():
            continue
        try:
            out.append(BackupManifest(**json.loads(manifest.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def prune_backups(keep_recent: int = KEEP_RECENT) -> list[str]:
    """Delete old backups except the last ``keep_recent`` and any
    first-install marker. Returns the IDs that were removed."""
    all_backups = list_backups()
    survivors: set[str] = {b.id for b in all_backups[:keep_recent]}
    survivors |= {b.id for b in all_backups if b.is_first_install}
    removed: list[str] = []
    for d in backups_dir().iterdir():
        if not d.is_dir() or d.name in survivors:
            continue
        for child in d.iterdir():
            child.unlink()
        d.rmdir()
        removed.append(d.name)
    return removed


def restore_backup(backup_id: str, *, db_path: Optional[Path] = None) -> Path:
    """Copy the snapshotted DB back over the live one. Returns the path
    that was overwritten so the caller can log it."""
    src_db = backups_dir() / backup_id / "data.db"
    if not src_db.exists():
        raise FileNotFoundError(f"Backup {backup_id} has no data.db")
    dst_db = db_path or (data_dir() / "data.db")
    dst_db.write_bytes(src_db.read_bytes())
    return dst_db
