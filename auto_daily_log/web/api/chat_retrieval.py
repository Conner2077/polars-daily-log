"""Pure string-parsing helpers for chat retrieval.

This module stays DB-agnostic on purpose: it takes ``(text, today)`` and
returns dates / issue keys the user mentioned. The chat handler then
translates those into SQL queries.

Why regex + stdlib only: the user types free-form Chinese questions that
often contain concrete date phrases (``昨天``, ``上周三``, ``4月3日到5日``)
or Jira issue keys (``PDL-42``). Parsing these with the LLM would burn
tokens and be less deterministic than a small regex layer. See
``AGENTS.md`` §核心原则: the underlying worklog data stays raw, but the
*retrieval* step is where we narrow to what the user actually asked about.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Optional


_ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

_YMD_RE = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")

# ``M月D日到Z日`` or ``M月D日至Z日`` — multi-day range within the same month.
_MONTH_RANGE_RE = re.compile(r"(\d{1,2})月(\d{1,2})[日号](?:到|至)(\d{1,2})[日号]")

# Single ``M月D日`` / ``M月D号``.
_MONTH_DAY_RE = re.compile(r"(\d{1,2})月(\d{1,2})[日号]")

# ``上周一`` ~ ``上周日`` / ``上周1`` ~ ``上周7``.
_LAST_WEEK_WD_RE = re.compile(r"上(?:个)?(?:周|星期)([一二三四五六日天1234567])")

# Bare ``周X`` / ``星期X`` — current week's version. Written as a negative
# lookbehind for ``上`` / ``这`` / ``本`` so ``上周一`` doesn't re-match here.
_CURRENT_WEEK_WD_RE = re.compile(r"(?<![上这本])(?:周|星期)([一二三四五六日天1234567])")

_WEEKDAY_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
}


def parse_date_anchors(text: str, today: date) -> list[date]:
    """Extract concrete dates the user mentioned, resolved against ``today``.

    Returns a deduped, ascending-sorted list. Empty if nothing matched.
    Future expressions (``明天`` / ``后天``) are skipped — we have no data
    for them.
    """
    if not text:
        return []
    found: set[date] = set()

    # Order matters a bit: strip multi-day ranges before we hit the single
    # ``M月D日`` matcher, otherwise the start-day gets double-counted via
    # the single regex. We track consumed spans instead of mutating text so
    # position info stays intact for the rest of the parsers.
    consumed: list[tuple[int, int]] = []

    for m in _MONTH_RANGE_RE.finditer(text):
        month, d1, d2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if d1 > d2:
            continue
        for day in range(d1, d2 + 1):
            anchor = _resolve_month_day(month, day, today)
            if anchor is not None:
                found.add(anchor)
        consumed.append(m.span())

    def _is_consumed(span: tuple[int, int]) -> bool:
        s, e = span
        return any(cs <= s and e <= ce for cs, ce in consumed)

    for m in _YMD_RE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            found.add(date(y, mo, d))
        except ValueError:
            pass

    for m in _MONTH_DAY_RE.finditer(text):
        if _is_consumed(m.span()):
            continue
        month, day = int(m.group(1)), int(m.group(2))
        anchor = _resolve_month_day(month, day, today)
        if anchor is not None:
            found.add(anchor)

    # Named relative dates — check longer phrases first to avoid the bare
    # ``周一`` regex swallowing a ``上周一`` match later.
    if "前天" in text:
        found.add(today - timedelta(days=2))
    if "昨天" in text or "昨日" in text:
        found.add(today - timedelta(days=1))
    if "今天" in text or "今日" in text:
        found.add(today)

    if "上月" in text or "上个月" in text:
        found.update(_previous_month_dates(today))
    if "本月" in text or "这个月" in text:
        found.update(_this_month_dates_up_to_today(today))

    if "上周" in text or "上个星期" in text:
        # Specific weekday overrides (``上周三``) are handled below; the bare
        # ``上周`` / ``上个星期`` expands to the full 7 days.
        if not _LAST_WEEK_WD_RE.search(text):
            found.update(_iso_week_dates(today - timedelta(days=7)))
        else:
            for m in _LAST_WEEK_WD_RE.finditer(text):
                wd = _WEEKDAY_MAP[m.group(1)]
                found.add(_weekday_of_iso_week(today - timedelta(days=7), wd))

    # ``这周`` / ``本周`` / ``这个星期`` → full current ISO week.
    if "这周" in text or "本周" in text or "这个星期" in text:
        found.update(_iso_week_dates(today))

    # Bare ``周X`` / ``星期X`` — current week's version.
    for m in _CURRENT_WEEK_WD_RE.finditer(text):
        wd = _WEEKDAY_MAP[m.group(1)]
        found.add(_weekday_of_iso_week(today, wd))

    return sorted(found)


def extract_issue_keys(text: str) -> list[str]:
    """Return Jira issue keys in first-appearance order, deduped, uppercase.

    Matches ``[A-Z][A-Z0-9]+-\\d+``. Trailing punctuation is stripped by
    the ``\\b`` anchor in the regex.
    """
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _ISSUE_KEY_RE.finditer(text):
        key = m.group(1).upper()
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


# ─── date helpers ────────────────────────────────────────────────────

def _resolve_month_day(month: int, day: int, today: date) -> Optional[date]:
    """Resolve ``M月D日`` against the current year; wrap to previous year
    if the resulting date would be in the future."""
    year = today.year
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    if candidate > today:
        try:
            return date(year - 1, month, day)
        except ValueError:
            return None
    return candidate


def _iso_week_dates(anchor: date) -> list[date]:
    """Return Monday..Sunday of the ISO week containing ``anchor``."""
    monday = anchor - timedelta(days=anchor.isoweekday() - 1)
    return [monday + timedelta(days=i) for i in range(7)]


def _weekday_of_iso_week(anchor: date, weekday: int) -> date:
    """Return the date of ``weekday`` (1=Mon, 7=Sun) in the ISO week
    containing ``anchor``."""
    monday = anchor - timedelta(days=anchor.isoweekday() - 1)
    return monday + timedelta(days=weekday - 1)


def _previous_month_dates(today: date) -> list[date]:
    """Every date of the calendar month prior to ``today``."""
    first_of_this_month = today.replace(day=1)
    last_of_prev = first_of_this_month - timedelta(days=1)
    year, month = last_of_prev.year, last_of_prev.month
    last_day = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last_day + 1)]


def _this_month_dates_up_to_today(today: date) -> list[date]:
    """Every date from the 1st of the current month up to and including ``today``."""
    return [date(today.year, today.month, d) for d in range(1, today.day + 1)]
