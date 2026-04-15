"""Chat endpoint — answers questions over local worklog + activity data.

MVP notes
---------
* Wire format is deep-chat compatible: SSE events carry JSON payloads
  ``{"text": "<chunk>"}`` with a final ``[DONE]`` sentinel.
* The underlying LLMEngine has no streaming method yet, so this endpoint
  calls ``generate()`` and fake-streams the full response in fixed-size
  chunks. Swap ``_chunk_text`` for ``engine.generate_stream()`` once the
  engine grows real streaming.
"""
from __future__ import annotations

import asyncio
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


RECENT_DAYS = 7
MAX_ACTIVITY_SUMMARIES = 40
MAX_DRAFT_ROWS = 30
CHUNK_SIZE = 32


class ChatMessage(BaseModel):
    role: str  # "user" | "ai"
    text: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    db = request.app.state.db

    user_question = _latest_user_question(body.messages)
    history = _format_history(body.messages[:-1] if body.messages else [])

    today = date.today()
    since = (today - timedelta(days=RECENT_DAYS)).isoformat()

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
        "  AND llm_summary <> '(failed)' "
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
            response = await llm.generate(prompt)
        except Exception as exc:
            yield _sse({"error": f"LLM call failed: {exc}"})
            yield _sse_done()
            return
        for chunk in _chunk_text(response, CHUNK_SIZE):
            yield _sse({"text": chunk})
            await asyncio.sleep(0)
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
        return "(最近 7 天无工作日志)"
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
