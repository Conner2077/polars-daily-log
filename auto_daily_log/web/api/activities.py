from fastapi import APIRouter, Request, Query
from fastapi.responses import FileResponse
from datetime import date
from pathlib import Path

router = APIRouter(tags=["activities"])


@router.get("/activities")
async def list_activities(
    request: Request,
    target_date: str = Query(default=None),
    machine_id: str = Query(default=None, description="Filter by collector machine_id; omit for all"),
):
    db = request.app.state.db
    target = target_date or date.today().isoformat()
    if machine_id:
        return await db.fetch_all(
            "SELECT * FROM activities WHERE date(timestamp) = ? AND machine_id = ? "
            "AND deleted_at IS NULL ORDER BY timestamp DESC",
            (target, machine_id),
        )
    return await db.fetch_all(
        "SELECT * FROM activities WHERE date(timestamp) = ? AND deleted_at IS NULL ORDER BY timestamp DESC",
        (target,),
    )


@router.get("/activities/dates")
async def list_activity_dates(
    request: Request,
    machine_id: str = Query(default=None),
):
    """Return dates with activity records; optionally filtered by machine."""
    db = request.app.state.db
    if machine_id:
        rows = await db.fetch_all(
            "SELECT date(timestamp) as date, COUNT(*) as count, SUM(duration_sec) as total_sec "
            "FROM activities WHERE deleted_at IS NULL AND machine_id = ? "
            "GROUP BY date(timestamp) ORDER BY date DESC",
            (machine_id,),
        )
    else:
        rows = await db.fetch_all(
            "SELECT date(timestamp) as date, COUNT(*) as count, SUM(duration_sec) as total_sec "
            "FROM activities WHERE deleted_at IS NULL GROUP BY date(timestamp) ORDER BY date DESC"
        )
    return rows


@router.delete("/activities/{activity_id}")
async def delete_activity(activity_id: int, request: Request):
    """Soft-delete a single activity (move to recycle bin)."""
    db = request.app.state.db
    await db.execute(
        "UPDATE activities SET deleted_at = datetime('now') WHERE id = ? AND deleted_at IS NULL",
        (activity_id,),
    )
    return {"status": "recycled"}


@router.delete("/activities")
async def delete_activities_by_date(request: Request, target_date: str = Query()):
    """Soft-delete all activities for a given date (move to recycle bin)."""
    db = request.app.state.db
    result = await db.fetch_one(
        "SELECT COUNT(*) as cnt FROM activities WHERE date(timestamp) = ? AND deleted_at IS NULL",
        (target_date,),
    )
    await db.execute(
        "UPDATE activities SET deleted_at = datetime('now') WHERE date(timestamp) = ? AND deleted_at IS NULL",
        (target_date,),
    )
    return {"status": "recycled", "count": result["cnt"] if result else 0}


# ─── Recycle Bin ─────────────────────────────────────────────────────

@router.get("/activities/recycle")
async def list_recycled(request: Request):
    """List recycled activity dates with counts."""
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT date(timestamp) as date, COUNT(*) as count, "
        "MIN(deleted_at) as deleted_at "
        "FROM activities WHERE deleted_at IS NOT NULL "
        "GROUP BY date(timestamp) ORDER BY deleted_at DESC"
    )
    return rows


@router.post("/activities/recycle/restore")
async def restore_activities(request: Request, target_date: str = Query()):
    """Restore all soft-deleted activities for a given date."""
    db = request.app.state.db
    await db.execute(
        "UPDATE activities SET deleted_at = NULL WHERE date(timestamp) = ? AND deleted_at IS NOT NULL",
        (target_date,),
    )
    return {"status": "restored"}


@router.delete("/activities/recycle/purge")
async def purge_activities(request: Request, target_date: str = Query()):
    """Permanently delete all recycled activities for a given date."""
    db = request.app.state.db
    await db.execute(
        "DELETE FROM activities WHERE date(timestamp) = ? AND deleted_at IS NOT NULL",
        (target_date,),
    )
    return {"status": "purged"}


@router.delete("/activities/recycle/purge-all")
async def purge_all(request: Request):
    """Permanently delete all recycled activities."""
    db = request.app.state.db
    result = await db.fetch_one(
        "SELECT COUNT(*) as cnt FROM activities WHERE deleted_at IS NOT NULL"
    )
    await db.execute("DELETE FROM activities WHERE deleted_at IS NOT NULL")
    return {"status": "purged", "count": result["cnt"] if result else 0}


# ─── Screenshot ──────────────────────────────────────────────────────

@router.get("/activities/screenshot")
async def get_screenshot(request: Request, path: str = Query(...)):
    """Serve a screenshot file by its absolute path."""
    file = Path(path)
    config = getattr(request.app.state, "config", None)
    if config:
        screenshot_dir = config.system.resolved_data_dir / "screenshots"
    else:
        screenshot_dir = Path.home() / ".auto_daily_log" / "screenshots"
    # Security: only serve files under screenshots dir
    try:
        file.resolve().relative_to(screenshot_dir.resolve())
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(403, "Access denied")
    if not file.exists():
        from fastapi import HTTPException
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(file, media_type="image/png")
