import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from auto_daily_log.scheduler.jobs import DailyWorkflow
from auto_daily_log.models.database import Database
from auto_daily_log.config import AutoApproveConfig


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_auto_approve_approves_good_draft(db):
    draft_id = await db.execute(
        """INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status)
           VALUES ('2026-04-12', 'PROJ-101', 3600, '修复了SQL解析', 'pending_review')"""
    )
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, is_active) VALUES ('PROJ-101', 'Fix SQL', 1)"
    )

    mock_engine = AsyncMock()
    mock_engine.generate.return_value = json.dumps({"approved": True})

    config = AutoApproveConfig(enabled=True, timeout_min=30)
    workflow = DailyWorkflow(db, mock_engine, config)
    await workflow.auto_approve_pending("2026-04-12")

    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    assert draft["status"] == "auto_approved"

    logs = await db.fetch_all("SELECT * FROM audit_logs WHERE draft_id = ?", (draft_id,))
    assert any(l["action"] == "auto_approved" for l in logs)


@pytest.mark.asyncio
async def test_auto_approve_rejects_bad_draft(db):
    draft_id = await db.execute(
        """INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status)
           VALUES ('2026-04-12', 'PROJ-101', 3600, '做了一些事情', 'pending_review')"""
    )
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, is_active) VALUES ('PROJ-101', 'Fix SQL', 1)"
    )

    mock_engine = AsyncMock()
    mock_engine.generate.return_value = json.dumps(
        {"approved": False, "reason": "日志内容过于笼统"}
    )

    config = AutoApproveConfig(enabled=True, timeout_min=30)
    workflow = DailyWorkflow(db, mock_engine, config)
    await workflow.auto_approve_pending("2026-04-12")

    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    assert draft["status"] == "pending_review"

    logs = await db.fetch_all("SELECT * FROM audit_logs WHERE draft_id = ?", (draft_id,))
    assert any(l["action"] == "auto_rejected" for l in logs)
