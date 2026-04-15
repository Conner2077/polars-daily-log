"""Built-in collector token self-distribution.

The server mints a plaintext token (stored in ``settings``) on first
startup and writes its sha256 hash onto ``collectors.token_hash`` for
``machine_id='local'``. Subsequent startups read the same token back —
rotation would invalidate any cached HTTPBackend client, so idempotency
is load-bearing.
"""
import hashlib

import pytest

from auto_daily_log.app import Application
from auto_daily_log.config import AppConfig


def _make_config(tmp_path) -> AppConfig:
    # Minimal config pointing at an isolated data_dir; monitor/scheduler
    # flags don't matter because _register_builtin_collector is invoked
    # directly in these tests.
    return AppConfig.model_validate({
        "system": {"data_dir": str(tmp_path)},
        "server": {"host": "127.0.0.1", "port": 18888},
        "monitor": {"enabled": True, "interval_sec": 30, "idle_threshold_sec": 180},
        "scheduler": {"enabled": False, "trigger_time": "18:30"},
        "llm": {"provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-x"},
        "embedding": {"provider": "openai", "model": "text-embedding-3-small", "api_key": "sk-x", "dimensions": 128},
        "auto_approve": {"enabled": False, "trigger_time": "18:45"},
    })


@pytest.mark.asyncio
async def test_register_builtin_collector_mints_token_first_time(tmp_path):
    app = Application(_make_config(tmp_path))
    await app._init_db()
    try:
        await app._register_builtin_collector()

        assert app._builtin_token is not None
        assert app._builtin_token.startswith("tk-builtin-")

        row = await app.db.fetch_one(
            "SELECT value FROM settings WHERE key = ?",
            ("builtin_collector_token",),
        )
        assert row is not None
        assert row["value"] == app._builtin_token

        collector = await app.db.fetch_one(
            "SELECT token_hash FROM collectors WHERE machine_id = ?",
            ("local",),
        )
        expected_hash = hashlib.sha256(app._builtin_token.encode("utf-8")).hexdigest()
        assert collector["token_hash"] == expected_hash
    finally:
        await app.db.close()


@pytest.mark.asyncio
async def test_register_builtin_collector_is_idempotent(tmp_path):
    app = Application(_make_config(tmp_path))
    await app._init_db()
    try:
        await app._register_builtin_collector()
        first_token = app._builtin_token

        # Second call: should reuse the existing settings value, NOT mint
        # a fresh token (otherwise every restart would invalidate the
        # cached HTTPBackend in the in-process collector).
        await app._register_builtin_collector()
        assert app._builtin_token == first_token

        # And token_hash must still match the plaintext
        collector = await app.db.fetch_one(
            "SELECT token_hash FROM collectors WHERE machine_id = ?",
            ("local",),
        )
        expected_hash = hashlib.sha256(first_token.encode("utf-8")).hexdigest()
        assert collector["token_hash"] == expected_hash
    finally:
        await app.db.close()


@pytest.mark.asyncio
async def test_register_builtin_collector_token_hash_matches_plaintext(tmp_path):
    app = Application(_make_config(tmp_path))
    await app._init_db()
    try:
        await app._register_builtin_collector()
        token = app._builtin_token

        # Manual sha256 of plaintext must equal what /api/ingest/* sees
        # when validating Bearer tokens in _authenticate_collector.
        manual_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = await app.db.fetch_one(
            "SELECT token_hash FROM collectors WHERE machine_id = ?",
            ("local",),
        )
        assert row["token_hash"] == manual_hash
    finally:
        await app.db.close()
