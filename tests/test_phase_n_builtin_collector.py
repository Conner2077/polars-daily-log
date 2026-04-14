"""Tests for auto-registering the built-in collector and pure-server mode."""
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


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _make_config(tmp_path: Path, port: int, monitor_enabled: bool) -> Path:
    data_dir = tmp_path / "server_data"
    data_dir.mkdir(exist_ok=True)
    cfg = {
        "server": {"host": "127.0.0.1", "port": port},
        "monitor": {
            "enabled": monitor_enabled,
            "interval_sec": 9999,
            "ocr_enabled": False,
        },
        "scheduler": {"enabled": False, "trigger_time": "23:59"},
        "auto_approve": {"enabled": False, "trigger_time": "23:59"},
        "system": {"data_dir": str(data_dir), "language": "zh"},
        "embedding": {"enabled": False, "model": "", "dimensions": 1024},
        "llm": {"engine": "kimi"},
        "git": {"repos": []},
        "jira": {"server_url": "", "pat": ""},
    }
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


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


async def _run_server(tmp_path: Path, monitor_enabled: bool):
    port = _free_port()
    cfg = _make_config(tmp_path, port, monitor_enabled)
    import os as _os
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "auto_daily_log", "--config", str(cfg)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=Path(__file__).parent.parent,
        env={**_os.environ, "HOME": str(tmp_path)},
    )
    url = f"http://127.0.0.1:{port}"
    if not await _wait(url):
        proc.kill()
        raise RuntimeError("server did not start")
    return proc, url, tmp_path / "server_data" / "data.db"


@pytest.mark.asyncio
async def test_builtin_collector_auto_registered_when_monitor_enabled(tmp_path):
    proc, url, db_path = await _run_server(tmp_path, monitor_enabled=True)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{url}/api/collectors")
            assert r.status_code == 200
            collectors = r.json()

        # Exactly one collector: the built-in 'local'
        assert len(collectors) == 1
        c0 = collectors[0]
        assert c0["machine_id"] == "local"
        assert c0["name"] == "Built-in (this machine)"
        assert c0["hostname"] == socket.gethostname()
        # Platform should be detected (macos / windows / linux-x11 / etc)
        assert c0["platform"] in {
            "macos", "windows", "linux-x11", "linux-wayland", "linux-headless",
        }
        assert c0["is_active"] is True
        assert c0["is_paused"] is False
        # Capabilities list must be present (possibly empty on headless)
        assert isinstance(c0["capabilities"], list)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_builtin_collector_not_registered_in_pure_server_mode(tmp_path):
    proc, url, db_path = await _run_server(tmp_path, monitor_enabled=False)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{url}/api/collectors")
            assert r.status_code == 200
            collectors = r.json()
            # Pure server — no built-in collector
            assert collectors == [], f"expected empty list, got {collectors}"

            # Ingest API should still work for remote collectors
            reg = await c.post(f"{url}/api/collectors/register", json={
                "name": "Remote-A", "hostname": "h", "platform": "windows",
                "capabilities": [],
            })
            assert reg.status_code == 200

            # After remote registration, there should be exactly one collector
            r2 = await c.get(f"{url}/api/collectors")
            assert len(r2.json()) == 1
            assert r2.json()[0]["name"] == "Remote-A"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_restart_updates_builtin_metadata_without_duplicate(tmp_path):
    """Starting the server twice must not create a duplicate 'local' row."""
    # First run
    proc1, url1, db_path = await _run_server(tmp_path, monitor_enabled=True)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{url1}/api/collectors")
            before = r.json()
            assert len(before) == 1
            orig_id = before[0]["id"]
    finally:
        proc1.terminate()
        proc1.wait(timeout=5)

    # Second run (same data_dir)
    proc2, url2, _ = await _run_server(tmp_path, monitor_enabled=True)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{url2}/api/collectors")
            after = r.json()
            # Still exactly one — the upsert on restart
            assert len(after) == 1
            assert after[0]["id"] == orig_id
            assert after[0]["machine_id"] == "local"
    finally:
        proc2.terminate()
        proc2.wait(timeout=5)


@pytest.mark.asyncio
async def test_builtin_and_remote_collectors_coexist(tmp_path):
    proc, url, db_path = await _run_server(tmp_path, monitor_enabled=True)
    try:
        async with httpx.AsyncClient() as c:
            # Register a remote collector
            reg = await c.post(f"{url}/api/collectors/register", json={
                "name": "Remote-Win", "hostname": "winbox", "platform": "windows",
                "capabilities": ["screenshot", "idle"],
            })
            assert reg.status_code == 200

            r = await c.get(f"{url}/api/collectors")
            collectors = r.json()

        assert len(collectors) == 2
        names = {c["name"] for c in collectors}
        assert names == {"Built-in (this machine)", "Remote-Win"}

        mids = {c["machine_id"] for c in collectors}
        assert "local" in mids
        assert any(m != "local" and m.startswith("m-") for m in mids)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
