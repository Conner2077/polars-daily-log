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


def _floor_to_bucket(dt: datetime, minutes: int) -> datetime:
    total_min = dt.hour * 60 + dt.minute
    floored_min = (total_min // minutes) * minutes
    return dt.replace(hour=floored_min // 60, minute=floored_min % 60, second=0, microsecond=0)


@pytest.mark.asyncio
async def test_timeline_empty_db(client_and_db):
    client, _db = client_and_db
    response = await client.get("/api/activities/timeline?hours=12&bucket=15m")
    assert response.status_code == 200
    data = response.json()
    assert data["bucket_minutes"] == 15
    assert len(data["buckets"]) == 48
    # Every bucket is empty.
    for b in data["buckets"]:
        assert b["active_mins"] == 0
        assert b["idle_mins"] == 0
        assert b["top_app"] is None


@pytest.mark.asyncio
async def test_timeline_normal_12h_15m(client_and_db):
    client, db = client_and_db

    # Pin test to a known bucket: insert activities whose timestamps fall
    # deterministically inside a single 15m bucket, 2h ago.
    now = datetime.now().replace(microsecond=0)
    # Pick a target time cleanly inside a bucket: 2h ago, floored to 15m,
    # then shifted by +1 minute so ts lands inside that bucket.
    target_bucket_start = _floor_to_bucket(now - timedelta(hours=2), 15)
    base = target_bucket_start + timedelta(minutes=1)

    # 4 VS Code rows (non-idle, 60s each = 4 mins), 2 Safari rows (30s each = 1 min),
    # 2 idle rows (30s each = 1 min) — all in same bucket.
    # Expected: active_mins = 4 + 1 = 5.0, idle_mins = 1.0, top_app = "VS Code".
    rows = []
    for i in range(4):
        rows.append((
            (base + timedelta(seconds=i * 10)).isoformat(timespec="seconds"),
            "VS Code", None, "coding", None, None, None, 60, "local",
        ))
    for i in range(2):
        rows.append((
            (base + timedelta(seconds=60 + i * 10)).isoformat(timespec="seconds"),
            "Safari", None, "browsing", None, None, None, 30, "local",
        ))
    for i in range(2):
        rows.append((
            (base + timedelta(seconds=120 + i * 10)).isoformat(timespec="seconds"),
            None, None, "idle", None, None, None, 30, "local",
        ))
    # Add two activities in a *different* bucket so the test verifies
    # bucketing isolates correctly: 5h ago, Safari only.
    far_bucket = _floor_to_bucket(now - timedelta(hours=5), 15) + timedelta(minutes=2)
    rows.append((
        far_bucket.isoformat(timespec="seconds"),
        "Safari", None, "browsing", None, None, None, 60, "local",
    ))
    rows.append((
        (far_bucket + timedelta(seconds=30)).isoformat(timespec="seconds"),
        "Safari", None, "browsing", None, None, None, 60, "local",
    ))

    for r in rows:
        await db.execute(
            "INSERT INTO activities (timestamp, app_name, window_title, category, "
            "confidence, url, signals, duration_sec, machine_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            r,
        )

    response = await client.get("/api/activities/timeline?hours=12&bucket=15m")
    assert response.status_code == 200
    data = response.json()
    assert data["bucket_minutes"] == 15
    assert len(data["buckets"]) == 48

    # Find the bucket matching target_bucket_start.
    target_iso = target_bucket_start.isoformat(timespec="seconds")
    matching = [b for b in data["buckets"] if b["bucket_start"] == target_iso]
    assert len(matching) == 1
    bucket = matching[0]
    assert bucket["active_mins"] == 5.0
    assert bucket["idle_mins"] == 1.0
    assert bucket["top_app"] == "VS Code"

    # Verify the far bucket (Safari only).
    far_start = _floor_to_bucket(now - timedelta(hours=5), 15)
    far_iso = far_start.isoformat(timespec="seconds")
    far_matching = [b for b in data["buckets"] if b["bucket_start"] == far_iso]
    assert len(far_matching) == 1
    assert far_matching[0]["active_mins"] == 2.0
    assert far_matching[0]["idle_mins"] == 0
    assert far_matching[0]["top_app"] == "Safari"


@pytest.mark.asyncio
async def test_timeline_param_validation(client_and_db):
    client, _db = client_and_db

    # hours=0 -> violates ge=1
    response = await client.get("/api/activities/timeline?hours=0&bucket=15m")
    assert response.status_code == 422

    # hours=100 -> violates le=72
    response = await client.get("/api/activities/timeline?hours=100&bucket=15m")
    assert response.status_code == 422

    # bucket=invalid -> violates pattern
    response = await client.get("/api/activities/timeline?hours=12&bucket=7m")
    assert response.status_code == 422
