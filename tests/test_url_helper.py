"""Tests for LLM base URL normalization (engine-aware)."""
import pytest
from auto_daily_log.summarizer.url_helper import normalize_base_url


class TestNormalizeBaseUrl:
    def test_empty_returns_empty(self):
        assert normalize_base_url("") == ""

    def test_clean_kimi_root_unchanged(self):
        assert normalize_base_url("https://api.moonshot.cn/v1", "kimi") == "https://api.moonshot.cn/v1"

    def test_trailing_slash_stripped(self):
        assert normalize_base_url("https://api.moonshot.cn/v1/", "kimi") == "https://api.moonshot.cn/v1"

    def test_multiple_trailing_slashes_stripped(self):
        assert normalize_base_url("https://api.moonshot.cn/v1///", "kimi") == "https://api.moonshot.cn/v1"

    # ─── Kimi/OpenAI: base should KEEP /v1 ───────────────────────────

    def test_kimi_full_endpoint_stripped_keeps_v1(self):
        assert normalize_base_url(
            "https://api.moonshot.cn/v1/chat/completions", "kimi"
        ) == "https://api.moonshot.cn/v1"

    def test_openai_full_endpoint_stripped_keeps_v1(self):
        assert normalize_base_url(
            "https://api.openai.com/v1/chat/completions", "openai"
        ) == "https://api.openai.com/v1"

    def test_kimi_endpoint_with_trailing_slash(self):
        assert normalize_base_url(
            "https://api.moonshot.cn/v1/chat/completions/", "kimi"
        ) == "https://api.moonshot.cn/v1"

    def test_kimi_case_insensitive(self):
        assert normalize_base_url(
            "https://api.moonshot.cn/v1/Chat/Completions", "kimi"
        ) == "https://api.moonshot.cn/v1"

    def test_openai_v1_alone_kept(self):
        """/v1 without endpoint suffix should remain for OpenAI-compat."""
        assert normalize_base_url("https://api.openai.com/v1", "openai") == "https://api.openai.com/v1"

    def test_custom_port_preserved_for_openai(self):
        assert normalize_base_url(
            "https://gpt.internal.company.com:8443/v1/chat/completions", "openai"
        ) == "https://gpt.internal.company.com:8443/v1"

    # ─── Claude: base must NOT end with /v1 ──────────────────────────

    def test_claude_root_unchanged(self):
        assert normalize_base_url(
            "https://api.anthropic.com", "claude"
        ) == "https://api.anthropic.com"

    def test_claude_full_endpoint_stripped(self):
        assert normalize_base_url(
            "https://api.anthropic.com/v1/messages", "claude"
        ) == "https://api.anthropic.com"

    def test_claude_strips_bare_v1(self):
        """If user pastes just /v1 for Claude, strip it (client appends /v1/messages)."""
        assert normalize_base_url(
            "https://api.anthropic.com/v1", "claude"
        ) == "https://api.anthropic.com"

    def test_claude_strips_bare_v1_with_slash(self):
        assert normalize_base_url(
            "https://api.anthropic.com/v1/", "claude"
        ) == "https://api.anthropic.com"

    def test_claude_messages_without_v1(self):
        assert normalize_base_url(
            "https://api.anthropic.com/messages", "claude"
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
            "  https://api.moonshot.cn/v1  ", "kimi"
        ) == "https://api.moonshot.cn/v1"

    def test_does_not_strip_non_endpoint_paths(self):
        """A path that isn't a known endpoint should be kept."""
        assert normalize_base_url(
            "https://proxy.example.com/openai-proxy", "openai"
        ) == "https://proxy.example.com/openai-proxy"

    def test_engine_none_behaves_like_openai(self):
        """No engine hint = keep /v1 (OpenAI convention)."""
        assert normalize_base_url(
            "https://api.openai.com/v1/chat/completions"
        ) == "https://api.openai.com/v1"

    def test_unknown_engine_keeps_v1(self):
        """Non-claude engines preserve /v1."""
        assert normalize_base_url(
            "https://api.example.com/v1/chat/completions", "mystery-engine"
        ) == "https://api.example.com/v1"


class TestPutSettingNormalizesBaseUrl:
    """End-to-end: putting llm_base_url via API stores engine-aware normalized value."""

    @pytest.mark.asyncio
    async def test_put_llm_base_url_normalizes_for_kimi(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        try:
            client = TestClient(app)
            # Set engine first so put knows context
            client.put("/api/settings/llm_engine", json={"value": "kimi"})
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
    async def test_put_llm_base_url_normalizes_for_claude(self, tmp_path):
        from auto_daily_log.models.database import Database
        from auto_daily_log.web.app import create_app
        from fastapi.testclient import TestClient

        db = Database(tmp_path / "t.db", embedding_dimensions=128)
        await db.initialize()
        app = create_app(db)
        try:
            client = TestClient(app)
            client.put("/api/settings/llm_engine", json={"value": "claude"})
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
            # api_key must not be stripped
            assert r.json()["value"] == "  sk-trailing-space  "
        finally:
            await db.close()
