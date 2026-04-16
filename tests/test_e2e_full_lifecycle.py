"""
Full lifecycle E2E test — from empty environment to Jira submission.

Simulates the complete user journey with ALL external services mocked:
  1. Fresh DB (empty environment, like first install)
  2. Collector registration + activity ingest
  3. Settings configuration (LLM, Jira, scheduler)
  4. Daily summary generation (mock LLM)
  5. Draft inspection + editing
  6. Manual approve + reject flow
  7. Scheduler auto-approve catch-up (mock cron trigger)
  8. Jira submission (mock Jira client)
  9. Audit trail verification
  10. Dashboard / timeline / machine status APIs
  11. Delete draft (full lifecycle end)
  12. Frontend static files served

Runs on all 3 CI platforms (ubuntu / macos / windows).
No real LLM, no real Jira, no real OCR, no network calls.
"""
import json
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from httpx import AsyncClient, ASGITransport

from auto_daily_log.models.database import Database
from auto_daily_log.web.app import create_app


# ─── Fixtures ────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def env(tmp_path):
    """Fresh environment: empty DB + FastAPI app + test HTTP client."""
    db = Database(tmp_path / "lifecycle.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    app.state.db = db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, db
    await db.close()


# ─── Mock data ───────────────────────────────────────────────────────

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

MOCK_LLM_FULL_SUMMARY = """今天主要在 VS Code 中开发 Polars Daily Log 的 UI 重构，
包括 Dashboard 时间轴组件和 sidebar 导航。下午在 Chrome 中查看 GitHub Actions CI 结果。"""

MOCK_LLM_ISSUES_JSON = json.dumps([
    {
        "issue_key": "PLS-100",
        "time_spent_hours": 5.0,
        "summary": "UI 重构：Dashboard 时间轴 + sidebar 导航开发"
    },
    {
        "issue_key": "PLS-101",
        "time_spent_hours": 2.0,
        "summary": "CI 多平台矩阵调试与修复"
    },
], ensure_ascii=False)


def _make_activities(date_str, count=20):
    """Generate realistic activity payloads for ingest."""
    apps = [
        ("VS Code", "coding", "Dashboard.vue — auto_daily_log"),
        ("Google Chrome", "browsing", "github.com/Conner2077/polars-daily-log/actions"),
        ("iTerm2", "coding", "npm run build"),
        ("Slack", "communication", "#polars-eng"),
        ("Figma", "design", "Polars Daily Log — Landing"),
    ]
    activities = []
    base_hour = 9
    for i in range(count):
        app_name, category, title = apps[i % len(apps)]
        h = base_hour + (i * 15 // 60)
        m = (i * 15) % 60
        ts = f"{date_str}T{h:02d}:{m:02d}:00"
        activities.append({
            "timestamp": ts,
            "app_name": app_name,
            "window_title": title,
            "category": category,
            "confidence": 0.85,
            "url": None,
            "signals": json.dumps({"ocr_text": f"OCR content for activity {i}"}),
            "duration_sec": 30,
        })
    return activities


# ─── The test ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_lifecycle(env):
    http, db = env

    # ════════════════════════════════════════════════════════════════
    # Phase 1: Empty environment — verify clean state
    # ════════════════════════════════════════════════════════════════

    r = await http.get("/api/dashboard", params={"target_date": TODAY})
    assert r.status_code == 200
    dash = r.json()
    assert dash["pending_review_count"] == 0
    assert dash["submitted_hours"] == 0

    r = await http.get("/api/worklogs", params={"date": TODAY})
    assert r.status_code == 200
    assert r.json() == []

    r = await http.get("/api/collectors")
    assert r.status_code == 200
    assert r.json() == []

    r = await http.get("/api/machines/status")
    assert r.status_code == 200
    assert r.json() == []

    # Frontend static files served
    r = await http.get("/")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower() or "<!DOCTYPE html>" in r.text

    # ════════════════════════════════════════════════════════════════
    # Phase 2: Collector registration + activity ingest
    # ════════════════════════════════════════════════════════════════

    # Register a collector
    r = await http.post("/api/collectors/register", json={
        "name": "Test MacBook",
        "hostname": "test-host.local",
        "platform": "macos",
        "platform_detail": "macOS 15.0",
        "capabilities": ["screenshot", "ocr", "window_title"],
    })
    assert r.status_code == 200
    reg = r.json()
    assert "machine_id" in reg
    assert "token" in reg
    machine_id = reg["machine_id"]
    token = reg["token"]

    # Verify collector appears
    r = await http.get("/api/collectors")
    assert r.status_code == 200
    collectors = r.json()
    assert len(collectors) == 1
    assert collectors[0]["name"] == "Test MacBook"

    # Ingest activities
    activities = _make_activities(TODAY, count=20)
    r = await http.post(
        "/api/ingest/activities",
        json={"activities": activities},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Machine-ID": machine_id,
        },
    )
    assert r.status_code == 200
    ingest_result = r.json()
    assert ingest_result["accepted"] == 20
    assert ingest_result["rejected"] == 0

    # Verify activities stored
    r = await http.get("/api/activities", params={"target_date": TODAY})
    assert r.status_code == 200
    stored = r.json()
    assert len(stored) == 20

    # Verify activity dates
    r = await http.get("/api/activities/dates")
    assert r.status_code == 200
    dates = r.json()
    assert any(d["date"] == TODAY for d in dates)

    # Verify timeline API
    r = await http.get("/api/activities/timeline", params={"hours": 12, "bucket": "15m"})
    assert r.status_code == 200
    tl = r.json()
    assert tl["bucket_minutes"] == 15
    assert len(tl["buckets"]) == 48
    # At least some buckets should have activity
    active_buckets = [b for b in tl["buckets"] if b["active_mins"] > 0]
    assert len(active_buckets) > 0

    # Verify recent activities API
    r = await http.get("/api/activities/recent", params={"limit": 5})
    assert r.status_code == 200
    recent = r.json()
    assert len(recent) == 5
    assert recent[0]["app_name"] in ("VS Code", "Google Chrome", "iTerm2", "Slack", "Figma")

    # Verify machines/status — collector should be online
    r = await http.get("/api/machines/status")
    assert r.status_code == 200
    machines = r.json()
    assert len(machines) == 1
    assert machines[0]["name"] == "Test MacBook"
    assert machines[0]["online"] == True

    # ════════════════════════════════════════════════════════════════
    # Phase 3: Settings configuration
    # ════════════════════════════════════════════════════════════════

    settings = {
        "llm_engine": "openai_compat",
        "llm_api_key": "test-key-123",
        "llm_base_url": "https://api.test.com/v1",
        "llm_model": "test-model",
        "jira_server_url": "https://jira.test.com",
        "jira_auth_mode": "pat",
        "jira_pat": "test-pat",
        "scheduler_enabled": "true",
        "scheduler_trigger_time": "18:00",
        "auto_approve_enabled": "true",
        "auto_approve_trigger_time": "21:30",
    }
    for key, value in settings.items():
        r = await http.put(f"/api/settings/{key}", json={"value": str(value)})
        assert r.status_code == 200

    # Verify settings persisted
    r = await http.get("/api/settings")
    assert r.status_code == 200
    saved = {s["key"]: s["value"] for s in r.json()}
    assert saved["llm_engine"] == "openai_compat"
    assert saved["jira_server_url"] == "https://jira.test.com"

    # Add a Jira issue for the summarizer to reference
    r = await http.post("/api/issues", json={
        "issue_key": "PLS-100",
        "summary": "UI 重构 Dashboard",
        "description": "Dashboard 时间轴 + sidebar 导航",
    })
    assert r.status_code in (200, 201)

    r = await http.post("/api/issues", json={
        "issue_key": "PLS-101",
        "summary": "CI 修复",
        "description": "多平台 CI 矩阵",
    })
    assert r.status_code in (200, 201)

    # Verify issues
    r = await http.get("/api/issues")
    assert r.status_code == 200
    assert len(r.json()) == 2

    # ════════════════════════════════════════════════════════════════
    # Phase 4: Daily summary generation (mock LLM)
    # ════════════════════════════════════════════════════════════════

    # Mock LLM engine: first call → full_summary, second → per-issue JSON
    call_count = {"n": 0}

    class MockEngine:
        async def generate(self, prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] % 2 == 1:
                return MOCK_LLM_FULL_SUMMARY
            return MOCK_LLM_ISSUES_JSON

    with patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings",
               return_value=MockEngine()):
        r = await http.post("/api/worklogs/generate", json={"type": "daily"})

    assert r.status_code == 200
    gen_result = r.json()
    assert "id" in gen_result
    draft_id = gen_result["id"]

    # Verify draft created
    r = await http.get("/api/worklogs", params={"date": TODAY})
    assert r.status_code == 200
    drafts = r.json()
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["id"] == draft_id
    assert draft["status"] == "pending_review"
    assert draft["tag"] == "daily"
    assert draft["date"] == TODAY

    # Verify summary contains our mock issue entries
    issues = json.loads(draft["summary"])
    assert len(issues) == 2
    assert issues[0]["issue_key"] == "PLS-100"
    assert issues[0]["time_spent_hours"] == 5.0
    assert issues[1]["issue_key"] == "PLS-101"

    # Verify full_summary is the raw LLM output
    assert "Dashboard" in (draft.get("full_summary") or "")

    # Dashboard should reflect pending draft
    r = await http.get("/api/dashboard", params={"target_date": TODAY})
    assert r.status_code == 200
    dash = r.json()
    assert dash["pending_review_count"] == 1

    # Extended dashboard
    r = await http.get("/api/dashboard/extended", params={"date": TODAY})
    assert r.status_code == 200
    ext = r.json()
    assert ext["activity_count"] == 20
    assert ext["pending_drafts_count"] == 1

    # Drafts preview
    r = await http.get("/api/worklogs/drafts/preview", params={"limit": 3})
    assert r.status_code == 200
    previews = r.json()
    assert len(previews) >= 1
    assert previews[0]["issue_key"] == "PLS-100"

    # ════════════════════════════════════════════════════════════════
    # Phase 5: Edit a draft issue
    # ════════════════════════════════════════════════════════════════

    r = await http.patch(f"/api/worklogs/{draft_id}/issues/0", json={
        "issue_key": "PLS-100",
        "time_spent_hours": 4.5,
        "summary": "UI 重构：Dashboard 时间轴（已修改）",
    })
    assert r.status_code == 200

    # Verify edit persisted
    r = await http.get("/api/worklogs", params={"date": TODAY})
    updated_issues = json.loads(r.json()[0]["summary"])
    assert updated_issues[0]["time_spent_hours"] == 4.5
    assert "已修改" in updated_issues[0]["summary"]

    # ════════════════════════════════════════════════════════════════
    # Phase 6: Reject flow
    # ════════════════════════════════════════════════════════════════

    r = await http.post(f"/api/worklogs/{draft_id}/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    # Verify status
    r = await http.get("/api/worklogs", params={"date": TODAY})
    assert r.json()[0]["status"] == "rejected"

    # Re-generate to get a fresh pending draft for approve flow
    call_count["n"] = 0
    with patch("auto_daily_log.web.api.worklogs._get_llm_engine_from_settings",
               return_value=MockEngine()):
        r = await http.post("/api/worklogs/generate", json={"type": "daily", "force": True})
    assert r.status_code == 200
    draft_id_2 = r.json()["id"]

    # ════════════════════════════════════════════════════════════════
    # Phase 7: Approve flow
    # ════════════════════════════════════════════════════════════════

    r = await http.post(f"/api/worklogs/{draft_id_2}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # Verify audit trail
    r = await http.get(f"/api/worklogs/{draft_id_2}/audit")
    assert r.status_code == 200
    audit = r.json()
    actions = [a["action"] for a in audit]
    assert "approved" in actions

    # ════════════════════════════════════════════════════════════════
    # Phase 8: Scheduler auto-approve catch-up (simulate)
    # ════════════════════════════════════════════════════════════════

    # Insert another draft dated yesterday to test auto-approve path
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, status, tag, summary, full_summary, "
        "time_spent_sec, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (YESTERDAY, "PLS-100", "pending_review", "daily", MOCK_LLM_ISSUES_JSON, "Yesterday summary", 25200),
    )
    yesterday_draft = await db.fetch_one(
        "SELECT id FROM worklog_drafts WHERE date = ? AND tag = 'daily' AND status = 'pending_review'",
        (YESTERDAY,),
    )
    assert yesterday_draft is not None
    yesterday_id = yesterday_draft["id"]

    # Run auto_approve_pending directly (simulates scheduler trigger)
    from auto_daily_log.scheduler.jobs import DailyWorkflow
    from auto_daily_log.config import AutoApproveConfig

    auto_cfg = AutoApproveConfig(enabled=True, trigger_time="21:30")
    workflow = DailyWorkflow(db, MagicMock(), auto_cfg)
    await workflow.auto_approve_pending(YESTERDAY)

    # Verify: yesterday's draft should be auto_approved (has non-empty issues)
    row = await db.fetch_one("SELECT status FROM worklog_drafts WHERE id = ?", (yesterday_id,))
    assert row["status"] == "auto_approved"

    # ════════════════════════════════════════════════════════════════
    # Phase 9: Jira submission (mock Jira client)
    # ════════════════════════════════════════════════════════════════

    mock_jira = AsyncMock()
    mock_jira.submit_worklog = AsyncMock(return_value={"id": "12345", "self": "https://jira.test.com/rest/api/2/issue/PLS-100/worklog/12345"})

    with patch("auto_daily_log.jira_client.client.build_jira_client_from_db",
               return_value=mock_jira):
        r = await http.post(f"/api/worklogs/{draft_id_2}/submit")

    assert r.status_code == 200

    # Verify: draft status = submitted
    r = await http.get("/api/worklogs", params={"date": TODAY})
    submitted_drafts = [d for d in r.json() if d["status"] == "submitted"]
    assert len(submitted_drafts) >= 1

    # Verify: jira_worklog_id written back into issue entries
    submitted_issues = json.loads(submitted_drafts[0]["summary"])
    has_worklog_id = any(i.get("jira_worklog_id") for i in submitted_issues)
    assert has_worklog_id == True

    # Verify audit trail includes submission
    r = await http.get(f"/api/worklogs/{draft_id_2}/audit")
    actions = [a["action"] for a in r.json()]
    assert "submitted" in actions

    # Dashboard should show submitted hours
    r = await http.get("/api/dashboard", params={"target_date": TODAY})
    dash = r.json()
    assert dash["submitted_hours"] > 0

    # Extended dashboard: submitted count
    r = await http.get("/api/dashboard/extended", params={"date": TODAY})
    ext = r.json()
    assert ext["submitted_jira_count"] >= 1

    # ════════════════════════════════════════════════════════════════
    # Phase 10: Scheduler catch-up logic
    # ════════════════════════════════════════════════════════════════

    from auto_daily_log.app import Application

    app_inst = Application.__new__(Application)
    app_inst.db = db

    # Simulate: server starts after both trigger times, but drafts exist
    # → catch-up should NOT re-run (already have output)
    gen_fn = AsyncMock()
    approve_fn = AsyncMock()

    now = datetime.now().replace(hour=22, minute=0, second=0)
    with patch("auto_daily_log.app.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app_inst._scheduler_catchup(18, 0, gen_fn, approve_fn, 21, 30)

    # Should NOT re-run because drafts already exist for today
    gen_fn.assert_not_called()

    # Now test the opposite: remove all today's drafts, catch-up should fire
    await db.execute("DELETE FROM worklog_drafts WHERE date = ?", (TODAY,))
    gen_fn2 = AsyncMock()
    approve_fn2 = AsyncMock()

    with patch("auto_daily_log.app.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await app_inst._scheduler_catchup(18, 0, gen_fn2, approve_fn2, 21, 30)

    gen_fn2.assert_called_once()
    approve_fn2.assert_called_once()

    # ════════════════════════════════════════════════════════════════
    # Phase 11: Delete draft
    # ════════════════════════════════════════════════════════════════

    # Re-insert a draft to delete
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, status, tag, summary, full_summary, "
        "time_spent_sec, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (TODAY, "PLS-DEL", "pending_review", "daily", "[]", "", 0),
    )
    del_draft = await db.fetch_one(
        "SELECT id FROM worklog_drafts WHERE date = ? AND status = 'pending_review'", (TODAY,)
    )
    r = await http.delete(f"/api/worklogs/{del_draft['id']}")
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"

    # Verify gone
    row = await db.fetch_one("SELECT id FROM worklog_drafts WHERE id = ?", (del_draft["id"],))
    assert row is None

    # ════════════════════════════════════════════════════════════════
    # Phase 12: Verify all key APIs still return 200 (regression gate)
    # ════════════════════════════════════════════════════════════════

    api_checks = [
        ("GET", "/api/dashboard", {"target_date": TODAY}),
        ("GET", "/api/dashboard/extended", {"date": TODAY}),
        ("GET", "/api/activities", {"target_date": TODAY}),
        ("GET", "/api/activities/dates", {}),
        ("GET", "/api/activities/timeline", {"hours": "12", "bucket": "15m"}),
        ("GET", "/api/activities/recent", {"limit": "5"}),
        ("GET", "/api/worklogs", {"date": TODAY}),
        ("GET", "/api/worklogs/drafts/preview", {"limit": "3"}),
        ("GET", "/api/issues", {}),
        ("GET", "/api/settings", {}),
        ("GET", "/api/settings/default-prompts", {}),
        ("GET", "/api/collectors", {}),
        ("GET", "/api/machines/status", {}),
        ("GET", "/", {}),  # Frontend index.html
    ]

    for method, path, params in api_checks:
        r = await http.request(method, path, params=params)
        assert r.status_code == 200, f"{method} {path} returned {r.status_code}: {r.text[:200]}"
