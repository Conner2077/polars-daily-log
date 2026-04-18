"""Tests for /api/summaries/* endpoints and generate_scope pipeline."""
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from auto_daily_log.models.database import Database
from auto_daily_log.publishers import PublishResult
from auto_daily_log.web.app import create_app


TODAY = datetime.now().strftime("%Y-%m-%d")

MOCK_FULL_SUMMARY = "今天在 VS Code 中开发 UI 重构，下午调试 CI。"
MOCK_ISSUES_JSON = json.dumps([
    {"issue_key": "PLS-100", "time_spent_hours": 5.0, "summary": "UI 重构"},
    {"issue_key": "PLS-101", "time_spent_hours": 2.0, "summary": "CI 调试"},
])


@pytest_asyncio.fixture
async def env(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    app.state.db = db
    transport = ASGITransport(app=app)

    # Seed activity + commit data for today
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, duration_sec) "
        "VALUES (?, 'VS Code', 'main.py', 'coding', 3600)",
        (f"{TODAY}T10:00:00",),
    )
    await db.execute(
        "INSERT INTO git_commits (repo_id, hash, message, author, committed_at, date) "
        "VALUES (NULL, 'abc123', 'feat: add pipeline', 'conner', ?, ?)",
        (f"{TODAY}T11:00:00", TODAY),
    )
    # Seed active jira issues
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, description, is_active) VALUES ('PLS-100', 'UI重构', '重构前端', 1)"
    )
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, description, is_active) VALUES ('PLS-101', 'CI修复', '修CI', 1)"
    )

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, db, app
    await db.close()


def _mock_engine():
    """Mock LLM engine that returns full summary first, then issues JSON."""
    engine = AsyncMock()
    engine.generate = AsyncMock(side_effect=[MOCK_FULL_SUMMARY, MOCK_ISSUES_JSON])
    return engine


# ── generate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_daily_creates_summaries(env):
    client, db, app = env
    engine = _mock_engine()

    with patch("auto_daily_log.web.api.summaries._get_llm_engine", return_value=engine), \
         patch("auto_daily_log.web.api.summaries.generate_scope") as mock_gen:
        # Make generate_scope return predictable data
        mock_gen.return_value = [
            {"id": 1, "scope_name": "daily", "output_id": 1, "date": TODAY, "content": MOCK_FULL_SUMMARY},
            {"id": 2, "scope_name": "daily", "output_id": 2, "date": TODAY,
             "issue_key": "PLS-100", "time_spent_sec": 18000, "content": "UI 重构"},
        ]

        r = await client.post("/api/summaries/generate", json={
            "scope_name": "daily",
            "target_date": TODAY,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["scope_name"] == "daily"
        assert body["summaries_created"] == 2


@pytest.mark.asyncio
async def test_generate_daily_conflict_409(env):
    client, db, app = env

    # Insert existing summary
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' LIMIT 1")
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'existing')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    r = await client.post("/api/summaries/generate", json={
        "scope_name": "daily",
        "target_date": TODAY,
    })
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_generate_daily_force_overwrites(env):
    client, db, app = env

    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' LIMIT 1")
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'old')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    with patch("auto_daily_log.web.api.summaries._get_llm_engine", return_value=None), \
         patch("auto_daily_log.web.api.summaries.generate_scope", return_value=[]):
        r = await client.post("/api/summaries/generate", json={
            "scope_name": "daily",
            "target_date": TODAY,
            "force": True,
        })
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_generate_nonexistent_scope_404(env):
    client, db, app = env
    r = await client.post("/api/summaries/generate", json={
        "scope_name": "nonexistent",
    })
    assert r.status_code == 404


# ── list / get ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_summaries(env):
    client, db, app = env
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' AND output_mode = 'single'")
    await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'test content')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    r = await client.get(f"/api/summaries?scope_name=daily&date={TODAY}")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["content"] == "test content"
    assert data[0]["output_display_name"] == "原汁原味日志"


@pytest.mark.asyncio
async def test_get_summary(env):
    client, db, app = env
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' AND output_mode = 'single'")
    sid = await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'detail test')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    r = await client.get(f"/api/summaries/{sid}")
    assert r.status_code == 200
    assert r.json()["content"] == "detail test"


@pytest.mark.asyncio
async def test_get_summary_not_found(env):
    client, db, app = env
    r = await client.get("/api/summaries/99999")
    assert r.status_code == 404


# ── update ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_summary(env):
    client, db, app = env
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' AND output_mode = 'single'")
    sid = await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'before')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    r = await client.patch(f"/api/summaries/{sid}", json={"content": "after edit"})
    assert r.status_code == 200

    r2 = await client.get(f"/api/summaries/{sid}")
    assert r2.json()["content"] == "after edit"


# ── publish ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_summary(env):
    client, db, app = env
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' AND output_mode = 'per_issue'")
    sid = await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, issue_key, time_spent_sec, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'PLS-100', 18000, 'UI 重构')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    mock_publisher = AsyncMock()
    mock_publisher.submit = AsyncMock(return_value=PublishResult(
        success=True, worklog_id="wl-999", platform="jira", raw={}
    ))

    with patch("auto_daily_log.publishers.registry.get_publisher_for_output", return_value=mock_publisher):
        r = await client.post(f"/api/summaries/{sid}/publish")
        assert r.status_code == 200
        assert r.json()["worklog_id"] == "wl-999"

    # Verify DB updated
    row = await db.fetch_one("SELECT * FROM summaries WHERE id = ?", (sid,))
    assert row["published_id"] == "wl-999"
    assert row["publisher_name"] == "jira"


@pytest.mark.asyncio
async def test_publish_summary_no_publisher_400(env):
    client, db, app = env
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' AND output_mode = 'single'")
    sid = await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'no publisher')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    r = await client.post(f"/api/summaries/{sid}/publish")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_publish_already_published_400(env):
    client, db, app = env
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' AND output_mode = 'per_issue'")
    sid = await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, issue_key, time_spent_sec, content, published_id) "
        "VALUES ('daily', ?, ?, ?, ?, 'PLS-100', 18000, 'done', 'wl-old')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    r = await client.post(f"/api/summaries/{sid}/publish")
    assert r.status_code == 400


# ── delete ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_summary(env):
    client, db, app = env
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' AND output_mode = 'single'")
    sid = await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'to delete')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )

    r = await client.delete(f"/api/summaries/{sid}")
    assert r.status_code == 200

    r2 = await client.get(f"/api/summaries/{sid}")
    assert r2.status_code == 404


# ── audit trail ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_audit_trail(env):
    client, db, app = env
    outputs = await db.fetch_all("SELECT * FROM scope_outputs WHERE scope_name = 'daily' AND output_mode = 'single'")
    sid = await db.execute(
        "INSERT INTO summaries (scope_name, output_id, date, period_start, period_end, content) "
        "VALUES ('daily', ?, ?, ?, ?, 'auditable')",
        (outputs[0]["id"], TODAY, TODAY, TODAY),
    )
    await db.execute(
        "INSERT INTO audit_logs (summary_id, action) VALUES (?, 'created')", (sid,)
    )

    r = await client.get(f"/api/summaries/{sid}/audit")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["action"] == "created"
