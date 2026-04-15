"""Phase B tests — schemas + HTTP storage backend.

After the data-path unification (phase 4), ``HTTPBackend`` is the ONLY
storage backend. Tests that used to drive ``LocalSQLiteBackend`` directly
now spin up the real FastAPI app over an in-memory ASGI transport and
push through ``/api/ingest/*`` — the same wire format external
collectors use — to prove the in-process collector's writes land in DB
with the expected shape.
"""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from auto_daily_log.models.backends import HTTPBackend
from auto_daily_log.models.database import Database
from auto_daily_log.web.app import create_app
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


# ─── HTTPBackend via in-memory ASGI transport ────────────────────────
#
# These tests mirror what the old LocalSQLiteBackend unit tests
# asserted, but route through the real /api/ingest/* endpoints so we're
# testing the exact path that both built-in and external collectors use.

async def _make_backend_against_app(tmp_path, machine_id: str = "mac-conner"):
    """Return (backend, db, token) wired up against a real FastAPI app.

    The collectors table is pre-populated with a row whose token_hash
    matches the returned plaintext so the backend's Bearer auth passes.
    """
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()

    token = "tk-test-" + "a" * 32
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    await db.execute(
        """INSERT INTO collectors
           (machine_id, name, hostname, platform, platform_detail,
            capabilities, token_hash, last_seen, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 1)""",
        (machine_id, f"test-{machine_id}", "test-host", "macos",
         "macOS test", json.dumps(["screenshot"]), token_hash),
    )

    app = create_app(db)
    backend = HTTPBackend(
        server_url="http://testserver",
        token=token,
        queue_dir=tmp_path / "queue",
    )
    # Replace the auto-created httpx client with an ASGI-transported one
    # so every call routes through the in-memory FastAPI instance.
    backend._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        timeout=5.0,
        headers={"Authorization": f"Bearer {token}"},
        base_url="http://testserver",
    )
    return backend, db, token


@pytest.mark.asyncio
async def test_http_backend_saves_activities_with_machine_id(tmp_path):
    backend, db, _ = await _make_backend_against_app(tmp_path, machine_id="mac-conner")
    try:
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
        assert len(ids) == 2
        assert ids[0] >= 1
        assert ids[1] == ids[0] + 1

        rows = await db.fetch_all("SELECT * FROM activities ORDER BY id")
        assert len(rows) == 2
        assert rows[0]["app_name"] == "Xcode"
        assert rows[0]["category"] == "coding"
        assert rows[0]["duration_sec"] == 30
        assert rows[0]["machine_id"] == "mac-conner"
        assert rows[0]["timestamp"] == "2026-04-14T10:00:00"
        assert rows[1]["app_name"] == "Slack"
        assert rows[1]["machine_id"] == "mac-conner"
    finally:
        await backend.close()
        await db.close()


@pytest.mark.asyncio
async def test_http_backend_preserves_signals_json(tmp_path):
    backend, db, _ = await _make_backend_against_app(tmp_path, machine_id="m1")
    try:
        signals = '{"ocr_text":"import os","screenshot_path":"/a/b.png"}'
        await backend.save_activities("m1", [
            ActivityPayload(timestamp="2026-04-14T10:00:00", app_name="X", signals=signals, duration_sec=10),
        ])
        row = await db.fetch_one("SELECT signals FROM activities WHERE id = 1")
        assert row["signals"] == signals
        parsed = json.loads(row["signals"])
        assert parsed["ocr_text"] == "import os"
        assert parsed["screenshot_path"] == "/a/b.png"
    finally:
        await backend.close()
        await db.close()


@pytest.mark.asyncio
async def test_http_backend_commits_dedupe_by_hash_and_machine(tmp_path):
    backend_mac, db, _ = await _make_backend_against_app(tmp_path, machine_id="mac")
    try:
        # Register a second collector 'win' against the same DB
        token_win = "tk-test-" + "b" * 32
        token_hash_win = hashlib.sha256(token_win.encode("utf-8")).hexdigest()
        await db.execute(
            """INSERT INTO collectors
               (machine_id, name, hostname, platform, platform_detail,
                capabilities, token_hash, last_seen, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 1)""",
            ("win", "test-win", "test-host", "windows",
             "Windows 11", json.dumps([]), token_hash_win),
        )
        app = backend_mac._client._transport.app
        backend_win = HTTPBackend(
            server_url="http://testserver",
            token=token_win,
            queue_dir=tmp_path / "queue-win",
        )
        backend_win._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            timeout=5.0,
            headers={"Authorization": f"Bearer {token_win}"},
            base_url="http://testserver",
        )

        c1 = CommitPayload(hash="abc1234", message="first", date="2026-04-14")
        c2 = CommitPayload(hash="abc1234", message="dup-on-same-machine", date="2026-04-14")
        c3 = CommitPayload(hash="abc1234", message="same-hash-different-machine", date="2026-04-14")

        n1 = await backend_mac.save_commits("mac", [c1])
        assert n1 == 1
        n2 = await backend_mac.save_commits("mac", [c2])
        assert n2 == 0
        n3 = await backend_win.save_commits("win", [c3])
        assert n3 == 1

        rows = await db.fetch_all("SELECT message, machine_id FROM git_commits ORDER BY id")
        assert len(rows) == 2
        assert rows[0]["message"] == "first"
        assert rows[0]["machine_id"] == "mac"
        assert rows[1]["message"] == "same-hash-different-machine"
        assert rows[1]["machine_id"] == "win"
        await backend_win.close()
    finally:
        await backend_mac.close()
        await db.close()


