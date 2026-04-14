from datetime import date
from fastapi import APIRouter, Request, Query

router = APIRouter(tags=["dashboard"])

@router.get("/dashboard")
async def get_dashboard(
    request: Request,
    target_date: str = Query(default=None),
    machine_id: str = Query(default=None),
):
    db = request.app.state.db
    target = target_date or date.today().isoformat()
    if machine_id:
        activities = await db.fetch_all(
            "SELECT category, SUM(duration_sec) as total_sec FROM activities "
            "WHERE date(timestamp) = ? AND machine_id = ? AND deleted_at IS NULL "
            "GROUP BY category",
            (target, machine_id),
        )
    else:
        activities = await db.fetch_all(
            "SELECT category, SUM(duration_sec) as total_sec FROM activities "
            "WHERE date(timestamp) = ? AND deleted_at IS NULL GROUP BY category",
            (target,),
        )
    pending = await db.fetch_all(
        "SELECT COUNT(*) as count FROM worklog_drafts WHERE date = ? AND status = 'pending_review'", (target,)
    )
    submitted = await db.fetch_all(
        "SELECT SUM(time_spent_sec) as total FROM worklog_drafts WHERE date = ? AND status = 'submitted'", (target,)
    )
    return {
        "date": target,
        "activity_summary": activities,
        "pending_review_count": pending[0]["count"] if pending else 0,
        "submitted_hours": round((submitted[0]["total"] or 0) / 3600, 1) if submitted else 0,
    }
