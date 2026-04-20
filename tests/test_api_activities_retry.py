"""Tests for POST /api/activities/retry-failed — manual retry of failed LLM summaries."""
import pytest


async def _insert(client_db, timestamp, summary):
    await client_db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, confidence, "
        "duration_sec, machine_id, llm_summary, llm_summary_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (timestamp, "VSCode", "main.py", "coding", 0.9, 60, "local", summary),
    )


async def _summaries_for(app_client_db, target_date):
    rows = await app_client_db.fetch_all(
        "SELECT timestamp, llm_summary, llm_summary_at FROM activities "
        "WHERE date(timestamp) = ? ORDER BY timestamp",
        (target_date,),
    )
    return rows


@pytest.mark.asyncio
async def test_retry_scoped_to_date_resets_failed_only(app_client, tmp_path):
    """Only (failed) rows on the target date should be reset."""
    from auto_daily_log.models.database import Database
    from auto_daily_log.web.app import create_app
    from httpx import AsyncClient, ASGITransport

    db = Database(tmp_path / "retry.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)

    await _insert(db, "2026-04-18T10:00:00", "(failed)")
    await _insert(db, "2026-04-18T10:05:00", "正常摘要")
    await _insert(db, "2026-04-18T10:10:00", None)
    await _insert(db, "2026-04-19T10:00:00", "(failed)")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/activities/retry-failed", params={"target_date": "2026-04-18"})
        assert r.status_code == 200
        assert r.json() == {"status": "requeued", "count": 1}

    rows_18 = await _summaries_for(db, "2026-04-18")
    assert rows_18[0]["llm_summary"] is None
    assert rows_18[0]["llm_summary_at"] is None
    assert rows_18[1]["llm_summary"] == "正常摘要"
    assert rows_18[2]["llm_summary"] is None

    rows_19 = await _summaries_for(db, "2026-04-19")
    assert rows_19[0]["llm_summary"] == "(failed)"

    await db.close()


@pytest.mark.asyncio
async def test_retry_without_date_resets_all_failed(app_client, tmp_path):
    """Omitting target_date resets every (failed) row regardless of date."""
    from auto_daily_log.models.database import Database
    from auto_daily_log.web.app import create_app
    from httpx import AsyncClient, ASGITransport

    db = Database(tmp_path / "retry_all.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)

    await _insert(db, "2026-04-18T10:00:00", "(failed)")
    await _insert(db, "2026-04-19T10:00:00", "(failed)")
    await _insert(db, "2026-04-20T10:00:00", "ok summary")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/activities/retry-failed")
        assert r.status_code == 200
        assert r.json() == {"status": "requeued", "count": 2}

    rows = await db.fetch_all(
        "SELECT llm_summary FROM activities ORDER BY timestamp"
    )
    assert [r["llm_summary"] for r in rows] == [None, None, "ok summary"]

    await db.close()


@pytest.mark.asyncio
async def test_retry_skips_soft_deleted(app_client, tmp_path):
    """Soft-deleted failed rows are not re-queued."""
    from auto_daily_log.models.database import Database
    from auto_daily_log.web.app import create_app
    from httpx import AsyncClient, ASGITransport

    db = Database(tmp_path / "retry_del.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)

    await _insert(db, "2026-04-18T10:00:00", "(failed)")
    await _insert(db, "2026-04-18T10:05:00", "(failed)")
    await db.execute(
        "UPDATE activities SET deleted_at = datetime('now') WHERE timestamp = ?",
        ("2026-04-18T10:05:00",),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/activities/retry-failed", params={"target_date": "2026-04-18"})
        assert r.json() == {"status": "requeued", "count": 1}

    live = await db.fetch_one(
        "SELECT llm_summary FROM activities WHERE timestamp = ? AND deleted_at IS NULL",
        ("2026-04-18T10:00:00",),
    )
    deleted = await db.fetch_one(
        "SELECT llm_summary FROM activities WHERE timestamp = ? AND deleted_at IS NOT NULL",
        ("2026-04-18T10:05:00",),
    )
    assert live["llm_summary"] is None
    assert deleted["llm_summary"] == "(failed)"

    await db.close()


@pytest.mark.asyncio
async def test_retry_no_matches_returns_zero(app_client, tmp_path):
    """Nothing to retry still returns 200 with count=0."""
    from auto_daily_log.models.database import Database
    from auto_daily_log.web.app import create_app
    from httpx import AsyncClient, ASGITransport

    db = Database(tmp_path / "retry_empty.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)

    await _insert(db, "2026-04-18T10:00:00", "fine")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/activities/retry-failed", params={"target_date": "2026-04-18"})
        assert r.json() == {"status": "requeued", "count": 0}

    await db.close()
