import json
from datetime import date
from fastapi import APIRouter, Request, Query, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["worklogs"])

class DraftSeed(BaseModel):
    date: str
    issue_key: str
    time_spent_sec: int
    summary: str

class DraftUpdate(BaseModel):
    time_spent_sec: Optional[int] = None
    summary: Optional[str] = None
    issue_key: Optional[str] = None
    full_summary: Optional[str] = None

class IssueUpdate(BaseModel):
    issue_key: Optional[str] = None
    time_spent_hours: Optional[float] = None
    summary: Optional[str] = None

@router.get("/worklogs/drafts/preview")
async def drafts_preview(
    request: Request,
    limit: int = Query(default=3, ge=1, le=20),
    status: str = Query(default="pending_review"),
):
    """Flattened preview of worklog drafts for the dashboard widget.

    Each daily draft may contain a JSON array of per-issue entries in
    `summary`. We flatten those into one row per issue, sorted by most
    recent draft first. If `summary` is not a JSON array (legacy format),
    we fall back to a single synthesized entry using `issue_key` and
    `time_spent_sec` on the draft row itself.
    """
    db = request.app.state.db
    drafts = await db.fetch_all(
        "SELECT id, date, issue_key, time_spent_sec, summary, status, created_at, updated_at "
        "FROM worklog_drafts WHERE status = ? "
        "ORDER BY date DESC, COALESCE(updated_at, created_at) DESC",
        (status,),
    )

    out: list[dict] = []
    for d in drafts:
        if len(out) >= limit:
            break
        summary_raw = d.get("summary") or ""
        entries = None
        try:
            parsed = json.loads(summary_raw)
            if isinstance(parsed, list):
                entries = parsed
        except (json.JSONDecodeError, TypeError):
            entries = None

        if entries is not None:
            for iss in entries:
                if len(out) >= limit:
                    break
                issue_key = iss.get("issue_key") or d.get("issue_key") or ""
                hours = iss.get("time_spent_hours")
                try:
                    hours_f = round(float(hours), 2) if hours is not None else 0.0
                except (TypeError, ValueError):
                    hours_f = 0.0
                title = (iss.get("summary") or "").strip()
                # Short single-line title: first line only
                if title:
                    title = title.splitlines()[0].strip()
                out.append({
                    "issue_key": issue_key,
                    "title": title,
                    "hours": hours_f,
                    "time_range": None,
                    "date": d.get("date"),
                })
        else:
            # Legacy row: whole draft is one entry
            hours_f = round((d.get("time_spent_sec") or 0) / 3600, 2)
            title = (summary_raw or "").strip().splitlines()[0].strip() if summary_raw else ""
            out.append({
                "issue_key": d.get("issue_key") or "",
                "title": title,
                "hours": hours_f,
                "time_range": None,
                "date": d.get("date"),
            })

    return out


@router.get("/worklogs")
async def list_drafts(request: Request, date: str = Query(default=None), tag: str = Query(default=None)):
    db = request.app.state.db
    if tag:
        return await db.fetch_all(
            "SELECT * FROM worklog_drafts WHERE tag = ? ORDER BY date DESC, created_at DESC", (tag,)
        )
    if date:
        return await db.fetch_all(
            "SELECT * FROM worklog_drafts WHERE date = ? ORDER BY CASE tag WHEN 'daily' THEN 0 ELSE 1 END, created_at DESC",
            (date,),
        )
    # No date, no tag → return all drafts (for "全部" history view)
    return await db.fetch_all(
        "SELECT * FROM worklog_drafts ORDER BY date DESC, CASE tag WHEN 'daily' THEN 0 ELSE 1 END, created_at DESC"
    )

class GenerateRequest(BaseModel):
    type: str  # daily, weekly, monthly, custom
    start_date: Optional[str] = None  # YYYY-MM-DD, for custom
    end_date: Optional[str] = None    # YYYY-MM-DD, for custom
    force: bool = False  # True to overwrite existing same-period log


def _resolve_period(tag: str, start_date: Optional[str], end_date: Optional[str]):
    from datetime import date, timedelta
    today = date.today()
    if tag == "daily":
        return today.isoformat(), today.isoformat()
    elif tag == "weekly":
        return (today - timedelta(days=today.weekday())).isoformat(), today.isoformat()
    elif tag == "monthly":
        return today.replace(day=1).isoformat(), today.isoformat()
    elif tag == "custom":
        return start_date, end_date
    return None, None


