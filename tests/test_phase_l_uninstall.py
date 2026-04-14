"""Phase L — collector uninstall command."""
import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
import pytest
import yaml

from auto_daily_log_collector.config import CollectorConfig
from auto_daily_log_collector.credentials import load_credentials
from auto_daily_log_collector.runner import CollectorRuntime
from auto_daily_log_collector.__main__ import _uninstall


# Reuse pattern from phase_f
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


@pytest.mark.asyncio
async def test_uninstall_deregisters_server_and_clears_local(tmp_path):
    async with ServerHandle(tmp_path) as server:
        collector_data = tmp_path / "c"
        config = CollectorConfig(
            server_url=server.url, name="Uninstall-Test",
            data_dir=str(collector_data), interval_sec=30,
        )
        runtime = CollectorRuntime(config)
        machine_id = await runtime.ensure_registered()
        await runtime.close()

        # Credentials exist
        assert config.credentials_file.exists()
        creds_before = load_credentials(config.credentials_file)
        assert creds_before is not None
        assert creds_before.machine_id == machine_id

        # Also create a fake queue dir to verify it gets cleaned
        queue_dir = collector_data / "queue"
        queue_dir.mkdir(exist_ok=True)
        (queue_dir / "pending.jsonl").write_text('{"kind":"activities"}\n')

        # Collector visible in server listing
        async with httpx.AsyncClient() as http:
            active_before = await http.get(f"{server.url}/api/collectors")
            ids = [c["machine_id"] for c in active_before.json()]
            assert machine_id in ids

        # Run uninstall
        await _uninstall(config)

        # Credentials gone
        assert not config.credentials_file.exists()
        # Queue dir gone
        assert not queue_dir.exists()

        # Server marks collector inactive (removed from active listing)
        async with httpx.AsyncClient() as http:
            active_after = await http.get(f"{server.url}/api/collectors")
            ids = [c["machine_id"] for c in active_after.json()]
            assert machine_id not in ids


@pytest.mark.asyncio
async def test_uninstall_without_credentials_is_idempotent(tmp_path):
    """Uninstalling a collector that was never registered doesn't crash."""
    async with ServerHandle(tmp_path) as server:
        config = CollectorConfig(
            server_url=server.url, name="Never-Registered",
            data_dir=str(tmp_path / "no-creds"),
        )
        # No registration; credentials file doesn't exist
        assert not config.credentials_file.exists()
        # Should not raise
        await _uninstall(config)


@pytest.mark.asyncio
async def test_uninstall_with_unreachable_server_still_clears_local(tmp_path):
    """If server is unreachable during uninstall, local state still gets cleared."""
    # Create credentials manually (simulating a collector whose server died)
    collector_data = tmp_path / "cd"
    config = CollectorConfig(
        server_url="http://unreachable.invalid:9999",
        name="Orphan",
        data_dir=str(collector_data),
    )
    from auto_daily_log_collector.credentials import save_credentials
    save_credentials(config.credentials_file, "m-orphan", "t" * 32)
    queue_dir = collector_data / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "x.jsonl").write_text("old data\n")

    assert config.credentials_file.exists()
    assert queue_dir.exists()

    await _uninstall(config)

    assert not config.credentials_file.exists()
    assert not queue_dir.exists()
