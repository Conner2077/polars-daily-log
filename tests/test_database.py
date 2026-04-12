import pytest
import pytest_asyncio
import aiosqlite
from pathlib import Path
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_initialize_creates_all_tables(db):
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = [t["name"] for t in tables]
    assert "activities" in table_names
    assert "git_repos" in table_names
    assert "git_commits" in table_names
    assert "jira_issues" in table_names
    assert "worklog_drafts" in table_names
    assert "audit_logs" in table_names
    assert "settings" in table_names


@pytest.mark.asyncio
async def test_insert_and_fetch_activity(db):
    await db.execute(
        """INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("2026-04-12T10:00:00+08:00", "IntelliJ IDEA", "Main.java", "coding", 0.92, 30),
    )
    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 1
    assert rows[0]["app_name"] == "IntelliJ IDEA"
    assert rows[0]["category"] == "coding"