@router.post("/worklogs/check-exists")
async def check_period_exists(body: GenerateRequest, request: Request):
    """Check if a log already exists for the same period."""
    db = request.app.state.db
    start, end = _resolve_period(body.type, body.start_date, body.end_date)
    if not start or not end:
        raise HTTPException(400, f"Invalid type or missing dates: {body.type}")
    existing = await db.fetch_one(
        "SELECT id, tag, period_start, period_end, summary FROM worklog_drafts "
        "WHERE tag = ? AND period_start = ? AND period_end = ?",
        (body.type, start, end),
    )
    if existing:
        return {"exists": True, "existing_id": existing["id"], "period_start": start, "period_end": end}
    return {"exists": False, "period_start": start, "period_end": end}


@router.post("/worklogs/generate")
async def generate_summary(body: GenerateRequest, request: Request):
    """Generate worklog summary for a time period.

    - daily: calls LLM with activities + commits + issues → per-issue drafts (pending_review)
    - weekly/monthly/custom: reads daily logs in the period → LLM generates period summary (archived)
    """
    from datetime import date as date_mod
    from collections import defaultdict
    db = request.app.state.db

    today = date_mod.today()
    tag = body.type
    start, end = _resolve_period(tag, body.start_date, body.end_date)
    if not start or not end:
        raise HTTPException(400, f"Invalid type or missing dates: {tag}")

    # Check for existing same-period log
    existing_rows = await db.fetch_all(
        "SELECT id FROM worklog_drafts WHERE tag = ? AND period_start = ? AND period_end = ?",
        (tag, start, end),
    )
    if existing_rows and not body.force:
        raise HTTPException(409, "Log already exists for this period. Use force=true to overwrite.")
    if existing_rows and body.force:
        for ex in existing_rows:
            await db.execute("DELETE FROM audit_logs WHERE draft_id = ?", (ex["id"],))
            await db.execute("DELETE FROM worklog_drafts WHERE id = ?", (ex["id"],))

    if tag == "daily":
        return await _generate_daily(db, request, today, start, end)
    else:
        return await _generate_period(db, request, tag, today, start, end)


async def _get_llm_engine_from_settings(db):
    """Build LLM engine from settings table (user may have configured via Web UI).

    Falls back to the install-time built-in config (~/.auto_daily_log/builtin.key)
    if the user hasn't set their own key — saves first-run friction when the
    author supplied a shared passphrase at install time.
    """
    from ...config import LLMConfig, LLMProviderConfig
    from ...summarizer.engine import VALID_PROTOCOLS, get_llm_engine
    from ...summarizer.url_helper import normalize_base_url
    from ...builtin_llm import load_builtin_llm_config

    protocol = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_engine'") or {}).get("value", "") or "openai_compat"
    api_key = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_api_key'") or {}).get("value", "")
    model = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_model'") or {}).get("value", "")
    base_url = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_base_url'") or {}).get("value", "")

    if not api_key:
        builtin = load_builtin_llm_config()
        if builtin:
            protocol = builtin.get("engine") or "openai_compat"
            api_key = builtin.get("api_key", "")
            model = model or builtin.get("model", "")
            base_url = base_url or builtin.get("base_url", "")

    if not api_key:
        return None

    if protocol not in VALID_PROTOCOLS:
        protocol = "openai_compat"

    default_url = {
        "openai_compat": "https://api.moonshot.cn/v1",
        "anthropic": "https://api.anthropic.com",
        "ollama": "http://localhost:11434",
    }[protocol]
    default_model = {
        "openai_compat": "moonshot-v1-8k",
        "anthropic": "claude-sonnet-4-20250514",
        "ollama": "llama3",
    }[protocol]

    model = model or default_model
    base_url = normalize_base_url(base_url, engine=protocol) or default_url

    provider = LLMProviderConfig(api_key=api_key, model=model, base_url=base_url)
    config = LLMConfig(engine=protocol, **{protocol: provider})
    return get_llm_engine(config)


