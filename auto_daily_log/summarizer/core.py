"""Pure functions for the activity → daily-summary pipeline.

Extracted so the algorithm can be tested and evaluated without booting the
DB, scheduler, or polling worker. The surrounding modules
(`activity_summarizer.py`, `summarizer.py`) keep their orchestration role
and delegate the actual prompt-rendering / LLM-calling / parsing here.

Engine input is a callable `async (prompt: str) -> str` so any source —
real LLM, mock, recorded fixture — plugs in identically.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

from .prompt import render_prompt

EngineCall = Callable[[str], Awaitable[str]]

FAILED_MARKER = "(failed)"
ACTIVITY_SUMMARY_MAX_CHARS = 200


# ─── Compression strategy ────────────────────────────────────────────────

@dataclass(frozen=True)
class CompressionStrategy:
    """Knobs controlling how a day's activities collapse into prompt text.

    Exposed as a dataclass so eval scripts can A/B different settings
    without forking compress_activities.
    """
    title_clip_chars: int = 60
    titles_per_group: int = 5
    summaries_per_group: int = 8
    ocr_clip_chars: int = 100
    ocr_snippets_per_group: int = 3
    min_group_hours: float = 0.1


DEFAULT_STRATEGY = CompressionStrategy()


# ─── Helpers shared across stages ────────────────────────────────────────

def parse_signals(raw: Any) -> dict:
    """Tolerant parse of the activities.signals column (TEXT/JSON/None/dict)."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def format_prev_activity_lines(prev_rows: Iterable[dict]) -> str:
    """Render prior llm_summary rows into the bullet block fed to the
    activity-summary prompt. Caller is responsible for ordering
    (early → late) and for excluding NULL/(failed) entries."""
    rows = list(prev_rows)
    if not rows:
        return "（无）"
    lines = []
    for r in rows:
        ts_full = r.get("timestamp") or ""
        ts = ts_full[11:16] if len(ts_full) >= 16 else ts_full
        lines.append(f"- {ts} [{r.get('app_name', '')}] {r.get('llm_summary', '')}")
    return "\n".join(lines)


def format_commits(commits: Iterable[dict]) -> str:
    rows = list(commits)
    if not rows:
        return "无"
    return "\n".join(
        f"- {(c.get('committed_at') or '')[:16]} {c.get('message', '')} ({c.get('files_changed', '') or ''})"
        for c in rows
    )


def format_jira_issues(issues: Iterable[dict]) -> str:
    rows = list(issues)
    if not rows:
        return "无（将所有工作汇总为一条，issue_key 使用 ALL）"
    return "\n".join(
        f"- {i['issue_key']}: {i.get('summary', '')} ({i.get('description') or ''})"
        for i in rows
    )


# ─── Stage 1: per-activity llm_summary ───────────────────────────────────

def render_activity_prompt(
    record: dict,
    prev_summaries: Iterable[dict],
    template: str,
) -> str:
    """Render the single-activity guess prompt. Pure — no IO."""
    signals = parse_signals(record.get("signals"))
    return render_prompt(
        template,
        prev_summaries=format_prev_activity_lines(prev_summaries),
        timestamp=record.get("timestamp", ""),
        app_name=record.get("app_name") or "",
        window_title=record.get("window_title") or "",
        url=record.get("url") or "",
        tab_title=signals.get("tab_title") or "",
        ocr_text=signals.get("ocr_text") or "",
        wecom_group=signals.get("wecom_group_name") or "",
    )


async def summarize_activity(
    record: dict,
    prev_summaries: Iterable[dict],
    engine_call: EngineCall,
    template: str,
    *,
    max_chars: int = ACTIVITY_SUMMARY_MAX_CHARS,
) -> str:
    """Produce one llm_summary string for a single activity row.

    Returns the FAILED_MARKER sentinel if the engine raises or returns
    blank — caller writes it back so the cooldown logic in
    ActivitySummarizer kicks in. Output longer than max_chars is clipped
    (the prompt asks ≤100 chars but LLMs drift)."""
    prompt = render_activity_prompt(record, prev_summaries, template)
    try:
        raw = await engine_call(prompt)
    except Exception as e:  # noqa: BLE001
        print(f"[summarizer.core] activity LLM failed: {e}")
        return FAILED_MARKER
    summary = (raw or "").strip()
    if not summary:
        return FAILED_MARKER
    if len(summary) > max_chars:
        summary = summary[:max_chars]
    return summary


# ─── Stage 2: compress a day's activities into prompt text ───────────────

