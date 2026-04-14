"""Phase C tests — Ingestion API + auth."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auto_daily_log.models.database import Database
from auto_daily_log.web.app import create_app


REGISTER_PAYLOAD = {
    "name": "Test-Mac",
    "hostname": "testmbp.local",
    "platform": "macos",
    "platform_detail": "macOS 14.2",
    "capabilities": ["screenshot", "ocr", "idle", "window_title"],
}


async def _setup(tmp_path: Path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    app = create_app(db)
    return TestClient(app), db


# ─── Registration ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_returns_machine_id_and_token(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        r = client.post("/api/collectors/register", json=REGISTER_PAYLOAD)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["machine_id"].startswith("m-")
        assert len(data["machine_id"]) == 18  # "m-" + 16 hex chars
        assert len(data["token"]) >= 32
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_register_rejects_unknown_capability(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        r = client.post("/api/collectors/register", json={
            "name": "X", "hostname": "h", "platform": "macos",
            "capabilities": ["bogus_cap", "screenshot"],
        })
        assert r.status_code == 400
        assert "bogus_cap" in r.text
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_register_idempotent_rotates_token(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        d1 = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        d2 = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        assert d1["machine_id"] == d2["machine_id"], "machine_id should be stable"
        assert d1["token"] != d2["token"], "token should rotate on re-register"
    finally:
        await db.close()


# ─── Auth enforcement ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_activities_without_token_returns_401(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        r = client.post("/api/ingest/activities", json={
            "activities": [{"timestamp": "2026-04-14T10:00:00", "duration_sec": 1}]
        })
        assert r.status_code == 401, r.text
        assert "authorization" in r.text.lower()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ingest_activities_with_wrong_token_returns_403(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        r = client.post(
            "/api/ingest/activities",
            json={"activities": [{"timestamp": "2026-04-14T10:00:00", "duration_sec": 1}]},
            headers={
                "Authorization": "Bearer wrong-token-" + "x" * 32,
                "X-Machine-ID": reg["machine_id"],
            },
        )
        assert r.status_code == 403, r.text
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ingest_activities_missing_machine_id_returns_400(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        r = client.post(
            "/api/ingest/activities",
            json={"activities": [{"timestamp": "2026-04-14T10:00:00", "duration_sec": 1}]},
            headers={"Authorization": f"Bearer {reg['token']}"},
        )
        assert r.status_code == 400
        assert "machine" in r.text.lower()
    finally:
        await db.close()


# ─── Happy path ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_activities_batch_persists_with_machine_id(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        machine_id = reg["machine_id"]
        headers = {
            "Authorization": f"Bearer {reg['token']}",
            "X-Machine-ID": machine_id,
        }

        batch = {
            "activities": [
                {
                    "timestamp": "2026-04-14T10:00:00",
                    "app_name": "Xcode",
                    "window_title": "MainView.swift",
                    "category": "coding",
                    "confidence": 0.95,
                    "duration_sec": 30,
                },
                {
                    "timestamp": "2026-04-14T10:00:30",
                    "app_name": "Slack",
                    "category": "communication",
                    "duration_sec": 45,
                },
            ]
        }
        r = client.post("/api/ingest/activities", json=batch, headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["accepted"] == 2
        assert data["rejected"] == 0
        assert data["first_id"] is not None
        assert data["last_id"] == data["first_id"] + 1

        rows = await db.fetch_all(
            "SELECT * FROM activities WHERE machine_id = ? ORDER BY id", (machine_id,)
        )
        assert len(rows) == 2
        assert rows[0]["app_name"] == "Xcode"
        assert rows[0]["window_title"] == "MainView.swift"
        assert rows[0]["category"] == "coding"
        assert abs(rows[0]["confidence"] - 0.95) < 1e-9
        assert rows[0]["duration_sec"] == 30
        assert rows[0]["timestamp"] == "2026-04-14T10:00:00"
        assert rows[1]["app_name"] == "Slack"
        assert rows[1]["duration_sec"] == 45
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ingest_commits_deduplicates_by_hash(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        headers = {
            "Authorization": f"Bearer {reg['token']}",
            "X-Machine-ID": reg["machine_id"],
        }

        r1 = client.post("/api/ingest/commits", json={
            "commits": [
                {"hash": "aaaaaaa", "message": "first", "date": "2026-04-14"},
                {"hash": "bbbbbbb", "message": "second", "date": "2026-04-14"},
            ]
        }, headers=headers)
        assert r1.status_code == 200
        assert r1.json()["accepted"] == 2
        assert r1.json()["duplicates"] == 0

        r2 = client.post("/api/ingest/commits", json={
            "commits": [
                {"hash": "aaaaaaa", "message": "dup", "date": "2026-04-14"},
                {"hash": "ccccccc", "message": "third", "date": "2026-04-14"},
            ]
        }, headers=headers)
        assert r2.status_code == 200
        assert r2.json()["accepted"] == 1
        assert r2.json()["duplicates"] == 1

        rows = await db.fetch_all(
            "SELECT hash, message FROM git_commits WHERE machine_id = ? ORDER BY hash",
            (reg["machine_id"],)
        )
        assert len(rows) == 3
        assert [r["hash"] for r in rows] == ["aaaaaaa", "bbbbbbb", "ccccccc"]
        # Original message preserved (not overwritten by dup)
        assert rows[0]["message"] == "first"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_list_collectors_returns_registered(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        client.post("/api/collectors/register", json=REGISTER_PAYLOAD)
        client.post("/api/collectors/register", json={
            **REGISTER_PAYLOAD, "name": "Second-Mac", "hostname": "mac2.local",
        })

        r = client.get("/api/collectors")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        names = {c["name"] for c in data}
        assert names == {"Test-Mac", "Second-Mac"}

        first = next(c for c in data if c["name"] == "Test-Mac")
        assert first["platform"] == "macos"
        assert first["platform_detail"] == "macOS 14.2"
        assert set(first["capabilities"]) == {"screenshot", "ocr", "idle", "window_title"}
        assert first["is_active"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_heartbeat_updates_last_seen(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        headers = {
            "Authorization": f"Bearer {reg['token']}",
            "X-Machine-ID": reg["machine_id"],
        }
        before = await db.fetch_one(
            "SELECT last_seen FROM collectors WHERE machine_id = ?", (reg["machine_id"],)
        )
        import time; time.sleep(1.1)

        r = client.post(
            f"/api/collectors/{reg['machine_id']}/heartbeat",
            json={"queue_size": 3},
            headers=headers,
        )
        assert r.status_code == 200
        data = r.json()
        from datetime import datetime
        datetime.fromisoformat(data["server_time"])

        after = await db.fetch_one(
            "SELECT last_seen FROM collectors WHERE machine_id = ?", (reg["machine_id"],)
        )
        assert after["last_seen"] > before["last_seen"], \
            f"last_seen should advance; before={before['last_seen']} after={after['last_seen']}"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_heartbeat_rejects_path_token_mismatch(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg1 = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        reg2 = client.post("/api/collectors/register", json={
            **REGISTER_PAYLOAD, "name": "Other", "hostname": "other.local",
        }).json()

        r = client.post(
            f"/api/collectors/{reg2['machine_id']}/heartbeat",
            json={},
            headers={
                "Authorization": f"Bearer {reg1['token']}",
                "X-Machine-ID": reg1["machine_id"],
            },
        )
        assert r.status_code == 403
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ingest_screenshot_saves_under_machine_dir(tmp_path, monkeypatch):
    # Redirect data_dir to tmp so tests don't pollute ~/.auto_daily_log
    monkeypatch.setenv("HOME", str(tmp_path))

    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        headers = {
            "Authorization": f"Bearer {reg['token']}",
            "X-Machine-ID": reg["machine_id"],
        }
        fake_png = b"\x89PNG\r\n\x1a\nfake_content_for_test"
        r = client.post(
            "/api/ingest/screenshot",
            files={"file": ("test.png", fake_png, "image/png")},
            params={"timestamp": "2026-04-14T10:30:15"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["size"] == len(fake_png)
        assert reg["machine_id"] in data["path"]
        assert "2026-04-14" in data["path"]

        saved = Path(data["path"])
        assert saved.exists()
        assert saved.read_bytes() == fake_png
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ingest_screenshot_rejects_bad_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        r = client.post(
            "/api/ingest/screenshot",
            files={"file": ("a.png", b"data", "image/png")},
            params={"timestamp": "not-a-date"},
            headers={
                "Authorization": f"Bearer {reg['token']}",
                "X-Machine-ID": reg["machine_id"],
            },
        )
        assert r.status_code == 400
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_delete_collector_deactivates_but_keeps_data(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=REGISTER_PAYLOAD).json()
        headers = {
            "Authorization": f"Bearer {reg['token']}",
            "X-Machine-ID": reg["machine_id"],
        }
        client.post("/api/ingest/activities", json={
            "activities": [{"timestamp": "2026-04-14T10:00:00", "duration_sec": 1}]
        }, headers=headers)

        collectors = client.get("/api/collectors").json()
        cid = collectors[0]["id"]

        r = client.delete(f"/api/collectors/{cid}")
        assert r.status_code == 200
        assert r.json()["status"] == "deactivated"

        active_list = client.get("/api/collectors").json()
        assert active_list == []

        acts = await db.fetch_all(
            "SELECT COUNT(*) as n FROM activities WHERE machine_id = ?",
            (reg["machine_id"],),
        )
        assert acts[0]["n"] == 1
    finally:
        await db.close()
