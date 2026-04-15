from pathlib import Path
from typing import Any, Optional

import aiosqlite
import sqlite_vec

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    app_name TEXT,
    window_title TEXT,
    category TEXT,
    confidence REAL,
    url TEXT,
    signals TEXT,
    duration_sec INTEGER
);
CREATE INDEX IF NOT EXISTS idx_activities_timestamp ON activities(timestamp);

CREATE TABLE IF NOT EXISTS git_repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    author_email TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS git_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER REFERENCES git_repos(id),
    hash TEXT NOT NULL,
    message TEXT,
    author TEXT,
    committed_at TEXT,
    files_changed TEXT,
    insertions INTEGER DEFAULT 0,
    deletions INTEGER DEFAULT 0,
    date TEXT
);
CREATE INDEX IF NOT EXISTS idx_git_commits_date ON git_commits(date);

CREATE TABLE IF NOT EXISTS jira_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_key TEXT UNIQUE NOT NULL,
    summary TEXT,
    description TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS worklog_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    time_spent_sec INTEGER DEFAULT 0,
    summary TEXT,
    raw_activities TEXT,
    raw_commits TEXT,
    status TEXT DEFAULT 'pending_review',
    user_edited INTEGER DEFAULT 0,
    jira_worklog_id TEXT,
    tag TEXT DEFAULT 'daily',
    period_start TEXT,
    period_end TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_drafts_date_status ON worklog_drafts(date, status);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES worklog_drafts(id),
    action TEXT NOT NULL,
    before_snapshot TEXT,
    after_snapshot TEXT,
    jira_response TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS collectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    hostname TEXT,
    platform TEXT,
    platform_detail TEXT,
    capabilities TEXT,
    token_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    last_seen TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_collectors_machine ON collectors(machine_id);
"""


class Database:
    def __init__(self, db_path: Path | str, embedding_dimensions: int = 1536):
        self._db_path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._embedding_dimensions = embedding_dimensions

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        return self._conn

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.enable_load_extension(True)
        await self._conn.load_extension(sqlite_vec.loadable_path())
        await self._conn.enable_load_extension(False)
        await self._conn.executescript(_SCHEMA_SQL)
        try:
            await self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS embeddings USING vec0("
                f"source_type TEXT, source_id INTEGER, text_content TEXT, "
                f"embedding FLOAT[{self._embedding_dimensions}])"
            )
        except Exception:
            await self._conn.execute("DROP TABLE IF EXISTS embeddings")
            await self._conn.execute(
                f"CREATE VIRTUAL TABLE embeddings USING vec0("
                f"source_type TEXT, source_id INTEGER, text_content TEXT, "
                f"embedding FLOAT[{self._embedding_dimensions}])"
            )
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        """Add columns that may be missing from older schema versions."""
        # worklog_drafts migrations
        columns = await self.fetch_all("PRAGMA table_info(worklog_drafts)")
        col_names = {c["name"] for c in columns}
        migrations = [
            ("tag", "ALTER TABLE worklog_drafts ADD COLUMN tag TEXT DEFAULT 'daily'"),
            ("period_start", "ALTER TABLE worklog_drafts ADD COLUMN period_start TEXT"),
            ("period_end", "ALTER TABLE worklog_drafts ADD COLUMN period_end TEXT"),
            ("full_summary", "ALTER TABLE worklog_drafts ADD COLUMN full_summary TEXT"),
        ]
        for col, sql in migrations:
            if col not in col_names:
                await self._require_conn().execute(sql)

        # activities migrations
        act_cols = await self.fetch_all("PRAGMA table_info(activities)")
        act_col_names = {c["name"] for c in act_cols}
        if "deleted_at" not in act_col_names:
            await self._require_conn().execute("ALTER TABLE activities ADD COLUMN deleted_at TEXT")
            await self._require_conn().execute("CREATE INDEX IF NOT EXISTS idx_activities_deleted ON activities(deleted_at)")
        if "machine_id" not in act_col_names:
            # Existing rows get 'local' as machine_id
            await self._require_conn().execute("ALTER TABLE activities ADD COLUMN machine_id TEXT DEFAULT 'local'")
            await self._require_conn().execute("UPDATE activities SET machine_id = 'local' WHERE machine_id IS NULL")
            await self._require_conn().execute("CREATE INDEX IF NOT EXISTS idx_activities_machine ON activities(machine_id)")

        # git_commits migrations
        commit_cols = await self.fetch_all("PRAGMA table_info(git_commits)")
        commit_col_names = {c["name"] for c in commit_cols}
        if "machine_id" not in commit_col_names:
            await self._require_conn().execute("ALTER TABLE git_commits ADD COLUMN machine_id TEXT DEFAULT 'local'")
            await self._require_conn().execute("UPDATE git_commits SET machine_id = 'local' WHERE machine_id IS NULL")
            await self._require_conn().execute("CREATE INDEX IF NOT EXISTS idx_commits_machine ON git_commits(machine_id)")

        # collectors migrations for Phase 3 (remote config + pause)
        col_cols = await self.fetch_all("PRAGMA table_info(collectors)")
        col_col_names = {c["name"] for c in col_cols}
        if "config_override" not in col_col_names:
            await self._require_conn().execute("ALTER TABLE collectors ADD COLUMN config_override TEXT")
        if "is_paused" not in col_col_names:
            await self._require_conn().execute("ALTER TABLE collectors ADD COLUMN is_paused INTEGER DEFAULT 0")

        await self._require_conn().commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: tuple = ()) -> int:
        cursor = await self._require_conn().execute(sql, params)
        await self._require_conn().commit()
        return cursor.lastrowid

    async def execute_many(self, sql: str, params_list: list[tuple]) -> None:
        await self._require_conn().executemany(sql, params_list)
        await self._require_conn().commit()

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cursor = await self._require_conn().execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._require_conn().execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
