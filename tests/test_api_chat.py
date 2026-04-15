"""Tests for /api/chat.

The LLM is monkeypatched — we verify context assembly, SSE framing, and
error paths, not actual model quality.
"""
import json
from datetime import date

import pytest
import pytest_asyncio

from auto_daily_log.web.api import chat as chat_module


class _FakeLLM:
    def __init__(self, response: str = "这是助手的回复，用来验证 SSE 流式分块逻辑。"):
        self.response = response
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class _FailingLLM:
    async def generate(self, prompt: str) -> str:
        raise RuntimeError("api key missing")


def _patch_engine(monkeypatch, engine):
    async def _factory(_db):
        return engine
    monkeypatch.setattr(chat_module, "_get_llm_engine_from_settings", _factory)


def _parse_sse(body: str) -> list:
    """Parse an SSE body into a list of events (dict for JSON, str for [DONE])."""
    events = []
    for block in body.strip().split("\n\n"):
        if not block.startswith("data: "):
            continue
        payload = block[len("data: "):].strip()
        if payload == "[DONE]":
            events.append("[DONE]")
        else:
            events.append(json.loads(payload))
    return events


# ─── happy path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_streams_chunked_response(app_client, monkeypatch):
    fake = _FakeLLM(response="Hello world from the fake LLM.")
    _patch_engine(monkeypatch, fake)

    resp = await app_client.post("/api/chat", json={
        "messages": [{"role": "user", "text": "今天我都做了什么？"}],
    })
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    text_events = [e for e in events if isinstance(e, dict) and "text" in e]
    assembled = "".join(e["text"] for e in text_events)
    assert assembled == "Hello world from the fake LLM."
    assert events[-1] == "[DONE]"


@pytest.mark.asyncio
async def test_chat_injects_user_question_into_prompt(app_client, monkeypatch):
    fake = _FakeLLM()
    _patch_engine(monkeypatch, fake)

    await app_client.post("/api/chat", json={
        "messages": [
            {"role": "user", "text": "上周我在 PDL-42 花了多少时间？"},
        ],
    })
    assert len(fake.prompts) == 1
    assert "上周我在 PDL-42 花了多少时间？" in fake.prompts[0]


@pytest.mark.asyncio
async def test_chat_includes_history(app_client, monkeypatch):
    fake = _FakeLLM()
    _patch_engine(monkeypatch, fake)

    await app_client.post("/api/chat", json={
        "messages": [
            {"role": "user", "text": "昨天干嘛了？"},
            {"role": "ai",   "text": "昨天你在写 chat endpoint。"},
            {"role": "user", "text": "具体改了哪些文件？"},
        ],
    })
    prompt = fake.prompts[0]
    assert "昨天干嘛了？" in prompt
    assert "昨天你在写 chat endpoint。" in prompt
    assert "具体改了哪些文件？" in prompt


@pytest.mark.asyncio
async def test_chat_pulls_recent_drafts_into_prompt(app_client, monkeypatch):
    # seed a worklog draft for today
    today = date.today().isoformat()
    await app_client.post("/api/worklogs/seed", json={
        "date": today,
        "issue_key": "PDL-42",
        "time_spent_sec": 3600,
        "summary": "Built the chat endpoint with SSE framing.",
    })

    fake = _FakeLLM()
    _patch_engine(monkeypatch, fake)

    await app_client.post("/api/chat", json={
        "messages": [{"role": "user", "text": "今天干了啥？"}],
    })
    prompt = fake.prompts[0]
    assert "PDL-42" in prompt
    assert "Built the chat endpoint with SSE framing." in prompt


# ─── error path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_reports_llm_error_as_sse_event(app_client, monkeypatch):
    _patch_engine(monkeypatch, _FailingLLM())

    resp = await app_client.post("/api/chat", json={
        "messages": [{"role": "user", "text": "hi"}],
    })
    assert resp.status_code == 200  # stream opens OK; error is inside the stream
    events = _parse_sse(resp.text)
    error_events = [e for e in events if isinstance(e, dict) and "error" in e]
    assert len(error_events) == 1
    assert "api key missing" in error_events[0]["error"]
    assert events[-1] == "[DONE]"


# ─── helper unit tests ──────────────────────────────────────────────

def test_chunk_text_splits_by_size():
    out = chat_module._chunk_text("abcdefghij", 4)
    assert out == ["abcd", "efgh", "ij"]


def test_chunk_text_empty():
    assert chat_module._chunk_text("", 4) == [""]


def test_format_summaries_groups_by_date():
    rows = [
        {"date": "2026-04-14", "issue_key": "PDL-1", "full_summary": "A", "summary": ""},
        {"date": "2026-04-15", "issue_key": "PDL-2", "full_summary": "B", "summary": ""},
        {"date": "2026-04-15", "issue_key": "PDL-3", "full_summary": "C", "summary": ""},
    ]
    out = chat_module._format_summaries(rows)
    # newest date comes first, each draft as a bullet
    lines = out.split("\n")
    assert lines[0] == "### 2026-04-15"
    assert "- [PDL-2] B" in lines
    assert "- [PDL-3] C" in lines
    assert "### 2026-04-14" in out


def test_format_summaries_empty():
    assert chat_module._format_summaries([]) == "(最近 7 天无工作日志)"


def test_format_history_skips_when_empty():
    assert chat_module._format_history([]) == ""


def test_latest_user_question_picks_last_user_message():
    msgs = [
        chat_module.ChatMessage(role="user", text="first"),
        chat_module.ChatMessage(role="ai", text="answer"),
        chat_module.ChatMessage(role="user", text="second"),
    ]
    assert chat_module._latest_user_question(msgs) == "second"