@pytest.mark.asyncio
async def test_http_backend_extend_duration_adds_seconds(tmp_path):
    backend, db, _ = await _make_backend_against_app(tmp_path, machine_id="m1")
    try:
        ids = await backend.save_activities("m1", [
            ActivityPayload(timestamp="2026-04-15T10:00:00", app_name="X", duration_sec=30),
        ])
        row_id = ids[0]

        await backend.extend_duration("m1", row_id, 15)
        await backend.extend_duration("m1", row_id, 45)

        row = await db.fetch_one("SELECT duration_sec FROM activities WHERE id = ?", (row_id,))
        assert row["duration_sec"] == 90
    finally:
        await backend.close()
        await db.close()


@pytest.mark.asyncio
async def test_http_backend_extend_duration_scoped_to_machine(tmp_path):
    backend_m1, db, _ = await _make_backend_against_app(tmp_path, machine_id="m1")
    try:
        # Second collector 'other' — its extend attempt must not touch m1's row
        token_other = "tk-test-" + "c" * 32
        token_hash_other = hashlib.sha256(token_other.encode("utf-8")).hexdigest()
        await db.execute(
            """INSERT INTO collectors
               (machine_id, name, hostname, platform, platform_detail,
                capabilities, token_hash, last_seen, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 1)""",
            ("other", "other-collector", "other-host", "macos",
             "macOS test", json.dumps([]), token_hash_other),
        )
        app = backend_m1._client._transport.app
        backend_other = HTTPBackend(
            server_url="http://testserver",
            token=token_other,
            queue_dir=tmp_path / "queue-other",
        )
        backend_other._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            timeout=5.0,
            headers={"Authorization": f"Bearer {token_other}"},
            base_url="http://testserver",
        )

        ids = await backend_m1.save_activities("m1", [
            ActivityPayload(timestamp="2026-04-15T10:00:00", app_name="X", duration_sec=30),
        ])
        row_id = ids[0]

        # Wrong machine_id — must not touch the row
        await backend_other.extend_duration("other", row_id, 100)

        row = await db.fetch_one("SELECT duration_sec FROM activities WHERE id = ?", (row_id,))
        assert row["duration_sec"] == 30
        await backend_other.close()
    finally:
        await backend_m1.close()
        await db.close()


# ─── HTTPBackend — offline queue / retry (mocked httpx) ──────────────

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
        new_batch = [ActivityPayload(timestamp="2026-04-14T10:00:10", app_name="new", duration_sec=1)]
        await backend.save_activities("m1", new_batch)

    assert len(posted) == 2, f"expected 2 POSTs, got {len(posted)}: {posted}"
    assert posted[0]["count"] == 3, f"drain should send 3 queued, got {posted[0]}"
    assert posted[1]["count"] == 1, f"new batch should be 1, got {posted[1]}"

    assert not queue_file.exists() or queue_file.stat().st_size == 0


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


@pytest.mark.asyncio
async def test_http_backend_save_screenshot_uploads_and_returns_server_path(tmp_path):
    """save_screenshot posts multipart to /api/ingest/screenshot and
    returns the server-side path recorded in the ingest response."""
    backend, db, _ = await _make_backend_against_app(tmp_path, machine_id="m1")
    try:
        shot = tmp_path / "source" / "s1.png"
        shot.parent.mkdir(parents=True)
        shot.write_bytes(b"fakepngbytes")

        result = await backend.save_screenshot("m1", shot)
        # Server writes under <data_dir>/screenshots/<machine>/<date>/<name>
        # but here we don't have app.state.config set so fallback is
        # ~/.auto_daily_log/screenshots — we just assert the path string
        # contains machine_id and ends with .png (server-side path echoed
        # back to the collector).
        assert "m1" in result
        assert result.endswith(".png")
    finally:
        await backend.close()
        await db.close()
