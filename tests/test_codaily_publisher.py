"""Tests for CoDailyPublisher — push-contract v1.0 adapter."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from auto_daily_log.models.database import Database
from auto_daily_log.publishers import WorklogPublisher
from auto_daily_log.publishers.codaily import CoDailyPublisher
from auto_daily_log.web.app import create_app


@pytest_asyncio.fixture
async def env(tmp_path):
    db = Database(tmp_path / "codaily.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, db


# ══════════════════════════════════════════════════════════════════════
# Protocol + basic shape
# ══════════════════════════════════════════════════════════════════════

def test_codaily_publisher_is_worklog_publisher():
    pub = CoDailyPublisher({"url": "https://codaily.example.com", "token": "t"})
    assert isinstance(pub, WorklogPublisher)
    assert pub.name == "codaily"
    assert pub.display_name == "CoDaily（日报广场）"


def test_codaily_config_defaults():
    pub = CoDailyPublisher({"url": "https://c.example.com/", "token": "t"})
    assert pub._url == "https://c.example.com"  # trailing slash stripped
    assert pub._scope == "day"
    assert pub._timeout == 15


def test_codaily_config_overrides():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t", "scope": "week", "timeout": 30})
    assert pub._scope == "week"
    assert pub._timeout == 30


# ══════════════════════════════════════════════════════════════════════
# Body construction (push-contract v1.0)
# ══════════════════════════════════════════════════════════════════════

def test_codaily_body_with_issue_key():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    body = pub._build_body(
        issue_key="POLARDB-123",
        time_spent_sec=7200,
        comment="Fixed SQL parser bug",
        started="2026-04-19T21:00:00.000+0800",
    )
    assert body == {
        "post_date": "2026-04-19",
        "scope": "day",
        "content": "Fixed SQL parser bug",
        "content_type": "markdown",
        "metadata": {
            "schema_version": "1.0",
            "time_spent_sec": 7200,
            "issue_keys": ["POLARDB-123"],
            "entries": [
                {"issue_key": "POLARDB-123", "hours": 2.0, "summary": "Fixed SQL parser bug"},
            ],
        },
        "source": "pdl",
    }


def test_codaily_body_skips_issue_keys_for_daily_sentinel():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    body = pub._build_body(
        issue_key="DAILY",
        time_spent_sec=28800,
        comment="Daily summary body",
        started="2026-04-19T21:00:00.000+0800",
    )
    assert "issue_keys" not in body["metadata"]
    assert "entries" not in body["metadata"]
    assert body["metadata"]["time_spent_sec"] == 28800
    assert body["content"] == "Daily summary body"


def test_codaily_body_skips_issue_keys_for_all_sentinel():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    body = pub._build_body(
        issue_key="ALL",
        time_spent_sec=3600,
        comment="All-up summary",
        started="2026-04-19T21:00",
    )
    assert "issue_keys" not in body["metadata"]


def test_codaily_body_truncates_entry_summary():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    long = "x" * 500
    body = pub._build_body(
        issue_key="POLARDB-1",
        time_spent_sec=3600,
        comment=long,
        started="2026-04-19T21:00",
    )
    assert body["metadata"]["entries"][0]["summary"] == "x" * 200
    assert body["content"] == long  # full comment preserved, only entry summary truncated


def test_codaily_body_scope_from_config():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t", "scope": "weekly-retro"})
    body = pub._build_body(
        issue_key="POLARDB-1",
        time_spent_sec=3600,
        comment="c",
        started="2026-04-19T21:00",
    )
    assert body["scope"] == "weekly-retro"


def test_codaily_body_empty_started():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    body = pub._build_body(issue_key="X-1", time_spent_sec=0, comment="c", started="")
    assert body["post_date"] == ""


# ══════════════════════════════════════════════════════════════════════
# submit() — HTTP paths
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_submit_missing_url():
    pub = CoDailyPublisher({"token": "t"})
    result = await pub.submit(issue_key="X-1", time_spent_sec=3600, comment="c", started="2026-04-19T21:00")
    assert result.success is False
    assert result.error == "CoDaily URL 未配置"
    assert result.platform == "codaily"


@pytest.mark.asyncio
async def test_submit_missing_token():
    pub = CoDailyPublisher({"url": "https://c.example.com"})
    result = await pub.submit(issue_key="X-1", time_spent_sec=3600, comment="c", started="2026-04-19T21:00")
    assert result.success is False
    assert result.error == "CoDaily token 未配置"


@pytest.mark.asyncio
async def test_submit_success_201_created():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    fake_response = MagicMock()
    fake_response.status_code = 201
    fake_response.text = '{"id":42}'
    fake_response.json.return_value = {"id": 42}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        result = await pub.submit(
            issue_key="POLARDB-1", time_spent_sec=3600, comment="c",
            started="2026-04-19T21:00",
        )
    assert result.success is True
    assert result.worklog_id == "42"
    assert result.platform == "codaily"
    assert result.raw["status_code"] == 201


@pytest.mark.asyncio
async def test_submit_success_200_updated():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.text = '{"id":99}'
    fake_response.json.return_value = {"id": 99}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        result = await pub.submit(
            issue_key="POLARDB-1", time_spent_sec=3600, comment="c",
            started="2026-04-19T21:00",
        )
    assert result.success is True
    assert result.worklog_id == "99"


@pytest.mark.asyncio
async def test_submit_http_400_returns_detail():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    fake_response = MagicMock()
    fake_response.status_code = 400
    fake_response.text = '{"detail":"Invalid metadata"}'
    fake_response.json.return_value = {"detail": "Invalid metadata"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        result = await pub.submit(
            issue_key="POLARDB-1", time_spent_sec=3600, comment="c",
            started="2026-04-19T21:00",
        )
    assert result.success is False
    assert "HTTP 400" in result.error
    assert "Invalid metadata" in result.error


@pytest.mark.asyncio
async def test_submit_http_401_token_revoked():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "bad"})
    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.text = '{"detail":"invalid_token"}'
    fake_response.json.return_value = {"detail": "invalid_token"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        result = await pub.submit(
            issue_key="X-1", time_spent_sec=3600, comment="c",
            started="2026-04-19T21:00",
        )
    assert result.success is False
    assert "HTTP 401" in result.error


@pytest.mark.asyncio
async def test_submit_timeout():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t", "timeout": 1})
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
        result = await pub.submit(
            issue_key="X-1", time_spent_sec=3600, comment="c",
            started="2026-04-19T21:00",
        )
    assert result.success is False
    assert "超时" in result.error


@pytest.mark.asyncio
async def test_submit_sends_bearer_header_and_body():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "secret-123"})
    fake_response = MagicMock()
    fake_response.status_code = 201
    fake_response.text = '{"id":1}'
    fake_response.json.return_value = {"id": 1}
    mock_post = AsyncMock(return_value=fake_response)
    with patch("httpx.AsyncClient.post", new=mock_post):
        await pub.submit(
            issue_key="POLARDB-9", time_spent_sec=3600, comment="x",
            started="2026-04-19T21:00",
        )
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-123"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json"]["post_date"] == "2026-04-19"
    assert kwargs["json"]["metadata"]["issue_keys"] == ["POLARDB-9"]


# ══════════════════════════════════════════════════════════════════════
# delete() and check_connection()
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_success():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    fake_response = MagicMock()
    fake_response.status_code = 204
    with patch("httpx.AsyncClient.delete", new=AsyncMock(return_value=fake_response)):
        ok = await pub.delete("42", issue_key="POLARDB-1")
    assert ok is True


@pytest.mark.asyncio
async def test_delete_404_is_failure():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    fake_response = MagicMock()
    fake_response.status_code = 404
    with patch("httpx.AsyncClient.delete", new=AsyncMock(return_value=fake_response)):
        ok = await pub.delete("9999", issue_key="X-1")
    assert ok is False


@pytest.mark.asyncio
async def test_delete_missing_worklog_id():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    assert await pub.delete("", issue_key="X-1") is False


@pytest.mark.asyncio
async def test_delete_missing_config():
    pub = CoDailyPublisher({})
    assert await pub.delete("42", issue_key="X-1") is False


@pytest.mark.asyncio
async def test_check_connection_success():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    fake_response = MagicMock()
    fake_response.status_code = 200
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)):
        assert await pub.check_connection() is True


@pytest.mark.asyncio
async def test_check_connection_server_error():
    pub = CoDailyPublisher({"url": "https://c.example.com", "token": "t"})
    fake_response = MagicMock()
    fake_response.status_code = 503
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)):
        assert await pub.check_connection() is False


@pytest.mark.asyncio
async def test_check_connection_missing_url():
    pub = CoDailyPublisher({"token": "t"})
    assert await pub.check_connection() is False


# ══════════════════════════════════════════════════════════════════════
# Registry resolves codaily with per-type config
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_registry_resolves_codaily_with_config(env):
    client, db = env
    await client.post("/api/summary-types", json={
        "name": "codaily-daily",
        "display_name": "日报广场",
        "scope_rule": '{"type":"day"}',
        "publisher_name": "codaily",
        "publisher_config": '{"url":"https://codaily.fanruan.com","token":"tok","scope":"day"}',
    })
    from auto_daily_log.publishers.registry import get_publisher
    pub = await get_publisher(db, "codaily-daily")
    assert pub.name == "codaily"
    assert pub._url == "https://codaily.fanruan.com"
    assert pub._token == "tok"
    assert pub._scope == "day"
