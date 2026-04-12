import httpx
from ..config import LLMProviderConfig
from .engine import LLMEngine

class ClaudeEngine(LLMEngine):
    name = "claude"
    def __init__(self, config: LLMProviderConfig):
        self._config = config
    async def generate(self, prompt: str) -> str:
        base_url = (self._config.base_url or "https://api.anthropic.com").rstrip("/")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base_url}/v1/messages",
                headers={"x-api-key": self._config.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                json={"model": self._config.model, "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]},
            )
            response.raise_for_status()
            return response.json()["content"][0]["text"]
