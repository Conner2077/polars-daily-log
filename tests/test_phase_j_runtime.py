"""Phase J runtime tests — collector consumes override + pause from heartbeat.

These are in-process tests using the same ServerHandle from Phase F.
"""
import asyncio
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


# ─── Reuse ServerHandle pattern ──────────────────────────────────────

def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


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


async def _wait(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=2.0) as c:
        while time.time() < deadline:
            try:
                r = await c.get(f"{url}/api/collectors")
                if r.status_code < 500:
                    return True
            except (httpx.HTTPError, ConnectionError):
                pass
            await asyncio.sleep(0.2)
    return False


class ServerHandle:
    def __init__(self, tmp_path: Path):
        self._tmp = tmp_path
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.db_path = tmp_path / "server_data" / "data.db"
        self._proc: Optional[subprocess.Popen] = None

    async def __aenter__(self):
        cfg = _write_server_config(self._tmp, self.port)
        import os as _os
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "auto_daily_log", "--config", str(cfg)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=Path(__file__).parent.parent,
            env={**_os.environ, "HOME": str(self._tmp)},
        )
        if not await _wait(self.url):
            self._proc.kill()
            raise RuntimeError("server start failed")
        return self

    async def __aexit__(self, *_):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


# ─── J tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_receives_is_paused_and_override(tmp_path):
    async with ServerHandle(tmp_path) as server:
        runtime = CollectorRuntime(CollectorConfig(
            server_url=server.url, name="HB-Test",
            data_dir=str(tmp_path / "c"),
            interval_sec=30, ocr_enabled=False,
        ))
        mid = await runtime.ensure_registered()

        # No override/pause yet
        r = await runtime.heartbeat()
        assert r is not None
        assert r["is_paused"] is False
        assert r["config_override"] is None
        # Runtime's internal state reflects
        assert runtime.paused is False
        assert runtime.config.interval_sec == 30
        assert runtime.config.ocr_enabled is False

        # Server-side: set override + pause
        async with httpx.AsyncClient() as http:
            await http.put(
                f"{server.url}/api/collectors/{mid}/config",
                json={"interval_sec": 60, "ocr_enabled": True},
            )
            await http.post(f"{server.url}/api/collectors/{mid}/pause")

        # Next heartbeat should receive both
        r2 = await runtime.heartbeat()
        assert r2["is_paused"] is True
        assert r2["config_override"] == {"interval_sec": 60, "ocr_enabled": True}

        # Runtime's in-memory config updated
        assert runtime.paused is True
        assert runtime.config.interval_sec == 60
        assert runtime.config.ocr_enabled is True

        await runtime.close()


@pytest.mark.asyncio
async def test_paused_collector_does_not_ingest_during_run(tmp_path):
    """After server sets pause=true, sample_once is not called during run loop."""
    async with ServerHandle(tmp_path) as server:
        runtime = CollectorRuntime(CollectorConfig(
            server_url=server.url, name="Pause-Test",
            data_dir=str(tmp_path / "c"),
            interval_sec=1,  # fast to observe
        ))
        mid = await runtime.ensure_registered()

        # Simulate pause via direct property (we're testing sample behavior;
        # heartbeat wiring is tested separately above)
        runtime.set_paused(True)

        # Count sample_once invocations via monkey-patch
        calls = {"n": 0}
        orig_sample = runtime.sample_once

        async def counting_sample():
            calls["n"] += 1
            return await orig_sample()

        runtime.sample_once = counting_sample  # type: ignore

        # Run briefly
        run_task = asyncio.create_task(runtime.run())
        await asyncio.sleep(2.2)
        runtime.stop()
        try:
            await asyncio.wait_for(run_task, timeout=3)
        except asyncio.TimeoutError:
            pass

        assert calls["n"] == 0, f"paused runtime sampled {calls['n']} times"

        # Resume and confirm it samples again
        runtime.set_paused(False)
        run_task2 = asyncio.create_task(runtime.run())
        await asyncio.sleep(2.2)
        runtime.stop()
        try:
            await asyncio.wait_for(run_task2, timeout=3)
        except asyncio.TimeoutError:
            pass

        assert calls["n"] > 0, "resumed runtime should have sampled"

        await runtime.close()


@pytest.mark.asyncio
async def test_config_override_unknown_key_ignored(tmp_path):
    """Server pushing a key not in HONORED_OVERRIDE_KEYS is a no-op."""
    async with ServerHandle(tmp_path) as server:
        runtime = CollectorRuntime(CollectorConfig(
            server_url=server.url, name="Unknown-Override-Test",
            data_dir=str(tmp_path / "c"),
            interval_sec=45,
        ))
        mid = await runtime.ensure_registered()

        original_interval = runtime.config.interval_sec

        # Apply an override dict directly (simulating what heartbeat would do)
        runtime._apply_override({"interval_sec": 90, "bogus_future_key": "hello"})

        # interval_sec honored
        assert runtime.config.interval_sec == 90
        # bogus key ignored (no attribute set)
        assert not hasattr(runtime.config, "bogus_future_key") or \
               getattr(runtime.config, "bogus_future_key", None) != "hello"

        await runtime.close()


@pytest.mark.asyncio
async def test_heartbeat_updates_runtime_config_after_server_change(tmp_path):
    """Set override BEFORE sampling loop runs — runtime should honor it."""
    async with ServerHandle(tmp_path) as server:
        runtime = CollectorRuntime(CollectorConfig(
            server_url=server.url, name="Pre-Pause",
            data_dir=str(tmp_path / "c"),
            interval_sec=30,
        ))
        mid = await runtime.ensure_registered()

        async with httpx.AsyncClient() as http:
            await http.put(
                f"{server.url}/api/collectors/{mid}/config",
                json={"interval_sec": 5, "blocked_apps": ["WeChat"]},
            )

        # Heartbeat pulls it
        await runtime.heartbeat()
        assert runtime.config.interval_sec == 5
        assert runtime.config.blocked_apps == ["WeChat"]

        await runtime.close()
