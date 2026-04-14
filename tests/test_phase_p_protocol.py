"""Protocol unification tests — engine → 3 canonical protocols."""
import pytest

from auto_daily_log.summarizer.engine import resolve_protocol, get_llm_engine
from auto_daily_log.summarizer.openai_compat import OpenAICompatEngine
from auto_daily_log.summarizer.claude_engine import ClaudeEngine
from auto_daily_log.summarizer.ollama import OllamaEngine
from auto_daily_log.config import LLMConfig, LLMProviderConfig


class TestResolveProtocol:
    def test_legacy_kimi_maps_to_openai_compat(self):
        assert resolve_protocol("kimi") == "openai_compat"

    def test_legacy_openai_maps_to_openai_compat(self):
        assert resolve_protocol("openai") == "openai_compat"

    def test_legacy_claude_maps_to_anthropic(self):
        assert resolve_protocol("claude") == "anthropic"

    def test_canonical_openai_compat_passthrough(self):
        assert resolve_protocol("openai_compat") == "openai_compat"

    def test_canonical_anthropic_passthrough(self):
        assert resolve_protocol("anthropic") == "anthropic"

    def test_ollama_unchanged(self):
        assert resolve_protocol("ollama") == "ollama"

    def test_case_insensitive(self):
        assert resolve_protocol("KIMI") == "openai_compat"
        assert resolve_protocol("Claude") == "anthropic"

    def test_empty_defaults_to_openai_compat(self):
        assert resolve_protocol("") == "openai_compat"
        assert resolve_protocol(None or "") == "openai_compat"


class TestGetLLMEngine:
    def _cfg(self, engine: str, api_key: str = "k", model: str = "m", base_url: str = "http://x"):
        provider = LLMProviderConfig(api_key=api_key, model=model, base_url=base_url)
        # Route provider into the right slot for the engine name
        slot = engine if engine in ("kimi", "openai", "claude", "ollama") else "kimi"
        return LLMConfig(engine=engine, **{slot: provider})

    def test_kimi_returns_openai_compat_engine(self):
        engine = get_llm_engine(self._cfg("kimi"))
        assert isinstance(engine, OpenAICompatEngine)

    def test_openai_returns_openai_compat_engine(self):
        engine = get_llm_engine(self._cfg("openai"))
        assert isinstance(engine, OpenAICompatEngine)

    def test_openai_compat_returns_openai_compat_engine(self):
        engine = get_llm_engine(self._cfg("openai_compat"))
        assert isinstance(engine, OpenAICompatEngine)

    def test_claude_returns_anthropic_engine(self):
        cfg = self._cfg("claude")
        engine = get_llm_engine(cfg)
        assert isinstance(engine, ClaudeEngine)

    def test_anthropic_returns_anthropic_engine(self):
        cfg = self._cfg("anthropic")
        # Must have claude slot populated for anthropic protocol
        cfg.claude = LLMProviderConfig(api_key="k", model="m", base_url="http://x")
        engine = get_llm_engine(cfg)
        assert isinstance(engine, ClaudeEngine)

    def test_ollama_returns_ollama_engine(self):
        engine = get_llm_engine(self._cfg("ollama"))
        assert isinstance(engine, OllamaEngine)

    def test_unknown_engine_raises_value_error(self):
        with pytest.raises(ValueError) as exc_info:
            get_llm_engine(self._cfg("not-a-real-engine"))
        assert "not-a-real-engine" in str(exc_info.value)


class TestCheckLLMEndpoint:
    """Accepts both legacy and canonical protocol values."""

    @pytest.mark.asyncio
    async def test_check_llm_accepts_openai_compat(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient
        from unittest.mock import patch, AsyncMock

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        client = TestClient(app)

        async def fake_post(self, url, json=None, headers=None, **kwargs):
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

        await db.close()

    @pytest.mark.asyncio
    async def test_check_llm_legacy_kimi_still_works(self, tmp_path):
        """Old saved settings with engine='kimi' must continue to work."""
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
                "engine": "kimi",
                "api_key": "sk-test",
                "model": "moonshot-v1-8k",
                "base_url": "https://api.moonshot.cn/v1",
            })
            assert r.status_code == 200
            assert r.json()["valid"] is True
            # Must have hit the OpenAI-compatible endpoint
            assert captured_url["url"] == "https://api.moonshot.cn/v1/chat/completions"

        await db.close()


class TestBuiltinKimiFallback:
    """When no api_key is configured, worklogs module should use built-in Kimi."""

    @pytest.mark.asyncio
    async def test_fallback_returns_engine_when_settings_empty(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.api.worklogs import _get_llm_engine_from_settings

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        # No settings written → should use built-in Kimi
        engine = await _get_llm_engine_from_settings(db)
        assert engine is not None
        assert isinstance(engine, OpenAICompatEngine)
        assert engine._config.model == "moonshot-v1-8k"
        assert engine._config.base_url == "https://api.moonshot.cn/v1"
        # Key should be the built-in one (starts with sk-kimi-)
        assert engine._config.api_key.startswith("sk-kimi-")
        await db.close()

    @pytest.mark.asyncio
    async def test_user_key_overrides_builtin(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.api.worklogs import _get_llm_engine_from_settings

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        await db.execute("INSERT INTO settings (key, value) VALUES ('llm_api_key', 'sk-user-own-key')")
        await db.execute("INSERT INTO settings (key, value) VALUES ('llm_engine', 'kimi')")
        engine = await _get_llm_engine_from_settings(db)
        assert engine._config.api_key == "sk-user-own-key"
        await db.close()
