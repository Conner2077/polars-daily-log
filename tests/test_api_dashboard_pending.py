"""Tests for GET /api/dashboard `pending_review_count` — counts unpublished
summaries (new pipeline), ignores legacy worklog_drafts orphans.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from auto_daily_log.web.app import create_app
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def client_and_db(tmp_path):
    db = Database(tmp_path / "dash.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, db
    await db.close()


async def _seed_scope_and_output(db, publisher="jira"):
    await db.execute(
        "INSERT OR IGNORE INTO time_scopes (name, display_name, scope_type) VALUES (?, ?, ?)",
        ("test_daily", "Test Daily", "day"),
    )
    await db.execute(
        "INSERT INTO scope_outputs (scope_name, display_name, output_mode, publisher_name) "
        "VALUES (?, ?, ?, ?)",
        ("test_daily", "Daily output", "per_issue", publisher),
    )
    row = await db.fetch_one(
        "SELECT id FROM scope_outputs WHERE scope_name='test_daily' ORDER BY id DESC LIMIT 1"
    )
    return row["id"]


async def _insert_summary(db, *, output_id, date, issue_key, published_id=None):
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, issue_key, content, published_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("test_daily", output_id, date, issue_key, "content", published_id),
    )


@pytest.mark.asyncio
async def test_pending_ignores_orphan_empty_summary_draft(client_and_db):
    """Legacy draft with summary='[]' (LLM-failure residue) must not count."""
    client, db = client_and_db
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, status, summary) "
        "VALUES (?, ?, ?, ?)",
        ("2026-04-20", "DAILY", "pending_review", "[]"),
    )
    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.status_code == 200
    assert r.json()["pending_review_count"] == 0


@pytest.mark.asyncio
async def test_pending_counts_legacy_worklog_draft_with_real_summary(client_and_db):
    """Legacy pending_review drafts with real content still count."""
    client, db = client_and_db
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, status, summary) "
        "VALUES (?, ?, ?, ?)",
        ("2026-04-20", "PROJ-1", "pending_review", '[{"issue_key":"PROJ-1"}]'),
    )
    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["pending_review_count"] == 1


@pytest.mark.asyncio
async def test_pending_sums_legacy_and_new(client_and_db):
    """Dashboard badge covers both pipelines in parallel."""
    client, db = client_and_db
    output_id = await _seed_scope_and_output(db)
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, status, summary) "
        "VALUES (?, ?, ?, ?)",
        ("2026-04-20", "LEGACY-1", "pending_review", '[{"issue_key":"LEGACY-1"}]'),
    )
    await _insert_summary(db, output_id=output_id, date="2026-04-20", issue_key="NEW-1")
    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["pending_review_count"] == 2


@pytest.mark.asyncio
async def test_pending_counts_unpublished_summary_with_issue(client_and_db):
    client, db = client_and_db
    output_id = await _seed_scope_and_output(db)
    await _insert_summary(db, output_id=output_id, date="2026-04-20", issue_key="PROJ-123")

    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["pending_review_count"] == 1


@pytest.mark.asyncio
async def test_pending_excludes_published_summary(client_and_db):
    client, db = client_and_db
    output_id = await _seed_scope_and_output(db)
    await _insert_summary(
        db, output_id=output_id, date="2026-04-20", issue_key="PROJ-1", published_id="WL-1"
    )
    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["pending_review_count"] == 0


@pytest.mark.asyncio
async def test_pending_excludes_empty_issue_key(client_and_db):
    """Aggregate summaries with issue_key='' have no target to push to."""
    client, db = client_and_db
    output_id = await _seed_scope_and_output(db)
    await _insert_summary(db, output_id=output_id, date="2026-04-20", issue_key="")
    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["pending_review_count"] == 0


@pytest.mark.asyncio
async def test_pending_excludes_sentinel_issue_keys(client_and_db):
    """'ALL' and 'DAILY' are aggregate/digest sentinels, not pushable items."""
    client, db = client_and_db
    output_id = await _seed_scope_and_output(db)
    await _insert_summary(db, output_id=output_id, date="2026-04-20", issue_key="DAILY")
    await _insert_summary(db, output_id=output_id, date="2026-04-20", issue_key="ALL")
    await _insert_summary(db, output_id=output_id, date="2026-04-20", issue_key="PROJ-7")

    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["pending_review_count"] == 1


@pytest.mark.asyncio
async def test_pending_excludes_outputs_without_publisher(client_and_db):
    """Without a publisher there's nowhere to push; UI can't act on it."""
    client, db = client_and_db
    output_id = await _seed_scope_and_output(db, publisher="")
    await _insert_summary(db, output_id=output_id, date="2026-04-20", issue_key="PROJ-9")

    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["pending_review_count"] == 0


@pytest.mark.asyncio
async def test_pending_is_date_scoped(client_and_db):
    client, db = client_and_db
    output_id = await _seed_scope_and_output(db)
    await _insert_summary(db, output_id=output_id, date="2026-04-19", issue_key="PROJ-1")
    await _insert_summary(db, output_id=output_id, date="2026-04-20", issue_key="PROJ-2")

    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["pending_review_count"] == 1


@pytest.mark.asyncio
async def test_submitted_hours_sums_published_summaries(client_and_db):
    client, db = client_and_db
    output_id = await _seed_scope_and_output(db)
    # 1h + 30min published, 1h unpublished should NOT be counted
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, issue_key, content, time_spent_sec, published_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test_daily", output_id, "2026-04-20", "P-1", "c", 3600, "WL-1"),
    )
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, issue_key, content, time_spent_sec, published_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test_daily", output_id, "2026-04-20", "P-2", "c", 1800, "WL-2"),
    )
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, issue_key, content, time_spent_sec, published_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test_daily", output_id, "2026-04-20", "P-3", "c", 3600, None),
    )
    r = await client.get("/api/dashboard?target_date=2026-04-20")
    assert r.json()["submitted_hours"] == 1.5
