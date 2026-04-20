"""Ingestion API — endpoints that collectors push data to.

Auth: Bearer token in Authorization header. The token is hashed (sha256)
and compared against collectors.token_hash. Each request also carries
X-Machine-ID so we can scope data correctly.

Registration is the only unauthenticated endpoint; it mints a fresh
token for the caller.
"""
import hashlib
import json
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from shared.schemas import (
    ActivityIngestRequest,
    ActivityIngestResponse,
    CollectorInfo,
    CollectorRegisterRequest,
    CollectorRegisterResponse,
    CommitIngestRequest,
    CommitIngestResponse,
    ConfigOverridePayload,
    HeartbeatRequest,
    HeartbeatResponse,
    ALL_CAPABILITIES,
)


class ExtendDurationRequest(BaseModel):
    """Request body for POST /ingest/extend-duration.

    ``extra_sec`` is clamped to [0, 3600] so a bad client (or row_id
    drift across restarts) can't quietly inflate a single row by hours.
    """

    row_id: int
    extra_sec: int = Field(..., ge=0, le=3600)

router = APIRouter(tags=["ingest"])


# ─── Auth helpers ────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _authenticate_collector(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_machine_id: Optional[str] = Header(None, alias="X-Machine-ID"),
) -> dict:
    """Validate Bearer token + machine_id header. Returns collector row."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    if not x_machine_id:
        raise HTTPException(400, "Missing X-Machine-ID header")

    token = authorization[len("Bearer "):]
    token_hash = _hash_token(token)

    db = request.app.state.db
    row = await db.fetch_one(
        "SELECT * FROM collectors WHERE machine_id = ? AND token_hash = ? AND is_active = 1",
        (x_machine_id, token_hash),
    )
    if not row:
        raise HTTPException(403, "Invalid token or machine_id")
    # Touch last_seen for every authenticated request
    await db.execute(
        "UPDATE collectors SET last_seen = datetime('now') WHERE id = ?", (row["id"],)
    )
    return row


# ─── Registration (no auth) ──────────────────────────────────────────

@router.post("/collectors/register", response_model=CollectorRegisterResponse)
async def register_collector(body: CollectorRegisterRequest, request: Request):
    """Register a new collector. Idempotent on (name + hostname): if a
    collector with the same name+hostname already exists, we rotate its
    token and return it."""
    db = request.app.state.db

    # Validate capabilities are known
    unknown = set(body.capabilities) - ALL_CAPABILITIES
    if unknown:
        raise HTTPException(400, f"Unknown capabilities: {sorted(unknown)}")

    existing = await db.fetch_one(
        "SELECT id, machine_id FROM collectors WHERE name = ? AND hostname = ?",
        (body.name, body.hostname),
    )

    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    caps_json = json.dumps(sorted(set(body.capabilities)))

    if existing:
        machine_id = existing["machine_id"]
        await db.execute(
            """UPDATE collectors
               SET platform = ?, platform_detail = ?, capabilities = ?,
                   token_hash = ?, is_active = 1,
                   last_seen = datetime('now')
               WHERE id = ?""",
            (body.platform, body.platform_detail, caps_json, token_hash, existing["id"]),
        )
    else:
        machine_id = f"m-{uuid.uuid4().hex[:16]}"
        await db.execute(
            """INSERT INTO collectors
               (machine_id, name, hostname, platform, platform_detail,
                capabilities, token_hash, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (machine_id, body.name, body.hostname, body.platform,
             body.platform_detail, caps_json, token_hash),
        )

    return CollectorRegisterResponse(machine_id=machine_id, token=token)


# ─── List collectors (UI) ────────────────────────────────────────────

@router.get("/collectors", response_model=list[CollectorInfo])
async def list_collectors(request: Request):
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT * FROM collectors WHERE is_active = 1 ORDER BY last_seen DESC"
    )
    out = []
    for r in rows:
        caps = []
        if r.get("capabilities"):
            try:
                caps = json.loads(r["capabilities"])
            except json.JSONDecodeError:
                caps = []
        override = None
        if r.get("config_override"):
            try:
                override = json.loads(r["config_override"])
            except json.JSONDecodeError:
                override = None
        out.append(CollectorInfo(
            id=r["id"],
            machine_id=r["machine_id"],
            name=r["name"],
            hostname=r.get("hostname"),
            platform=r.get("platform"),
            platform_detail=r.get("platform_detail"),
            capabilities=caps,
            created_at=r.get("created_at"),
            last_seen=r.get("last_seen"),
            is_active=bool(r["is_active"]),
            is_paused=bool(r.get("is_paused", 0)),
            config_override=override,
        ))
    return out


