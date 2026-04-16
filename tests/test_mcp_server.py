"""Tests for the MCP server tool handlers.

Each test seeds a fresh in-memory DB, calls the handler directly,
and asserts on exact output strings.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from auto_daily_log.models.database import Database


# ── Fixture ──────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db(tmp_path):
    """Fresh test database with schema initialised."""
    _db = Database(tmp_path / "mcp_test.db", embedding_dimensions=4)
    await _db.initialize()
    yield _db
    await _db.close()


# Patch _get_db so tool handlers use the test database instead of the
# real ~/.auto_daily_log/data.db.  Each test passes its fixture db in.

def _patch_db(db):
    """Return an async context-manager-compatible mock for _get_db."""

    async def _fake_get_db(db_path=None):
        return db

    return patch("auto_daily_log.mcp_server.server._get_db", side_effect=_fake_get_db)


# We also need to prevent the handler from closing the shared fixture db.
# Monkey-patch db.close to no-op during tests.
@pytest_asyncio.fixture(autouse=True)
async def _no_close(db):
    original_close = db.close
    db.close = AsyncMock()
    yield
    db.close = original_close


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_activities_returns_formatted_text(db):
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, llm_summary, duration_sec, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        ("2026-04-16T09:00:00", "VS Code", "server.py", "Editing MCP server module", 1800),
    )
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, llm_summary, duration_sec, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        ("2026-04-16T10:30:00", "Chrome", "Jira Board", "Reviewing Jira tickets", 600),
    )

    from auto_daily_log.mcp_server.server import query_activities

    with _patch_db(db):
        result = await query_activities(date="2026-04-16")

    assert "Found 2 activities on 2026-04-16:" in result
    assert "09:00 VS Code — Editing MCP server module (30.0min)" in result
    assert "10:30 Chrome — Reviewing Jira tickets (10.0min)" in result


@pytest.mark.asyncio
async def test_query_activities_empty_date(db):
    from auto_daily_log.mcp_server.server import query_activities

    with _patch_db(db):
        result = await query_activities(date="2099-01-01")

    assert result == "No activities found for 2099-01-01."


@pytest.mark.asyncio
async def test_query_activities_keyword_filter(db):
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, llm_summary, duration_sec, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        ("2026-04-16T09:00:00", "VS Code", "server.py", "Editing MCP server module", 1800),
    )
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, llm_summary, duration_sec, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        ("2026-04-16T10:00:00", "Chrome", "Jira", "Reviewing Jira tickets", 600),
    )
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, llm_summary, duration_sec, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        ("2026-04-16T11:00:00", "Slack", "DM", "Chatting with team", 300),
    )

    from auto_daily_log.mcp_server.server import query_activities

    with _patch_db(db):
        result = await query_activities(date="2026-04-16", keyword="Jira")

    assert "Found 1 activities on 2026-04-16:" in result
    assert "Reviewing Jira tickets" in result
    assert "Editing MCP server module" not in result
    assert "Chatting with team" not in result


@pytest.mark.asyncio
async def test_query_worklogs_by_issue(db):
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-16", "PDL-42", 7200, "MCP server implementation", "pending_review"),
    )
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-16", "PDL-99", 3600, "Bug fix", "approved"),
    )

    from auto_daily_log.mcp_server.server import query_worklogs

    with _patch_db(db):
        result = await query_worklogs(issue_key="PDL-42")

    assert "Found 1 worklogs:" in result
    assert "[PDL-42]" in result
    assert "MCP server implementation" in result
    assert "PDL-99" not in result


@pytest.mark.asyncio
async def test_get_jira_issues_active_only(db):
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, description, is_active) VALUES (?, ?, ?, ?)",
        ("PDL-10", "Active task", "Do the thing", 1),
    )
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, description, is_active) VALUES (?, ?, ?, ?)",
        ("PDL-20", "Archived task", "Old stuff", 0),
    )

    from auto_daily_log.mcp_server.server import get_jira_issues

    with _patch_db(db):
        result = await get_jira_issues(active_only=True)

    assert "1 Jira issues:" in result
    assert "[PDL-10] Active task" in result
    assert "PDL-20" not in result


@pytest.mark.asyncio
async def test_get_git_commits_by_date(db):
    # Need a repo first
    await db.execute(
        "INSERT INTO git_repos (path, author_email, is_active) VALUES (?, ?, ?)",
        ("/tmp/repo", "test@test.com", 1),
    )
    await db.execute(
        "INSERT INTO git_commits (repo_id, hash, message, author, committed_at, insertions, deletions, date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "abc1234567890", "feat: add MCP server", "conner", "2026-04-16T14:00:00", 150, 10, "2026-04-16"),
    )
    await db.execute(
        "INSERT INTO git_commits (repo_id, hash, message, author, committed_at, insertions, deletions, date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "def9876543210", "fix: unrelated", "conner", "2026-04-15T10:00:00", 5, 2, "2026-04-15"),
    )

    from auto_daily_log.mcp_server.server import get_git_commits

    with _patch_db(db):
        result = await get_git_commits(date="2026-04-16")

    assert "1 commits on 2026-04-16:" in result
    assert "[abc1234] feat: add MCP server (+150/-10)" in result
    assert "def9876" not in result


@pytest.mark.asyncio
async def test_search_activities_by_keyword(db):
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, llm_summary, duration_sec, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        ("2026-04-16T09:00:00", "VS Code", "models.py", "Refactoring database schema", 1800),
    )
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, llm_summary, duration_sec, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        ("2026-04-16T10:00:00", "Chrome", "YouTube", "Watching tutorial video", 600),
    )

    from auto_daily_log.mcp_server.server import search_activities

    with _patch_db(db):
        result = await search_activities(query="database")

    assert "Found 1 matches:" in result
    assert "Refactoring database schema" in result
    assert "Watching tutorial video" not in result


@pytest.mark.asyncio
async def test_submit_worklog_uses_jira_client(db):
    """Verify submit_worklog calls build_jira_client_from_db and JiraClient.submit_worklog
    with expected args, and inserts a worklog_drafts row."""
    fake_client = AsyncMock()
    fake_client.submit_worklog.return_value = {"id": "99999"}

    async def fake_build(db_arg):
        return fake_client

    from auto_daily_log.mcp_server.server import submit_worklog

    with _patch_db(db), \
         patch("auto_daily_log.jira_client.client.build_jira_client_from_db", side_effect=fake_build):
        result = await submit_worklog(
            issue_key="PDL-42",
            hours=3.0,
            summary="Implemented MCP server",
            date="2026-04-16",
        )

    # Verify return message
    assert result == "Submitted 3.0 hours to PDL-42 on 2026-04-16. Jira worklog ID: 99999"

    # Verify Jira client was called with correct args
    fake_client.submit_worklog.assert_called_once_with(
        issue_key="PDL-42",
        time_spent_sec=10800,
        comment="Implemented MCP server",
        started="2026-04-16T09:00:00.000+0800",
    )

    # Verify a worklog_drafts row was inserted
    row = await db.fetch_one(
        "SELECT issue_key, time_spent_sec, summary, status, jira_worklog_id "
        "FROM worklog_drafts WHERE issue_key = 'PDL-42'"
    )
    assert row is not None
    assert row["issue_key"] == "PDL-42"
    assert row["time_spent_sec"] == 10800
    assert row["summary"] == "Implemented MCP server"
    assert row["status"] == "submitted"
    assert row["jira_worklog_id"] == "99999"
