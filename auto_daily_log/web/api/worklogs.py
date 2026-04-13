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

@router.get("/worklogs")
async def list_drafts(request: Request, date: str = Query(default=None), tag: str = Query(default=None)):
    db = request.app.state.db
    target = date or __import__("datetime").date.today().isoformat()
    if tag:
        return await db.fetch_all(
            "SELECT * FROM worklog_drafts WHERE tag = ? ORDER BY created_at DESC", (tag,)
        )
    return await db.fetch_all(
        "SELECT * FROM worklog_drafts WHERE date = ? ORDER BY created_at", (target,)
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
    """Build LLM engine from settings table (user may have configured via Web UI)."""
    from ...config import LLMConfig, LLMProviderConfig
    from ...summarizer.engine import get_llm_engine

    engine_name = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_engine'") or {}).get("value", "kimi")
    api_key = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_api_key'") or {}).get("value", "")
    model = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_model'") or {}).get("value", "")
    base_url = (await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_base_url'") or {}).get("value", "")

    if not api_key:
        return None

    # Fill in defaults for empty model/base_url
    defaults = {
        "kimi": ("moonshot-v1-8k", "https://api.moonshot.cn/v1"),
        "openai": ("gpt-4o", "https://api.openai.com/v1"),
        "ollama": ("llama3", "http://localhost:11434"),
        "claude": ("claude-sonnet-4-20250514", "https://api.anthropic.com"),
    }
    default_model, default_url = defaults.get(engine_name, ("", ""))
    model = model or default_model
    base_url = base_url or default_url

    provider = LLMProviderConfig(api_key=api_key, model=model, base_url=base_url)
    config = LLMConfig(engine=engine_name, **{engine_name: provider})
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
            summarizer = WorklogSummarizer(db, llm_engine)
            drafts = await summarizer.generate_drafts(start)

            if drafts:
                for d in drafts:
                    await db.execute(
                        "UPDATE worklog_drafts SET tag = 'daily', period_start = ?, period_end = ? WHERE id = ?",
                        (start, end, d["id"]),
                    )
                return {"ids": [d["id"] for d in drafts], "tag": "daily", "period_start": start, "period_end": end, "count": len(drafts)}
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
        "SELECT * FROM activities WHERE date(timestamp) >= ? AND date(timestamp) <= ? AND category != 'idle' ORDER BY timestamp",
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

    # Build text from daily logs
    daily_text_parts = []
    total_sec = 0
    for log in daily_logs:
        total_sec += log.get("time_spent_sec", 0)
        daily_text_parts.append(
            f"【{log.get('period_start', log['date'])}】{log.get('issue_key', '')} ({round(log.get('time_spent_sec', 0) / 3600, 1)}h)\n{log['summary']}"
        )
    daily_text = "\n\n".join(daily_text_parts)

    # Try LLM (settings table first, then app state)
    llm_engine = await _get_llm_engine_from_settings(db)
    if not llm_engine:
        llm_engine = getattr(request.app.state, "_llm_engine", None)
    if llm_engine:
        from ...summarizer.prompt import DEFAULT_PERIOD_SUMMARY_PROMPT, render_prompt
        period_type_label = {"weekly": "周报", "monthly": "月报", "custom": "阶段性总结"}[tag]

        # Try custom prompt from settings first
        setting = await db.fetch_one("SELECT value FROM settings WHERE key = 'period_summary_prompt'")
        template = setting["value"] if setting else DEFAULT_PERIOD_SUMMARY_PROMPT

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

@router.post("/worklogs/{draft_id}/submit")
async def submit_to_jira(draft_id: int, request: Request):
    db = request.app.state.db
    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft["status"] not in ("approved", "auto_approved"):
        raise HTTPException(400, f"Draft status is '{draft['status']}', must be approved first")

    jira_url = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_server_url'") or {}).get("value", "")
    jira_pat = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_pat'") or {}).get("value", "")
    jira_cookie = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_cookie'") or {}).get("value", "")
    jira_auth_mode = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_auth_mode'") or {}).get("value", "cookie")

    if not jira_url:
        raise HTTPException(400, "Jira Server URL not configured in Settings.")
    if jira_auth_mode == "cookie" and not jira_cookie:
        raise HTTPException(400, "Jira Cookie not configured. Go to Settings → Jira to set it.")
    if jira_auth_mode == "bearer" and not jira_pat:
        raise HTTPException(400, "Jira PAT not configured in Settings.")

    from ...config import JiraConfig
    from ...jira_client.client import JiraClient
    jira_config = JiraConfig(server_url=jira_url, pat=jira_pat, auth_mode=jira_auth_mode, cookie=jira_cookie)
    jira = JiraClient(jira_config)

    started = f"{draft['date']}T09:00:00.000+0800"
    try:
        result = await jira.submit_worklog(issue_key=draft["issue_key"], time_spent_sec=draft["time_spent_sec"], comment=draft["summary"], started=started)
        jira_worklog_id = result.get("id", "")
    except Exception as e:
        raise HTTPException(502, f"Jira API error: {str(e)}")

    await db.execute("UPDATE worklog_drafts SET status = 'submitted', jira_worklog_id = ?, updated_at = datetime('now') WHERE id = ?", (str(jira_worklog_id), draft_id))
    await db.execute("INSERT INTO audit_logs (draft_id, action, jira_response) VALUES (?, 'submitted', ?)", (draft_id, json.dumps(result, ensure_ascii=False)))
    return {"status": "submitted", "jira_worklog_id": jira_worklog_id}

@router.get("/worklogs/{draft_id}/audit")
async def get_audit_trail(draft_id: int, request: Request):
    db = request.app.state.db
    return await db.fetch_all("SELECT * FROM audit_logs WHERE draft_id = ? ORDER BY created_at", (draft_id,))
