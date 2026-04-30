"""Direct unit tests for summarizer.core — pure functions, no DB.

These tests pin down the algorithm so the eval harness has a stable
contract to compare against. If something here breaks, the daily log
quality is going to shift even before anyone changes a prompt.
"""
import json

import pytest

from auto_daily_log.summarizer import core
from auto_daily_log.summarizer.prompt import (
    DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    DEFAULT_AUTO_APPROVE_PROMPT,
    DEFAULT_SUMMARIZE_PROMPT,
)


# ─── parse_signals ───────────────────────────────────────────────────────

def test_parse_signals_none_returns_empty_dict():
    assert core.parse_signals(None) == {}


def test_parse_signals_empty_string_returns_empty_dict():
    assert core.parse_signals("") == {}


def test_parse_signals_passes_through_dict():
    payload = {"ocr_text": "x"}
    assert core.parse_signals(payload) is payload


def test_parse_signals_parses_json_string():
    raw = json.dumps({"ocr_text": "abc", "tab_title": "t"})
    assert core.parse_signals(raw) == {"ocr_text": "abc", "tab_title": "t"}


def test_parse_signals_swallows_invalid_json():
    assert core.parse_signals("not json") == {}


# ─── format_prev_activity_lines ──────────────────────────────────────────

def test_format_prev_empty_returns_marker():
    assert core.format_prev_activity_lines([]) == "（无）"


def test_format_prev_extracts_hh_mm_from_iso_timestamp():
    rows = [{"timestamp": "2026-04-14T10:30:00", "app_name": "VSCode", "llm_summary": "调试"}]
    assert core.format_prev_activity_lines(rows) == "- 10:30 [VSCode] 调试"


def test_format_prev_handles_short_timestamp():
    rows = [{"timestamp": "10:30", "app_name": "X", "llm_summary": "y"}]
    # Short timestamps fall through unmodified
    assert core.format_prev_activity_lines(rows) == "- 10:30 [X] y"


# ─── format_commits ──────────────────────────────────────────────────────

def test_format_commits_empty_returns_无():
    assert core.format_commits([]) == "无"


def test_format_commits_renders_one_line_per_commit():
    out = core.format_commits([
        {"committed_at": "2026-04-12T10:30:00", "message": "fix: foo", "files_changed": "a.py"},
    ])
    assert out == "- 2026-04-12T10:30 fix: foo (a.py)"


# ─── format_jira_issues ──────────────────────────────────────────────────

def test_format_jira_issues_empty_returns_fallback():
    out = core.format_jira_issues([])
    assert out == "无（将所有工作汇总为一条，issue_key 使用 ALL）"


def test_format_jira_issues_renders_lines():
    out = core.format_jira_issues([
        {"issue_key": "PROJ-1", "summary": "S", "description": "D"},
    ])
    assert out == "- PROJ-1: S (D)"


# ─── compress_activities ────────────────────────────────────────────────

def test_compress_activities_empty_returns_无():
    assert core.compress_activities([]) == "无"


def test_compress_activities_drops_groups_below_min_hours():
    """A 5-minute activity rounds to 0.1h — but with default min 0.1, included.
    A 2-minute activity rounds to 0.0 → dropped."""
    activities = [
        {
            "category": "coding", "app_name": "VSCode", "window_title": "main.py",
            "duration_sec": 120, "llm_summary": "短任务", "signals": None,
        },
    ]
    assert core.compress_activities(activities) == "无"


def test_compress_activities_prefers_llm_summary_over_ocr():
    activities = [{
        "category": "coding", "app_name": "VSCode", "window_title": "main.py",
        "duration_sec": 3600, "llm_summary": "调试 main.py",
        "signals": json.dumps({"ocr_text": "x" * 200}),
    }]
    out = core.compress_activities(activities)
    assert "内容: 调试 main.py" in out
    assert "OCR:" not in out


