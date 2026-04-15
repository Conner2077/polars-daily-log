import json
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from auto_daily_log.web.app import create_app
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def client_and_db(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, db
    await db.close()


# ─── /api/dashboard/extended ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard_extended_empty_db(client_and_db):
    client, _db = client_and_db
    response = await client.get("/api/dashboard/extended?date=2026-04-15")
    assert response.status_code == 200
    data = response.json()
    assert data == {
        "date": "2026-04-15",
        "work_hours": 0.0,
        "activity_count": 0,
        "activity_count_with_summary": 0,
        "pending_drafts_count": 0,
        "submitted_jira_count": 0,
        "submitted_jira_hours": 0.0,
        "latest_submit_time": None,
        "work_hours_delta": 0.0,
    }


@pytest.mark.asyncio
async def test_dashboard_extended_populated(client_and_db):
    client, db = client_and_db
    target = "2026-04-15"
    prev = "2026-04-14"

    # Activities on target: 3 active (60s, 120s, 180s) + 1 idle (600s)
    # work_hours = (60+120+180)/3600 = 360/3600 = 0.1
    # activity_count = 4 total non-deleted
    # activity_count_with_summary = 2 (one has '', one has '(failed)', two have real text)
    async def insert_activity(ts, app, category, duration, llm_summary=None):
        await db.execute(
            "INSERT INTO activities (timestamp, app_name, window_title, category, "
            "duration_sec, machine_id, llm_summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, app, "t", category, duration, "local", llm_summary),
        )

    await insert_activity(f"{target}T09:00:00", "VS Code", "coding", 60, "Edited file A")
    await insert_activity(f"{target}T09:05:00", "Chrome", "browsing", 120, "Read docs")
    await insert_activity(f"{target}T09:10:00", "Slack", "chat", 180, "")
    await insert_activity(f"{target}T22:00:00", None, "idle", 600, "(failed)")

    # Previous day: 2 active (1800s + 1800s = 3600s = 1.0h)
    # work_hours_delta = 0.1 - 1.0 = -0.9
    await insert_activity(f"{prev}T10:00:00", "VS Code", "coding", 1800)
    await insert_activity(f"{prev}T11:00:00", "VS Code", "coding", 1800)

    # Drafts:
    #  - 2 pending_review on target
    #  - 2 submitted on target: 3600s + 7200s = 10800s = 3.0h
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status) "
        "VALUES (?, ?, ?, ?, 'pending_review')",
        (target, "POLARS-1", 3600, "Work A"),
    )
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status) "
        "VALUES (?, ?, ?, ?, 'pending_review')",
        (target, "POLARS-2", 1800, "Work B"),
    )
    # Submitted: set updated_at explicitly so latest_submit_time is deterministic
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, updated_at) "
        "VALUES (?, ?, ?, ?, 'submitted', ?)",
        (target, "POLARS-3", 3600, "Sub A", f"{target}T14:30:00"),
    )
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, updated_at) "
        "VALUES (?, ?, ?, ?, 'submitted', ?)",
        (target, "POLARS-4", 7200, "Sub B", f"{target}T15:42:00"),
    )

    response = await client.get(f"/api/dashboard/extended?date={target}")
    assert response.status_code == 200
    data = response.json()
    assert data["date"] == "2026-04-15"
    assert data["work_hours"] == 0.1
    assert data["activity_count"] == 4
    assert data["activity_count_with_summary"] == 2
    assert data["pending_drafts_count"] == 2
    assert data["submitted_jira_count"] == 2
    assert data["submitted_jira_hours"] == 3.0
    assert data["latest_submit_time"] == "15:42"
    assert data["work_hours_delta"] == -0.9


# ─── /api/worklogs/drafts/preview ────────────────────────────────────


@pytest.mark.asyncio
async def test_drafts_preview_empty(client_and_db):
    client, _db = client_and_db
    response = await client.get("/api/worklogs/drafts/preview?limit=3")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_drafts_preview_flattens_json_entries(client_and_db):
    client, db = client_and_db

    # Draft 1: JSON array with 2 issues
    issues_json = json.dumps([
        {"issue_key": "POLARS-1847", "time_spent_hours": 2.3, "summary": "Refactor parser"},
        {"issue_key": "POLARS-1848", "time_spent_hours": 1.5, "summary": "Fix bug\nextra details"},
    ], ensure_ascii=False)
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending_review', ?)",
        ("2026-04-15", "DAILY", 13680, issues_json, "2026-04-15T10:00:00"),
    )
    # Draft 2: legacy non-JSON, older date
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending_review', ?)",
        ("2026-04-14", "POLARS-999", 3600, "Plain summary text", "2026-04-14T10:00:00"),
    )
    # Draft 3: submitted (should be excluded by default status filter)
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, created_at) "
        "VALUES (?, ?, ?, ?, 'submitted', ?)",
        ("2026-04-15", "POLARS-500", 1800, "Already done", "2026-04-15T11:00:00"),
    )

    response = await client.get("/api/worklogs/drafts/preview?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data == [
        {
            "issue_key": "POLARS-1847",
            "title": "Refactor parser",
            "hours": 2.3,
            "time_range": None,
            "date": "2026-04-15",
        },
        {
            "issue_key": "POLARS-1848",
            "title": "Fix bug",
            "hours": 1.5,
            "time_range": None,
            "date": "2026-04-15",
        },
        {
            "issue_key": "POLARS-999",
            "title": "Plain summary text",
            "hours": 1.0,
            "time_range": None,
            "date": "2026-04-14",
        },
    ]


