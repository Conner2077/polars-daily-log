"""Phase B tests — schemas + storage backends."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from auto_daily_log.models.backends import LocalSQLiteBackend, HTTPBackend
from auto_daily_log.models.database import Database
from shared.schemas import (
    ActivityPayload,
    CollectorRegisterRequest,
    CommitPayload,
    PLATFORM_MACOS,
    CAPABILITY_SCREENSHOT,
    CAPABILITY_OCR,
)


# ─── Schema validation ───────────────────────────────────────────────

def test_activity_payload_requires_timestamp():
    with pytest.raises(Exception) as exc_info:
        ActivityPayload()  # no timestamp
    assert "timestamp" in str(exc_info.value).lower()


def test_activity_payload_accepts_full_record():
    a = ActivityPayload(
        timestamp="2026-04-14T10:00:00",
        app_name="Xcode",
        window_title="MainView.swift",
        category="coding",
        confidence=0.95,
        url=None,
        signals='{"ocr_text":"hello"}',
        duration_sec=30,
    )
    assert a.timestamp == "2026-04-14T10:00:00"
    assert a.app_name == "Xcode"
    assert a.category == "coding"
    assert a.confidence == 0.95
    assert a.duration_sec == 30


def test_commit_payload_hash_min_length():
    with pytest.raises(Exception):
        CommitPayload(hash="abc")  # too short
    # 7 chars works (git short sha)
    c = CommitPayload(hash="abc1234", message="fix")
    assert c.hash == "abc1234"


def test_collector_register_request_valid():
    req = CollectorRegisterRequest(
        name="Mac-Office",
        hostname="mbp-conner.local",
        platform=PLATFORM_MACOS,
        platform_detail="macOS 14.2",
        capabilities=[CAPABILITY_SCREENSHOT, CAPABILITY_OCR],
    )
    assert req.name == "Mac-Office"
    assert req.platform == "macos"
    assert CAPABILITY_SCREENSHOT in req.capabilities


def test_collector_register_rejects_empty_name():
    with pytest.raises(Exception) as exc_info:
        CollectorRegisterRequest(name="", hostname="h", platform=PLATFORM_MACOS)
    # Field validator catches empty name
    msg = str(exc_info.value).lower()
    assert "name" in msg or "length" in msg


# ─── LocalSQLiteBackend ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_backend_saves_activities_with_machine_id(tmp_path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    backend = LocalSQLiteBackend(db)

    activities = [
        ActivityPayload(
            timestamp="2026-04-14T10:00:00",
            app_name="Xcode",
            category="coding",
            duration_sec=30,
        ),
        ActivityPayload(
            timestamp="2026-04-14T10:00:30",
            app_name="Slack",
            category="communication",
            duration_sec=60,
        ),
    ]
    ids = await backend.save_activities("mac-conner", activities)
    assert len(ids) == 2, f"expected 2 IDs, got {ids}"
    assert ids[0] > 0
    assert ids[1] == ids[0] + 1

    # Verify DB state
    rows = await db.fetch_all("SELECT * FROM activities ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["app_name"] == "Xcode"
    assert rows[0]["category"] == "coding"
    assert rows[0]["duration_sec"] == 30
    assert rows[0]["machine_id"] == "mac-conner"
    assert rows[0]["timestamp"] == "2026-04-14T10:00:00"
    assert rows[1]["app_name"] == "Slack"
    assert rows[1]["machine_id"] == "mac-conner"

    await db.close()


@pytest.mark.asyncio
async def test_local_backend_preserves_signals_json(tmp_path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    backend = LocalSQLiteBackend(db)

    signals = '{"ocr_text":"import os","screenshot_path":"/a/b.png"}'
    await backend.save_activities("m1", [
        ActivityPayload(timestamp="2026-04-14T10:00:00", app_name="X", signals=signals, duration_sec=10),
    ])
    row = await db.fetch_one("SELECT signals FROM activities WHERE id = 1")
    assert row["signals"] == signals, f"signals mangled: {row['signals']!r}"
    # Assert it's still valid JSON after roundtrip
    parsed = json.loads(row["signals"])
    assert parsed["ocr_text"] == "import os"
    assert parsed["screenshot_path"] == "/a/b.png"
    await db.close()


@pytest.mark.asyncio
async def test_local_backend_commits_dedupe_by_hash_and_machine(tmp_path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    backend = LocalSQLiteBackend(db)

    c1 = CommitPayload(hash="abc1234", message="first", date="2026-04-14")
    c2 = CommitPayload(hash="abc1234", message="dup-on-same-machine", date="2026-04-14")
    c3 = CommitPayload(hash="abc1234", message="same-hash-different-machine", date="2026-04-14")

    # First batch to machine 'mac'
    n1 = await backend.save_commits("mac", [c1])
    assert n1 == 1

    # Duplicate on same machine — should not insert
    n2 = await backend.save_commits("mac", [c2])
    assert n2 == 0, f"expected dedupe, got {n2}"

    # Same hash on different machine — DOES insert (machine-scoped dedupe)
    n3 = await backend.save_commits("win", [c3])
    assert n3 == 1, f"expected cross-machine insert, got {n3}"

    rows = await db.fetch_all("SELECT message, machine_id FROM git_commits ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["message"] == "first"
    assert rows[0]["machine_id"] == "mac"
    assert rows[1]["message"] == "same-hash-different-machine"
    assert rows[1]["machine_id"] == "win"
    await db.close()


# ─── HTTPBackend ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_backend_enqueues_on_network_failure(tmp_path):
    backend = HTTPBackend(
        server_url="http://nonexistent.invalid:9999",
        token="t" * 32,
        queue_dir=tmp_path,
    )

    # Force underlying HTTP to fail by pointing at an invalid host
    activities = [
        ActivityPayload(timestamp="2026-04-14T10:00:00", app_name="X", duration_sec=5),
        ActivityPayload(timestamp="2026-04-14T10:00:05", app_name="Y", duration_sec=5),
    ]
    ids = await backend.save_activities("m1", activities)
    assert ids == [], "expected empty IDs on network failure"

    # Queue file should exist with 2 lines
    queue_file = tmp_path / "pending.jsonl"
    assert queue_file.exists(), "queue file missing"
    with queue_file.open(encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert len(lines) == 2, f"expected 2 queued items, got {len(lines)}"
    assert lines[0]["kind"] == "activities"
    assert lines[0]["machine_id"] == "m1"
    assert lines[0]["payload"]["app_name"] == "X"
    assert lines[1]["payload"]["app_name"] == "Y"

    await backend.close()


@pytest.mark.asyncio
async def test_http_backend_posts_with_auth_header(tmp_path):
    """Mock httpx to verify the Authorization header and URL."""
    backend = HTTPBackend(
        server_url="http://server.test:8080",
        token="my-secret-token-with-32-chars!!",
        queue_dir=tmp_path,
    )

    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers or {}
        captured["auth"] = self.headers.get("Authorization")
        # Return a mock response
        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"accepted": len(json["activities"]), "first_id": 1, "last_id": len(json["activities"])}
        return FakeResp()

    with patch("httpx.AsyncClient.post", new=fake_post):
        activities = [
            ActivityPayload(timestamp="2026-04-14T10:00:00", app_name="Safari", duration_sec=15),
        ]
        ids = await backend.save_activities("mac-1", activities)

    assert captured["url"] == "http://server.test:8080/api/ingest/activities"
    assert captured["auth"] == "Bearer my-secret-token-with-32-chars!!"
    assert captured["headers"].get("X-Machine-ID") == "mac-1"
    assert captured["body"]["activities"][0]["app_name"] == "Safari"
    assert ids == [1], f"expected [1], got {ids}"

    await backend.close()


@pytest.mark.asyncio
async def test_http_backend_drains_queue_on_success(tmp_path):
    """When server comes back online, queued items should be sent."""
    backend = HTTPBackend(
        server_url="http://server.test:8080",
        token="t" * 32,
        queue_dir=tmp_path,
    )

    # Pre-populate queue with 3 items
    queue_file = tmp_path / "pending.jsonl"
    with queue_file.open("w", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({
                "kind": "activities",
                "machine_id": "m1",
                "payload": {
                    "timestamp": f"2026-04-14T10:00:{i:02d}",
                    "app_name": f"app{i}",
                    "duration_sec": 1,
                }
            }) + "\n")

    posted = []

    async def fake_post(self, url, json=None, headers=None):
        posted.append({"url": url, "count": len(json.get("activities", []) or json.get("commits", []))})
        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self_): return {"accepted": posted[-1]["count"], "first_id": 1, "last_id": posted[-1]["count"]}
        return FakeResp()

    with patch("httpx.AsyncClient.post", new=fake_post):
        # Trigger a new send — _drain_queue runs first
        new_batch = [ActivityPayload(timestamp="2026-04-14T10:00:10", app_name="new", duration_sec=1)]
        await backend.save_activities("m1", new_batch)

    # Should have: 1 drain call (3 items) + 1 new call (1 item)
    assert len(posted) == 2, f"expected 2 POSTs, got {len(posted)}: {posted}"
    assert posted[0]["count"] == 3, f"drain should send 3 queued, got {posted[0]}"
    assert posted[1]["count"] == 1, f"new batch should be 1, got {posted[1]}"

    # Queue should be empty after drain
    assert not queue_file.exists() or queue_file.stat().st_size == 0


# ─── Phase 3: extend_duration + save_screenshot ─────────────────────

@pytest.mark.asyncio
async def test_local_backend_extend_duration_adds_seconds(tmp_path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    backend = LocalSQLiteBackend(db)

    ids = await backend.save_activities("m1", [
        ActivityPayload(timestamp="2026-04-15T10:00:00", app_name="X", duration_sec=30),
    ])
    row_id = ids[0]

    await backend.extend_duration("m1", row_id, 15)
    await backend.extend_duration("m1", row_id, 45)

    row = await db.fetch_one("SELECT duration_sec FROM activities WHERE id = ?", (row_id,))
    assert row["duration_sec"] == 90
    await db.close()


@pytest.mark.asyncio
async def test_local_backend_extend_duration_scoped_to_machine(tmp_path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    backend = LocalSQLiteBackend(db)

    ids = await backend.save_activities("m1", [
        ActivityPayload(timestamp="2026-04-15T10:00:00", app_name="X", duration_sec=30),
    ])
    row_id = ids[0]

    # Wrong machine_id — must not touch the row
    await backend.extend_duration("other", row_id, 100)

    row = await db.fetch_one("SELECT duration_sec FROM activities WHERE id = ?", (row_id,))
    assert row["duration_sec"] == 30
    await db.close()


@pytest.mark.asyncio
async def test_local_backend_save_screenshot_returns_path_unchanged(tmp_path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    backend = LocalSQLiteBackend(db)

    shot = tmp_path / "2026-04-15" / "s1.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"png")

    result = await backend.save_screenshot("m1", shot)
    assert result == str(shot)
    assert Path(result).exists()
    await db.close()


@pytest.mark.asyncio
async def test_http_backend_extend_duration_posts_to_server(tmp_path):
    backend = HTTPBackend(
        server_url="http://server.test:8080",
        token="x" * 32,
        queue_dir=tmp_path,
    )
    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers or {}
        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"ok": True}
        return FakeResp()

    with patch("httpx.AsyncClient.post", new=fake_post):
        await backend.extend_duration("m1", 42, 30)

    assert captured["url"] == "http://server.test:8080/api/ingest/extend-duration"
    assert captured["body"] == {"row_id": 42, "extra_sec": 30}
    assert captured["headers"]["X-Machine-ID"] == "m1"
    await backend.close()


@pytest.mark.asyncio
async def test_http_backend_extend_duration_zero_is_noop(tmp_path):
    backend = HTTPBackend(
        server_url="http://server.test:8080",
        token="x" * 32,
        queue_dir=tmp_path,
    )
    calls = []

    async def fake_post(self, url, json=None, headers=None):
        calls.append(url)
        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"ok": True}
        return FakeResp()

    with patch("httpx.AsyncClient.post", new=fake_post):
        await backend.extend_duration("m1", 42, 0)

    assert calls == []
    await backend.close()

    await backend.close()
