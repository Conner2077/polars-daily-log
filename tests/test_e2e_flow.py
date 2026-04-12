"""
End-to-end integration test: simulates the full daily workflow
without external dependencies (no real Jira, no real LLM).
"""
import json
import pytest
import pytest_asyncio
from datetime import date
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from auto_daily_log.models.database import Database
from auto_daily_log.web.app import create_app


@pytest_asyncio.fixture
async def client(tmp_path):
    db = Database(tmp_path / "e2e_test.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    app.state.db = db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, db
    await db.close()


@pytest.mark.asyncio
async def test_full_daily_workflow(client):
    """
    Full flow:
    1. Configure settings
    2. Add Jira issues
    3. Add git repo
    4. Simulate activity data + git commits
    5. Generate worklog drafts via summarizer
    6. Edit a draft
    7. Approve drafts
    8. Verify audit trail
    9. Dashboard shows correct stats
    """
    http, db = client

    # ── Step 1: Configure settings ──
    settings = {
        "jira_server_url": "https://jira.test.com",
        "jira_pat": "test-pat-token",
        "llm_engine": "kimi",
        "llm_api_key": "test-key",
        "monitor_interval_sec": "30",
        "monitor_ocr_enabled": "true",
        "scheduler_enabled": "true",
        "scheduler_trigger_time": "18:00",
        "auto_approve_enabled": "true",
        "auto_approve_timeout_min": "30",
    }
    for key, value in settings.items():
        resp = await http.put(f"/api/settings/{key}", json={"value": value})
        assert resp.status_code == 200

    # Verify settings saved
    resp = await http.get("/api/settings")
    assert resp.status_code == 200
    saved = {s["key"]: s["value"] for s in resp.json()}
    assert saved["jira_server_url"] == "https://jira.test.com"
    assert saved["llm_engine"] == "kimi"
    print("  [OK] Step 1: Settings configured")

    # ── Step 2: Add Jira issues ──
    issues = [
        {"issue_key": "PROJ-101", "summary": "Refactor SQL parser", "description": "Rewrite JOIN handling logic"},
        {"issue_key": "PROJ-102", "summary": "Fix login bug", "description": "Session timeout not working"},
        {"issue_key": "PROJ-103", "summary": "Write unit tests", "description": "Add tests for auth module"},
    ]
    for issue in issues:
        resp = await http.post("/api/issues", json=issue)
        assert resp.status_code == 201

    resp = await http.get("/api/issues")
    assert len(resp.json()) == 3
    assert all(i["is_active"] for i in resp.json())

    # Toggle one issue inactive
    resp = await http.patch("/api/issues/PROJ-103", json={"is_active": False})
    assert resp.status_code == 200
    print("  [OK] Step 2: 3 issues added, 1 toggled inactive")

    # ── Step 3: Add git repo ──
    resp = await http.post("/api/git-repos", json={"path": "/tmp/test_repo", "author_email": "conner@test.com"})
    assert resp.status_code == 201

    resp = await http.get("/api/git-repos")
    assert len(resp.json()) == 1
    print("  [OK] Step 3: Git repo configured")

    # ── Step 4: Simulate activity data + git commits ──
    today = date.today().isoformat()

    # Insert activities directly into DB
    activities = [
        (f"{today}T09:00:00", "IntelliJ IDEA", "AstToPlanConverter.java — polars", "coding", 0.92, None, 7200),
        (f"{today}T11:00:00", "Google Chrome", "PROJ-101 Refactor SQL - Jira", "browsing", 0.70, "https://jira.test.com/browse/PROJ-101", 1800),
        (f"{today}T11:30:00", "Zoom", "Sprint Review Meeting", "meeting", 0.95, None, 3600),
        (f"{today}T14:00:00", "IntelliJ IDEA", "LoginController.java — polars", "coding", 0.92, None, 5400),
        (f"{today}T15:30:00", "Google Chrome", "Stack Overflow - session timeout", "research", 0.70, "https://stackoverflow.com/q/12345", 1800),
        (f"{today}T16:00:00", "Slack", "dev-team channel", "communication", 0.85, None, 900),
    ]
    for ts, app, title, cat, conf, url, dur in activities:
        await db.execute(
            "INSERT INTO activities (timestamp, app_name, window_title, category, confidence, url, duration_sec) VALUES (?,?,?,?,?,?,?)",
            (ts, app, title, cat, conf, url, dur),
        )

    # Insert git commits directly
    commits = [
        ("abc1234", "fix: resolve JOIN qualified name parsing", "conner@test.com", f"{today}T10:32:00", '["AstToPlanConverter.java"]', 45, 12),
        ("def5678", "feat: add cross join WHERE clause support", "conner@test.com", f"{today}T10:58:00", '["AstToPlanConverter.java", "JoinHandler.java"]', 78, 5),
        ("ghi9012", "fix: session timeout not refreshing token", "conner@test.com", f"{today}T15:15:00", '["LoginController.java", "SessionManager.java"]', 32, 8),
    ]
    for h, msg, author, at, files, ins, dels in commits:
        await db.execute(
            "INSERT INTO git_commits (repo_id, hash, message, author, committed_at, files_changed, insertions, deletions, date) VALUES (?,?,?,?,?,?,?,?,?)",
            (1, h, msg, author, at, files, ins, dels, today),
        )
    print("  [OK] Step 4: 6 activities + 3 git commits simulated")

    # ── Step 5: Generate worklog drafts via summarizer ──
    from auto_daily_log.summarizer.summarizer import WorklogSummarizer

    mock_engine = AsyncMock()
    mock_engine.generate.return_value = json.dumps([
        {
            "issue_key": "PROJ-101",
            "time_spent_hours": 3.0,
            "summary": "重构SQL解析器，修复了JOIN限定名解析错误，新增cross join WHERE子句支持，完成代码review。",
        },
        {
            "issue_key": "PROJ-102",
            "time_spent_hours": 2.5,
            "summary": "修复登录模块session超时不刷新token的bug，排查了Stack Overflow上的相关方案并实现修复。",
        },
    ])

    summarizer = WorklogSummarizer(db, mock_engine)
    drafts = await summarizer.generate_drafts(today)
    assert len(drafts) == 2
    assert drafts[0]["issue_key"] == "PROJ-101"
    assert drafts[1]["issue_key"] == "PROJ-102"

    # Verify via API
    resp = await http.get(f"/api/worklogs?date={today}")
    assert resp.status_code == 200
    api_drafts = resp.json()
    assert len(api_drafts) == 2
    assert all(d["status"] == "pending_review" for d in api_drafts)
    print("  [OK] Step 5: 2 worklog drafts generated (pending_review)")

    # ── Step 6: Edit a draft ──
    draft_101 = next(d for d in api_drafts if d["issue_key"] == "PROJ-101")
    resp = await http.patch(f"/api/worklogs/{draft_101['id']}", json={
        "summary": "重构SQL解析器：修复JOIN限定名解析错误（AstToPlanConverter.java），新增cross join WHERE子句支持。通过code review。",
        "time_spent_sec": 12600,  # 3.5h
    })
    assert resp.status_code == 200

    # Verify edit
    resp = await http.get(f"/api/worklogs?date={today}")
    edited = next(d for d in resp.json() if d["issue_key"] == "PROJ-101")
    assert edited["user_edited"] == 1
    assert edited["time_spent_sec"] == 12600
    assert "code review" in edited["summary"]
    print("  [OK] Step 6: Draft PROJ-101 edited (3.5h, updated summary)")

    # ── Step 7: Approve drafts ──
    # Approve PROJ-101 manually
    resp = await http.post(f"/api/worklogs/{draft_101['id']}/approve")
    assert resp.status_code == 200

    # Approve all remaining (PROJ-102)
    resp = await http.post(f"/api/worklogs/approve-all?date={today}")
    assert resp.status_code == 200

    resp = await http.get(f"/api/worklogs?date={today}")
    assert all(d["status"] == "approved" for d in resp.json())
    print("  [OK] Step 7: All drafts approved")

    # ── Step 8: Test auto-approve flow (on a new draft) ──
    from auto_daily_log.scheduler.jobs import DailyWorkflow
    from auto_daily_log.config import AutoApproveConfig

    # Seed a new pending draft
    resp = await http.post("/api/worklogs/seed", json={
        "date": today, "issue_key": "PROJ-101",
        "time_spent_sec": 1800, "summary": "额外的调试工作",
    })
    new_draft_id = resp.json()["id"]

    auto_engine = AsyncMock()
    auto_engine.generate.return_value = json.dumps({"approved": True})
    config = AutoApproveConfig(enabled=True, timeout_min=30)
    workflow = DailyWorkflow(db, auto_engine, config)
    await workflow.auto_approve_pending(today)

    resp = await http.get(f"/api/worklogs?date={today}")
    auto_draft = next(d for d in resp.json() if d["id"] == new_draft_id)
    assert auto_draft["status"] == "auto_approved"
    print("  [OK] Step 8: Auto-approve workflow works")

    # ── Step 9: Verify audit trail ──
    resp = await http.get(f"/api/worklogs/{draft_101['id']}/audit")
    assert resp.status_code == 200
    audit = resp.json()
    actions = [a["action"] for a in audit]
    assert "created" in actions
    assert "edited" in actions
    assert "approved" in actions
    print(f"  [OK] Step 9: Audit trail has {len(audit)} entries: {actions}")

    # ── Step 10: Dashboard shows correct stats ──
    resp = await http.get(f"/api/dashboard?target_date={today}")
    assert resp.status_code == 200
    dash = resp.json()
    assert dash["date"] == today
    assert len(dash["activity_summary"]) > 0
    total_activity_sec = sum(a["total_sec"] for a in dash["activity_summary"])
    assert total_activity_sec == 20700  # 7200+1800+3600+5400+1800+900
    print(f"  [OK] Step 10: Dashboard shows {len(dash['activity_summary'])} categories, {total_activity_sec/3600:.1f}h total")

    # ── Step 11: Delete issue and verify ──
    resp = await http.delete("/api/issues/PROJ-103")
    assert resp.status_code == 200
    resp = await http.get("/api/issues")
    assert len(resp.json()) == 2
    print("  [OK] Step 11: Issue cleanup works")

    # ── Step 12: Reject flow ──
    resp = await http.post("/api/worklogs/seed", json={
        "date": today, "issue_key": "PROJ-102",
        "time_spent_sec": 900, "summary": "低质量日志",
    })
    reject_id = resp.json()["id"]
    resp = await http.post(f"/api/worklogs/{reject_id}/reject")
    assert resp.status_code == 200

    resp = await http.get(f"/api/worklogs?date={today}")
    rejected = next(d for d in resp.json() if d["id"] == reject_id)
    assert rejected["status"] == "rejected"
    print("  [OK] Step 12: Reject flow works")

    print("\n  === ALL 12 E2E STEPS PASSED ===")
