from fastapi import APIRouter, Request, Query, HTTPException
from typing import Optional

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(default=20, le=100),
    source_type: Optional[str] = Query(default=None, description="Filter: activity/git_commit/worklog"),
):
    db = request.app.state.db
    searcher = getattr(request.app.state, "searcher", None)
    if not searcher:
        raise HTTPException(503, "Search not available — embedding engine not configured")
    results = await searcher.search(q, limit=limit, source_type=source_type)
    return results
