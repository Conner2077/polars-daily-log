"""API for viewing scheduler run history."""
from typing import Optional

from fastapi import APIRouter, Query, Request

router = APIRouter(tags=["scheduler"])


@router.get("/scheduler/runs")
async def list_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    scope_name: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
):
    db = request.app.state.db
    sql = "SELECT * FROM scheduler_runs WHERE 1=1"
    params: list = []
    if scope_name:
        sql += " AND scope_name = ?"
        params.append(scope_name)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return await db.fetch_all(sql, tuple(params))
