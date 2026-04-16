"""Chat endpoint — answers questions over local worklog + activity data.

Wire format (deep-chat compatible):
  SSE events carry JSON payloads ``{"text": "<chunk>"}`` with a final
  ``data: [DONE]`` sentinel. On error: ``{"error": "<msg>"}`` then DONE.

The endpoint delegates token emission to ``LLMEngine.generate_stream`` —
engines that speak SSE upstream (OpenAI-compatible, etc.) forward deltas
live; engines without native streaming fall back to the base class's
fake-stream implementation.

A chat "session" is just an id + rolling message log. The first event of
a brand-new session is a control event ``{"session_id": "<hex>"}`` so the
client can pin it to localStorage before any text chunk arrives.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import date, timedelta
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...summarizer import prompt as prompt_module
from ...summarizer.prompt import render_prompt
from .chat_retrieval import extract_issue_keys, parse_date_anchors
from .worklogs import _get_llm_engine_from_settings

router = APIRouter(tags=["chat"])


# Strong refs to in-flight producer tasks so they survive client
# disconnects (refresh, network drop). Without this, Python's GC can
# reap the coroutine mid-run. Entries self-remove via done-callback.
_bg_tasks: set = set()


# Tight defaults keep the prefill small — any LLM/provider answers faster
# when less context is stuffed in. Callers that genuinely want a wider
# window can raise ``context_days`` per request (capped at MAX_CONTEXT_DAYS).
DEFAULT_CONTEXT_DAYS = 2
MAX_CONTEXT_DAYS = 14
MAX_ACTIVITY_SUMMARIES = 15
MAX_DRAFT_ROWS = 10
# Anchored queries (date / issue explicitly mentioned) are worth more
# context — the user is asking about a specific slice, so pack more of it.
MAX_ACTIVITY_SUMMARIES_ANCHORED = 80
MAX_DRAFT_ROWS_ANCHORED = 30
SESSION_TITLE_MAX = 40


class ChatMessage(BaseModel):
    role: str  # "user" | "ai"
    text: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    context_days: Optional[int] = None  # override default window; clamped server-side
    session_id: Optional[str] = None


@router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    db = request.app.state.db

    user_question = _latest_user_question(body.messages)
    history = _format_history(body.messages[:-1] if body.messages else [])

    # Resolve session: existing id (if row present) or brand-new uuid.
    session_id, is_new_session = await _resolve_session(db, body.session_id, user_question)

    # Persist the user message BEFORE calling the LLM — if the model fails
    # the user can retry without re-typing; the record of what was asked
    # stays intact either way (see AGENTS.md "原汁原味").
    if user_question:
        await db.execute(
            "INSERT INTO chat_messages (session_id, role, text) VALUES (?, 'user', ?)",
            (session_id, user_question),
        )

    today = date.today()
    window = body.context_days if body.context_days is not None else DEFAULT_CONTEXT_DAYS
    window = max(1, min(window, MAX_CONTEXT_DAYS))

    # Resolve the retrieval scope from the user's question. Order of precedence:
    # 1. Explicit dates mentioned → use those dates exactly (ignore context_days)
    # 2. Issue keys mentioned → pull jira_issues rows for those keys in addition
    #    to the time/date window (so the LLM sees titles + descriptions)
    # 3. Neither → fall back to the rolling time window
    date_anchors = parse_date_anchors(user_question, today)
    issue_keys = extract_issue_keys(user_question)

    if date_anchors:
        anchor_strs = [d.isoformat() for d in date_anchors]
        placeholders = ",".join("?" * len(anchor_strs))
        summaries = await db.fetch_all(
            f"SELECT date, issue_key, full_summary, summary, time_spent_sec "
            f"FROM worklog_drafts "
            f"WHERE date IN ({placeholders}) AND (tag IS NULL OR tag = 'daily') "
            f"ORDER BY date DESC LIMIT ?",
            (*anchor_strs, MAX_DRAFT_ROWS_ANCHORED),
        )
        # Activities: timestamp is ISO ``YYYY-MM-DDTHH:MM:SS``. ``date(timestamp)``
        # in sqlite parses that and returns ``YYYY-MM-DD`` — perfect for IN.
        activities = await db.fetch_all(
            f"SELECT timestamp, llm_summary FROM activities "
            f"WHERE date(timestamp) IN ({placeholders}) "
            f"  AND llm_summary IS NOT NULL "
            f"  AND llm_summary NOT IN ('(failed)', '(skipped-risk)') "
            f"  AND (deleted_at IS NULL) "
            f"ORDER BY timestamp DESC LIMIT ?",
            (*anchor_strs, MAX_ACTIVITY_SUMMARIES_ANCHORED),
        )
    else:
        since = (today - timedelta(days=window)).isoformat()
        summaries = await db.fetch_all(
            "SELECT date, issue_key, full_summary, summary, time_spent_sec "
            "FROM worklog_drafts "
            "WHERE date >= ? AND (tag IS NULL OR tag = 'daily') "
            "ORDER BY date DESC LIMIT ?",
            (since, MAX_DRAFT_ROWS),
        )
        activities = await db.fetch_all(
            "SELECT timestamp, llm_summary FROM activities "
            "WHERE timestamp >= ? "
            "  AND llm_summary IS NOT NULL "
            "  AND llm_summary NOT IN ('(failed)', '(skipped-risk)') "
            "  AND (deleted_at IS NULL) "
            "ORDER BY timestamp DESC LIMIT ?",
            (since, MAX_ACTIVITY_SUMMARIES),
        )

    jira_issue_rows: list[dict] = []
    if issue_keys:
        placeholders = ",".join("?" * len(issue_keys))
        jira_issue_rows = await db.fetch_all(
            f"SELECT issue_key, summary, description FROM jira_issues "
            f"WHERE issue_key IN ({placeholders})",
            tuple(issue_keys),
        )

    prompt = render_prompt(
        prompt_module.DEFAULT_CHAT_PROMPT,
        today=today.isoformat(),
        recent_summaries=_format_summaries(summaries),
        jira_issues=_format_jira_issues(jira_issue_rows),
        recent_activities=_format_activities(activities),
        history=history or "(无历史)",
        question=user_question or "(空)",
    )

    llm = await _get_llm_engine_from_settings(db)

    # Producer runs the LLM + persist pipeline as a DETACHED task, so a
    # mid-stream client disconnect (refresh, nav-away, abort button) does
    # NOT cancel the LLM call or lose the AI reply. The task writes into
    # an asyncio.Queue; the HTTP response tails it for live streaming.
    # If the client drops, the queue just backs up in memory (LLM replies
    # are at most a few KB) while the task finishes and persists to DB.
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL_DONE = object()

    async def producer() -> None:
        assembled_parts: list[str] = []
        errored = False
        try:
            async for chunk in llm.generate_stream(prompt):
                if chunk:
                    assembled_parts.append(chunk)
                    await queue.put(("text", chunk))
        except Exception as exc:
            errored = True
            await queue.put(("error", f"LLM call failed: {exc}"))

        if not errored:
            assembled = "".join(assembled_parts)
            # Persist regardless of whether the client is still listening.
            # A non-empty AI message becomes a real row; the updated_at
            # touch keeps the session list in chronological order.
            if assembled:
                await db.execute(
                    "INSERT INTO chat_messages (session_id, role, text) VALUES (?, 'ai', ?)",
                    (session_id, assembled),
                )
            await db.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                (session_id,),
            )

        await queue.put(SENTINEL_DONE)

    task = asyncio.create_task(producer())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    async def gen() -> AsyncGenerator[str, None]:
        # Announce the session id up-front so the client can pin it before
        # any text arrives and before it needs to reconcile on reload.
        if is_new_session:
            yield _sse({"session_id": session_id})

        while True:
            item = await queue.get()
            if item is SENTINEL_DONE:
                break
            kind, data = item
            if kind == "text":
                yield _sse({"text": data})
            elif kind == "error":
                yield _sse({"error": data})

        yield _sse_done()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/chat/sessions")
async def list_sessions(request: Request):
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT s.id, s.title, s.updated_at, "
        "  (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) "
        "  AS message_count "
        "FROM chat_sessions s "
        "ORDER BY s.updated_at DESC "
        "LIMIT 50",
        (),
    )
    return rows


@router.get("/chat/suggestions")
async def get_suggestions(request: Request):
    """Dynamic suggestion chips for the chat intro screen.

    Suggestions are derived from what's actually in the user's local data
    right now — no point suggesting "PDL-42 今天进展怎样" if nothing
    mentions PDL-42. Ordering follows the priority in the docstring of
    ``_compute_suggestions``; output is deduped + capped at 5.
    """
    db = request.app.state.db
    suggestions = await _compute_suggestions(db, today=date.today())
    return {"suggestions": suggestions}


@router.get("/chat/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Single-session metadata. Used by the frontend to label the export
    file with the session title. 404 when the id is unknown so a stale
    localStorage id surfaces the same way as the messages endpoint."""
    db = request.app.state.db
    row = await db.fetch_one(
        "SELECT id, title, created_at, updated_at FROM chat_sessions WHERE id = ?",
        (session_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    return row


@router.get("/chat/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request):
    db = request.app.state.db
    session = await db.fetch_one(
        "SELECT id FROM chat_sessions WHERE id = ?", (session_id,)
    )
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    rows = await db.fetch_all(
        "SELECT role, text, created_at FROM chat_messages "
        "WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    return rows


@router.delete("/chat/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request):
    db = request.app.state.db
    session = await db.fetch_one(
        "SELECT id FROM chat_sessions WHERE id = ?", (session_id,)
    )
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    await db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    return Response(status_code=204)


# ─── Phase 3: extract worklog drafts from a chat + push to Jira ──────

class ExtractRequest(BaseModel):
    target_date: Optional[str] = None  # YYYY-MM-DD. Defaults to today.


class PushRequest(BaseModel):
    drafts: list[dict]  # [{issue_key, time_spent_hours, summary}, ...]
    target_date: str    # YYYY-MM-DD


@router.post("/chat/sessions/{session_id}/extract_worklog")
async def extract_worklog(session_id: str, body: ExtractRequest, request: Request):
    """Run the auto-approve prompt against a chat transcript to derive
    worklog draft rows. Returns a list of dicts the UI can preview + edit
    before the separate ``push_to_jira`` endpoint actually submits them.

    We deliberately do NOT write to the DB here — the "原汁原味" principle
    (AGENTS.md) says downstream filtering belongs in the downstream step,
    and the user gets a chance to tweak before anything is committed.
    """
    db = request.app.state.db
    session = await db.fetch_one(
        "SELECT id FROM chat_sessions WHERE id = ?", (session_id,)
    )
    if not session:
        raise HTTPException(status_code=404, detail="session not found")

    target_date = body.target_date or date.today().isoformat()

    messages = await db.fetch_all(
        "SELECT role, text FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    transcript = _format_transcript(messages)

    jira_issue_rows = await db.fetch_all(
        "SELECT issue_key, summary, description FROM jira_issues WHERE is_active = 1",
        (),
    )

    prompt = render_prompt(
        prompt_module.DEFAULT_AUTO_APPROVE_PROMPT,
        date=target_date,
        jira_issues=_format_jira_issues(jira_issue_rows),
        git_commits="(无)",
        full_summary=transcript or "(无)",
    )

    llm = await _get_llm_engine_from_settings(db)
    raw = await llm.generate(prompt)

    parsed = _parse_json_array(raw)
    if parsed is None:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "could not parse LLM output",
                "raw": (raw or "")[:500],
            },
        )

    return _validate_draft_rows(parsed)


@router.post("/chat/sessions/{session_id}/push_to_jira")
async def push_to_jira(session_id: str, body: PushRequest, request: Request):
    """Submit the (possibly user-edited) drafts to Jira via the single
    sanctioned entry point ``build_jira_client_from_db`` + ``submit_worklog``
    (AGENTS.md rule). Records each submit as a ``worklog_drafts`` row for
    auditability, then returns the partial-success summary.
    """
    db = request.app.state.db
    session = await db.fetch_one(
        "SELECT id FROM chat_sessions WHERE id = ?", (session_id,)
    )
    if not session:
        raise HTTPException(status_code=404, detail="session not found")

    from ...jira_client.client import MissingJiraConfig, build_jira_client_from_db

    try:
        client = await build_jira_client_from_db(db)
    except MissingJiraConfig:
        raise HTTPException(status_code=400, detail="Jira not configured")

    target_date = body.target_date
    # Jira `started` format matches the daily-submit path (21:00 local
    # on the target day) so chat-derived worklogs line up with scheduler
    # output in the history view.
    started = f"{target_date}T21:00:00.000+0800"

    submitted: list[dict] = []
    failed: list[dict] = []
    skip_keys = {"OTHER", "ALL", "DAILY"}

    for draft in body.drafts or []:
        issue_key = str(draft.get("issue_key") or "").strip()
        try:
            hours = float(draft.get("time_spent_hours") or 0)
        except (TypeError, ValueError):
            hours = 0.0
        summary = str(draft.get("summary") or "")

        if not issue_key or issue_key in skip_keys or hours <= 0:
            continue

        time_sec = int(hours * 3600)

        # Record the draft BEFORE attempting the Jira submit — we want
        # a persistent trail even if the network call fails (AGENTS.md
        # 原汁原味 + auditability). The status reflects that the user
        # already approved these via the UI preview step.
        await db.execute(
            "INSERT INTO worklog_drafts "
            "(date, issue_key, time_spent_sec, summary, status, tag) "
            "VALUES (?, ?, ?, ?, 'approved', 'daily')",
            (target_date, issue_key, time_sec, summary),
        )

        try:
            result = await client.submit_worklog(
                issue_key=issue_key,
                time_spent_sec=time_sec,
                comment=summary,
                started=started,
            )
            worklog_id = str(result.get("id", "")) if isinstance(result, dict) else ""
            submitted.append({"issue_key": issue_key, "worklog_id": worklog_id})
        except Exception as exc:  # noqa: BLE001 — surface message verbatim
            failed.append({"issue_key": issue_key, "error": str(exc)})

    return {"submitted": submitted, "failed": failed}


# ─── helpers ─────────────────────────────────────────────────────────

async def _resolve_session(db, requested_id: Optional[str], user_question: str) -> tuple[str, bool]:
    """Return (session_id, is_new). If the caller sent an id that doesn't
    exist we treat it as new (and write the row with that id) so that a
    stale client-side id doesn't silently turn into a different session."""
    if requested_id:
        existing = await db.fetch_one(
            "SELECT id FROM chat_sessions WHERE id = ?", (requested_id,)
        )
        if existing:
            return requested_id, False
        # Stale id from the client — honour it but create the row.
        title = _make_title(user_question)
        await db.execute(
            "INSERT INTO chat_sessions (id, title) VALUES (?, ?)",
            (requested_id, title),
        )
        return requested_id, True

    new_id = uuid.uuid4().hex
    title = _make_title(user_question)
    await db.execute(
        "INSERT INTO chat_sessions (id, title) VALUES (?, ?)",
        (new_id, title),
    )
    return new_id, True


def _make_title(user_question: str) -> str:
    q = (user_question or "").strip()
    if not q:
        return "New chat"
    return q[:SESSION_TITLE_MAX]


def _latest_user_question(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return m.text
    return ""


def _format_history(messages: list[ChatMessage]) -> str:
    if not messages:
        return ""
    lines = []
    for m in messages:
        role = "用户" if m.role == "user" else "助手"
        lines.append(f"{role}: {m.text}")
    return "\n".join(lines)


def _format_summaries(rows: list[dict]) -> str:
    if not rows:
        return "(窗口期内无工作日志)"
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    lines: list[str] = []
    for d in sorted(by_date.keys(), reverse=True):
        lines.append(f"### {d}")
        for r in by_date[d]:
            body = (r.get("full_summary") or r.get("summary") or "").strip()
            if body:
                lines.append(f"- [{r['issue_key']}] {body}")
    return "\n".join(lines)


def _format_activities(rows: list[dict]) -> str:
    if not rows:
        return "(无活动级摘要)"
    return "\n".join(f"- {r['timestamp']}: {r['llm_summary']}" for r in rows)


def _format_jira_issues(rows: list[dict]) -> str:
    """Render the jira_issues block — one bullet per issue with a
    title + truncated description so the LLM has enough context to
    answer questions about a task even when no draft has been written
    against it yet."""
    if not rows:
        return "(未提及 Jira 任务)"
    lines: list[str] = []
    for r in rows:
        title = (r.get("summary") or "").strip()
        desc = (r.get("description") or "").strip()
        if len(desc) > 120:
            desc = desc[:120] + "…"
        lines.append(f"- [{r['issue_key']}] {title} — {desc}")
    return "\n".join(lines)


def _chunk_text(text: str, size: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + size] for i in range(0, len(text), size)]


def _format_transcript(messages: list[dict]) -> str:
    """Render the chat log the way the auto-approve prompt expects to see
    the daily summary — one tagged line per turn so the LLM can tell user
    questions apart from AI answers."""
    if not messages:
        return ""
    lines: list[str] = []
    for m in messages:
        tag = "[USER]" if m.get("role") == "user" else "[AI]"
        lines.append(f"{tag} {m.get('text', '')}")
    return "\n".join(lines)


def _parse_json_array(raw: str) -> Optional[list]:
    """Extract a JSON array from an LLM response.

    Robust to:
    - code fences (```json ... ```)
    - leading/trailing chatter around the array
    - arrays embedded in wider text

    Returns ``None`` when no array can be parsed — callers turn that into
    HTTP 422 so the client sees the raw output for debugging.
    """
    if not raw:
        return None

    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences if the whole body is wrapped.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    # Fast path: already a pure array.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: find the first `[ ... ]` span and try to parse it.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def _validate_draft_rows(rows: list) -> list[dict]:
    """Keep only rows that conform to the extract contract. Bad rows are
    dropped silently — the LLM occasionally emits stub entries and we'd
    rather show the user a clean preview than error-halt the whole flow."""
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        issue_key = r.get("issue_key")
        hours = r.get("time_spent_hours")
        summary = r.get("summary")
        if not isinstance(issue_key, str) or not issue_key.strip():
            continue
        if not isinstance(hours, (int, float)) or isinstance(hours, bool):
            continue
        if hours < 0:
            continue
        if not isinstance(summary, str):
            continue
        out.append({
            "issue_key": issue_key.strip(),
            "time_spent_hours": float(hours),
            "summary": summary,
        })
    return out


async def _compute_suggestions(db, today: date) -> list[str]:
    """Build 3-5 suggestion chips tailored to the user's recent data.

    Priority order (used to break ties when we need to trim to 5):
      1. Active jira_issues whose key appears in activity llm_summary in the
         last 2 days — suggest "{key} 昨天/今天进展怎样？" for up to 2 most-
         frequently-mentioned issues.
      2. "今天干了什么？" if today has activity rows.
      3. "本周的开发主线是什么？" if ≥2 days this week have worklog_drafts.
      4. "最近在哪个 Jira 任务上花时间最多？" if ≥3 unique jira_issues have
         recent activity.
      5. Fallback "最近一周干了啥？" is always appended (deduped).

    Dedupe is by string equality; ordering is stable so lower-priority chips
    never push higher-priority ones out when we cap at 5.
    """
    suggestions: list[str] = []

    # (1) active issues mentioned in last-2-day activity llm_summary.
    # Pull candidate keys first (bounded set), then count mentions in SQL so
    # we don't have to drag the full llm_summary text back into Python.
    since_2d = (today - timedelta(days=2)).isoformat()
    active_issues = await db.fetch_all(
        "SELECT issue_key FROM jira_issues WHERE is_active = 1",
        (),
    )
    mention_counts: list[tuple[str, int]] = []
    for row in active_issues:
        key = row["issue_key"]
        if not key:
            continue
        count_row = await db.fetch_one(
            "SELECT COUNT(*) AS c FROM activities "
            "WHERE date(timestamp) >= ? "
            "  AND llm_summary IS NOT NULL "
            "  AND llm_summary LIKE ? "
            "  AND (deleted_at IS NULL)",
            (since_2d, f"%{key}%"),
        )
        c = int(count_row["c"]) if count_row else 0
        if c > 0:
            mention_counts.append((key, c))
    mention_counts.sort(key=lambda kv: (-kv[1], kv[0]))
    for key, _ in mention_counts[:2]:
        suggestions.append(f"{key} 昨天/今天进展怎样？")

    # (2) today had any activities?
    today_iso = today.isoformat()
    today_act = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM activities "
        "WHERE date(timestamp) = ? AND (deleted_at IS NULL)",
        (today_iso,),
    )
    if today_act and int(today_act["c"]) > 0:
        suggestions.append("今天干了什么？")

    # (3) this week ≥2 distinct days of worklog_drafts.
    # ISO week Monday — matches chat_retrieval's week semantics.
    monday = today - timedelta(days=today.isoweekday() - 1)
    sunday = monday + timedelta(days=6)
    week_days = await db.fetch_one(
        "SELECT COUNT(DISTINCT date) AS c FROM worklog_drafts "
        "WHERE date >= ? AND date <= ?",
        (monday.isoformat(), sunday.isoformat()),
    )
    if week_days and int(week_days["c"]) >= 2:
        suggestions.append("本周的开发主线是什么？")

    # (4) ≥3 unique active issues with recent (≤7d) activity mentions.
    since_7d = (today - timedelta(days=7)).isoformat()
    unique_recent = 0
    for row in active_issues:
        key = row["issue_key"]
        if not key:
            continue
        hit = await db.fetch_one(
            "SELECT 1 FROM activities "
            "WHERE date(timestamp) >= ? "
            "  AND llm_summary IS NOT NULL "
            "  AND llm_summary LIKE ? "
            "  AND (deleted_at IS NULL) LIMIT 1",
            (since_7d, f"%{key}%"),
        )
        if hit:
            unique_recent += 1
            if unique_recent >= 3:
                break
    if unique_recent >= 3:
        suggestions.append("最近在哪个 Jira 任务上花时间最多？")

    # (5) fallback is always available.
    suggestions.append("最近一周干了啥？")

    # Dedupe while preserving order, then cap at 5.
    seen: set[str] = set()
    deduped: list[str] = []
    for s in suggestions:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped[:5]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"
