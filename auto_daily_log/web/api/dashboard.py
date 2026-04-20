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
    # Pending count = legacy worklog_drafts + new-pipeline summaries.
    # Both tables are still written to — legacy flow (auto_approve + manual
    # review) and new pipeline (scope_outputs + direct publish) coexist.
    # Orphan guard on legacy side: skip empty-summary drafts ('[]' / NULL),
    # which are typically LLM-failure residue and can't be acted on.
    # Skip guard on new side mirrors MyLogs.vue:355 `unpublishedCount`:
    # require non-sentinel issue_key + a publisher on the output so the
    # badge only surfaces items the user can actually push somewhere.
    pending_legacy = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM worklog_drafts "
        "WHERE date = ? AND status = 'pending_review' "
        "  AND summary IS NOT NULL AND summary != '[]' AND summary != ''",
        (target,),
    )
    pending_new = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM summaries s "
        "JOIN scope_outputs so ON so.id = s.output_id "
        "WHERE s.date = ? "
        "  AND s.published_id IS NULL "
        "  AND s.issue_key IS NOT NULL AND s.issue_key != '' "
        "  AND s.issue_key NOT IN ('ALL', 'DAILY') "
        "  AND COALESCE(so.publisher_name, '') != ''",
        (target,),
    )
    pending_count = (
        (pending_legacy["count"] if pending_legacy else 0)
        + (pending_new["count"] if pending_new else 0)
    )
    submitted_legacy = await db.fetch_one(
        "SELECT COALESCE(SUM(time_spent_sec), 0) AS total FROM worklog_drafts "
        "WHERE date = ? AND status = 'submitted'",
        (target,),
    )
    submitted_new = await db.fetch_one(
        "SELECT COALESCE(SUM(time_spent_sec), 0) AS total FROM summaries "
        "WHERE date = ? AND published_id IS NOT NULL",
        (target,),
    )
    submitted_total = (
        (submitted_legacy["total"] or 0)
        + (submitted_new["total"] or 0)
    )
    return {
        "date": target,
        "activity_summary": activities,
        "pending_review_count": pending_count,
        "submitted_hours": round(submitted_total / 3600, 1),
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
