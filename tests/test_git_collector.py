import json
import pytest
import pytest_asyncio
from pathlib import Path
from auto_daily_log.collector.git_collector import GitCollector
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo with a commit for testing."""
    import subprocess
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo_path, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True
    )
    (repo_path / "hello.py").write_text("print('hello')")
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add hello script"],
        cwd=repo_path, capture_output=True,
    )
    return repo_path


@pytest.mark.asyncio
async def test_collect_todays_commits(db, git_repo):
    await db.execute(
        "INSERT INTO git_repos (path, author_email, is_active) VALUES (?, ?, ?)",
        (str(git_repo), "test@example.com", 1),
    )
    collector = GitCollector(db)
    count = await collector.collect_today()
    assert count >= 1

    commits = await db.fetch_all("SELECT * FROM git_commits")
    assert len(commits) >= 1
    assert "hello" in commits[0]["message"]
    assert commits[0]["insertions"] >= 1


@pytest.mark.asyncio
async def test_skip_inactive_repos(db, git_repo):
    await db.execute(
        "INSERT INTO git_repos (path, author_email, is_active) VALUES (?, ?, ?)",
        (str(git_repo), "test@example.com", 0),
    )
    collector = GitCollector(db)
    count = await collector.collect_today()
    assert count == 0
