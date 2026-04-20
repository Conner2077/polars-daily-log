"""Tests for LLM base URL normalization (protocol-aware)."""
import pytest
from auto_daily_log.summarizer.url_helper import normalize_base_url


class TestNormalizeBaseUrl:
    def test_empty_returns_empty(self):
        assert normalize_base_url("") == ""

    def test_clean_root_unchanged(self):
        assert normalize_base_url("https://api.moonshot.cn/v1", "openai_compat") == "https://api.moonshot.cn/v1"

    def test_trailing_slash_stripped(self):
        assert normalize_base_url("https://api.moonshot.cn/v1/", "openai_compat") == "https://api.moonshot.cn/v1"

    def test_multiple_trailing_slashes_stripped(self):
        assert normalize_base_url("https://api.moonshot.cn/v1///", "openai_compat") == "https://api.moonshot.cn/v1"

    # ─── openai_compat: base should KEEP /v1 ─────────────────────────

    def test_openai_compat_full_endpoint_stripped_keeps_v1(self):
        assert normalize_base_url(
            "https://api.moonshot.cn/v1/chat/completions", "openai_compat"
        ) == "https://api.moonshot.cn/v1"

    def test_openai_full_url_stripped_keeps_v1(self):
        assert normalize_base_url(
            "https://api.openai.com/v1/chat/completions", "openai_compat"
        ) == "https://api.openai.com/v1"

    def test_endpoint_with_trailing_slash(self):
        assert normalize_base_url(
            "https://api.moonshot.cn/v1/chat/completions/", "openai_compat"
        ) == "https://api.moonshot.cn/v1"

    def test_case_insensitive_endpoint_match(self):
        assert normalize_base_url(
            "https://api.moonshot.cn/v1/Chat/Completions", "openai_compat"
        ) == "https://api.moonshot.cn/v1"

    def test_openai_compat_v1_alone_kept(self):
        assert normalize_base_url("https://api.openai.com/v1", "openai_compat") == "https://api.openai.com/v1"

    def test_custom_port_preserved(self):
        assert normalize_base_url(
            "https://gpt.internal.company.com:8443/v1/chat/completions", "openai_compat"
        ) == "https://gpt.internal.company.com:8443/v1"

    # ─── anthropic: base must NOT end with /v1 ────────────────────────

    def test_anthropic_root_unchanged(self):
        assert normalize_base_url(
            "https://api.anthropic.com", "anthropic"
        ) == "https://api.anthropic.com"

    def test_anthropic_full_endpoint_stripped(self):
        assert normalize_base_url(
            "https://api.anthropic.com/v1/messages", "anthropic"
        ) == "https://api.anthropic.com"

    def test_anthropic_strips_bare_v1(self):
        assert normalize_base_url(
            "https://api.anthropic.com/v1", "anthropic"
        ) == "https://api.anthropic.com"

    def test_anthropic_strips_bare_v1_with_slash(self):
        assert normalize_base_url(
            "https://api.anthropic.com/v1/", "anthropic"
        ) == "https://api.anthropic.com"

    def test_anthropic_messages_without_v1(self):
        assert normalize_base_url(
            "https://api.anthropic.com/messages", "anthropic"
        ) == "https://api.anthropic.com"

    # ─── Ollama ───────────────────────────────────────────────────────

    def test_ollama_api_tags_stripped(self):
        assert normalize_base_url(
            "http://localhost:11434/api/tags", "ollama"
        ) == "http://localhost:11434"

    def test_ollama_api_chat_stripped(self):
        assert normalize_base_url(
            "http://localhost:11434/api/chat", "ollama"
        ) == "http://localhost:11434"

    def test_ollama_root_unchanged(self):
        assert normalize_base_url(
            "http://localhost:11434", "ollama"
        ) == "http://localhost:11434"

    # ─── General ──────────────────────────────────────────────────────

    def test_whitespace_stripped(self):
        assert normalize_base_url(
            "  https://api.moonshot.cn/v1  ", "openai_compat"
        ) == "https://api.moonshot.cn/v1"

    def test_does_not_strip_non_endpoint_paths(self):
        assert normalize_base_url(
            "https://proxy.example.com/openai-proxy", "openai_compat"
        ) == "https://proxy.example.com/openai-proxy"

    def test_engine_none_behaves_like_openai_compat(self):
        """No engine hint = keep /v1 (OpenAI convention)."""
        assert normalize_base_url(
            "https://api.openai.com/v1/chat/completions"
        ) == "https://api.openai.com/v1"

    def test_non_anthropic_engine_keeps_v1(self):
        assert normalize_base_url(
            "https://api.example.com/v1/chat/completions", "openai_compat"
        ) == "https://api.example.com/v1"

    # ─── openai_compat: bare host is NOT touched ──────────────────────
    # Auto-appending /v1 would break reverse proxies and custom gateways.
    # "forgot /v1" hinting belongs in the UI, not the normalizer.

    def test_openai_compat_bare_localhost_preserved(self):
        """Local gateways stay as-is; UI warns if /v1 is missing."""
        assert normalize_base_url(
            "http://localhost:3001", "openai_compat"
        ) == "http://localhost:3001"

    def test_openai_compat_bare_api_host_preserved(self):
        assert normalize_base_url(
            "https://api.openai.com", "openai_compat"
        ) == "https://api.openai.com"

    def test_openai_compat_proxy_path_preserved(self):
        """Reverse proxy with custom mount point stays untouched."""
        assert normalize_base_url(
            "https://proxy.example.com/openai-proxy", "openai_compat"
        ) == "https://proxy.example.com/openai-proxy"


class TestPutSettingNormalizesBaseUrl:
    @pytest.mark.asyncio
    async def test_put_llm_base_url_normalizes_for_openai_compat(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        try:
            client = TestClient(app)
            client.put("/api/settings/llm_engine", json={"value": "openai_compat"})
            r = client.put(
                "/api/settings/llm_base_url",
                json={"value": "https://api.moonshot.cn/v1/chat/completions/"},
            )
            assert r.status_code == 200
            assert r.json() == {
                "key": "llm_base_url",
                "value": "https://api.moonshot.cn/v1",
            }
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_put_llm_base_url_normalizes_for_anthropic(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        try:
            client = TestClient(app)
            client.put("/api/settings/llm_engine", json={"value": "anthropic"})
            r = client.put(
                "/api/settings/llm_base_url",
                json={"value": "https://api.anthropic.com/v1/messages"},
            )
            assert r.status_code == 200
            assert r.json()["value"] == "https://api.anthropic.com"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_put_non_url_setting_unchanged(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        try:
            client = TestClient(app)
            r = client.put(
                "/api/settings/llm_api_key",
                json={"value": "  sk-trailing-space  "},
            )
            assert r.json()["value"] == "  sk-trailing-space  "
        finally:
            await db.close()
