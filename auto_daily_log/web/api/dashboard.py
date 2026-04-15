from datetime import date, datetime, timedelta
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


async def _work_hours_for_date(db, target: str) -> float:
    """Total active (non-idle) duration in hours for a given date, 1 decimal."""
    row = await db.fetch_one(
        "SELECT COALESCE(SUM(duration_sec), 0) AS total_sec FROM activities "
        "WHERE date(timestamp) = ? AND category != 'idle' AND deleted_at IS NULL",
        (target,),
    )
    total_sec = (row or {}).get("total_sec") or 0
    return round(total_sec / 3600, 1)


@router.get("/dashboard/extended")
async def get_dashboard_extended(
    request: Request,
    date: str = Query(default=None),
):
    """Extended dashboard payload for the rebuilt UI.

    Single object response — counts default to 0, strings default to null.
    """
    db = request.app.state.db
    target = date or datetime.now().date().isoformat()

    # Compute previous date (YYYY-MM-DD - 1 day)
    try:
        target_dt = datetime.strptime(target, "%Y-%m-%d").date()
    except ValueError:
        target_dt = datetime.now().date()
        target = target_dt.isoformat()
    prev_date = (target_dt - timedelta(days=1)).isoformat()

    work_hours = await _work_hours_for_date(db, target)
    prev_work_hours = await _work_hours_for_date(db, prev_date)
    work_hours_delta = round(work_hours - prev_work_hours, 1)

    # Activity counts
    act_count_row = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM activities WHERE date(timestamp) = ? AND deleted_at IS NULL",
        (target,),
    )
    activity_count = (act_count_row or {}).get("cnt") or 0

    act_with_summary_row = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM activities WHERE date(timestamp) = ? AND deleted_at IS NULL "
        "AND llm_summary IS NOT NULL AND llm_summary != '' AND llm_summary != '(failed)'",
        (target,),
    )
    activity_count_with_summary = (act_with_summary_row or {}).get("cnt") or 0

    # Drafts
    pending_row = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM worklog_drafts WHERE date = ? AND status = 'pending_review'",
        (target,),
    )
    pending_drafts_count = (pending_row or {}).get("cnt") or 0

    submitted_row = await db.fetch_one(
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(time_spent_sec), 0) AS total_sec "
        "FROM worklog_drafts WHERE date = ? AND status = 'submitted'",
        (target,),
    )
    submitted_jira_count = (submitted_row or {}).get("cnt") or 0
    submitted_total_sec = (submitted_row or {}).get("total_sec") or 0
    submitted_jira_hours = round(submitted_total_sec / 3600, 1)

    latest_row = await db.fetch_one(
        "SELECT updated_at FROM worklog_drafts WHERE date = ? AND status = 'submitted' "
        "ORDER BY updated_at DESC LIMIT 1",
        (target,),
    )
    latest_submit_time = None
    if latest_row and latest_row.get("updated_at"):
        try:
            dt = datetime.fromisoformat(latest_row["updated_at"])
            latest_submit_time = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            latest_submit_time = None

    return {
        "date": target,
        "work_hours": work_hours,
        "activity_count": activity_count,
        "activity_count_with_summary": activity_count_with_summary,
        "pending_drafts_count": pending_drafts_count,
        "submitted_jira_count": submitted_jira_count,
        "submitted_jira_hours": submitted_jira_hours,
        "latest_submit_time": latest_submit_time,
        "work_hours_delta": work_hours_delta,
    }