def compress_activities(
    activities: Iterable[dict],
    strategy: CompressionStrategy = DEFAULT_STRATEGY,
) -> str:
    """Group by (category, app_name), collapse into one bullet per group.

    Prefers llm_summary (the dense, ≤100 char per-activity guess); falls
    back to truncated OCR only when the activity-summary worker hasn't
    reached this row yet or gave up on it ('(failed)' sentinel)."""
    rows = list(activities)
    if not rows:
        return "无"

    groups: dict[tuple, dict] = defaultdict(lambda: {
        "duration": 0,
        "titles": set(),
        "llm_summaries": [],
        "ocr_fallback": [],
    })
    for a in rows:
        key = (a.get("category", "other"), a.get("app_name", "Unknown"))
        groups[key]["duration"] += a.get("duration_sec", 0) or 0
        title = a.get("window_title")
        if title:
            groups[key]["titles"].add(title[: strategy.title_clip_chars])

        llm_sum = a.get("llm_summary")
        if llm_sum and llm_sum != FAILED_MARKER:
            if llm_sum not in groups[key]["llm_summaries"]:
                groups[key]["llm_summaries"].append(llm_sum)
        else:
            signals = parse_signals(a.get("signals"))
            ocr = (signals.get("ocr_text") or "")[: strategy.ocr_clip_chars]
            if ocr and len(groups[key]["ocr_fallback"]) < strategy.ocr_snippets_per_group:
                groups[key]["ocr_fallback"].append(ocr)

    lines = []
    for (cat, app), info in sorted(groups.items(), key=lambda x: -x[1]["duration"]):
        hours = round(info["duration"] / 3600, 1)
        if hours < strategy.min_group_hours:
            continue
        titles = list(info["titles"])[: strategy.titles_per_group]
        title_str = ", ".join(titles) if titles else ""
        line = f"- [{cat}] {app} ({hours}h): {title_str}"
        if info["llm_summaries"]:
            summaries = "；".join(info["llm_summaries"][: strategy.summaries_per_group])
            line += f" | 内容: {summaries}"
        elif info["ocr_fallback"]:
            line += f" | OCR: {'; '.join(info['ocr_fallback'][:2])}"
        lines.append(line)

    return "\n".join(lines) or "无"


# ─── Stage 3: full daily summary (Step 1 of two-step pipeline) ───────────

async def generate_full_summary(
    target_date: str,
    activities_text: str,
    commits_text: str,
    engine_call: EngineCall,
    template: str,
) -> str:
    """Return the unfiltered Markdown daily log. Empty string means the
    engine returned nothing — caller decides whether to skip."""
    prompt = render_prompt(
        template,
        date=target_date,
        git_commits=commits_text,
        activities=activities_text,
    )
    raw = await engine_call(prompt)
    return (raw or "").strip()


# ─── Stage 4: per-issue Jira entries (Step 2 of two-step pipeline) ───────

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def parse_issue_entries(response: str) -> list[dict]:
    """Extract the JSON array out of the auto-approve response.

    The prompt asks for a clean array but LLMs sometimes wrap it in
    prose or code fences; grab the first [...] block and parse it."""
    if not response:
        return []
    m = _JSON_ARRAY_RE.search(response)
    if not m:
        return []
    try:
        result = json.loads(m.group())
    except json.JSONDecodeError:
        return []
    return result if isinstance(result, list) else []


def merge_issue_entries(parsed: Iterable[dict]) -> list[dict]:
    """Dedup by issue_key, sum hours, drop OTHER and unmapped rows.

    Mirrors the in-code enforcement of "同一 issue_key 合并为一条" from
    DEFAULT_AUTO_APPROVE_PROMPT — the LLM occasionally splits them and
    we'd rather not push duplicate worklogs."""
    merged: dict[str, dict] = {}
    for item in parsed:
        try:
            hours = float(item.get("time_spent_hours", 0))
        except (TypeError, ValueError):
            continue
        key = item.get("issue_key") or ""
        if not key or key == "OTHER":
            continue
        summary_text = (item.get("summary") or "").strip()
        if key in merged:
            merged[key]["time_spent_hours"] = round(
                merged[key]["time_spent_hours"] + hours, 2
            )
            if summary_text:
                existing = merged[key]["summary"]
                merged[key]["summary"] = (
                    f"{existing}；{summary_text}" if existing else summary_text
                )
        else:
            merged[key] = {
                "issue_key": key,
                "time_spent_hours": round(hours, 2),
                "summary": summary_text,
                "jira_worklog_id": None,
            }
    return list(merged.values())


async def generate_issue_entries(
    target_date: str,
    full_summary: str,
    commits_text: str,
    issues_text: str,
    engine_call: EngineCall,
    template: str,
) -> list[dict]:
    """Step 2: filter + polish the full summary into per-issue Jira entries.

    Pure pipeline: render → call → parse JSON array → merge by issue_key.
    No DB, no side effects."""
    prompt = render_prompt(
        template,
        date=target_date,
        jira_issues=issues_text,
        full_summary=full_summary,
        git_commits=commits_text,
    )
    response = await engine_call(prompt)
    parsed = parse_issue_entries(response)
    return merge_issue_entries(parsed)