async def _generate_daily(db, request, today, start, end):
    """Daily: use LLM Summarizer to generate per-issue worklog drafts. Falls back to raw data if LLM fails."""
    # Try to get LLM engine from settings table first (user-configured via Web UI)
    llm_engine = await _get_llm_engine_from_settings(db)
    # Fallback to app state engine (from config.yaml)
    if not llm_engine:
        llm_engine = getattr(request.app.state, "_llm_engine", None)

    if llm_engine:
        try:
            from ...summarizer.summarizer import WorklogSummarizer
            from ...collector.git_collector import GitCollector

            collector = GitCollector(db)
            await collector.collect_today()
            activity_summarizer = getattr(request.app.state, "activity_summarizer", None)
            summarizer = WorklogSummarizer(
                db, llm_engine, activity_summarizer=activity_summarizer
            )
            drafts = await summarizer.generate_drafts(start)

            if drafts:
                draft = drafts[0]  # Single record per day
                await db.execute(
                    "UPDATE worklog_drafts SET tag = 'daily', period_start = ?, period_end = ? WHERE id = ?",
                    (start, end, draft["id"]),
                )
                return {"id": draft["id"], "tag": "daily", "period_start": start, "period_end": end}
            # LLM returned empty, fall through to fallback
            print("[Generate] LLM returned empty result, using fallback")
        except Exception as e:
            print(f"[Generate] LLM failed: {e}")

    # Fallback: generate without LLM (raw data summary)
    return await _generate_daily_fallback(db, today, start, end)


async def _generate_daily_fallback(db, today, start, end):
    """Fallback daily generation without LLM — raw activity + commit summary."""
    from collections import defaultdict
    activities = await db.fetch_all(
        "SELECT * FROM activities WHERE date(timestamp) >= ? AND date(timestamp) <= ? AND category != 'idle' AND deleted_at IS NULL ORDER BY timestamp",
        (start, end),
    )
    commits = await db.fetch_all(
        "SELECT * FROM git_commits WHERE date >= ? AND date <= ? ORDER BY committed_at",
        (start, end),
    )
    if not activities and not commits:
        raise HTTPException(404, f"No activity or commit data found for {start} to {end}")

    summary_parts = []
    cat_duration = defaultdict(int)
    for a in activities:
        cat_duration[a["category"]] += a.get("duration_sec", 0)
    if cat_duration:
        summary_parts.append("Activity summary:")
        for cat, sec in sorted(cat_duration.items(), key=lambda x: -x[1]):
            summary_parts.append(f"  - {cat}: {round(sec / 3600, 1)}h")
    if commits:
        summary_parts.append(f"\nGit commits ({len(commits)}):")
        for c in commits[:20]:
            summary_parts.append(f"  - {c['message']}")

    total_sec = sum(a.get("duration_sec", 0) for a in activities)
    summary_text = "\n".join(summary_parts) or "No data"

    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
        "VALUES (?, ?, ?, ?, 'pending_review', 'daily', ?, ?)",
        (today.isoformat(), "ALL", total_sec, summary_text, start, end),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'created', ?)",
        (draft_id, json.dumps({"tag": "daily", "period": f"{start} to {end}"})),
    )
    return {"id": draft_id, "tag": "daily", "period_start": start, "period_end": end}


async def _generate_period(db, request, tag, today, start, end):
    """Weekly/Monthly/Custom: read daily logs in the period → LLM generates period summary."""
    # Fetch all daily logs within the period
    daily_logs = await db.fetch_all(
        "SELECT * FROM worklog_drafts WHERE tag = 'daily' AND period_start >= ? AND period_end <= ? ORDER BY period_start",
        (start, end),
    )

    if not daily_logs:
        raise HTTPException(404, f"No daily logs found for {start} to {end}. Generate daily logs first.")

    # Build text from daily logs (new format: summary is JSON array)
    daily_text_parts = []
    total_sec = 0
    for log in daily_logs:
        total_sec += log.get("time_spent_sec", 0)
        log_date = log.get('period_start', log['date'])
        # Parse JSON array summary
        try:
            issues = json.loads(log['summary'])
            issue_parts = []
            for iss in issues:
                issue_parts.append(f"  - {iss['issue_key']} ({iss['time_spent_hours']}h): {iss['summary']}")
            daily_text_parts.append(f"【{log_date}】\n" + "\n".join(issue_parts))
        except (json.JSONDecodeError, TypeError):
            # Fallback for old format
            daily_text_parts.append(
                f"【{log_date}】{log.get('issue_key', '')} ({round(log.get('time_spent_sec', 0) / 3600, 1)}h)\n{log['summary']}"
            )
    daily_text = "\n\n".join(daily_text_parts)

    # Try LLM (settings table first, then app state)
    llm_engine = await _get_llm_engine_from_settings(db)
    if not llm_engine:
        llm_engine = getattr(request.app.state, "_llm_engine", None)
    if llm_engine:
        from ...summarizer.prompt import DEFAULT_PERIOD_SUMMARY_PROMPT, render_prompt
        period_type_label = {"weekly": "周报", "monthly": "月报", "custom": "阶段性总结"}[tag]

        # Prompt resolution: per-type template → global setting → hardcoded default
        type_row = await db.fetch_one(
            "SELECT prompt_template FROM summary_types WHERE name = ?", (tag,)
        )
        if type_row and type_row.get("prompt_template") and type_row["prompt_template"].strip():
            template = type_row["prompt_template"]
        else:
            setting = await db.fetch_one("SELECT value FROM settings WHERE key = 'period_summary_prompt'")
            template = (setting["value"] if setting and setting["value"] else DEFAULT_PERIOD_SUMMARY_PROMPT)

        prompt = render_prompt(
            template,
            period_start=start,
            period_end=end,
            period_type=period_type_label,
            daily_logs=daily_text,
        )
        try:
            summary_text = await llm_engine.generate(prompt)
        except Exception:
            summary_text = daily_text  # Fallback to raw daily logs
    else:
        summary_text = f"=== {start} ~ {end} ===\n\n{daily_text}"

    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status, tag, period_start, period_end) "
        "VALUES (?, ?, ?, ?, 'archived', ?, ?, ?)",
        (today.isoformat(), "SUMMARY", total_sec, summary_text, tag, start, end),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'created', ?)",
        (draft_id, json.dumps({"tag": tag, "period": f"{start} to {end}"})),
    )
    return {"id": draft_id, "tag": tag, "period_start": start, "period_end": end}

