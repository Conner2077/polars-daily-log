"""Tests for /api/scopes and /api/scopes/{name}/outputs CRUD."""
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from auto_daily_log.models.database import Database
from auto_daily_log.web.app import create_app


@pytest_asyncio.fixture
async def client(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ── time_scopes ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_scopes_returns_builtin(client):
    r = await client.get("/api/scopes")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "daily" in names
    assert "weekly" in names
    assert "monthly" in names


@pytest.mark.asyncio
async def test_list_scopes_includes_outputs(client):
    r = await client.get("/api/scopes")
    daily = next(s for s in r.json() if s["name"] == "daily")
    assert len(daily["outputs"]) == 2
    modes = {o["output_mode"] for o in daily["outputs"]}
    assert modes == {"single", "per_issue"}


@pytest.mark.asyncio
async def test_create_scope(client):
    r = await client.post("/api/scopes", json={
        "name": "sprint",
        "display_name": "Sprint 报告",
        "scope_type": "custom",
    })
    assert r.status_code == 201
    assert r.json()["name"] == "sprint"

    # Verify in list
    r2 = await client.get("/api/scopes")
    names = [s["name"] for s in r2.json()]
    assert "sprint" in names


@pytest.mark.asyncio
async def test_create_scope_duplicate_409(client):
    r = await client.post("/api/scopes", json={
        "name": "daily",
        "display_name": "重复",
        "scope_type": "day",
    })
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_scope_invalid_type_400(client):
    r = await client.post("/api/scopes", json={
        "name": "bad",
        "display_name": "Bad",
        "scope_type": "invalid",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_scope(client):
    r = await client.put("/api/scopes/daily", json={
        "display_name": "每日记录",
    })
    assert r.status_code == 200

    r2 = await client.get("/api/scopes")
    daily = next(s for s in r2.json() if s["name"] == "daily")
    assert daily["display_name"] == "每日记录"


@pytest.mark.asyncio
async def test_update_scope_not_found(client):
    r = await client.put("/api/scopes/nonexistent", json={"display_name": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_builtin_scope_403(client):
    r = await client.delete("/api/scopes/daily")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_custom_scope(client):
    await client.post("/api/scopes", json={
        "name": "temp", "display_name": "Temp", "scope_type": "custom",
    })
    r = await client.delete("/api/scopes/temp")
    assert r.status_code == 200

    r2 = await client.get("/api/scopes")
    names = [s["name"] for s in r2.json()]
    assert "temp" not in names


# ── scope_outputs ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_outputs(client):
    r = await client.get("/api/scopes/daily/outputs")
    assert r.status_code == 200
    assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_create_output(client):
    r = await client.post("/api/scopes/daily/outputs", json={
        "display_name": "企微推送",
        "output_mode": "single",
        "publisher_name": "webhook",
        "publisher_config": '{"url":"https://example.com"}',
    })
    assert r.status_code == 201
    output_id = r.json()["id"]
    assert output_id > 0

    r2 = await client.get("/api/scopes/daily/outputs")
    assert len(r2.json()) == 3


@pytest.mark.asyncio
async def test_create_output_invalid_mode(client):
    r = await client.post("/api/scopes/daily/outputs", json={
        "display_name": "Bad",
        "output_mode": "invalid",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_output(client):
    outputs = (await client.get("/api/scopes/daily/outputs")).json()
    single_id = next(o["id"] for o in outputs if o["output_mode"] == "single")

    r = await client.put(f"/api/scopes/outputs/{single_id}", json={
        "display_name": "全活动日志",
    })
    assert r.status_code == 200

    outputs2 = (await client.get("/api/scopes/daily/outputs")).json()
    updated = next(o for o in outputs2 if o["id"] == single_id)
    assert updated["display_name"] == "全活动日志"


@pytest.mark.asyncio
async def test_delete_output(client):
    # Create then delete
    r = await client.post("/api/scopes/daily/outputs", json={
        "display_name": "临时", "output_mode": "single",
    })
    output_id = r.json()["id"]

    r2 = await client.delete(f"/api/scopes/outputs/{output_id}")
    assert r2.status_code == 200

    outputs = (await client.get("/api/scopes/daily/outputs")).json()
    ids = [o["id"] for o in outputs]
    assert output_id not in ids
