"""Phase G + K + L server-side tests:
- G: machine_id filter on query APIs
- K: remote config override + pause/resume endpoints
- L: uninstall → delete collector (covered in deregister path)
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auto_daily_log.models.database import Database
from auto_daily_log.web.app import create_app


async def _setup(tmp_path: Path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    app = create_app(db)
    return TestClient(app), db


def _reg_payload(name: str, hostname: str):
    return {
        "name": name, "hostname": hostname, "platform": "macos",
        "capabilities": ["screenshot", "idle"],
    }


# ═══ G. machine_id filter on queries ═══════════════════════════════

@pytest.mark.asyncio
async def test_list_activities_filters_by_machine_id(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        r1 = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()
        r2 = client.post("/api/collectors/register", json=_reg_payload("B", "b.local")).json()

        # Push 2 from A, 3 from B
        for i in range(2):
            client.post("/api/ingest/activities",
                json={"activities": [{"timestamp": f"2026-04-14T10:{i:02d}:00", "app_name": f"A_app{i}", "duration_sec": 10}]},
                headers={"Authorization": f"Bearer {r1['token']}", "X-Machine-ID": r1["machine_id"]},
            )
        for i in range(3):
            client.post("/api/ingest/activities",
                json={"activities": [{"timestamp": f"2026-04-14T11:{i:02d}:00", "app_name": f"B_app{i}", "duration_sec": 10}]},
                headers={"Authorization": f"Bearer {r2['token']}", "X-Machine-ID": r2["machine_id"]},
            )

        # No filter → all 5
        all_rows = client.get("/api/activities?target_date=2026-04-14").json()
        assert len(all_rows) == 5
        names = {r["app_name"] for r in all_rows}
        assert names == {"A_app0", "A_app1", "B_app0", "B_app1", "B_app2"}

        # Filter to A → 2
        a_rows = client.get(f"/api/activities?target_date=2026-04-14&machine_id={r1['machine_id']}").json()
        assert len(a_rows) == 2
        a_names = {r["app_name"] for r in a_rows}
        assert a_names == {"A_app0", "A_app1"}
        for r in a_rows:
            assert r["machine_id"] == r1["machine_id"]

        # Filter to B → 3
        b_rows = client.get(f"/api/activities?target_date=2026-04-14&machine_id={r2['machine_id']}").json()
        assert len(b_rows) == 3
        b_names = {r["app_name"] for r in b_rows}
        assert b_names == {"B_app0", "B_app1", "B_app2"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_activity_dates_filters_by_machine_id(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        r1 = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()
        r2 = client.post("/api/collectors/register", json=_reg_payload("B", "b.local")).json()

        # A: 2026-04-14 only
        client.post("/api/ingest/activities",
            json={"activities": [{"timestamp": "2026-04-14T10:00:00", "app_name": "a", "duration_sec": 10}]},
            headers={"Authorization": f"Bearer {r1['token']}", "X-Machine-ID": r1["machine_id"]},
        )
        # B: 2026-04-14 and 2026-04-13
        for ts in ["2026-04-13T09:00:00", "2026-04-14T11:00:00"]:
            client.post("/api/ingest/activities",
                json={"activities": [{"timestamp": ts, "app_name": "b", "duration_sec": 10}]},
                headers={"Authorization": f"Bearer {r2['token']}", "X-Machine-ID": r2["machine_id"]},
            )

        all_dates = client.get("/api/activities/dates").json()
        dates_set = {d["date"] for d in all_dates}
        assert dates_set == {"2026-04-13", "2026-04-14"}

        a_dates = client.get(f"/api/activities/dates?machine_id={r1['machine_id']}").json()
        assert len(a_dates) == 1
        assert a_dates[0]["date"] == "2026-04-14"
        assert a_dates[0]["count"] == 1

        b_dates = client.get(f"/api/activities/dates?machine_id={r2['machine_id']}").json()
        assert len(b_dates) == 2
        b_date_set = {d["date"] for d in b_dates}
        assert b_date_set == {"2026-04-13", "2026-04-14"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dashboard_filters_by_machine_id(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        r1 = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()
        r2 = client.post("/api/collectors/register", json=_reg_payload("B", "b.local")).json()

        # A: 2 coding activities totaling 200s
        client.post("/api/ingest/activities", json={"activities": [
            {"timestamp": "2026-04-14T10:00:00", "category": "coding", "duration_sec": 100},
            {"timestamp": "2026-04-14T10:05:00", "category": "coding", "duration_sec": 100},
        ]}, headers={"Authorization": f"Bearer {r1['token']}", "X-Machine-ID": r1["machine_id"]})
        # B: 1 meeting totaling 1800s
        client.post("/api/ingest/activities", json={"activities": [
            {"timestamp": "2026-04-14T11:00:00", "category": "meeting", "duration_sec": 1800},
        ]}, headers={"Authorization": f"Bearer {r2['token']}", "X-Machine-ID": r2["machine_id"]})

        all_dash = client.get("/api/dashboard?target_date=2026-04-14").json()
        cats = {s["category"]: s["total_sec"] for s in all_dash["activity_summary"]}
        assert cats == {"coding": 200, "meeting": 1800}

        a_dash = client.get(f"/api/dashboard?target_date=2026-04-14&machine_id={r1['machine_id']}").json()
        a_cats = {s["category"]: s["total_sec"] for s in a_dash["activity_summary"]}
        assert a_cats == {"coding": 200}

        b_dash = client.get(f"/api/dashboard?target_date=2026-04-14&machine_id={r2['machine_id']}").json()
        b_cats = {s["category"]: s["total_sec"] for s in b_dash["activity_summary"]}
        assert b_cats == {"meeting": 1800}
    finally:
        await db.close()


# ═══ K. Remote config override + pause/resume ══════════════════════

@pytest.mark.asyncio
async def test_set_config_override_stores_on_collector(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()

        r = client.put(
            f"/api/collectors/{reg['machine_id']}/config",
            json={"interval_sec": 60, "ocr_enabled": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["config_override"] == {"interval_sec": 60, "ocr_enabled": True}

        # Verify in DB
        row = await db.fetch_one(
            "SELECT config_override FROM collectors WHERE machine_id = ?", (reg["machine_id"],)
        )
        stored = json.loads(row["config_override"])
        assert stored == {"interval_sec": 60, "ocr_enabled": True}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_set_config_override_merges_with_existing(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()

        client.put(f"/api/collectors/{reg['machine_id']}/config", json={"interval_sec": 30})
        r = client.put(f"/api/collectors/{reg['machine_id']}/config", json={"ocr_enabled": True})
        assert r.json()["config_override"] == {"interval_sec": 30, "ocr_enabled": True}

        # New value overrides
        r2 = client.put(f"/api/collectors/{reg['machine_id']}/config", json={"interval_sec": 120})
        assert r2.json()["config_override"] == {"interval_sec": 120, "ocr_enabled": True}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_set_config_override_empty_clears(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()
        client.put(f"/api/collectors/{reg['machine_id']}/config", json={"interval_sec": 60})

        r = client.put(f"/api/collectors/{reg['machine_id']}/config", json={})
        assert r.status_code == 200
        assert r.json()["config_override"] is None

        row = await db.fetch_one(
            "SELECT config_override FROM collectors WHERE machine_id = ?", (reg["machine_id"],)
        )
        assert row["config_override"] is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_config_override_returned_in_heartbeat(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()
        client.put(f"/api/collectors/{reg['machine_id']}/config", json={"interval_sec": 90})

        r = client.post(
            f"/api/collectors/{reg['machine_id']}/heartbeat",
            json={"queue_size": 0},
            headers={
                "Authorization": f"Bearer {reg['token']}",
                "X-Machine-ID": reg["machine_id"],
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["config_override"] == {"interval_sec": 90}
        assert data["is_paused"] is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pause_then_heartbeat_reports_paused(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()

        r = client.post(f"/api/collectors/{reg['machine_id']}/pause")
        assert r.status_code == 200
        assert r.json()["status"] == "paused"

        hb = client.post(
            f"/api/collectors/{reg['machine_id']}/heartbeat",
            json={"queue_size": 0},
            headers={
                "Authorization": f"Bearer {reg['token']}",
                "X-Machine-ID": reg["machine_id"],
            },
        ).json()
        assert hb["is_paused"] is True

        client.post(f"/api/collectors/{reg['machine_id']}/resume")
        hb2 = client.post(
            f"/api/collectors/{reg['machine_id']}/heartbeat",
            json={"queue_size": 0},
            headers={
                "Authorization": f"Bearer {reg['token']}",
                "X-Machine-ID": reg["machine_id"],
            },
        ).json()
        assert hb2["is_paused"] is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pause_unknown_machine_returns_404(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        r = client.post("/api/collectors/m-nonexistent/pause")
        assert r.status_code == 404
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_config_override_unknown_machine_returns_404(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        r = client.put(
            "/api/collectors/m-nonexistent/config",
            json={"interval_sec": 30},
        )
        assert r.status_code == 404
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_list_collectors_exposes_paused_and_override(tmp_path):
    client, db = await _setup(tmp_path)
    try:
        reg = client.post("/api/collectors/register", json=_reg_payload("A", "a.local")).json()
        client.put(f"/api/collectors/{reg['machine_id']}/config", json={"interval_sec": 45})
        client.post(f"/api/collectors/{reg['machine_id']}/pause")

        info = client.get("/api/collectors").json()
        assert len(info) == 1
        c = info[0]
        assert c["is_paused"] is True
        assert c["config_override"] == {"interval_sec": 45}
    finally:
        await db.close()