@router.post("/worklogs/seed", status_code=201)
async def seed_draft(body: DraftSeed, request: Request):
    db = request.app.state.db
    draft_id = await db.execute(
        "INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status) VALUES (?, ?, ?, ?, 'pending_review')",
        (body.date, body.issue_key, body.time_spent_sec, body.summary),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'created', ?)",
        (draft_id, json.dumps(body.model_dump(), ensure_ascii=False)),
    )
    return {"id": draft_id}

@router.patch("/worklogs/{draft_id}")
async def update_draft(draft_id: int, body: DraftUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    if not existing:
        raise HTTPException(404, "Draft not found")
    before = json.dumps(dict(existing), ensure_ascii=False, default=str)
    updates = ["user_edited = 1", "updated_at = datetime('now')"]
    params = []
    if body.time_spent_sec is not None: updates.append("time_spent_sec = ?"); params.append(body.time_spent_sec)
    if body.summary is not None: updates.append("summary = ?"); params.append(body.summary)
    if body.issue_key is not None: updates.append("issue_key = ?"); params.append(body.issue_key)
    if body.full_summary is not None: updates.append("full_summary = ?"); params.append(body.full_summary)
    params.append(draft_id)
    await db.execute(f"UPDATE worklog_drafts SET {', '.join(updates)} WHERE id = ?", tuple(params))
    updated = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    after = json.dumps(dict(updated), ensure_ascii=False, default=str)
    await db.execute("INSERT INTO audit_logs (draft_id, action, before_snapshot, after_snapshot) VALUES (?, 'edited', ?, ?)", (draft_id, before, after))
    return {"status": "updated"}

@router.post("/worklogs/{draft_id}/approve")
async def approve_draft(draft_id: int, request: Request):
    db = request.app.state.db
    await db.execute("UPDATE worklog_drafts SET status = 'approved', updated_at = datetime('now') WHERE id = ?", (draft_id,))
    await db.execute("INSERT INTO audit_logs (draft_id, action) VALUES (?, 'approved')", (draft_id,))
    return {"status": "approved"}

@router.post("/worklogs/{draft_id}/reject")
async def reject_draft(draft_id: int, request: Request):
    db = request.app.state.db
    await db.execute("UPDATE worklog_drafts SET status = 'rejected', updated_at = datetime('now') WHERE id = ?", (draft_id,))
    await db.execute("INSERT INTO audit_logs (draft_id, action) VALUES (?, 'rejected')", (draft_id,))
    return {"status": "rejected"}

@router.post("/worklogs/approve-all")
async def approve_all(request: Request, date: str = Query(default=None)):
    db = request.app.state.db
    target = date or __import__("datetime").date.today().isoformat()
    await db.execute("UPDATE worklog_drafts SET status = 'approved', updated_at = datetime('now') WHERE date = ? AND status = 'pending_review'", (target,))
    drafts = await db.fetch_all("SELECT id FROM worklog_drafts WHERE date = ? AND status = 'approved'", (target,))
    for d in drafts:
        await db.execute("INSERT INTO audit_logs (draft_id, action) VALUES (?, 'approved')", (d["id"],))
    return {"status": "all_approved", "count": len(drafts)}

async def _get_publisher(db, draft_tag: str):
    """Resolve the WorklogPublisher for a draft's summary type.

    Raises HTTP 400 when:
      - the summary type has no publisher configured (e.g. weekly/monthly)
      - Jira (or other platform) credentials are missing
      - the tag has no matching summary_types row AND no fallback exists

    Pre-migration drafts with tag='daily' work because the built-in seed
    always creates the 'daily' row. Drafts with tag='custom' that predate
    the summary_types table will get a 400 — users must re-generate or
    manually add a 'custom' summary type (Phase 2 UI covers this).
    """
    from ...publishers.registry import get_publisher
    from ...jira_client.client import MissingJiraConfig
    try:
        publisher = await get_publisher(db, draft_tag)
    except MissingJiraConfig as e:
        raise HTTPException(400, str(e))
    if publisher is None:
        raise HTTPException(400, f"总结类型 '{draft_tag}' 没有配置推送平台")
    return publisher


async def _get_started_timestamp(db, draft_date: str) -> str:
    """Jira `started` = {draft_date}T21:00:00.000+0800.

    We always use 21:00 of the day the work happened (per user request):
    even historical drafts keep their original date, so submitting a
    week-old log still records it against that week-old date.
    """
    return f"{draft_date}T21:00:00.000+0800"


@router.post("/worklogs/{draft_id}/submit")
async def submit_to_platform(draft_id: int, request: Request):
    """Submit ALL issues in a draft to the configured platform (Jira, etc.)."""
    db = request.app.state.db
    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft["status"] not in ("approved", "auto_approved"):
        raise HTTPException(400, f"Draft status is '{draft['status']}', must be approved first")

    publisher = await _get_publisher(db, draft["tag"] or "daily")
    started = await _get_started_timestamp(db, draft['date'])

    # Parse issue entries from summary JSON
    try:
        issues = json.loads(draft["summary"])
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(400, "Invalid summary format, expected JSON array")

    _SKIP_KEYS = {"ALL", "DAILY"}
    results = []
    for i, issue in enumerate(issues):
        if issue.get("jira_worklog_id"):
            continue  # Already submitted
        if issue["issue_key"] in _SKIP_KEYS:
            results.append({"issue_key": issue["issue_key"], "skipped": True})
            continue
        time_sec = int(issue["time_spent_hours"] * 3600)
        pub_result = await publisher.submit(
            issue_key=issue["issue_key"], time_spent_sec=time_sec,
            comment=issue["summary"], started=started,
        )
        if pub_result.success:
            issues[i]["jira_worklog_id"] = pub_result.worklog_id
            results.append({"issue_key": issue["issue_key"], "jira_worklog_id": pub_result.worklog_id})
            await db.execute(
                "INSERT INTO audit_logs (draft_id, action, jira_response, issue_index, issue_key, source) "
                "VALUES (?, 'submitted_issue', ?, ?, ?, 'manual_all')",
                (draft_id, json.dumps({"issue_key": issue["issue_key"], "result": pub_result.raw}, ensure_ascii=False),
                 i, issue["issue_key"]),
            )
        else:
            results.append({"issue_key": issue["issue_key"], "error": pub_result.error})
            await db.execute(
                "INSERT INTO audit_logs (draft_id, action, jira_response, issue_index, issue_key, source) "
                "VALUES (?, 'submit_failed_issue', ?, ?, ?, 'manual_all')",
                (draft_id, json.dumps({"issue_key": issue["issue_key"], "error": pub_result.error}, ensure_ascii=False),
                 i, issue["issue_key"]),
            )

    # Only mark as 'submitted' if every non-skipped issue got a worklog ID;
    # otherwise keep the draft open for retry (mirrors scheduler logic).
    _SKIP_KEYS_SET = _SKIP_KEYS
    all_done = all(
        iss.get("jira_worklog_id") or iss["issue_key"] in _SKIP_KEYS_SET
        for iss in issues
    )
    new_status = "submitted" if all_done else draft["status"]
    await db.execute(
        "UPDATE worklog_drafts SET summary = ?, status = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(issues, ensure_ascii=False), new_status, draft_id),
    )
    return {"status": new_status, "results": results}


@router.post("/worklogs/{draft_id}/submit-issue/{issue_index}")
async def submit_single_issue(draft_id: int, issue_index: int, request: Request):
    """Submit a single issue from a draft to the configured platform."""
    db = request.app.state.db
    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft["status"] not in ("approved", "auto_approved"):
        raise HTTPException(400, f"Draft status is '{draft['status']}', must be approved first")

    try:
        issues = json.loads(draft["summary"])
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(400, "Invalid summary format")

    if issue_index < 0 or issue_index >= len(issues):
        raise HTTPException(400, f"Invalid issue index {issue_index}")

    issue = issues[issue_index]
    if issue.get("jira_worklog_id"):
        raise HTTPException(400, f"Issue {issue['issue_key']} already submitted")

    publisher = await _get_publisher(db, draft["tag"] or "daily")
    started = await _get_started_timestamp(db, draft['date'])

    time_sec = int(issue["time_spent_hours"] * 3600)
    pub_result = await publisher.submit(
        issue_key=issue["issue_key"], time_spent_sec=time_sec,
        comment=issue["summary"], started=started,
    )
    if not pub_result.success:
        raise HTTPException(502, f"Publish error: {pub_result.error}")
    issues[issue_index]["jira_worklog_id"] = pub_result.worklog_id

    # Check if all issues are submitted → mark whole draft as submitted
    all_submitted = all(iss.get("jira_worklog_id") for iss in issues)
    new_status = "submitted" if all_submitted else draft["status"]

    await db.execute(
        "UPDATE worklog_drafts SET summary = ?, status = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(issues, ensure_ascii=False), new_status, draft_id),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action, jira_response, issue_index, issue_key, source) "
        "VALUES (?, 'submitted_issue', ?, ?, ?, 'manual_single')",
        (draft_id,
         json.dumps({"issue_key": issue["issue_key"], "result": pub_result.raw}, ensure_ascii=False),
         issue_index, issue["issue_key"]),
    )
    return {"status": "submitted", "issue_key": issue["issue_key"], "jira_worklog_id": issues[issue_index]["jira_worklog_id"], "all_submitted": all_submitted}


@router.patch("/worklogs/{draft_id}/issues/{issue_index}")
async def update_issue(draft_id: int, issue_index: int, body: IssueUpdate, request: Request):
    """Update a single issue entry within a daily record."""
    db = request.app.state.db
    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    if not draft:
        raise HTTPException(404, "Draft not found")

    try:
        issues = json.loads(draft["summary"])
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(400, "Invalid summary format")

    if issue_index < 0 or issue_index >= len(issues):
        raise HTTPException(400, f"Invalid issue index {issue_index}")

    before = json.dumps(issues[issue_index], ensure_ascii=False)

    if body.issue_key is not None:
        issues[issue_index]["issue_key"] = body.issue_key
    if body.time_spent_hours is not None:
        issues[issue_index]["time_spent_hours"] = body.time_spent_hours
    if body.summary is not None:
        issues[issue_index]["summary"] = body.summary

    # Recalculate total time
    total_sec = sum(int(iss["time_spent_hours"] * 3600) for iss in issues)

    await db.execute(
        "UPDATE worklog_drafts SET summary = ?, time_spent_sec = ?, user_edited = 1, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(issues, ensure_ascii=False), total_sec, draft_id),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action, before_snapshot, after_snapshot) VALUES (?, 'edited_issue', ?, ?)",
        (draft_id, before, json.dumps(issues[issue_index], ensure_ascii=False)),
    )
    return {"status": "updated"}

@router.get("/worklogs/{draft_id}/audit")
async def get_audit_trail(draft_id: int, request: Request):
    db = request.app.state.db
    return await db.fetch_all("SELECT * FROM audit_logs WHERE draft_id = ? ORDER BY created_at", (draft_id,))


@router.delete("/worklogs/{draft_id}")
async def delete_draft(draft_id: int, request: Request):
    """Soft-delete a draft (any status). Removes draft + audit logs."""
    db = request.app.state.db
    existing = await db.fetch_one("SELECT id FROM worklog_drafts WHERE id = ?", (draft_id,))
    if not existing:
        raise HTTPException(404, "Draft not found")
    await db.execute("INSERT INTO audit_logs (draft_id, action) VALUES (?, 'deleted')", (draft_id,))
    await db.execute("DELETE FROM worklog_drafts WHERE id = ?", (draft_id,))
    return {"status": "deleted"}
