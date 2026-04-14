"""Two-step pipeline + 21:00 submit + full_summary field tests.

Covers:
- DB migration: full_summary column exists
- Summarizer: 2 LLM calls (SUMMARIZE then AUTO_APPROVE), stores both
- API update: full_summary editable via PATCH
- Submit: started timestamp always uses draft.date + T21:00
- Scheduler auto-approve: flips pending→auto_approved without LLM
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from auto_daily_log.config import AutoApproveConfig
from auto_daily_log.models.database import Database
from auto_daily_log.scheduler.jobs import DailyWorkflow
from auto_daily_log.summarizer.summarizer import WorklogSummarizer
from auto_daily_log.web.api.worklogs import _get_started_timestamp
from auto_daily_log.web.app import create_app


async def _setup(tmp_path: Path):
    db = Database(tmp_path / "t.db", embedding_dimensions=128)
    await db.initialize()
    return db


# ─── DB migration ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_summary_column_exists(tmp_path):
    db = await _setup(tmp_path)
    cols = await db.fetch_all("PRAGMA table_info(worklog_drafts)")
    names = [c["name"] for c in cols]
    assert "full_summary" in names
    await db.close()


# ─── Submit timestamp (always date+T21:00) ───────────────────────────

@pytest.mark.asyncio
async def test_started_timestamp_always_21_regardless_of_activities(tmp_path):
    db = await _setup(tmp_path)
    # Insert activities at various times — they must NOT influence started
    await db.execute(
        "INSERT INTO activities (timestamp, duration_sec, machine_id) VALUES (?, ?, 'local')",
        ("2026-04-14T07:00:00", 30),
    )
    await db.execute(
        "INSERT INTO activities (timestamp, duration_sec, machine_id) VALUES (?, ?, 'local')",
        ("2026-04-14T15:30:00", 30),
    )

    started = await _get_started_timestamp(db, "2026-04-14")
    assert started == "2026-04-14T21:00:00.000+0800"

    # Historical date also fixed at 21:00
    started_hist = await _get_started_timestamp(db, "2025-01-01")
    assert started_hist == "2025-01-01T21:00:00.000+0800"
    await db.close()


# ─── Summarizer 2-step pipeline ──────────────────────────────────────

class _FakeTwoStepEngine:
    """Fake LLM that returns step 1 text then step 2 JSON on successive calls."""
    name = "fake"

    def __init__(self, full_text: str, issues_json: list[dict]):
        self._calls = 0
        self._full_text = full_text
        self._issues_json = issues_json

    async def generate(self, prompt: str) -> str:
        self._calls += 1
        if self._calls == 1:
            return self._full_text
        return json.dumps(self._issues_json, ensure_ascii=False)


@pytest.mark.asyncio
async def test_summarizer_two_steps_store_both_outputs(tmp_path):
    db = await _setup(tmp_path)
    # Seed activities + issue
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, category, duration_sec, machine_id) "
        "VALUES (?, ?, ?, ?, 'local')",
        ("2026-04-14T10:00:00", "Xcode", "coding", 3600),
    )
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, is_active) VALUES ('PLS-1', 'Q1 task', 1)"
    )

    full_text = "今天用 Xcode 开发 1 小时，刷 B 站 15 分钟。"
    refined = [{"issue_key": "PLS-1", "time_spent_hours": 1.0, "summary": "推进 Q1 任务"}]

    engine = _FakeTwoStepEngine(full_text, refined)
    summarizer = WorklogSummarizer(db, engine)
    result = await summarizer.generate_drafts("2026-04-14")

    # 2 LLM calls made
    assert engine._calls == 2

    # Returned result
    assert len(result) == 1
    assert result[0]["full_summary"] == full_text

    # DB state: full_summary + per-issue JSON both stored
    row = await db.fetch_one(
        "SELECT full_summary, summary, issue_key, time_spent_sec FROM worklog_drafts WHERE id = ?",
        (result[0]["id"],),
    )
    assert row["full_summary"] == full_text
    assert row["issue_key"] == "DAILY"
    assert row["time_spent_sec"] == 3600  # 1.0h

    entries = json.loads(row["summary"])
    assert len(entries) == 1
    assert entries[0]["issue_key"] == "PLS-1"
    assert entries[0]["time_spent_hours"] == 1.0
    assert entries[0]["summary"] == "推进 Q1 任务"
    assert entries[0]["jira_worklog_id"] is None

    await db.close()


@pytest.mark.asyncio
async def test_summarizer_skips_when_no_activities(tmp_path):
    db = await _setup(tmp_path)
    engine = _FakeTwoStepEngine("", [])
    summarizer = WorklogSummarizer(db, engine)
    result = await summarizer.generate_drafts("2026-04-14")
    assert result == []
    # No LLM calls either
    assert engine._calls == 0
    await db.close()


@pytest.mark.asyncio
async def test_summarizer_step1_empty_aborts_without_writing(tmp_path):
    """If SUMMARIZE returns empty, skip step 2 and don't write any row."""
    db = await _setup(tmp_path)
    await db.execute(
        "INSERT INTO activities (timestamp, duration_sec, machine_id) VALUES (?, ?, 'local')",
        ("2026-04-14T10:00:00", 30),
    )
    engine = _FakeTwoStepEngine("", [])
    summarizer = WorklogSummarizer(db, engine)
    result = await summarizer.generate_drafts("2026-04-14")
    assert result == []
    assert engine._calls == 1  # only step 1 called
    rows = await db.fetch_all("SELECT * FROM worklog_drafts")
    assert rows == []
    await db.close()


