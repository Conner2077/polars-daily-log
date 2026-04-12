import httpx
from ..config import LLMProviderConfig
from .engine import LLMEngine

class OllamaEngine(LLMEngine):
    name = "ollama"
    def __init__(self, config: LLMProviderConfig):
        self._config = config
    async def generate(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._config.base_url}/api/generate",
                json={"model": self._config.model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            return response.json()["response"]
