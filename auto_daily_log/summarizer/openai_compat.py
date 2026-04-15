"""OpenAI-compatible protocol client.

Works with any service speaking OpenAI's chat/completions API: OpenAI,
Moonshot (Kimi), DeepSeek, 智谱, 通义, Groq, self-hosted vLLM, etc.
Only the base_url + model + api_key need to change per service.
"""
import json
from typing import AsyncIterator

import httpx

from ..config import LLMProviderConfig
from .engine import LLMEngine


class OpenAICompatEngine(LLMEngine):
    name = "openai_compat"

    def __init__(self, config: LLMProviderConfig):
        self._config = config

    async def generate(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    async def generate_stream(self, prompt: str) -> AsyncIterator[str]:
        """Real-time SSE streaming from OpenAI-compatible /chat/completions.

        Yields text deltas as they arrive from the upstream server. The
        upstream format is OpenAI-standard:
            data: {"choices":[{"delta":{"content":"..."}}, ...]}
            data: [DONE]
        We parse the delta.content and drop everything else.
        """
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=120.0)) as client:
            async with client.stream(
                "POST",
                f"{self._config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                json={
                    "model": self._config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "stream": True,
                },
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise httpx.HTTPStatusError(
                        f"upstream {response.status_code}: {body.decode('utf-8', 'replace')[:500]}",
                        request=response.request,
                        response=response,
                    )
                async for raw in response.aiter_lines():
                    line = raw.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    chunk = delta.get("content")
                    if chunk:
                        yield chunk
