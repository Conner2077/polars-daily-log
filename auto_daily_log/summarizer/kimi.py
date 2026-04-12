import httpx
from ..config import LLMProviderConfig
from .engine import LLMEngine

class KimiEngine(LLMEngine):
    name = "kimi"
    def __init__(self, config: LLMProviderConfig):
        self._config = config
    async def generate(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._config.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._config.api_key}", "Content-Type": "application/json"},
                json={"model": self._config.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
