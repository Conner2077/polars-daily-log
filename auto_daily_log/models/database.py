import asyncio
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

CREATE TABLE IF NOT EXISTS summary_types (
    name TEXT PRIMARY KEY,                    -- "daily", "weekly", "sprint-review", ...
    display_name TEXT NOT NULL,               -- "每日日志", "周报", ...
    scope_rule TEXT NOT NULL DEFAULT '{}',    -- JSON: {"type":"day"} / {"type":"week"} / {"type":"issue_based","platform":"jira"}
    schedule_rule TEXT,                       -- JSON: {"type":"daily","time":"18:00"} or NULL (manual)
    prompt_key TEXT DEFAULT 'summarize',      -- fallback key in global settings table
    prompt_template TEXT,                     -- per-type custom prompt (overrides prompt_key + global)
    review_mode TEXT DEFAULT 'manual',        -- "auto" | "manual"
    publisher_name TEXT,                      -- "jira" / "feishu" / "webhook" / NULL (no push)
    publisher_config TEXT DEFAULT '{}',       -- JSON: publisher-specific settings
    is_builtin INTEGER DEFAULT 0,            -- 1 = cannot delete, config editable
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS time_scopes (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    schedule_rule TEXT,
    is_builtin INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scope_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_name TEXT NOT NULL REFERENCES time_scopes(name),
    display_name TEXT NOT NULL,
    output_mode TEXT DEFAULT 'single',
    issue_source TEXT,
    llm_engine_name TEXT,
    prompt_template TEXT,
    publisher_name TEXT,
    publisher_config TEXT DEFAULT '{}',
    auto_publish INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_name TEXT NOT NULL,
    output_id INTEGER NOT NULL REFERENCES scope_outputs(id),
    date TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    issue_key TEXT,
    time_spent_sec INTEGER,
    content TEXT,
    published_id TEXT,
    published_at TEXT,
    publisher_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_summaries_date ON summaries(date, scope_name);

CREATE TABLE IF NOT EXISTS llm_engines (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    protocol TEXT NOT NULL DEFAULT 'openai_compat',
    api_key TEXT,
    model TEXT,
    base_url TEXT,
    is_default INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_name TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    target_date TEXT,
    status TEXT NOT NULL,
    summaries_created INTEGER DEFAULT 0,
    duration_ms INTEGER,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scheduler_runs_created ON scheduler_runs(created_at DESC);

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
        self._write_lock = asyncio.Lock()

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

        # summary_types: per-type prompt template (Phase 2).
        st_cols = await self.fetch_all("PRAGMA table_info(summary_types)")
        st_col_names = {c["name"] for c in st_cols}
        if "prompt_template" not in st_col_names:
            await self._conn.execute("ALTER TABLE summary_types ADD COLUMN prompt_template TEXT")

        # Seed built-in summary types (idempotent — INSERT OR IGNORE).
        _BUILTIN_TYPES = [
            ("daily",     "每日日志", '{"type":"day"}',
             '{"type":"daily","time":"18:00"}', "summarize", "auto", "jira", "{}"),
            ("weekly",    "周报",     '{"type":"week"}',
             None, "period_summary", "manual", None, "{}"),
            ("monthly",   "月报",     '{"type":"month"}',
             None, "period_summary", "manual", None, "{}"),
            ("quarterly", "季报",     '{"type":"quarter"}',
             None, "period_summary", "manual", None, "{}"),
        ]
        for name, disp, scope, sched, prompt, review, pub, pub_cfg in _BUILTIN_TYPES:
            await self._conn.execute(
                "INSERT OR IGNORE INTO summary_types "
                "(name, display_name, scope_rule, schedule_rule, prompt_key, "
                "review_mode, publisher_name, publisher_config, is_builtin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (name, disp, scope, sched, prompt, review, pub, pub_cfg),
            )

        # ── Pipeline refactor: time_scopes + scope_outputs + summaries ──
        await self._migrate_pipeline()

        await self._conn.commit()

    async def _migrate_pipeline(self) -> None:
        """Seed time_scopes / scope_outputs from summary_types; migrate worklog_drafts → summaries."""
        import json as _json

        # audit_logs: add summary_id for new pipeline rows
        audit_cols = await self.fetch_all("PRAGMA table_info(audit_logs)")
        audit_col_names = {c["name"] for c in audit_cols}
        if "summary_id" not in audit_col_names:
            await self._conn.execute("ALTER TABLE audit_logs ADD COLUMN summary_id INTEGER")

        # 1. Seed time_scopes from summary_types (idempotent)
        ts_count = await self._conn.execute("SELECT COUNT(*) AS n FROM time_scopes")
        ts_row = await ts_count.fetchone()
        if ts_row["n"] == 0:
            st_rows = await self.fetch_all("SELECT * FROM summary_types")
            _SCOPE_TYPE_MAP = {"day": "day", "week": "week", "month": "month"}
            for st in st_rows:
                try:
                    scope_rule = _json.loads(st["scope_rule"]) if st["scope_rule"] else {}
                except (_json.JSONDecodeError, TypeError):
                    scope_rule = {}
                scope_type = _SCOPE_TYPE_MAP.get(scope_rule.get("type", ""), "custom")
                # Convert schedule_rule: summary_types stores {"type":"daily","time":"18:00"}
                # time_scopes stores {"time":"18:00"} (scope_type already implies cadence)
                sched = None
                if st.get("schedule_rule"):
                    try:
                        sr = _json.loads(st["schedule_rule"])
                        sr.pop("type", None)
                        sched = _json.dumps(sr, ensure_ascii=False) if sr else None
                    except (_json.JSONDecodeError, TypeError):
                        pass
                await self._conn.execute(
                    "INSERT OR IGNORE INTO time_scopes "
                    "(name, display_name, scope_type, schedule_rule, is_builtin, enabled) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (st["name"], st["display_name"], scope_type, sched,
                     st.get("is_builtin", 0), st.get("enabled", 1)),
                )

        # 2. Seed default scope_outputs (idempotent)
        so_count = await self._conn.execute("SELECT COUNT(*) AS n FROM scope_outputs")
        so_row = await so_count.fetchone()
        if so_row["n"] == 0:
            # daily → two outputs: full summary (archive) + Jira per-issue
            await self._conn.execute(
                "INSERT INTO scope_outputs "
                "(scope_name, display_name, output_mode, issue_source, "
                "prompt_template, publisher_name, publisher_config, auto_publish) "
                "VALUES ('daily', '原汁原味日志', 'single', NULL, NULL, NULL, '{}', 0)"
            )
            await self._conn.execute(
                "INSERT INTO scope_outputs "
                "(scope_name, display_name, output_mode, issue_source, "
                "prompt_template, publisher_name, publisher_config, auto_publish) "
                "VALUES ('daily', 'Jira 工时日志', 'per_issue', 'jira', NULL, 'jira', '{}', 1)"
            )
            # weekly / monthly → single summary, no publisher
            await self._conn.execute(
                "INSERT INTO scope_outputs "
                "(scope_name, display_name, output_mode, issue_source, "
                "prompt_template, publisher_name, publisher_config, auto_publish) "
                "VALUES ('weekly', '周报', 'single', NULL, NULL, NULL, '{}', 0)"
            )
            await self._conn.execute(
                "INSERT INTO scope_outputs "
                "(scope_name, display_name, output_mode, issue_source, "
                "prompt_template, publisher_name, publisher_config, auto_publish) "
                "VALUES ('monthly', '月报', 'single', NULL, NULL, NULL, '{}', 0)"
            )
            await self._conn.execute(
                "INSERT INTO scope_outputs "
                "(scope_name, display_name, output_mode, issue_source, "
                "prompt_template, publisher_name, publisher_config, auto_publish) "
                "VALUES ('quarterly', '季报', 'single', NULL, NULL, NULL, '{}', 0)"
            )

        # Ensure quarterly scope_output exists (added in v0.6.0; older DBs lack it)
        q_out = await self._conn.execute(
            "SELECT id FROM scope_outputs WHERE scope_name = 'quarterly'"
        )
        if not await q_out.fetchone():
            await self._conn.execute(
                "INSERT INTO scope_outputs "
                "(scope_name, display_name, output_mode, issue_source, "
                "prompt_template, publisher_name, publisher_config, auto_publish) "
                "VALUES ('quarterly', '季报', 'single', NULL, NULL, NULL, '{}', 0)"
            )

        # 3. Migrate worklog_drafts → summaries (idempotent: only if summaries empty)
        sum_count = await self._conn.execute("SELECT COUNT(*) AS n FROM summaries")
        sum_row = await sum_count.fetchone()
        if sum_row["n"] > 0:
            return  # already migrated

        drafts = await self.fetch_all("SELECT * FROM worklog_drafts")
        if not drafts:
            return

        # Lookup scope_outputs by (scope_name, output_mode)
        outputs = await self.fetch_all("SELECT * FROM scope_outputs")
        _output_map: dict[tuple[str, str], int] = {}
        for o in outputs:
            _output_map[(o["scope_name"], o["output_mode"])] = o["id"]

        for draft in drafts:
            tag = draft.get("tag") or "daily"
            scope_name = tag if tag in ("daily", "weekly", "monthly") else "daily"

            # 3a. full_summary → single output row
            full_summary = draft.get("full_summary")
            single_output_id = _output_map.get((scope_name, "single"))
            if full_summary and single_output_id:
                await self._conn.execute(
                    "INSERT INTO summaries "
                    "(scope_name, output_id, date, period_start, period_end, "
                    "content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (scope_name, single_output_id, draft["date"],
                     draft.get("period_start"), draft.get("period_end"),
                     full_summary, draft.get("created_at")),
                )

            # 3b. per-issue JSON entries → per_issue output rows
            per_issue_output_id = _output_map.get((scope_name, "per_issue"))
            if per_issue_output_id and draft.get("summary"):
                try:
                    issues = _json.loads(draft["summary"])
                except (_json.JSONDecodeError, TypeError):
                    issues = []
                if isinstance(issues, list):
                    for issue in issues:
                        if not isinstance(issue, dict):
                            continue
                        issue_key = issue.get("issue_key")
                        if not issue_key or issue_key == "OTHER":
                            continue
                        try:
                            hours = float(issue.get("time_spent_hours", 0))
                        except (TypeError, ValueError):
                            hours = 0
                        time_sec = int(hours * 3600)
                        pub_id = issue.get("jira_worklog_id")
                        await self._conn.execute(
                            "INSERT INTO summaries "
                            "(scope_name, output_id, date, period_start, period_end, "
                            "issue_key, time_spent_sec, content, published_id, "
                            "published_at, publisher_name, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (scope_name, per_issue_output_id, draft["date"],
                             draft.get("period_start"), draft.get("period_end"),
                             issue_key, time_sec,
                             issue.get("summary", ""),
                             pub_id,
                             draft.get("updated_at") if pub_id else None,
                             "jira" if pub_id else None,
                             draft.get("created_at")),
                        )
            elif not per_issue_output_id and draft.get("summary") and scope_name != "daily":
                # weekly/monthly: summary text stored directly
                if single_output_id and not full_summary:
                    await self._conn.execute(
                        "INSERT INTO summaries "
                        "(scope_name, output_id, date, period_start, period_end, "
                        "content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (scope_name, single_output_id, draft["date"],
                         draft.get("period_start"), draft.get("period_end"),
                         draft["summary"], draft.get("created_at")),
                    )

        # 4. scope_outputs: add llm_engine_name column
        so_cols = await self.fetch_all("PRAGMA table_info(scope_outputs)")
        so_col_names = {c["name"] for c in so_cols}
        if "llm_engine_name" not in so_col_names:
            await self._conn.execute(
                "ALTER TABLE scope_outputs ADD COLUMN llm_engine_name TEXT"
            )

        # 5. Seed llm_engines from settings (idempotent)
        le_count = await self._conn.execute("SELECT COUNT(*) AS n FROM llm_engines")
        le_row = await le_count.fetchone()
        if le_row["n"] == 0:
            # Migrate current LLM settings into a "default" engine
            llm_settings = {}
            for key in ("llm_engine", "llm_api_key", "llm_model", "llm_base_url"):
                row = await self.fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
                llm_settings[key] = (row["value"] if row and row.get("value") else "")
            api_key = llm_settings.get("llm_api_key", "")
            if api_key:
                protocol = llm_settings.get("llm_engine") or "openai_compat"
                model = llm_settings.get("llm_model") or ""
                base_url = llm_settings.get("llm_base_url") or ""
                # Derive a display name from model or protocol
                display = model or protocol
                await self._conn.execute(
                    "INSERT OR IGNORE INTO llm_engines "
                    "(name, display_name, protocol, api_key, model, base_url, is_default, enabled) "
                    "VALUES ('default', ?, ?, ?, ?, ?, 1, 1)",
                    (display, protocol, api_key, model, base_url),
                )

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with self._write_lock:
            cursor = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cursor.lastrowid

    async def execute_many(self, sql: str, params_list: list[tuple]) -> None:
        async with self._write_lock:
            await self._conn.executemany(sql, params_list)
            await self._conn.commit()

    async def execute_many_returning_ids(self, sql: str, params_list: list[tuple]) -> list[int]:
        row_ids: list[int] = []
        async with self._write_lock:
            await self._conn.execute("BEGIN")
            try:
                for params in params_list:
                    cursor = await self._conn.execute(sql, params)
                    row_ids.append(cursor.lastrowid)
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
        return row_ids

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