# ─── Activity ingestion ──────────────────────────────────────────────

@router.post("/ingest/activities", response_model=ActivityIngestResponse)
async def ingest_activities(
    body: ActivityIngestRequest,
    request: Request,
    collector: dict = Depends(_authenticate_collector),
):
    db = request.app.state.db
    machine_id = collector["machine_id"]
    row_ids = await db.execute_many_returning_ids(
        """INSERT INTO activities
           (timestamp, app_name, window_title, category, confidence,
            url, signals, duration_sec, machine_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                a.timestamp,
                a.app_name,
                a.window_title,
                a.category,
                a.confidence,
                a.url,
                a.signals,
                a.duration_sec,
                machine_id,
            )
            for a in body.activities
        ],
    )
    first_id = row_ids[0] if row_ids else None
    last_id = row_ids[-1] if row_ids else None

    return ActivityIngestResponse(
        accepted=len(body.activities),
        rejected=0,
        first_id=first_id,
        last_id=last_id,
        row_ids=row_ids,
    )


# ─── Extend duration (same-window aggregation over HTTP) ────────────

@router.post("/ingest/extend-duration")
async def ingest_extend_duration(
    body: ExtendDurationRequest,
    request: Request,
    collector: dict = Depends(_authenticate_collector),
):
    """Add extra_sec to an existing activity row's duration.

    Used by remote collectors to aggregate same-window samples without
    inserting a new row each tick. Scoped to the caller's machine_id so
    a collector can't bump another machine's rows.
    """
    db = request.app.state.db
    await db.execute(
        "UPDATE activities SET duration_sec = duration_sec + ? "
        "WHERE id = ? AND machine_id = ?",
        (body.extra_sec, body.row_id, collector["machine_id"]),
    )
    return {"ok": True}


# ─── Commit ingestion ────────────────────────────────────────────────

@router.post("/ingest/commits", response_model=CommitIngestResponse)
async def ingest_commits(
    body: CommitIngestRequest,
    request: Request,
    collector: dict = Depends(_authenticate_collector),
):
    db = request.app.state.db
    machine_id = collector["machine_id"]

    inserted = 0
    duplicates = 0
    for c in body.commits:
        existing = await db.fetch_one(
            "SELECT id FROM git_commits WHERE hash = ? AND machine_id = ?",
            (c.hash, machine_id),
        )
        if existing:
            duplicates += 1
            continue
        await db.execute(
            """INSERT INTO git_commits
               (hash, message, author, committed_at, files_changed,
                insertions, deletions, date, machine_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (c.hash, c.message, c.author, c.committed_at, c.files_changed,
             c.insertions, c.deletions, c.date, machine_id),
        )
        inserted += 1

    return CommitIngestResponse(accepted=inserted, duplicates=duplicates)


# ─── Screenshot upload ───────────────────────────────────────────────

@router.post("/ingest/screenshot")
async def ingest_screenshot(
    request: Request,
    file: UploadFile = File(...),
    timestamp: str = "",
    collector: dict = Depends(_authenticate_collector),
):
    """Upload a screenshot. Returns the server-side path (collector stores
    this in activity.signals.screenshot_path)."""
    if not timestamp:
        raise HTTPException(400, "Missing timestamp query parameter")
    # Validate timestamp is parseable as date
    try:
        date_part = timestamp[:10]
        datetime.strptime(date_part, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, f"Invalid timestamp format: {timestamp}")

    config = getattr(request.app.state, "config", None)
    if config:
        base_dir = config.system.resolved_data_dir / "screenshots"
    else:
        base_dir = Path.home() / ".auto_daily_log" / "screenshots"

    machine_dir = base_dir / collector["machine_id"] / date_part
    machine_dir.mkdir(parents=True, exist_ok=True)

    # Filename: timestamp.png (sanitized)
    fname = timestamp.replace(":", "").replace("T", "_").replace("-", "") + ".png"
    dest = machine_dir / fname
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    dest.write_bytes(content)

    return {"path": str(dest), "size": len(content)}


# ─── Heartbeat ───────────────────────────────────────────────────────

@router.post("/collectors/{machine_id}/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    machine_id: str,
    body: HeartbeatRequest,
    request: Request,
    collector: dict = Depends(_authenticate_collector),
):
    if collector["machine_id"] != machine_id:
        raise HTTPException(403, "machine_id in path does not match token")

    override: Optional[dict] = None
    if collector.get("config_override"):
        try:
            override = json.loads(collector["config_override"])
        except json.JSONDecodeError:
            override = None

    # For the built-in 'local' collector, also surface live settings-table
    # runtime knobs (ocr_enabled / ocr_engine / interval_sec) so UI edits
    # take effect on the next heartbeat without a restart. Matches the
    # behaviour the old LocalSQLiteBackend.heartbeat() exposed in-process.
    if machine_id == "local":
        db = request.app.state.db
        rows = await db.fetch_all(
            "SELECT key, value FROM settings WHERE key IN "
            "('monitor_ocr_enabled', 'monitor_ocr_engine', 'monitor_interval_sec', "
            "'monitor_screenshot_retention_days')"
        )
        s = {r["key"]: r["value"] for r in rows}
        if s:
            def _bool(val):
                if val is None:
                    return None
                return str(val).lower() in ("true", "1", "yes", "on")

            settings_override: dict = {}
            if "monitor_ocr_enabled" in s:
                settings_override["ocr_enabled"] = _bool(s["monitor_ocr_enabled"])
            if "monitor_ocr_engine" in s and s["monitor_ocr_engine"]:
                settings_override["ocr_engine"] = s["monitor_ocr_engine"]
            if "monitor_interval_sec" in s and s["monitor_interval_sec"]:
                try:
                    settings_override["interval_sec"] = int(s["monitor_interval_sec"])
                except (TypeError, ValueError):
                    pass
            if "monitor_screenshot_retention_days" in s and s["monitor_screenshot_retention_days"]:
                try:
                    settings_override["screenshot_retention_days"] = int(s["monitor_screenshot_retention_days"])
                except (TypeError, ValueError):
                    pass
            if settings_override:
                # Merge: collector-row config_override wins over settings
                merged = {**settings_override, **(override or {})}
                override = merged

    return HeartbeatResponse(
        server_time=datetime.now().isoformat(timespec="seconds"),
        config_override=override,
        is_paused=bool(collector.get("is_paused", 0)),
    )


# ─── Remote config + pause/resume (Phase 3) ──────────────────────────

@router.put("/collectors/{machine_id}/config")
async def set_config_override(
    machine_id: str,
    body: ConfigOverridePayload,
    request: Request,
):
    """Push a partial config override to a collector.

    The override is stored on the collector row and returned on the next
    heartbeat. Set a field to null to clear just that field; PUT with
    an empty object clears the whole override.
    """
    db = request.app.state.db
    row = await db.fetch_one(
        "SELECT id, config_override FROM collectors WHERE machine_id = ? AND is_active = 1",
        (machine_id,),
    )
    if not row:
        raise HTTPException(404, f"Collector not found: {machine_id}")

    # Build merged override dict (only non-None fields)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}

    if payload:
        # Merge with existing override if any
        existing = {}
        if row.get("config_override"):
            try:
                existing = json.loads(row["config_override"]) or {}
            except json.JSONDecodeError:
                existing = {}
        merged = {**existing, **payload}
        await db.execute(
            "UPDATE collectors SET config_override = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), row["id"]),
        )
        stored = merged
    else:
        await db.execute(
            "UPDATE collectors SET config_override = NULL WHERE id = ?",
            (row["id"],),
        )
        stored = None

    return {"status": "ok", "config_override": stored}


@router.post("/collectors/{machine_id}/pause")
async def pause_collector(machine_id: str, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one(
        "SELECT id FROM collectors WHERE machine_id = ? AND is_active = 1",
        (machine_id,),
    )
    if not existing:
        raise HTTPException(404, f"Collector not found: {machine_id}")
    await db.execute(
        "UPDATE collectors SET is_paused = 1 WHERE id = ?", (existing["id"],)
    )
    return {"status": "paused"}


@router.post("/collectors/{machine_id}/resume")
async def resume_collector(machine_id: str, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one(
        "SELECT id FROM collectors WHERE machine_id = ? AND is_active = 1",
        (machine_id,),
    )
    if not existing:
        raise HTTPException(404, f"Collector not found: {machine_id}")
    await db.execute(
        "UPDATE collectors SET is_paused = 0 WHERE id = ?", (existing["id"],)
    )
    return {"status": "resumed"}


# ─── Collector management ────────────────────────────────────────────

@router.delete("/collectors/{collector_id}")
async def delete_collector(collector_id: int, request: Request):
    """Soft-delete a collector (is_active = 0). Data stays."""
    db = request.app.state.db
    existing = await db.fetch_one(
        "SELECT id FROM collectors WHERE id = ?", (collector_id,)
    )
    if not existing:
        raise HTTPException(404, "Collector not found")
    await db.execute(
        "UPDATE collectors SET is_active = 0 WHERE id = ?", (collector_id,)
    )
    return {"status": "deactivated"}
