"""HTTP backend — POSTs batches to a remote Auto Daily Log server.

Used by standalone collectors. Keeps a small local offline queue
(JSON-lines file) so network blips don't lose data.
"""
import asyncio
import json
from pathlib import Path
from typing import Optional

import httpx

from shared.schemas import (
    ActivityIngestRequest,
    ActivityPayload,
    CommitIngestRequest,
    CommitPayload,
    HeartbeatRequest,
)

from .base import StorageBackend


class HTTPBackend(StorageBackend):
    """Pushes data to server via HTTP with retry + offline queue."""

    def __init__(
        self,
        server_url: str,
        token: str,
        queue_dir: Path,
        timeout: float = 10.0,
    ):
        self._server_url = server_url.rstrip("/")
        self._token = token
        self._queue_dir = queue_dir
        self._queue_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        # Persistent queue: newline-delimited JSON with (kind, payload)
        self._queue_file = self._queue_dir / "pending.jsonl"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        return self._client

    async def save_activities(self, machine_id: str, activities: list[ActivityPayload]) -> list[int]:
        return await self._send("activities", activities, machine_id)

    async def save_commits(self, machine_id: str, commits: list[CommitPayload]) -> int:
        # _send returns ``accepted`` for commits (see _post_batch); for
        # activities it returns the inserted row IDs.
        result = await self._send("commits", commits, machine_id)
        if isinstance(result, int):
            return result
        return len(result)

    async def extend_duration(self, machine_id: str, row_id: int, extra_sec: int) -> None:
        """Ask the server to add ``extra_sec`` to ``row_id``'s duration.

        Best-effort: network errors are swallowed because the sampler is
        about to send another tick in 30 seconds anyway. Dropping an
        extend means a row is short by one tick, not corrupt.
        """
        if extra_sec <= 0:
            return
        client = self._get_client()
        try:
            await client.post(
                f"{self._server_url}/api/ingest/extend-duration",
                json={"row_id": row_id, "extra_sec": extra_sec},
                headers={"X-Machine-ID": machine_id},
            )
        except httpx.HTTPError:
            pass

    async def save_screenshot(self, machine_id: str, local_path: Path) -> str:
        """Upload the screenshot and return the server-side path.

        Uses the existing ``/api/ingest/screenshot`` endpoint which takes
        a ``timestamp`` query string. We reconstruct it from the local
        filename's mtime so the server can shard by day.
        """
        from datetime import datetime
        client = self._get_client()
        ts = datetime.fromtimestamp(local_path.stat().st_mtime).isoformat(timespec="seconds")
        with open(local_path, "rb") as f:
            files = {"file": (local_path.name, f, "image/png")}
            r = await client.post(
                f"{self._server_url}/api/ingest/screenshot",
                params={"timestamp": ts},
                headers={"X-Machine-ID": machine_id},
                files=files,
            )
        r.raise_for_status()
        return r.json()["path"]

    async def heartbeat(self, machine_id: str) -> Optional[dict]:
        """Send heartbeat. Returns full response dict (config_override +
        is_paused + server_time) on success, None on network failure."""
        client = self._get_client()
        body = HeartbeatRequest(queue_size=self._queue_depth()).model_dump()
        try:
            r = await client.post(
                f"{self._server_url}/api/collectors/{machine_id}/heartbeat",
                json=body,
                headers={"X-Machine-ID": machine_id},
            )
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        return None

    async def _send(self, kind: str, payload_list: list, machine_id: str):
        """Dispatch a batch. Returns list[int] row IDs for activities or
        int accepted-count for commits."""
        if not payload_list:
            return 0 if kind == "commits" else []

        # First try to drain any queued items
        await self._drain_queue(machine_id)

        try:
            return await self._post_batch(kind, payload_list, machine_id)
        except (httpx.HTTPError, Exception) as e:
            # Persist to disk queue for retry
            self._enqueue(kind, payload_list, machine_id)
            return 0 if kind == "commits" else []

    async def _post_batch(self, kind: str, payload_list: list, machine_id: str):
        """POST a batch. Returns list[int] of row IDs for activities,
        int accepted count for commits."""
        client = self._get_client()
        if kind == "activities":
            req = ActivityIngestRequest(activities=payload_list)
            url = f"{self._server_url}/api/ingest/activities"
        elif kind == "commits":
            req = CommitIngestRequest(commits=payload_list)
            url = f"{self._server_url}/api/ingest/commits"
        else:
            raise ValueError(f"unknown kind: {kind}")

        r = await client.post(
            url,
            json=req.model_dump(),
            headers={"X-Machine-ID": machine_id},
        )
        r.raise_for_status()
        data = r.json()
        if kind == "commits":
            return int(data.get("accepted", 0))
        first = data.get("first_id")
        last = data.get("last_id")
        if first is not None and last is not None:
            return list(range(first, last + 1))
        return []

    def _enqueue(self, kind: str, payload_list: list, machine_id: str) -> None:
        with self._queue_file.open("a", encoding="utf-8") as f:
            for p in payload_list:
                data = p.model_dump() if hasattr(p, "model_dump") else p
                line = json.dumps({"kind": kind, "machine_id": machine_id, "payload": data}, ensure_ascii=False)
                f.write(line + "\n")

    def _queue_depth(self) -> int:
        if not self._queue_file.exists():
            return 0
        try:
            with self._queue_file.open(encoding="utf-8") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    async def _drain_queue(self, machine_id: str) -> None:
        """Attempt to re-send queued items. If any fails, keep the rest in queue."""
        if not self._queue_file.exists() or self._queue_file.stat().st_size == 0:
            return

        with self._queue_file.open(encoding="utf-8") as f:
            lines = f.readlines()

        # Group by kind
        batches: dict[str, list] = {"activities": [], "commits": []}
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = entry["kind"]
            payload = entry["payload"]
            if kind == "activities":
                batches["activities"].append(ActivityPayload(**payload))
            elif kind == "commits":
                batches["commits"].append(CommitPayload(**payload))

        # Try to send each batch. If ANY send fails, stop draining.
        try:
            if batches["activities"]:
                await self._post_batch("activities", batches["activities"], machine_id)
            if batches["commits"]:
                await self._post_batch("commits", batches["commits"], machine_id)
            # Success — clear queue
            self._queue_file.unlink()
        except (httpx.HTTPError, Exception):
            # Leave queue intact; will retry next cycle
            return

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
