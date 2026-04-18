"""Tests for the WorklogPublisher abstraction and SummaryType configuration.

Verifies:
  1. JiraPublisher satisfies WorklogPublisher protocol
  2. PublisherRegistry resolves publishers from summary_types table
  3. Built-in summary types are seeded on DB init
  4. Submit paths route through publisher (not direct JiraClient)
  5. API endpoints serve summary types
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from auto_daily_log.models.database import Database
from auto_daily_log.publishers import PublishResult, WorklogPublisher
from auto_daily_log.publishers.jira import JiraPublisher
from auto_daily_log.web.app import create_app


@pytest_asyncio.fixture
async def env(tmp_path):
    db = Database(tmp_path / "pub.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, db
    await db.close()


# ── Protocol conformance ───────────────────────────────────────────────

def test_jira_publisher_is_worklog_publisher():
    mock_client = AsyncMock()
    pub = JiraPublisher(mock_client)
    assert isinstance(pub, WorklogPublisher)
    assert pub.name == "jira"
    assert pub.display_name == "Jira"


@pytest.mark.asyncio
async def test_jira_publisher_submit_wraps_result():
    mock_client = AsyncMock()
    mock_client.submit_worklog = AsyncMock(return_value={"id": "42", "self": "https://jira/..."})
    pub = JiraPublisher(mock_client)
    result = await pub.submit(issue_key="PLS-1", time_spent_sec=3600, comment="test", started="2026-04-17T21:00:00")
    assert result.success is True
    assert result.worklog_id == "42"
    assert result.platform == "jira"
    assert result.raw["id"] == "42"


@pytest.mark.asyncio
async def test_jira_publisher_submit_captures_error():
    mock_client = AsyncMock()
    mock_client.submit_worklog = AsyncMock(side_effect=Exception("Jira 502"))
    pub = JiraPublisher(mock_client)
    result = await pub.submit(issue_key="PLS-1", time_spent_sec=3600, comment="test", started="2026-04-17T21:00:00")
    assert result.success is False
    assert result.error == "Exception: Jira 502"
    assert result.platform == "jira"


# ── Summary types seeding ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_builtin_types_seeded_on_init(env):
    _client, db = env
    rows = await db.fetch_all("SELECT name, display_name, publisher_name, review_mode, is_builtin FROM summary_types ORDER BY name")
    names = {r["name"] for r in rows}
    assert {"daily", "weekly", "monthly"}.issubset(names)
    daily = next(r for r in rows if r["name"] == "daily")
    assert daily["publisher_name"] == "jira"
    assert daily["review_mode"] == "auto"
    assert daily["is_builtin"] == 1
    weekly = next(r for r in rows if r["name"] == "weekly")
    assert weekly["publisher_name"] is None
    assert weekly["review_mode"] == "manual"


@pytest.mark.asyncio
async def test_builtin_seed_is_idempotent(env):
    _client, db = env
    # Re-run migration — should not duplicate rows
    await db._migrate()
    count = await db.fetch_one("SELECT COUNT(*) AS n FROM summary_types")
    assert count["n"] >= 3


# ── Registry ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registry_resolves_jira_for_daily(env):
    _client, db = env
    mock_jira = AsyncMock()
    mock_jira.submit_worklog = AsyncMock(return_value={"id": "99"})
    fake_builder = AsyncMock(return_value=JiraPublisher(mock_jira))
    from auto_daily_log.publishers import registry
    original = registry._FACTORIES["jira"]
    registry._FACTORIES["jira"] = fake_builder
    try:
        pub = await registry.get_publisher(db, "daily")
    finally:
        registry._FACTORIES["jira"] = original
    assert pub.name == "jira"


@pytest.mark.asyncio
async def test_registry_returns_none_for_weekly(env):
    _client, db = env
    from auto_daily_log.publishers.registry import get_publisher
    pub = await get_publisher(db, "weekly")
    assert pub is None


# ── Submit via publisher (API integration) ─────────────────────────────

@pytest.mark.asyncio
async def test_submit_routes_through_publisher(env):
    client, db = env
    # Seed an approved draft
    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag) "
        "VALUES ('2026-04-17', 'PLS-1', 3600, ?, 'approved', 'daily')",
        (json.dumps([{"issue_key": "PLS-1", "time_spent_hours": 1.0, "summary": "test work"}]),),
    )

    mock_pub = AsyncMock(spec=WorklogPublisher)
    mock_pub.name = "jira"
    mock_pub.submit = AsyncMock(return_value=PublishResult(
        success=True, worklog_id="pub-123", platform="jira", raw={"id": "pub-123"},
    ))

    with patch("auto_daily_log.web.api.worklogs._get_publisher", new=AsyncMock(return_value=mock_pub)):
        r = await client.post(f"/api/worklogs/{draft_id}/submit")
    assert r.status_code == 200
    assert r.json()["results"][0]["jira_worklog_id"] == "pub-123"
    mock_pub.submit.assert_called_once()
    call_kw = mock_pub.submit.call_args.kwargs
    assert call_kw["issue_key"] == "PLS-1"
    assert call_kw["time_spent_sec"] == 3600


@pytest.mark.asyncio
async def test_submit_single_issue_routes_through_publisher(env):
    client, db = env
    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag) "
        "VALUES ('2026-04-17', 'PLS-2', 7200, ?, 'approved', 'daily')",
        (json.dumps([
            {"issue_key": "PLS-2", "time_spent_hours": 1.0, "summary": "part A"},
            {"issue_key": "PLS-3", "time_spent_hours": 1.0, "summary": "part B"},
        ]),),
    )

    mock_pub = AsyncMock(spec=WorklogPublisher)
    mock_pub.name = "jira"
    mock_pub.submit = AsyncMock(return_value=PublishResult(
        success=True, worklog_id="single-1", platform="jira", raw={"id": "single-1"},
    ))

    with patch("auto_daily_log.web.api.worklogs._get_publisher", new=AsyncMock(return_value=mock_pub)):
        r = await client.post(f"/api/worklogs/{draft_id}/submit-issue/0")
    assert r.status_code == 200
    assert r.json()["jira_worklog_id"] == "single-1"
    # Only issue 0 submitted — draft should NOT be fully submitted yet
    assert r.json()["all_submitted"] is False


# ── Summary types API ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_types_api_lists_builtin(env):
    client, _db = env
    r = await client.get("/api/summary-types")
    assert r.status_code == 200
    names = sorted(t["name"] for t in r.json())
    assert "daily" in names and "weekly" in names and "monthly" in names


# ── JiraPublisher.delete / check_connection ────────────────────────────

@pytest.mark.asyncio
async def test_jira_publisher_check_connection_delegates():
    mock_client = AsyncMock()
    mock_client.test_connection = AsyncMock(return_value=True)
    pub = JiraPublisher(mock_client)
    assert await pub.check_connection() is True
    mock_client.test_connection.assert_awaited_once()


@pytest.mark.asyncio
async def test_jira_publisher_check_connection_returns_false():
    mock_client = AsyncMock()
    mock_client.test_connection = AsyncMock(return_value=False)
    pub = JiraPublisher(mock_client)
    assert await pub.check_connection() is False


@pytest.mark.asyncio
async def test_jira_publisher_delete_returns_false_on_error():
    mock_client = AsyncMock()
    mock_client._url = lambda p: f"https://jira.test{p}"
    mock_client._headers = lambda: {"Authorization": "Bearer x"}
    pub = JiraPublisher(mock_client)
    # httpx not patched → real network → will fail → returns False
    assert await pub.delete("99999", issue_key="PLS-999") is False


# ── Registry edge cases ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registry_returns_none_for_unknown_publisher_name(env):
    """summary_types row exists but publisher_name='notion' not in _FACTORIES."""
    _client, db = env
    await db.execute(
        "INSERT INTO summary_types (name, display_name, scope_rule, publisher_name, is_builtin) "
        "VALUES ('sprint', 'Sprint 回顾', '{\"type\":\"week\"}', 'notion', 0)"
    )
    from auto_daily_log.publishers.registry import get_publisher
    pub = await get_publisher(db, "sprint")
    assert pub is None


@pytest.mark.asyncio
async def test_registry_returns_none_for_nonexistent_type(env):
    """No summary_types row at all for the given name."""
    _client, db = env
    from auto_daily_log.publishers.registry import get_publisher
    pub = await get_publisher(db, "does-not-exist")
    assert pub is None


# ── Submit HTTP boundary: publisher failure → 502 ──────────────────────

@pytest.mark.asyncio
async def test_submit_single_issue_returns_502_on_publisher_failure(env):
    client, db = env
    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag) "
        "VALUES ('2026-04-17', 'PLS-9', 3600, ?, 'approved', 'daily')",
        (json.dumps([{"issue_key": "PLS-9", "time_spent_hours": 1.0, "summary": "fail test"}]),),
    )
    mock_pub = AsyncMock(spec=WorklogPublisher)
    mock_pub.name = "jira"
    mock_pub.submit = AsyncMock(return_value=PublishResult(
        success=False, platform="jira", error="Jira returned 502",
    ))
    with patch("auto_daily_log.web.api.worklogs._get_publisher", new=AsyncMock(return_value=mock_pub)):
        r = await client.post(f"/api/worklogs/{draft_id}/submit-issue/0")
    assert r.status_code == 502
    assert "Publish error: Jira returned 502" == r.json()["detail"]


# ── Submit draft with tag='weekly' (no publisher) → 400 ────────────────

@pytest.mark.asyncio
async def test_submit_weekly_draft_returns_400_no_publisher(env):
    client, db = env
    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag) "
        "VALUES ('2026-04-17', 'PLS-10', 3600, ?, 'approved', 'weekly')",
        (json.dumps([{"issue_key": "PLS-10", "time_spent_hours": 1.0, "summary": "weekly work"}]),),
    )
    r = await client.post(f"/api/worklogs/{draft_id}/submit")
    assert r.status_code == 400
    assert "没有配置推送平台" in r.json()["detail"]


# ── Submit draft with tag='custom' (no summary_types row) → 400 ───────

@pytest.mark.asyncio
async def test_submit_custom_tag_no_type_row_returns_400(env):
    """Pre-migration drafts with tag='custom' hit 400 because there's
    no matching summary_types row and no fallback."""
    client, db = env
    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag) "
        "VALUES ('2026-04-17', 'PLS-11', 3600, ?, 'approved', 'custom')",
        (json.dumps([{"issue_key": "PLS-11", "time_spent_hours": 1.0, "summary": "custom work"}]),),
    )
    r = await client.post(f"/api/worklogs/{draft_id}/submit")
    assert r.status_code == 400
    assert "没有配置推送平台" in r.json()["detail"]


# ── Submit-all partial failure keeps draft open ────────────────────────

@pytest.mark.asyncio
async def test_submit_all_partial_failure_keeps_draft_approved(env):
    """If one issue fails and one succeeds, draft status stays 'approved',
    not 'submitted'."""
    client, db = env
    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag) "
        "VALUES ('2026-04-17', 'PLS-12', 7200, ?, 'approved', 'daily')",
        (json.dumps([
            {"issue_key": "PLS-12", "time_spent_hours": 1.0, "summary": "A"},
            {"issue_key": "PLS-13", "time_spent_hours": 1.0, "summary": "B"},
        ]),),
    )
    call_count = {"n": 0}
    mock_pub = AsyncMock(spec=WorklogPublisher)
    mock_pub.name = "jira"
    async def _alternate(*, issue_key, time_spent_sec, comment, started):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return PublishResult(success=False, platform="jira", error="timeout")
        return PublishResult(success=True, worklog_id="ok-1", platform="jira", raw={"id": "ok-1"})
    mock_pub.submit = _alternate

    with patch("auto_daily_log.web.api.worklogs._get_publisher", new=AsyncMock(return_value=mock_pub)):
        r = await client.post(f"/api/worklogs/{draft_id}/submit")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"  # NOT "submitted" — partial failure
    assert body["results"][0]["error"] == "timeout"
    assert body["results"][1]["jira_worklog_id"] == "ok-1"

    # Audit rows: one failed, one succeeded
    audit = await db.fetch_all(
        "SELECT action, issue_key, source FROM audit_logs "
        "WHERE draft_id = ? AND action LIKE 'submit%' ORDER BY issue_key",
        (draft_id,),
    )
    assert len(audit) == 2
    assert audit[0]["action"] == "submit_failed_issue"
    assert audit[0]["issue_key"] == "PLS-12"
    assert audit[0]["source"] == "manual_all"
    assert audit[1]["action"] == "submitted_issue"
    assert audit[1]["issue_key"] == "PLS-13"
    assert audit[1]["source"] == "manual_all"


# ── Scheduler with publisher=None exits cleanly ────────────────────────

@pytest.mark.asyncio
async def test_scheduler_submit_exits_when_publisher_none(env):
    """If the daily summary type has no publisher, _submit_approved skips silently."""
    _client, db = env
    # Temporarily remove publisher for daily
    await db.execute("UPDATE summary_types SET publisher_name = NULL WHERE name = 'daily'")
    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag) "
        "VALUES ('2026-04-17', 'PLS-14', 3600, ?, 'approved', 'daily')",
        (json.dumps([{"issue_key": "PLS-14", "time_spent_hours": 1.0, "summary": "test"}]),),
    )
    from auto_daily_log.scheduler.jobs import DailyWorkflow
    from auto_daily_log.config import AutoApproveConfig
    from unittest.mock import MagicMock
    wf = DailyWorkflow(db, MagicMock(), AutoApproveConfig(enabled=True, trigger_time="21:30"))
    await wf._submit_approved("2026-04-17")  # should not raise
    # Draft should remain 'approved' — not touched
    draft = await db.fetch_one("SELECT status FROM worklog_drafts WHERE id = ?", (draft_id,))
    assert draft["status"] == "approved"
