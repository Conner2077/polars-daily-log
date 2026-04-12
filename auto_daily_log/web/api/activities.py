from fastapi import APIRouter, Request, Query
from datetime import date

router = APIRouter(tags=["activities"])


@router.get("/activities")
async def list_activities(request: Request, target_date: str = Query(default=None)):
    db = request.app.state.db
    target = target_date or date.today().isoformat()
    return await db.fetch_all(
        "SELECT * FROM activities WHERE date(timestamp) = ? ORDER BY timestamp", (target,)
    )


@router.get("/activities/dates")
async def list_activity_dates(request: Request):
    """Return all dates that have activity records, newest first."""
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT date(timestamp) as date, COUNT(*) as count, SUM(duration_sec) as total_sec "
        "FROM activities GROUP BY date(timestamp) ORDER BY date DESC"
    )
    return rows


@router.delete("/activities/{activity_id}")
async def delete_activity(activity_id: int, request: Request):
    db = request.app.state.db
    await db.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
    return {"status": "deleted"}


@router.delete("/activities")
async def delete_activities_by_date(request: Request, target_date: str = Query()):
    """Delete all activities for a given date."""
    db = request.app.state.db
    await db.execute("DELETE FROM activities WHERE date(timestamp) = ?", (target_date,))
    return {"status": "deleted"}
