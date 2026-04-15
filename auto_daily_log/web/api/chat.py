"""Chat endpoint — answers questions over local worklog + activity data.

Wire format (deep-chat compatible):
  SSE events carry JSON payloads ``{"text": "<chunk>"}`` with a final
  ``data: [DONE]`` sentinel. On error: ``{"error": "<msg>"}`` then DONE.

The endpoint delegates token emission to ``LLMEngine.generate_stream`` —
engines that speak SSE upstream (OpenAI-compatible, etc.) forward deltas
live; engines without native streaming fall back to the base class's
fake-stream implementation.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...summarizer import prompt as prompt_module
from ...summarizer.prompt import render_prompt
from .worklogs import _get_llm_engine_from_settings

router = APIRouter(tags=["chat"])


# Tight defaults keep the prefill small — any LLM/provider answers faster
# when less context is stuffed in. Callers that genuinely want a wider
# window can raise ``context_days`` per request (capped at MAX_CONTEXT_DAYS).
DEFAULT_CONTEXT_DAYS = 2
MAX_CONTEXT_DAYS = 14
MAX_ACTIVITY_SUMMARIES = 15
MAX_DRAFT_ROWS = 10


class ChatMessage(BaseModel):
    role: str  # "user" | "ai"
    text: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    context_days: int | None = None  # override default window; clamped server-side


@router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    db = request.app.state.db

    user_question = _latest_user_question(body.messages)
    history = _format_history(body.messages[:-1] if body.messages else [])

    today = date.today()
    window = body.context_days if body.context_days is not None else DEFAULT_CONTEXT_DAYS
    window = max(1, min(window, MAX_CONTEXT_DAYS))
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

    prompt = render_prompt(
        prompt_module.DEFAULT_CHAT_PROMPT,
        today=today.isoformat(),
        recent_summaries=_format_summaries(summaries),
        recent_activities=_format_activities(activities),
        history=history or "(无历史)",
        question=user_question or "(空)",
    )

    llm = await _get_llm_engine_from_settings(db)

    async def gen() -> AsyncGenerator[str, None]:
        try:
            async for chunk in llm.generate_stream(prompt):
                if chunk:
                    yield _sse({"text": chunk})
        except Exception as exc:
            yield _sse({"error": f"LLM call failed: {exc}"})
        yield _sse_done()

    return StreamingResponse(gen(), media_type="text/event-stream")


# ─── helpers ─────────────────────────────────────────────────────────

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


def _chunk_text(text: str, size: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + size] for i in range(0, len(text), size)]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


