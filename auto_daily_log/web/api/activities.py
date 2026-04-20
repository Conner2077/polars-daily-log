from fastapi import APIRouter, Request, Query
from fastapi.responses import FileResponse
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import Counter

router = APIRouter(tags=["activities"])


_BUCKET_MINUTES = {"5m": 5, "15m": 15, "1h": 60}


@router.get("/activities/timeline")
async def activities_timeline(
    request: Request,
    hours: int = Query(default=12, ge=1, le=72),
    bucket: str = Query(default="15m", pattern="^(5m|15m|1h)$"),
):
    """Return a per-bucket active/idle minute breakdown for the last `hours`.

    Used by the frontend "right-now" widget to render a flat-line when idle.
    All buckets inside `[now - hours, now]` are always returned — empty ones
    included — so the chart renders consistently.

    Duration semantics: each row has `duration_sec` (30s default per sample).
    We sum `duration_sec` per-bucket matching the pattern used in
    ``dashboard.py`` and ``_compress_activities``. Rows whose timestamp falls
    inside the bucket contribute their full duration to that bucket (we do
    not split samples across bucket boundaries — samples are small enough
    that this is noise, and the schema has no end-time column to split on).
    """
    db = request.app.state.db
    bucket_min = _BUCKET_MINUTES[bucket]

    # Local server time per spec — no timezone conversion.
    now = datetime.now().replace(microsecond=0)
    window_start = now - timedelta(hours=hours)

    # Align first bucket to clock boundary <= window_start.
    # For 15m buckets: :00 :15 :30 :45. For 1h: :00. For 5m: every 5.
    def floor_to_bucket(dt: datetime, minutes: int) -> datetime:
        total_min = dt.hour * 60 + dt.minute
        floored_min = (total_min // minutes) * minutes
        return dt.replace(hour=floored_min // 60, minute=floored_min % 60, second=0, microsecond=0)

    first_bucket_start = floor_to_bucket(window_start, bucket_min)

    # Emit exactly hours*60/bucket_min buckets so the frontend always gets a
    # fixed shape. First bucket may be partial (its start < window_start).
    total_buckets = (hours * 60) // bucket_min
    bucket_starts: list[datetime] = [
        first_bucket_start + timedelta(minutes=bucket_min * i)
        for i in range(total_buckets)
    ]

    # Query rows within window.
    rows = await db.fetch_all(
        "SELECT timestamp, app_name, category, duration_sec FROM activities "
        "WHERE timestamp >= ? AND timestamp <= ? AND deleted_at IS NULL",
        (window_start.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
    )

    # Group rows by bucket index.
    per_bucket_active_sec: dict[int, float] = {}
    per_bucket_idle_sec: dict[int, float] = {}
    per_bucket_apps: dict[int, Counter] = {}

    for row in rows:
        ts_str = row["timestamp"]
        try:
            ts = datetime.fromisoformat(ts_str)
        except (TypeError, ValueError):
            continue
        if ts < first_bucket_start or ts > now:
            continue
        offset_min = int((ts - first_bucket_start).total_seconds() // 60)
        idx = offset_min // bucket_min
        if idx < 0 or idx >= len(bucket_starts):
            continue
        dur = row["duration_sec"] or 0
        category = row["category"]
        app_name = row["app_name"]
        if category == "idle":
            per_bucket_idle_sec[idx] = per_bucket_idle_sec.get(idx, 0) + dur
        else:
            per_bucket_active_sec[idx] = per_bucket_active_sec.get(idx, 0) + dur
            if app_name:
                per_bucket_apps.setdefault(idx, Counter())[app_name] += 1

    buckets_out = []
    for idx, bs in enumerate(bucket_starts):
        active_sec = per_bucket_active_sec.get(idx, 0)
        idle_sec = per_bucket_idle_sec.get(idx, 0)
        top_app = None
        if idx in per_bucket_apps:
            apps_counter = per_bucket_apps[idx]
            # Sort by (-count, name) so ties break alphabetically (ascending).
            top_app = sorted(apps_counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        buckets_out.append({
            "bucket_start": bs.isoformat(timespec="seconds"),
            "active_mins": round(active_sec / 60.0, 2),
            "idle_mins": round(idle_sec / 60.0, 2),
            "top_app": top_app,
        })

    return {
        "window_start": window_start.isoformat(timespec="seconds"),
        "window_end": now.isoformat(timespec="seconds"),
        "bucket_minutes": bucket_min,
        "buckets": buckets_out,
    }


@router.get("/activities/recent")
async def recent_activities(
    request: Request,
    limit: int = Query(default=5, ge=1, le=50),
):
    """Most-recent non-idle, non-soft-deleted activities.

    Joins against `collectors` to resolve `machine_name` from the
    activity's `machine_id`. Activities with no matching collector row
    (e.g. legacy 'local' before registration) pass through the raw
    `machine_id` string as the machine name.
    """
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT a.timestamp, a.app_name, a.window_title, a.llm_summary, "
        "       a.machine_id, c.name AS machine_name "
        "FROM activities a "
        "LEFT JOIN collectors c ON c.machine_id = a.machine_id "
        "WHERE a.deleted_at IS NULL AND a.category != 'idle' "
        "ORDER BY a.timestamp DESC LIMIT ?",
        (limit,),
    )
    out = []
    for r in rows:
        ts_str = r.get("timestamp") or ""
        hhmm = ""
        try:
            hhmm = datetime.fromisoformat(ts_str).strftime("%H:%M")
        except (ValueError, TypeError):
            hhmm = ""
        out.append({
            "timestamp": hhmm,
            "app_name": r.get("app_name") or "",
            "window_title": r.get("window_title") or "",
            "llm_summary": r.get("llm_summary") or "",
            "machine_name": r.get("machine_name") or r.get("machine_id") or "",
        })
    return out


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
    """Return dates with activity records; optionally filtered by machine.

    `count` includes all rows (idle + real). `total_sec` **excludes idle**
    so the sidebar "Xh" reflects actual working time, not the 14-hour
    overnight idle that would otherwise dominate the number.
    """
    db = request.app.state.db
    # Count everything; sum only non-idle duration.
    base_sql = (
        "SELECT date(timestamp) AS date, "
        "       COUNT(*) AS count, "
        "       COALESCE(SUM(CASE WHEN category != 'idle' THEN duration_sec ELSE 0 END), 0) AS total_sec "
        "FROM activities WHERE deleted_at IS NULL "
    )
    if machine_id:
        rows = await db.fetch_all(
            base_sql + "AND machine_id = ? GROUP BY date(timestamp) ORDER BY date DESC",
            (machine_id,),
        )
    else:
        rows = await db.fetch_all(
            base_sql + "GROUP BY date(timestamp) ORDER BY date DESC"
        )
    return rows


@router.post("/activities/retry-failed")
async def retry_failed_activities(
    request: Request,
    target_date: str = Query(default=None),
):
    """Re-queue activities whose LLM summarization previously failed.

    Resets ``llm_summary='(failed)'`` back to NULL so ActivitySummarizer's
    5-second polling loop picks them up on the next tick. Without this
    endpoint, failed rows sit out the 24h cooldown in
    ``ActivitySummarizer.FAIL_COOLDOWN_HOURS`` — which protects against
    hot-looping on genuinely unacceptable content, but is the wrong call
    when the failure was transient (bad API key, upstream outage).

    ``target_date`` optional: scoped per-date (matches Activities page
    view). Omit to reset every failed row across all dates.
    """
    db = request.app.state.db
    if target_date:
        count_row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM activities "
            "WHERE llm_summary = '(failed)' AND deleted_at IS NULL AND date(timestamp) = ?",
            (target_date,),
        )
        await db.execute(
            "UPDATE activities SET llm_summary = NULL, llm_summary_at = NULL "
            "WHERE llm_summary = '(failed)' AND deleted_at IS NULL AND date(timestamp) = ?",
            (target_date,),
        )
    else:
        count_row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM activities "
            "WHERE llm_summary = '(failed)' AND deleted_at IS NULL"
        )
        await db.execute(
            "UPDATE activities SET llm_summary = NULL, llm_summary_at = NULL "
            "WHERE llm_summary = '(failed)' AND deleted_at IS NULL"
        )
    return {"status": "requeued", "count": count_row["cnt"] if count_row else 0}


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