def test_compress_activities_uses_ocr_when_summary_failed():
    activities = [{
        "category": "coding", "app_name": "VSCode", "window_title": "main.py",
        "duration_sec": 3600, "llm_summary": "(failed)",
        "signals": json.dumps({"ocr_text": "print(hello)"}),
    }]
    out = core.compress_activities(activities)
    assert "OCR: print(hello)" in out
    assert "(failed)" not in out


def test_compress_activities_strategy_clip():
    """Custom strategy reduces ocr_clip_chars — verifies parameterization."""
    strategy = core.CompressionStrategy(ocr_clip_chars=5)
    activities = [{
        "category": "x", "app_name": "Y", "window_title": "Z",
        "duration_sec": 3600, "llm_summary": None,
        "signals": json.dumps({"ocr_text": "0123456789"}),
    }]
    out = core.compress_activities(activities, strategy=strategy)
    assert "OCR: 01234" in out
    assert "0123456789" not in out


# ─── parse_issue_entries ────────────────────────────────────────────────

def test_parse_issue_entries_handles_pure_json():
    payload = json.dumps([{"issue_key": "A-1", "time_spent_hours": 1, "summary": "x"}])
    out = core.parse_issue_entries(payload)
    assert out == [{"issue_key": "A-1", "time_spent_hours": 1, "summary": "x"}]


def test_parse_issue_entries_extracts_array_from_prose():
    response = '前言话术 [{"issue_key": "A-1", "time_spent_hours": 0.5, "summary": "y"}] 后语'
    out = core.parse_issue_entries(response)
    assert len(out) == 1
    assert out[0]["issue_key"] == "A-1"


def test_parse_issue_entries_empty_input_returns_empty():
    assert core.parse_issue_entries("") == []


def test_parse_issue_entries_invalid_json_returns_empty():
    assert core.parse_issue_entries("[ this is broken json") == []


def test_parse_issue_entries_non_array_returns_empty():
    assert core.parse_issue_entries('{"issue_key": "A-1"}') == []


# ─── merge_issue_entries ────────────────────────────────────────────────

def test_merge_issue_entries_drops_other_and_blank_keys():
    parsed = [
        {"issue_key": "OTHER", "time_spent_hours": 1, "summary": "drop me"},
        {"issue_key": "", "time_spent_hours": 1, "summary": "drop me too"},
        {"issue_key": "PROJ-1", "time_spent_hours": 0.5, "summary": "keep"},
    ]
    out = core.merge_issue_entries(parsed)
    assert len(out) == 1
    assert out[0]["issue_key"] == "PROJ-1"


def test_merge_issue_entries_skips_invalid_hours():
    parsed = [{"issue_key": "PROJ-1", "time_spent_hours": "not a number", "summary": "x"}]
    assert core.merge_issue_entries(parsed) == []


def test_merge_issue_entries_dedups_and_sums_hours():
    parsed = [
        {"issue_key": "PROJ-1", "time_spent_hours": 1.0, "summary": "morning work"},
        {"issue_key": "PROJ-1", "time_spent_hours": 0.5, "summary": "afternoon"},
        {"issue_key": "PROJ-2", "time_spent_hours": 2.0, "summary": "other"},
    ]
    out = core.merge_issue_entries(parsed)
    by_key = {e["issue_key"]: e for e in out}
    assert by_key["PROJ-1"]["time_spent_hours"] == 1.5
    assert by_key["PROJ-1"]["summary"] == "morning work；afternoon"
    assert by_key["PROJ-2"]["time_spent_hours"] == 2.0
    assert by_key["PROJ-2"]["summary"] == "other"


def test_merge_issue_entries_attaches_jira_worklog_id_none():
    parsed = [{"issue_key": "PROJ-1", "time_spent_hours": 1, "summary": "x"}]
    out = core.merge_issue_entries(parsed)
    assert out[0]["jira_worklog_id"] is None


# ─── summarize_activity (async) ──────────────────────────────────────────