# ─── PATCH full_summary via API ──────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_full_summary_updates_field(tmp_path):
    db = await _setup(tmp_path)
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, full_summary, status, tag) "
        "VALUES ('2026-04-14', 'DAILY', 3600, '[]', 'original', 'pending_review', 'daily')"
    )
    draft_id = (await db.fetch_one("SELECT id FROM worklog_drafts LIMIT 1"))["id"]

    app = create_app(db)
    client = TestClient(app)
    r = client.patch(f"/api/worklogs/{draft_id}", json={"full_summary": "edited text"})
    assert r.status_code == 200

    row = await db.fetch_one(
        "SELECT full_summary, user_edited FROM worklog_drafts WHERE id = ?", (draft_id,)
    )
    assert row["full_summary"] == "edited text"
    assert row["user_edited"] == 1
    await db.close()


# ─── Scheduler auto-approve: flip state without LLM ──────────────────

class _ShouldNotBeCalledEngine:
    name = "noop"
    async def generate(self, prompt: str) -> str:
        raise AssertionError("LLM should NOT be called during scheduler auto-approve")


@pytest.mark.asyncio
async def test_scheduler_auto_approve_flips_without_llm(tmp_path):
    db = await _setup(tmp_path)
    entries = [{"issue_key": "PLS-1", "time_spent_hours": 1.0, "summary": "x", "jira_worklog_id": None}]
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, full_summary, status, tag) "
        "VALUES (?, 'DAILY', 3600, ?, 'full text', 'pending_review', 'daily')",
        ("2026-04-14", json.dumps(entries)),
    )

    workflow = DailyWorkflow(
        db, _ShouldNotBeCalledEngine(),
        AutoApproveConfig(enabled=True, trigger_time="21:30"),
    )
    await workflow.auto_approve_pending("2026-04-14")

    row = await db.fetch_one("SELECT status FROM worklog_drafts WHERE date = '2026-04-14'")
    assert row["status"] == "auto_approved"
    await db.close()


@pytest.mark.asyncio
async def test_scheduler_auto_approve_skips_empty_entries(tmp_path):
    """Drafts whose per-issue JSON is empty shouldn't be auto-approved."""
    db = await _setup(tmp_path)
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, full_summary, status, tag) "
        "VALUES ('2026-04-14', 'DAILY', 0, '[]', 'only watched videos', 'pending_review', 'daily')"
    )

    workflow = DailyWorkflow(
        db, _ShouldNotBeCalledEngine(),
        AutoApproveConfig(enabled=True, trigger_time="21:30"),
    )
    await workflow.auto_approve_pending("2026-04-14")

    row = await db.fetch_one("SELECT status FROM worklog_drafts WHERE date = '2026-04-14'")
    assert row["status"] == "pending_review"  # unchanged

    # audit log records the skip
    audit = await db.fetch_all("SELECT action FROM audit_logs")
    assert any(a["action"] == "auto_skipped" for a in audit)
    await db.close()
