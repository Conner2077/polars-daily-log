"""Collector HTTP client for registration and server communication."""
from typing import Optional

import httpx

from shared.schemas import (
    CollectorRegisterRequest,
    CollectorRegisterResponse,
)


class RegistrationClient:
    """One-shot client for the register endpoint (no auth needed)."""

    def __init__(self, server_url: str, timeout: float = 10.0):
        self._server_url = server_url.rstrip("/")
        self._timeout = timeout

    async def register(
        self,
        name: str,
        hostname: str,
        platform: str,
        platform_detail: Optional[str],
        capabilities: set[str],
    ) -> CollectorRegisterResponse:
        req = CollectorRegisterRequest(
            name=name,
            hostname=hostname,
            platform=platform,
            platform_detail=platform_detail,
            capabilities=sorted(capabilities),
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                f"{self._server_url}/api/collectors/register",
                json=req.model_dump(),
            )
            r.raise_for_status()
            return CollectorRegisterResponse(**r.json())