class FakeEngine:
    def __init__(self, response="正在调试", raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.captured_prompt = None

    async def __call__(self, prompt: str) -> str:
        self.captured_prompt = prompt
        if self.raise_exc:
            raise self.raise_exc
        return self.response


@pytest.mark.asyncio
async def test_summarize_activity_clips_to_max_chars():
    engine = FakeEngine(response="x" * 500)
    out = await core.summarize_activity(
        {"timestamp": "2026-04-14T10:00:00", "app_name": "A"},
        prev_summaries=[],
        engine_call=engine,
        template=DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    )
    assert len(out) == core.ACTIVITY_SUMMARY_MAX_CHARS
    assert out == "x" * core.ACTIVITY_SUMMARY_MAX_CHARS


@pytest.mark.asyncio
async def test_summarize_activity_returns_failed_on_exception():
    engine = FakeEngine(raise_exc=RuntimeError("upstream 500"))
    out = await core.summarize_activity(
        {"timestamp": "2026-04-14T10:00:00", "app_name": "A"},
        prev_summaries=[],
        engine_call=engine,
        template=DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    )
    assert out == core.FAILED_MARKER


@pytest.mark.asyncio
async def test_summarize_activity_returns_failed_on_blank():
    engine = FakeEngine(response="   \n  ")
    out = await core.summarize_activity(
        {"timestamp": "2026-04-14T10:00:00", "app_name": "A"},
        prev_summaries=[],
        engine_call=engine,
        template=DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    )
    assert out == core.FAILED_MARKER


@pytest.mark.asyncio
async def test_summarize_activity_renders_signals_into_prompt():
    engine = FakeEngine(response="ok")
    record = {
        "timestamp": "2026-04-14T10:00:00",
        "app_name": "Chrome",
        "window_title": "GH",
        "url": "https://github.com",
        "signals": json.dumps({
            "ocr_text": "print('hi')",
            "tab_title": "GitHub",
            "wecom_group_name": "研发组",
        }),
    }
    await core.summarize_activity(
        record, prev_summaries=[], engine_call=engine,
        template=DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    )
    assert "print('hi')" in engine.captured_prompt
    assert "GitHub" in engine.captured_prompt
    assert "研发组" in engine.captured_prompt
    assert "https://github.com" in engine.captured_prompt


# ─── generate_full_summary / generate_issue_entries (async) ──────────────

@pytest.mark.asyncio
async def test_generate_full_summary_strips_whitespace():
    engine = FakeEngine(response="  日报正文  \n")
    out = await core.generate_full_summary(
        "2026-04-14", activities_text="活动", commits_text="commits",
        engine_call=engine, template=DEFAULT_SUMMARIZE_PROMPT,
    )
    assert out == "日报正文"


@pytest.mark.asyncio
async def test_generate_full_summary_empty_response_returns_empty():
    engine = FakeEngine(response="")
    out = await core.generate_full_summary(
        "2026-04-14", activities_text="x", commits_text="y",
        engine_call=engine, template=DEFAULT_SUMMARIZE_PROMPT,
    )
    assert out == ""


@pytest.mark.asyncio
async def test_generate_issue_entries_pipeline():
    engine = FakeEngine(response=json.dumps([
        {"issue_key": "PROJ-1", "time_spent_hours": 1.5, "summary": "调通 join"},
        {"issue_key": "OTHER", "time_spent_hours": 0.5, "summary": "应被丢"},
    ]))
    out = await core.generate_issue_entries(
        "2026-04-14", full_summary="日报", commits_text="c",
        issues_text="- PROJ-1: x", engine_call=engine,
        template=DEFAULT_AUTO_APPROVE_PROMPT,
    )
    assert len(out) == 1
    assert out[0]["issue_key"] == "PROJ-1"
    assert out[0]["time_spent_hours"] == 1.5
    assert out[0]["jira_worklog_id"] is None
