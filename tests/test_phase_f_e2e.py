"""Phase F — end-to-end: server subprocess + collector runtime push activities.

Starts a real uvicorn server in a subprocess, spins up a CollectorRuntime
in-process, registers it, pushes activity batches, and asserts exact DB
state via raw sqlite3 reads (bypassing the HTTP layer).
"""
import asyncio
import json
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
import pytest
import yaml

from auto_daily_log_collector.config import CollectorConfig
from auto_daily_log_collector.runner import CollectorRuntime
from shared.schemas import ActivityPayload


# ─── Server subprocess helpers ───────────────────────────────────────

def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _write_server_config(tmp_path: Path, port: int) -> Path:
    data_dir = tmp_path / "server_data"
    data_dir.mkdir(exist_ok=True)
    cfg = {
        "server": {"host": "127.0.0.1", "port": port},
        "monitor": {"interval_sec": 9999, "ocr_enabled": False},
        "scheduler": {"enabled": False, "trigger_time": "23:59"},
        "auto_approve": {"enabled": False, "trigger_time": "23:59"},
        "system": {"data_dir": str(data_dir), "language": "zh"},
        "embedding": {"enabled": False, "model": "", "dimensions": 1024},
        "llm": {"engine": "kimi"},
        "git": {"repos": []},
        "jira": {"server_url": "", "pat": ""},
    }
    path = tmp_path / "server.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