@pytest.mark.asyncio
async def test_drafts_preview_respects_limit(client_and_db):
    client, db = client_and_db
    issues_json = json.dumps([
        {"issue_key": "P-1", "time_spent_hours": 1.0, "summary": "s1"},
        {"issue_key": "P-2", "time_spent_hours": 1.0, "summary": "s2"},
        {"issue_key": "P-3", "time_spent_hours": 1.0, "summary": "s3"},
    ])
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status) "
        "VALUES (?, ?, ?, ?, 'pending_review')",
        ("2026-04-15", "DAILY", 10800, issues_json),
    )
    response = await client.get("/api/worklogs/drafts/preview?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["issue_key"] == "P-1"
    assert data[1]["issue_key"] == "P-2"


# ─── /api/activities/recent ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_activities_recent_empty(client_and_db):
    client, _db = client_and_db
    response = await client.get("/api/activities/recent?limit=5")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_activities_recent_returns_recent_non_idle(client_and_db):
    client, db = client_and_db

    # Register a collector so machine_name resolves via JOIN
    await db.execute(
        "INSERT INTO collectors (machine_id, name, token_hash) VALUES (?, ?, ?)",
        ("laptop-01", "MacBook Pro", "hash"),
    )

    # Older activity (non-idle)
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, "
        "duration_sec, machine_id, llm_summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-15T17:00:00", "Terminal", "zsh", "coding", 60, "laptop-01", "Ran tests"),
    )
    # Idle activity — should be excluded
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, "
        "duration_sec, machine_id, llm_summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-15T17:15:00", None, None, "idle", 600, "laptop-01", None),
    )
    # Newer activity (non-idle)
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, "
        "duration_sec, machine_id, llm_summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-15T17:32:00", "VS Code", "index.astro", "coding", 60, "laptop-01", "Edited page"),
    )
    # Soft-deleted — should be excluded
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, "
        "duration_sec, machine_id, llm_summary, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-15T18:00:00", "Deleted", "x", "browsing", 30, "laptop-01", "gone",
         "2026-04-15T19:00:00"),
    )

    response = await client.get("/api/activities/recent?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data == [
        {
            "timestamp": "17:32",
            "app_name": "VS Code",
            "window_title": "index.astro",
            "llm_summary": "Edited page",
            "machine_name": "MacBook Pro",
        },
        {
            "timestamp": "17:00",
            "app_name": "Terminal",
            "window_title": "zsh",
            "llm_summary": "Ran tests",
            "machine_name": "MacBook Pro",
        },
    ]


# ─── /api/machines/status ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_machines_status_empty(client_and_db):
    client, _db = client_and_db
    response = await client.get("/api/machines/status")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_machines_status_online_and_offline(client_and_db):
    client, db = client_and_db

    now = datetime.now().replace(microsecond=0)
    recent = now - timedelta(minutes=1)      # online (<5min)
    stale = now - timedelta(hours=2, minutes=6)  # offline → 2.1h

    # Online primary machine
    await db.execute(
        "INSERT INTO collectors (machine_id, name, token_hash, last_seen) "
        "VALUES (?, ?, ?, ?)",
        ("local", "MacBook Pro", "hash1", recent.isoformat(timespec="seconds")),
    )
    # Offline secondary machine
    await db.execute(
        "INSERT INTO collectors (machine_id, name, token_hash, last_seen) "
        "VALUES (?, ?, ?, ?)",
        ("linux-box", "Linux 小机", "hash2", stale.isoformat(timespec="seconds")),
    )
    # Inactive — should be excluded
    await db.execute(
        "INSERT INTO collectors (machine_id, name, token_hash, last_seen, is_active) "
        "VALUES (?, ?, ?, ?, 0)",
        ("old-box", "Old Box", "hash3", stale.isoformat(timespec="seconds")),
    )

    response = await client.get("/api/machines/status")
    assert response.status_code == 200
    data = response.json()
    # Order is by last_seen DESC → online first
    assert len(data) == 2
    assert data[0] == {
        "name": "MacBook Pro",
        "online": True,
        "last_seen_hours_ago": None,
        "is_primary": True,
    }
    assert data[1]["name"] == "Linux 小机"
    assert data[1]["online"] is False
    assert data[1]["last_seen_hours_ago"] == 2.1
    assert data[1]["is_primary"] is False
