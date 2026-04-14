"""Phase A tests — DB schema: machine_id + collectors table."""
import asyncio
import sqlite3
from pathlib import Path

import pytest

from auto_daily_log.models.database import Database


@pytest.mark.asyncio
async def test_machine_id_column_added_to_activities(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=128)
    await db.initialize()
    cols = await db.fetch_all("PRAGMA table_info(activities)")
    names = [c["name"] for c in cols]
    assert "machine_id" in names, f"machine_id missing; got columns: {names}"
    await db.close()


@pytest.mark.asyncio
async def test_existing_rows_default_to_local_machine_id(tmp_path):
    """Migration must set existing rows' machine_id to 'local', not NULL."""
    db_path = tmp_path / "legacy.db"
    # Simulate pre-migration schema — no machine_id column
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            app_name TEXT,
            window_title TEXT,
            category TEXT,
            confidence REAL,
            url TEXT,
            signals TEXT,
            duration_sec INTEGER
        );
        INSERT INTO activities (timestamp, app_name, category, duration_sec)
        VALUES ('2026-04-14T10:00:00', 'Safari', 'browsing', 30);
        INSERT INTO activities (timestamp, app_name, category, duration_sec)
        VALUES ('2026-04-14T10:01:00', 'iTerm', 'coding', 60);
    """)
    conn.commit()
    conn.close()

    db = Database(db_path, embedding_dimensions=128)
    await db.initialize()

    # Assert each pre-existing row has machine_id='local'
    rows = await db.fetch_all("SELECT id, app_name, machine_id FROM activities ORDER BY id")
    assert len(rows) == 2, f"expected 2 legacy rows, got {len(rows)}"
    assert rows[0]["app_name"] == "Safari", f"row 0 app: {rows[0]['app_name']}"
    assert rows[0]["machine_id"] == "local", f"row 0 machine_id: {rows[0]['machine_id']!r}"
    assert rows[1]["app_name"] == "iTerm", f"row 1 app: {rows[1]['app_name']}"
    assert rows[1]["machine_id"] == "local", f"row 1 machine_id: {rows[1]['machine_id']!r}"

    await db.close()


@pytest.mark.asyncio
async def test_collectors_table_created_with_correct_columns(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=128)
    await db.initialize()
    cols = await db.fetch_all("PRAGMA table_info(collectors)")
    names = {c["name"] for c in cols}
    expected = {
        "id", "machine_id", "name", "hostname", "platform",
        "platform_detail", "capabilities", "token_hash",
        "created_at", "last_seen", "is_active",
    }
    missing = expected - names
    assert not missing, f"collectors table missing columns: {missing}"
    await db.close()


@pytest.mark.asyncio
async def test_collectors_machine_id_unique_constraint(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=128)
    await db.initialize()
    await db.execute(
        "INSERT INTO collectors (machine_id, name, token_hash) VALUES (?, ?, ?)",
        ("m-1", "Mac", "hash1"),
    )
    with pytest.raises(Exception) as exc_info:
        await db.execute(
            "INSERT INTO collectors (machine_id, name, token_hash) VALUES (?, ?, ?)",
            ("m-1", "Mac-dup", "hash2"),
        )
    assert "UNIQUE" in str(exc_info.value).upper(), f"expected UNIQUE violation, got: {exc_info.value}"
    await db.close()


@pytest.mark.asyncio
async def test_inserting_activity_with_machine_id_roundtrip(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=128)
    await db.initialize()

    # Insert one activity per machine
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, category, duration_sec, machine_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-14T10:00:00", "Xcode", "coding", 120, "mac-conner"),
    )
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, category, duration_sec, machine_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-14T10:00:30", "Chrome", "browsing", 60, "win-office"),
    )

    mac_rows = await db.fetch_all(
        "SELECT * FROM activities WHERE machine_id = ? ORDER BY id", ("mac-conner",)
    )
    win_rows = await db.fetch_all(
        "SELECT * FROM activities WHERE machine_id = ? ORDER BY id", ("win-office",)
    )

    assert len(mac_rows) == 1
    assert mac_rows[0]["app_name"] == "Xcode"
    assert mac_rows[0]["duration_sec"] == 120
    assert mac_rows[0]["machine_id"] == "mac-conner"

    assert len(win_rows) == 1
    assert win_rows[0]["app_name"] == "Chrome"
    assert win_rows[0]["duration_sec"] == 60
    assert win_rows[0]["machine_id"] == "win-office"

    await db.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    """Running migrations twice must not fail or double-add columns."""
    db_path = tmp_path / "test.db"
    db1 = Database(db_path, embedding_dimensions=128)
    await db1.initialize()
    await db1.close()

    # Re-initialize same DB — should not error
    db2 = Database(db_path, embedding_dimensions=128)
    await db2.initialize()
    cols = await db2.fetch_all("PRAGMA table_info(activities)")
    machine_id_cols = [c for c in cols if c["name"] == "machine_id"]
    assert len(machine_id_cols) == 1, f"duplicated machine_id column: {machine_id_cols}"
    await db2.close()
