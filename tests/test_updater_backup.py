"""Unit tests for backup snapshot, list, prune, and restore."""
from __future__ import annotations

import json
import sqlite3
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_daily_log.updater import backup as backup_mod
from auto_daily_log.updater.paths import backups_dir, data_dir


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"system:\n  data_dir: {tmp_path}/data\n")
    monkeypatch.setenv("PDL_SERVER_CONFIG", str(cfg))
    yield tmp_path


def _seed_db(path: Path, *, rows: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(rows):
            conn.execute("INSERT INTO t VALUES(?, ?)", (i, f"row-{i}"))


def test_create_backup_snapshots_db_and_writes_manifest(isolated_data_dir):
    db = data_dir() / "data.db"
    _seed_db(db, rows=5)
    cfg_a = isolated_data_dir / "config.yaml"
    cfg_a.write_text("server: {port: 9000}\n")

    manifest = backup_mod.create_backup(
        old_version="0.4.0",
        new_version="0.5.0",
        config_paths=[cfg_a],
    )
    snap = backups_dir() / manifest.id / "data.db"
    with sqlite3.connect(str(snap)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert count == 5
    assert manifest.old_version == "0.4.0"
    assert manifest.new_version == "0.5.0"
    assert len(manifest.db_sha256) == 64

    with tarfile.open(backups_dir() / manifest.id / "config.tar.gz") as tar:
        assert tar.getnames() == ["config.yaml"]


def test_create_backup_handles_missing_db(isolated_data_dir):
    """First-install case: no DB exists yet — backup must still succeed."""
    manifest = backup_mod.create_backup(
        old_version="0.4.0",
        new_version="0.5.0",
        config_paths=[],
        is_first_install=True,
    )
    assert manifest.is_first_install is True
    assert manifest.db_size_bytes == 0
    assert (backups_dir() / manifest.id / backup_mod.FIRST_INSTALL_TAG).exists()


def test_list_backups_returns_newest_first(isolated_data_dir):
    base = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
    for offset in (0, 1, 2):
        backup_mod.create_backup(
            old_version="0.4.0", new_version="0.5.0",
            config_paths=[], now=base + timedelta(seconds=offset),
        )
    listed = backup_mod.list_backups()
    assert [b.id for b in listed] == [
        "20260416-120002", "20260416-120001", "20260416-120000",
    ]


def test_prune_keeps_recent_and_first_install(isolated_data_dir):
    base = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
    backup_mod.create_backup(
        old_version="0.0.0", new_version="0.4.0",
        config_paths=[], is_first_install=True, now=base,
    )
    for offset in (1, 2, 3, 4, 5):
        backup_mod.create_backup(
            old_version="0.4.0", new_version="0.5.0",
            config_paths=[], now=base + timedelta(seconds=offset),
        )
    removed = backup_mod.prune_backups(keep_recent=2)
    surviving = {b.id for b in backup_mod.list_backups()}
    assert "20260416-120000" in surviving       # first-install always kept
    assert "20260416-120005" in surviving       # newest
    assert "20260416-120004" in surviving       # second newest
    assert "20260416-120001" in removed
    assert "20260416-120002" in removed


def test_restore_overwrites_live_db(isolated_data_dir):
    db = data_dir() / "data.db"
    _seed_db(db, rows=2)
    manifest = backup_mod.create_backup(
        old_version="0.4.0", new_version="0.5.0", config_paths=[],
    )
    # Mutate live DB after backup
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT INTO t VALUES(99, 'after-backup')")
    backup_mod.restore_backup(manifest.id)
    with sqlite3.connect(str(db)) as conn:
        rows = [r[0] for r in conn.execute("SELECT id FROM t").fetchall()]
    assert rows == [0, 1]


def test_restore_rejects_unknown_id(isolated_data_dir):
    with pytest.raises(FileNotFoundError):
        backup_mod.restore_backup("does-not-exist")
