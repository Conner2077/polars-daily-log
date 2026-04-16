"""Protocol tests — engine → 3 canonical protocols (openai_compat / anthropic / ollama)."""
import pytest

from auto_daily_log.summarizer.engine import VALID_PROTOCOLS, get_llm_engine
from auto_daily_log.summarizer.openai_compat import OpenAICompatEngine
from auto_daily_log.summarizer.claude_engine import ClaudeEngine
from auto_daily_log.summarizer.ollama import OllamaEngine
from auto_daily_log.config import LLMConfig, LLMProviderConfig


class TestValidProtocols:
    def test_exactly_three_protocols(self):
        assert VALID_PROTOCOLS == ("openai_compat", "anthropic", "ollama")


class TestGetLLMEngine:
    def _cfg(self, protocol: str):
        provider = LLMProviderConfig(api_key="k", model="m", base_url="http://x")
        return LLMConfig(engine=protocol, **{protocol: provider})

    def test_openai_compat_returns_openai_compat_engine(self):
        engine = get_llm_engine(self._cfg("openai_compat"))
        assert isinstance(engine, OpenAICompatEngine)

    def test_anthropic_returns_claude_engine(self):
        engine = get_llm_engine(self._cfg("anthropic"))
        assert isinstance(engine, ClaudeEngine)

    def test_ollama_returns_ollama_engine(self):
        engine = get_llm_engine(self._cfg("ollama"))
        assert isinstance(engine, OllamaEngine)

    def test_unknown_engine_raises_value_error(self):
        cfg = LLMConfig(engine="not-a-real-engine")
        with pytest.raises(ValueError) as exc_info:
            get_llm_engine(cfg)
        assert "not-a-real-engine" in str(exc_info.value)


class TestCheckLLMEndpoint:
    @pytest.mark.asyncio
    async def test_check_llm_accepts_openai_compat(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient
        from unittest.mock import patch

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        client = TestClient(app)

        captured_url = {}

        async def fake_post(self, url, json=None, headers=None, **kwargs):
            captured_url["url"] = url
            class R:
                status_code = 200
                text = "ok"
                def json(self_): return {"choices": [{"message": {"content": "hi"}}]}
            return R()

        with patch("httpx.AsyncClient.post", new=fake_post):
            r = client.post("/api/settings/check-llm", json={
                "engine": "openai_compat",
                "api_key": "sk-test",
                "model": "moonshot-v1-8k",
                "base_url": "https://api.moonshot.cn/v1",
            })
            assert r.status_code == 200
            assert r.json()["valid"] is True
            assert captured_url["url"] == "https://api.moonshot.cn/v1/chat/completions"

        await db.close()

    @pytest.mark.asyncio
    async def test_check_llm_accepts_anthropic_and_enables_streaming(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient
        from unittest.mock import patch

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        client = TestClient(app)

        captured = {}

        async def fake_post(self, url, json=None, headers=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            class R:
                status_code = 200
                text = "ok"
                def json(self_): return {"content": [{"text": "hi"}]}
            return R()

        with patch("httpx.AsyncClient.post", new=fake_post):
            r = client.post("/api/settings/check-llm", json={
                "engine": "anthropic",
                "api_key": "sk-test",
                "model": "claude-sonnet-4-20250514",
                "base_url": "https://api.anthropic.com/v1/messages",
            })
            assert r.status_code == 200
            assert r.json()["valid"] is True
            assert captured["url"] == "https://api.anthropic.com/v1/messages"
            assert captured["json"] == {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1,
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            }
            assert captured["headers"]["Accept"] == "text/event-stream"

        await db.close()

        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        client = TestClient(app)

        r = client.post("/api/settings/check-llm", json={
            "engine": "kimi",
            "api_key": "sk-test",
            "model": "x",
            "base_url": "https://x",
        })
        assert r.status_code == 200
        assert r.json()["valid"] is False
        assert "Unknown protocol" in r.json()["message"]

        await db.close()


class TestBuiltinKimiFallback:
    @pytest.mark.asyncio
    async def test_fallback_returns_engine_when_settings_empty(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.api.worklogs import _get_llm_engine_from_settings

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        engine = await _get_llm_engine_from_settings(db)
        assert isinstance(engine, OpenAICompatEngine)
        assert engine._config.model == "moonshot-v1-8k"
        assert engine._config.base_url == "https://api.moonshot.cn/v1"
        assert engine._config.api_key.startswith("sk-kimi-")
        await db.close()

    @pytest.mark.asyncio
    async def test_user_key_overrides_builtin(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.api.worklogs import _get_llm_engine_from_settings

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        await db.execute("INSERT INTO settings (key, value) VALUES ('llm_api_key', 'sk-user-own-key')")
        await db.execute("INSERT INTO settings (key, value) VALUES ('llm_engine', 'openai_compat')")
        engine = await _get_llm_engine_from_settings(db)
        assert engine._config.api_key == "sk-user-own-key"
        await db.close()


class TestLegacyDBValueMigration:
    """Startup migration rewrites legacy engine values to canonical protocols."""

    @pytest.mark.asyncio
    async def test_legacy_claude_migrated_to_anthropic(self, tmp_path):
        from auto_daily_log.models.database import Database
        # Initialize once, insert legacy value, re-initialize to trigger migration
        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        await db.execute("INSERT INTO settings (key, value) VALUES ('llm_engine', 'claude')")
        await db.close()

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        row = await db.fetch_one("SELECT value FROM settings WHERE key='llm_engine'")
        assert row["value"] == "anthropic"
        await db.close()

    @pytest.mark.asyncio
    async def test_legacy_kimi_migrated_to_openai_compat(self, tmp_path):
        from auto_daily_log.models.database import Database
        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        await db.execute("INSERT INTO settings (key, value) VALUES ('llm_engine', 'kimi')")
        await db.close()

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        row = await db.fetch_one("SELECT value FROM settings WHERE key='llm_engine'")
        assert row["value"] == "openai_compat"
        await db.close()
