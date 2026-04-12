from fastapi import APIRouter, Request, Query, HTTPException
from typing import Optional

from ...search.searcher import Searcher
from ...search.embedding import OpenAICompatibleEmbedding
from ...config import LLMProviderConfig

router = APIRouter(tags=["search"])


async def _get_searcher(request) -> Searcher:
    """Build a Searcher using embedding config from settings table."""
    db = request.app.state.db

    api_key = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_api_key'") or {}).get("value", "")
    base_url = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_base_url'") or {}).get("value", "")

    if not api_key:
        raise HTTPException(503, "Search unavailable — LLM API Key not configured in Settings")

    # Embedding uses OpenAI-compatible /v1/embeddings
    # Ensure base_url ends with /v1 for the embedding endpoint
    base_url = base_url or "https://api.moonshot.cn/v1"
    if not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    config = LLMProviderConfig(api_key=api_key, model="bge_m3_embed", base_url=base_url)
    engine = OpenAICompatibleEmbedding(config, model="bge_m3_embed", dimensions=1024)
    return Searcher(db, engine)


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(default=20, le=100),
    source_type: Optional[str] = Query(default=None, description="Filter: activity/git_commit/worklog"),
):
    searcher = await _get_searcher(request)
    results = await searcher.search(q, limit=limit, source_type=source_type)
    return results
