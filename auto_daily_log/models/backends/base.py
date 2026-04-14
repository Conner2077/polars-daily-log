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

    async def close(self) -> None:
        """Release resources. Default no-op."""
        return None
