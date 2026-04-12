from abc import ABC, abstractmethod
from ..config import LLMConfig

class LLMEngine(ABC):
    name: str
    @abstractmethod
    async def generate(self, prompt: str) -> str: ...

def get_llm_engine(config: LLMConfig) -> LLMEngine:
    engine_name = config.engine.lower()
    if engine_name == "kimi":
        from .kimi import KimiEngine
        return KimiEngine(config.kimi)
    elif engine_name == "openai":
        from .openai_engine import OpenAIEngine
        return OpenAIEngine(config.openai)
    elif engine_name == "ollama":
        from .ollama import OllamaEngine
        return OllamaEngine(config.ollama)
    elif engine_name == "claude":
        from .claude_engine import ClaudeEngine
        return ClaudeEngine(config.claude)
    else:
        raise ValueError(f"Unknown LLM engine: {engine_name}")
