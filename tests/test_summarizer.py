import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from auto_daily_log.summarizer.summarizer import WorklogSummarizer
from auto_daily_log.summarizer.prompt import DEFAULT_SUMMARIZE_PROMPT, render_prompt
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


def test_default_prompt_has_placeholders():
    assert "{jira_issues}" in DEFAULT_SUMMARIZE_PROMPT
    assert "{git_commits}" in DEFAULT_SUMMARIZE_PROMPT
    assert "{activities}" in DEFAULT_SUMMARIZE_PROMPT
    assert "{date}" in DEFAULT_SUMMARIZE_PROMPT


def test_render_prompt():
    rendered = render_prompt(
        DEFAULT_SUMMARIZE_PROMPT,
        date="2026-04-12",
        jira_issues="- PROJ-101: Fix SQL parser",
        git_commits="- 10:30 fix: resolve JOIN issue",
        activities="- 9:00-11:00 IntelliJ (Main.java) coding",
    )
    assert "PROJ-101" in rendered
    assert "2026-04-12" in rendered
    assert "IntelliJ" in rendered


@pytest.mark.asyncio
async def test_summarizer_generates_drafts(db):
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, description, is_active) VALUES (?, ?, ?, ?)",
        ("PROJ-101", "Fix SQL parser", "Fix JOIN handling in parser", 1),
    )
    await db.execute(
        """INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("2026-04-12T10:00:00", "IntelliJ IDEA", "AstToPlanConverter.java", "coding", 0.92, 3600),
    )

    mock_engine = AsyncMock()
    mock_engine.generate.return_value = json.dumps([
        {"issue_key": "PROJ-101", "time_spent_hours": 1.0, "summary": "修复了SQL解析器的JOIN处理逻辑"}
    ])

    summarizer = WorklogSummarizer(db, mock_engine)
    drafts = await summarizer.generate_drafts("2026-04-12")

    assert len(drafts) == 1
    assert drafts[0]["issue_key"] == "PROJ-101"
    assert drafts[0]["time_spent_sec"] == 3600

    rows = await db.fetch_all("SELECT * FROM worklog_drafts WHERE date = '2026-04-12'")
    assert len(rows) == 1
    assert rows[0]["status"] == "pending_review"

    logs = await db.fetch_all("SELECT * FROM audit_logs")
    assert len(logs) == 1
    assert logs[0]["action"] == "created"
