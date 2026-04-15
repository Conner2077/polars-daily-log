from datetime import datetime, timedelta
from fastapi import APIRouter, Request

router = APIRouter(tags=["machines"])


@router.get("/machines/status")
async def machines_status(request: Request):
    """List all known (active) collectors with online state.

    `online` is True if `last_seen` is within the last 5 minutes.
    `last_seen_hours_ago` is the float hours since the collector's
    `last_seen`; None when the collector is currently online (avoids
    rendering a stale "0.0h ago").
    `is_primary` flags the collector whose `machine_id` matches the
    server's built-in local collector (the machine the server runs on).
    If no clear primary exists, it is False for all rows.
    """
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT machine_id, name, last_seen FROM collectors "
        "WHERE is_active = 1 ORDER BY last_seen DESC"
    )

    # Identify primary via the built-in "local" machine_id used by the
    # server-side collector (see collector/builtin.py). We treat the row
    # whose machine_id == 'local' as primary when present.
    primary_machine_id = "local"

    now = datetime.now()
    out = []
    for r in rows:
        last_seen_str = r.get("last_seen")
        online = False
        hours_ago: float | None = None
        if last_seen_str:
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_str)
                delta = now - last_seen_dt
                if delta.total_seconds() <= 300:  # 5 minutes
                    online = True
                    hours_ago = None
                else:
                    hours_ago = round(delta.total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                hours_ago = None
        out.append({
            "name": r.get("name") or r.get("machine_id") or "",
            "online": online,
            "last_seen_hours_ago": hours_ago,
            "is_primary": r.get("machine_id") == primary_machine_id,
        })
    return out
