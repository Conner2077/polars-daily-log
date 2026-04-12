from fastapi import APIRouter, Request, Query
from datetime import date

router = APIRouter(tags=["activities"])

@router.get("/activities")
async def list_activities(request: Request, target_date: str = Query(default=None)):
    db = request.app.state.db
    target = target_date or date.today().isoformat()
    return await db.fetch_all("SELECT * FROM activities WHERE date(timestamp) = ? ORDER BY timestamp", (target,))
