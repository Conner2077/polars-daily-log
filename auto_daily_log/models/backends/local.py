"""Local SQLite storage backend — used by server's built-in collector."""
from datetime import datetime
from typing import Optional

from shared.schemas import ActivityPayload, CommitPayload

from ..database import Database
from .base import StorageBackend


class LocalSQLiteBackend(StorageBackend):
    """Writes straight to the shared Database instance."""

    def __init__(self, db: Database):
        self._db = db

    async def save_activities(self, machine_id: str, activities: list[ActivityPayload]) -> list[int]:
        ids: list[int] = []
        for a in activities:
            row_id = await self._db.execute(
                """INSERT INTO activities
                   (timestamp, app_name, window_title, category, confidence,
                    url, signals, duration_sec, machine_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    a.timestamp, a.app_name, a.window_title, a.category,
                    a.confidence, a.url, a.signals, a.duration_sec, machine_id,
                ),
            )
            ids.append(row_id)
        return ids

    async def save_commits(self, machine_id: str, commits: list[CommitPayload]) -> int:
        inserted = 0
        for c in commits:
            # Dedupe by (repo_path, hash, machine_id)
            existing = await self._db.fetch_one(
                "SELECT id FROM git_commits WHERE hash = ? AND machine_id = ?",
                (c.hash, machine_id),
            )
            if existing:
                continue
            await self._db.execute(
                """INSERT INTO git_commits
                   (hash, message, author, committed_at, files_changed,
                    insertions, deletions, date, machine_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    c.hash, c.message, c.author, c.committed_at, c.files_changed,
                    c.insertions, c.deletions, c.date, machine_id,
                ),
            )
            inserted += 1
        return inserted

    async def heartbeat(self, machine_id: str) -> Optional[dict]:
        # Update last_seen if collector exists in table (built-in machine_id='local'
        # is not registered by default but we can still touch it idempotently).
        await self._db.execute(
            "UPDATE collectors SET last_seen = datetime('now') WHERE machine_id = ?",
            (machine_id,),
        )
        return None
