"""LLM engine factory.

Three canonical protocols:
  - openai_compat : OpenAI-compatible API (OpenAI, Kimi, DeepSeek, 智谱, ...)
  - anthropic     : Claude / Anthropic Messages API
  - ollama        : Ollama local API
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator

from ..config import LLMConfig

VALID_PROTOCOLS = ("openai_compat", "anthropic", "ollama")


class LLMEngine(ABC):
    name: str

    @abstractmethod
    async def generate(self, prompt: str) -> str: ...

    async def generate_stream(self, prompt: str) -> AsyncIterator[str]:
        """Yield response text as chunks. Default: fall back to full generate() then chunk.

        Subclasses that can talk to a streaming upstream (OpenAI-compatible,
        Anthropic, Ollama with stream=true) should override this to pass
        deltas through as they arrive — drastically better chat UX.
        """
        result = await self.generate(prompt)
        if not result:
            yield ""
            return
        for i in range(0, len(result), 32):
            yield result[i:i + 32]


def get_llm_engine(config: LLMConfig) -> LLMEngine:
    protocol = (config.engine or "openai_compat").lower()

    if protocol == "openai_compat":
        from .openai_compat import OpenAICompatEngine
        return OpenAICompatEngine(config.openai_compat)

    if protocol == "anthropic":
        from .claude_engine import ClaudeEngine
        return ClaudeEngine(config.anthropic)

    if protocol == "ollama":
        from .ollama import OllamaEngine
        return OllamaEngine(config.ollama)

    raise ValueError(f"Unknown LLM engine/protocol: {config.engine}")
