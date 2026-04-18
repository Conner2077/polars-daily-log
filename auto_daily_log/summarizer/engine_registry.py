"""Unified LLM engine registry — resolve engine by name from llm_engines table.

Usage:
    engine = await get_engine_by_name(db, "default")
    engine = await get_engine_by_name(db, "kimi")
    engine = await get_engine_by_name(db, None)  # returns default engine
"""
from __future__ import annotations

from typing import Optional

from ..models.database import Database
from .engine import LLMEngine, get_llm_engine
from ..config import LLMConfig, LLMProviderConfig


async def get_engine_by_name(db: Database, name: Optional[str] = None) -> Optional[LLMEngine]:
    """Build an LLM engine from the llm_engines table by name.

    If name is None or "default", returns the engine marked is_default=1.
    Returns None if the engine is not found or has no API key.
    """
    if not name or name == "default":
        row = await db.fetch_one(
            "SELECT * FROM llm_engines WHERE is_default = 1 AND enabled = 1 LIMIT 1"
        )
    else:
        row = await db.fetch_one(
            "SELECT * FROM llm_engines WHERE name = ? AND enabled = 1",
            (name,),
        )

    if not row:
        return None

    api_key = row.get("api_key") or ""
    if not api_key:
        return None

    protocol = row.get("protocol") or "openai_compat"
    model = row.get("model") or ""
    base_url = row.get("base_url") or ""

    from .url_helper import normalize_base_url
    base_url = normalize_base_url(base_url, engine=protocol) or _default_url(protocol)
    model = model or _default_model(protocol)

    provider = LLMProviderConfig(api_key=api_key, model=model, base_url=base_url)
    config = LLMConfig(engine=protocol, **{protocol: provider})
    return get_llm_engine(config)


async def list_engines(db: Database) -> list[dict]:
    """Return all enabled engines."""
    return await db.fetch_all(
        "SELECT name, display_name, protocol, model, base_url, is_default, enabled, created_at "
        "FROM llm_engines ORDER BY is_default DESC, name"
    )


def _default_url(protocol: str) -> str:
    return {
        "openai_compat": "https://api.moonshot.cn/v1",
        "anthropic": "https://api.anthropic.com",
        "ollama": "http://localhost:11434",
    }.get(protocol, "")


def _default_model(protocol: str) -> str:
    return {
        "openai_compat": "moonshot-v1-8k",
        "anthropic": "claude-sonnet-4-20250514",
        "ollama": "llama3",
    }.get(protocol, "")
