import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from auto_daily_log.models.database import Database
from auto_daily_log.search.indexer import Indexer
from auto_daily_log.search.searcher import Searcher


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def mock_engine():
    engine = AsyncMock()
    engine.dimensions = 4
    async def fake_embed(text):
        if "coding" in text.lower() or "sql" in text.lower():
            return [1.0, 0.0, 0.0, 0.0]
        elif "meeting" in text.lower() or "zoom" in text.lower():
            return [0.0, 1.0, 0.0, 0.0]
        else:
            return [0.5, 0.5, 0.0, 0.0]
    engine.embed = fake_embed
    return engine


@pytest.mark.asyncio
async def test_index_worklogs(db, mock_engine):
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
        "VALUES ('2026-04-12', 'PROJ-101', 3600, 'Fixed SQL parser JOIN handling', 'submitted', 'daily', '2026-04-12', '2026-04-12')"
    )
    indexer = Indexer(db, mock_engine)
    count = await indexer.index_worklogs("2026-04-12")
    assert count == 1

    rows = await db.fetch_all("SELECT * FROM embeddings WHERE source_type = 'worklog'")
    assert len(rows) == 1
    assert "SQL" in rows[0]["text_content"]


@pytest.mark.asyncio
async def test_search_returns_ranked_results(db, mock_engine):
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
        "VALUES ('2026-04-12', 'PROJ-101', 3600, 'Fixed SQL parser coding bug', 'submitted', 'daily', '2026-04-12', '2026-04-12')"
    )
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
        "VALUES ('2026-04-12', 'PROJ-102', 1800, 'Sprint meeting discussion', 'submitted', 'daily', '2026-04-12', '2026-04-12')"
    )
    indexer = Indexer(db, mock_engine)
    await indexer.index_worklogs("2026-04-12")

    searcher = Searcher(db, mock_engine)
    results = await searcher.search("SQL coding", limit=2)
    assert len(results) == 2
    assert "SQL" in results[0]["text_content"]


@pytest.mark.asyncio
async def test_index_commits(db, mock_engine):
    await db.execute(
        "INSERT INTO git_repos (path, author_email, is_active) VALUES ('/tmp/repo', 'test@test.com', 1)"
    )
    await db.execute(
        "INSERT INTO git_commits (repo_id, hash, message, author, committed_at, files_changed, date) "
        "VALUES (1, 'abc', 'fix coding bug', 'test', '2026-04-12T10:30:00', '[\"Main.java\"]', '2026-04-12')"
    )
    indexer = Indexer(db, mock_engine)
    count = await indexer.index_commits("2026-04-12")
    assert count == 1


@pytest.mark.asyncio
async def test_no_duplicate_indexing(db, mock_engine):
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
        "VALUES ('2026-04-12', 'ALL', 3600, 'Did some coding work', 'submitted', 'daily', '2026-04-12', '2026-04-12')"
    )
    indexer = Indexer(db, mock_engine)
    await indexer.index_worklogs("2026-04-12")
    await indexer.index_worklogs("2026-04-12")

    rows = await db.fetch_all("SELECT * FROM embeddings WHERE source_type = 'worklog'")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_reindex_all(db, mock_engine):
    await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
        "VALUES ('2026-04-12', 'ALL', 3600, 'Coding work', 'submitted', 'daily', '2026-04-12', '2026-04-12')"
    )
    await db.execute(
        "INSERT INTO git_repos (path, author_email, is_active) VALUES ('/tmp/repo', 'test@test.com', 1)"
    )
    await db.execute(
        "INSERT INTO git_commits (repo_id, hash, message, author, committed_at, files_changed, date) "
        "VALUES (1, 'abc', 'fix bug', 'test', '2026-04-12T10:30:00', '[]', '2026-04-12')"
    )
    indexer = Indexer(db, mock_engine)
    result = await indexer.reindex_all()
    assert result["worklogs"] == 1
    assert result["git_commits"] == 1
