"""Storage backend abstraction.

Monitor code writes activities/commits through this interface. Two
implementations:

- LocalSQLiteBackend — writes directly to SQLite (in-process, used by
  the server's built-in collector for backwards compatibility).
- HTTPBackend — POSTs batches to a remote server over HTTP (used by
  standalone collector processes).

The interface is intentionally small: just `save_activities`,
`save_commits`, `heartbeat`. Keeping it small makes the HTTP contract
simple and testable.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from shared.schemas import ActivityPayload, CommitPayload


class StorageBackend(ABC):
    """Where collected data is persisted (local DB or remote server)."""

    @abstractmethod
    async def save_activities(self, machine_id: str, activities: list[ActivityPayload]) -> list[int]:
        """Persist a batch of activities. Returns list of assigned row IDs."""
        ...

    @abstractmethod
    async def save_commits(self, machine_id: str, commits: list[CommitPayload]) -> int:
        """Persist commits. Returns count of newly inserted (duplicates ignored)."""
        ...

    @abstractmethod
    async def heartbeat(self, machine_id: str) -> Optional[dict]:
        """Ping server / touch local collector. Returns optional config override."""
        ...

    @abstractmethod
    async def extend_duration(self, machine_id: str, row_id: int, extra_sec: int) -> None:
        """Add extra_sec to the existing row's duration_sec.

        Used for same-window aggregation: when the current window matches
        the last sample, don't insert a new row — just extend the previous
        row's duration.
        """
        ...

    @abstractmethod
    async def save_screenshot(self, machine_id: str, local_path: Path) -> str:
        """Persist a screenshot and return the path to store in activity signals.

        - LocalSQLiteBackend: file already on disk under the server's
          screenshot dir; returns ``str(local_path)`` unchanged.
        - HTTPBackend: uploads the image over multipart to
          ``/api/ingest/screenshot`` and returns the server-side path from
          the response (what gets baked into the activity signals JSON).
        """
        ...

    async def close(self) -> None:
        """Release resources. Default no-op."""
        return None
