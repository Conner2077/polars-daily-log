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
    issue_index INTEGER,           -- which issue within the draft (NULL = draft-level)
    issue_key TEXT,                -- denormalised for fast filtering / display
    source TEXT,                   -- "manual_single" | "manual_all" | "auto" | NULL (legacy)
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);

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

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        # Load sqlite-vec extension
        await self._conn.enable_load_extension(True)
        await self._conn.load_extension(sqlite_vec.loadable_path())
        await self._conn.enable_load_extension(False)
        # Create standard tables
        await self._conn.executescript(_SCHEMA_SQL)
        # Create or recreate vec0 virtual table if dimension changed
        try:
            row = await self.fetch_one("SELECT embedding FROM embeddings LIMIT 0")
            # Table exists — check if we need to recreate (dimension mismatch handled by dropping)
        except Exception:
            # Table doesn't exist, create it
            pass
        try:
            await self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS embeddings USING vec0("
                f"source_type TEXT, source_id INTEGER, text_content TEXT, "
                f"embedding FLOAT[{self._embedding_dimensions}])"
            )
        except Exception:
            # Dimension mismatch — drop and recreate
            await self._conn.execute("DROP TABLE IF EXISTS embeddings")
            await self._conn.execute(
                f"CREATE VIRTUAL TABLE embeddings USING vec0("
                f"source_type TEXT, source_id INTEGER, text_content TEXT, "
                f"embedding FLOAT[{self._embedding_dimensions}])"
            )
        await self._conn.commit()
        # Migrate: add new columns if missing (for existing databases)
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
                await self._conn.execute(sql)

        # activities migrations
        act_cols = await self.fetch_all("PRAGMA table_info(activities)")
        act_col_names = {c["name"] for c in act_cols}
        if "deleted_at" not in act_col_names:
            await self._conn.execute("ALTER TABLE activities ADD COLUMN deleted_at TEXT")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_activities_deleted ON activities(deleted_at)")
        if "machine_id" not in act_col_names:
            # Existing rows get 'local' as machine_id
            await self._conn.execute("ALTER TABLE activities ADD COLUMN machine_id TEXT DEFAULT 'local'")
            await self._conn.execute("UPDATE activities SET machine_id = 'local' WHERE machine_id IS NULL")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_activities_machine ON activities(machine_id)")
        if "llm_summary" not in act_col_names:
            await self._conn.execute("ALTER TABLE activities ADD COLUMN llm_summary TEXT")
        if "llm_summary_at" not in act_col_names:
            await self._conn.execute("ALTER TABLE activities ADD COLUMN llm_summary_at TEXT")
        # Partial index: worker frequently scans pending rows (NULL or failed)
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activities_llm_pending "
            "ON activities(timestamp) WHERE llm_summary IS NULL OR llm_summary='(failed)'"
        )

        # git_commits migrations
        commit_cols = await self.fetch_all("PRAGMA table_info(git_commits)")
        commit_col_names = {c["name"] for c in commit_cols}
        if "machine_id" not in commit_col_names:
            await self._conn.execute("ALTER TABLE git_commits ADD COLUMN machine_id TEXT DEFAULT 'local'")
            await self._conn.execute("UPDATE git_commits SET machine_id = 'local' WHERE machine_id IS NULL")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_commits_machine ON git_commits(machine_id)")

        # collectors migrations for Phase 3 (remote config + pause)
        col_cols = await self.fetch_all("PRAGMA table_info(collectors)")
        col_col_names = {c["name"] for c in col_cols}
        if "config_override" not in col_col_names:
            await self._conn.execute("ALTER TABLE collectors ADD COLUMN config_override TEXT")
        if "is_paused" not in col_col_names:
            await self._conn.execute("ALTER TABLE collectors ADD COLUMN is_paused INTEGER DEFAULT 0")

        # audit_logs: per-issue granularity for submit actions.
        # Legacy rows (action='submitted' with batch jira_response) keep
        # NULL in these columns and render as "全部提交" in the UI.
        audit_cols = await self.fetch_all("PRAGMA table_info(audit_logs)")
        audit_col_names = {c["name"] for c in audit_cols}
        if "issue_index" not in audit_col_names:
            await self._conn.execute("ALTER TABLE audit_logs ADD COLUMN issue_index INTEGER")
        if "issue_key" not in audit_col_names:
            await self._conn.execute("ALTER TABLE audit_logs ADD COLUMN issue_key TEXT")
        if "source" not in audit_col_names:
            await self._conn.execute("ALTER TABLE audit_logs ADD COLUMN source TEXT")

        # Normalize legacy llm_engine values to canonical protocols
        await self._conn.execute(
            "UPDATE settings SET value='openai_compat' WHERE key='llm_engine' AND value IN ('kimi','openai')"
        )
        await self._conn.execute(
            "UPDATE settings SET value='anthropic' WHERE key='llm_engine' AND value='claude'"
        )

        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor.lastrowid

    async def execute_many(self, sql: str, params_list: list[tuple]) -> None:
        await self._conn.executemany(sql, params_list)
        await self._conn.commit()

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