async def _wait_for_server(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                r = await client.get(f"{url}/api/collectors")
                if r.status_code < 500:
                    return True
            except (httpx.HTTPError, ConnectionError):
                pass
            await asyncio.sleep(0.2)
    return False


class ServerHandle:
    """Runs the server as subprocess; async context manager."""

    def __init__(self, tmp_path: Path):
        self._tmp_path = tmp_path
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.db_path = tmp_path / "server_data" / "data.db"
        self._proc: Optional[subprocess.Popen] = None

    async def __aenter__(self):
        cfg_path = _write_server_config(self._tmp_path, self.port)
        import os as _os
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "auto_daily_log", "--config", str(cfg_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=Path(__file__).parent.parent,
            env={**_os.environ, "HOME": str(self._tmp_path)},
        )
        ok = await _wait_for_server(self.url)
        if not ok:
            try:
                out, _ = self._proc.communicate(timeout=2)
                err = out.decode(errors='replace')
            except Exception:
                err = "(server did not produce output)"
            self._proc.kill()
            raise RuntimeError(f"Server failed to start:\n{err}")
        return self

    async def __aexit__(self, *args):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


# ─── E2E tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collector_registers_then_pushes_batch_verified_in_db(tmp_path):
    async with ServerHandle(tmp_path) as server:
        collector_data = tmp_path / "collector_data"
        config = CollectorConfig(
            server_url=server.url,
            name="E2E-Test-Machine",
            data_dir=str(collector_data),
            interval_sec=30,
        )
        runtime = CollectorRuntime(config)

        machine_id = await runtime.ensure_registered()
        assert machine_id.startswith("m-")
        assert len(machine_id) == 18

        cred_file = collector_data / "credentials.json"
        assert cred_file.exists()
        cred_data = json.loads(cred_file.read_text())
        assert cred_data["machine_id"] == machine_id
        assert len(cred_data["token"]) >= 32

        batch = [
            ActivityPayload(
                timestamp="2026-04-14T10:00:00",
                app_name="Xcode",
                window_title="ViewController.swift",
                category="coding",
                confidence=0.95,
                duration_sec=30,
            ),
            ActivityPayload(
                timestamp="2026-04-14T10:00:30",
                app_name="Safari",
                window_title="Apple Documentation",
                category="research",
                confidence=0.80,
                url="https://developer.apple.com/",
                duration_sec=30,
            ),
            ActivityPayload(
                timestamp="2026-04-14T10:01:00",
                app_name="iTerm2",
                category="coding",
                confidence=0.90,
                duration_sec=30,
            ),
        ]

        ids = await runtime.push_batch(batch)
        assert len(ids) == 3
        assert ids == [ids[0], ids[0] + 1, ids[0] + 2]

        await runtime.close()

        conn = sqlite3.connect(server.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM activities WHERE machine_id = ? ORDER BY id", (machine_id,)
        ).fetchall()
        conn.close()

        assert len(rows) == 3
        assert rows[0]["app_name"] == "Xcode"
        assert rows[0]["window_title"] == "ViewController.swift"
        assert rows[0]["category"] == "coding"
        assert abs(rows[0]["confidence"] - 0.95) < 1e-9
        assert rows[0]["duration_sec"] == 30
        assert rows[0]["timestamp"] == "2026-04-14T10:00:00"
        assert rows[0]["machine_id"] == machine_id

        assert rows[1]["app_name"] == "Safari"
        assert rows[1]["url"] == "https://developer.apple.com/"
        assert rows[1]["category"] == "research"

        assert rows[2]["app_name"] == "iTerm2"


@pytest.mark.asyncio
async def test_collector_registration_is_idempotent_on_restart(tmp_path):
    """Restarting the collector with same creds file should reuse machine_id."""
    async with ServerHandle(tmp_path) as server:
        collector_data = tmp_path / "collector_data"
        config = CollectorConfig(
            server_url=server.url, name="Restart-Test",
            data_dir=str(collector_data), interval_sec=30,
        )
        r1 = CollectorRuntime(config)
        mid1 = await r1.ensure_registered()
        await r1.close()

        r2 = CollectorRuntime(config)
        mid2 = await r2.ensure_registered()
        await r2.close()

        assert mid1 == mid2


@pytest.mark.asyncio
async def test_two_collectors_independent_machine_ids(tmp_path):
    """Two distinct collectors get distinct machine_ids and data."""
    async with ServerHandle(tmp_path) as server:
        c1 = CollectorRuntime(CollectorConfig(
            server_url=server.url, name="Mac-A",
            data_dir=str(tmp_path / "cA"), interval_sec=30,
        ))
        c2 = CollectorRuntime(CollectorConfig(
            server_url=server.url, name="Mac-B",
            data_dir=str(tmp_path / "cB"), interval_sec=30,
        ))

        mid1 = await c1.ensure_registered()
        mid2 = await c2.ensure_registered()
        assert mid1 != mid2

        await c1.push_batch([ActivityPayload(
            timestamp="2026-04-14T10:00:00",
            app_name="VSCode", duration_sec=10, category="coding",
        )])
        await c2.push_batch([ActivityPayload(
            timestamp="2026-04-14T10:00:00",
            app_name="Chrome", duration_sec=10, category="browsing",
        )])

        async with httpx.AsyncClient() as http:
            r = await http.get(f"{server.url}/api/collectors")
            assert r.status_code == 200
            clist = r.json()
            # Server auto-registers 'local' (built-in) plus Mac-A and Mac-B
            assert len(clist) == 3
            names = {c["name"] for c in clist}
            assert names == {"Built-in (this machine)", "Mac-A", "Mac-B"}

        conn = sqlite3.connect(server.db_path)
        conn.row_factory = sqlite3.Row
        a1 = conn.execute(
            "SELECT app_name FROM activities WHERE machine_id = ?", (mid1,)
        ).fetchone()
        a2 = conn.execute(
            "SELECT app_name FROM activities WHERE machine_id = ?", (mid2,)
        ).fetchone()
        conn.close()

        assert a1["app_name"] == "VSCode"
        assert a2["app_name"] == "Chrome"

        await c1.close()
        await c2.close()


@pytest.mark.asyncio
async def test_collector_heartbeat_updates_server_last_seen(tmp_path):
    async with ServerHandle(tmp_path) as server:
        config = CollectorConfig(
            server_url=server.url, name="HB-Test",
            data_dir=str(tmp_path / "hb"), interval_sec=30,
        )
        runtime = CollectorRuntime(config)
        machine_id = await runtime.ensure_registered()

        await asyncio.sleep(1.2)

        response = await runtime.heartbeat()
        assert response is not None
        assert response["config_override"] is None
        assert response["is_paused"] is False

        conn = sqlite3.connect(server.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT last_seen, created_at FROM collectors WHERE machine_id = ?",
            (machine_id,),
        ).fetchone()
        conn.close()

        assert row["last_seen"] is not None
        assert row["last_seen"] >= row["created_at"]

        await runtime.close()


@pytest.mark.asyncio
async def test_offline_queue_survives_server_restart(tmp_path):
    """Collector queues on network failure, drains on reconnect."""
    async with ServerHandle(tmp_path) as server:
        collector_data = tmp_path / "offline_test"
        config = CollectorConfig(
            server_url=server.url, name="Offline-Test",
            data_dir=str(collector_data), interval_sec=30,
        )
        runtime = CollectorRuntime(config)
        machine_id = await runtime.ensure_registered()

        # Tamper: redirect backend to invalid URL
        runtime._backend._server_url = "http://definitely.not.reachable.invalid:1"

        batch = [
            ActivityPayload(timestamp="2026-04-14T11:00:00", app_name="OffA", duration_sec=5),
            ActivityPayload(timestamp="2026-04-14T11:00:05", app_name="OffB", duration_sec=5),
        ]
        ids = await runtime.push_batch(batch)
        assert ids == []

        queue_file = collector_data / "queue" / "pending.jsonl"
        assert queue_file.exists()
        with queue_file.open() as f:
            lines = f.readlines()
        assert len(lines) == 2

        # Restore real URL and send another batch
        runtime._backend._server_url = server.url.rstrip("/")
        await runtime.push_batch([
            ActivityPayload(
                timestamp="2026-04-14T11:00:10",
                app_name="OnlineAgain", duration_sec=5,
            )
        ])

        # Queue drained
        assert not queue_file.exists() or queue_file.stat().st_size == 0

        # All 3 activities landed on server
        conn = sqlite3.connect(server.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT app_name FROM activities WHERE machine_id = ? ORDER BY id",
            (machine_id,),
        ).fetchall()
        conn.close()

        app_names = [r["app_name"] for r in rows]
        assert app_names == ["OffA", "OffB", "OnlineAgain"]

        await runtime.close()
