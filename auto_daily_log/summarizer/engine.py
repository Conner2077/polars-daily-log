"""LLM engine factory.

Engine values are normalized to 3 protocol identifiers:
  - openai_compat : OpenAI-compatible API (OpenAI, Kimi, DeepSeek, 智谱, ...)
  - anthropic     : Claude / Anthropic Messages API
  - ollama        : Ollama local API

Legacy values (kimi, openai, claude) are mapped for backward compat
so old settings in DB keep working without migration.
"""
from abc import ABC, abstractmethod

from ..config import LLMConfig


class LLMEngine(ABC):
    name: str

    @abstractmethod
    async def generate(self, prompt: str) -> str: ...


# Maps legacy engine names to current protocol identifiers
_LEGACY_ALIASES = {
    "kimi": "openai_compat",
    "openai": "openai_compat",
    "claude": "anthropic",
}


def resolve_protocol(engine: str) -> str:
    """Normalize any engine value (legacy or new) to the canonical protocol."""
    if not engine:
        return "openai_compat"
    e = engine.lower()
    return _LEGACY_ALIASES.get(e, e)


def get_llm_engine(config: LLMConfig) -> LLMEngine:
    protocol = resolve_protocol(config.engine)

    if protocol == "openai_compat":
        from .openai_compat import OpenAICompatEngine
        # Pick the provider config block that matches. Since Kimi/OpenAI
        # both map to openai_compat, prefer whichever has an api_key set.
        for attr in ("kimi", "openai"):
            provider = getattr(config, attr, None)
            if provider and (provider.api_key or provider.base_url):
                return OpenAICompatEngine(provider)
        # Fallback: first non-None
        return OpenAICompatEngine(config.kimi)

    if protocol == "anthropic":
        from .claude_engine import ClaudeEngine
        return ClaudeEngine(config.claude)

    if protocol == "ollama":
        from .ollama import OllamaEngine
        return OllamaEngine(config.ollama)

    raise ValueError(f"Unknown LLM engine/protocol: {config.engine}")
