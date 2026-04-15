import json
from typing import AsyncIterator

import httpx

from ..config import LLMProviderConfig
from .engine import LLMEngine


class ClaudeEngine(LLMEngine):
    name = "anthropic"

    def __init__(self, config: LLMProviderConfig):
        self._config = config

    def _base_url(self) -> str:
        return (self._config.base_url or "https://api.anthropic.com").rstrip("/")

    def _headers(self) -> dict:
        return {
            "x-api-key": self._config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    async def generate(self, prompt: str) -> str:
        if not prompt or not prompt.strip():
            raise Exception("Empty prompt provided to LLM engine")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._base_url()}/v1/messages",
                headers=self._headers(),
                json={
                    "model": self._config.model,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if response.status_code != 200:
                error_body = response.text[:500]
                raise Exception(f"LLM API error {response.status_code}: {error_body}")
            return response.json()["content"][0]["text"]

    async def generate_stream(self, prompt: str) -> AsyncIterator[str]:
        """Real-time SSE streaming via Anthropic's Messages API.

        Event wire format (anthropic-version 2023-06-01):
            event: content_block_delta
            data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}
            event: message_stop
            data: {"type":"message_stop"}
        We only care about ``text_delta`` payloads — everything else is metadata.
        """
        if not prompt or not prompt.strip():
            raise Exception("Empty prompt provided to LLM engine")
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=180.0)) as client:
            async with client.stream(
                "POST",
                f"{self._base_url()}/v1/messages",
                headers={**self._headers(), "Accept": "text/event-stream"},
                json={
                    "model": self._config.model,
                    "max_tokens": 4096,
                    "stream": True,
                    "messages": [{"role": "user", "content": prompt}],
                },
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise Exception(f"LLM API error {response.status_code}: {body.decode('utf-8', 'replace')[:500]}")
                async for raw in response.aiter_lines():
                    line = raw.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "content_block_delta":
                        delta = obj.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            text = delta.get("text")
                            if text:
                                yield text
