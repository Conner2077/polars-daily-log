"""Unit tests for chat_retrieval parsing helpers.

Dates are anchored to ``date(2026, 4, 16)`` — a Thursday — so every
expected value in this file can be hand-computed without needing to know
``date.today()``.
"""
from datetime import date, timedelta

import pytest

from auto_daily_log.web.api.chat_retrieval import (
    extract_issue_keys,
    parse_date_anchors,
)


TODAY = date(2026, 4, 16)  # Thursday


# ─── named relative dates ────────────────────────────────────────────

def test_today_maps_to_today():
    assert parse_date_anchors("今天干了啥", TODAY) == [date(2026, 4, 16)]


def test_jinri_alias_for_today():
    assert parse_date_anchors("今日进度", TODAY) == [date(2026, 4, 16)]


def test_yesterday_maps_to_minus_one():
    assert parse_date_anchors("昨天干了啥", TODAY) == [date(2026, 4, 15)]


def test_zuori_alias_for_yesterday():
    assert parse_date_anchors("昨日进度", TODAY) == [date(2026, 4, 15)]


def test_day_before_yesterday_maps_to_minus_two():
    assert parse_date_anchors("前天干了啥", TODAY) == [date(2026, 4, 14)]


def test_tomorrow_is_skipped():
    assert parse_date_anchors("明天要做啥", TODAY) == []


def test_day_after_tomorrow_is_skipped():
    assert parse_date_anchors("后天要做啥", TODAY) == []


def test_multiple_relative_dates_merge():
    assert parse_date_anchors("昨天和前天", TODAY) == [
        date(2026, 4, 14),
        date(2026, 4, 15),
    ]


# ─── week / month ranges ────────────────────────────────────────────

def test_this_week_expands_to_seven_days():
    assert parse_date_anchors("这周干了啥", TODAY) == [
        date(2026, 4, 13),
        date(2026, 4, 14),
        date(2026, 4, 15),
        date(2026, 4, 16),
        date(2026, 4, 17),
        date(2026, 4, 18),
        date(2026, 4, 19),
    ]


def test_benzhou_alias_for_this_week():
    assert parse_date_anchors("本周干了啥", TODAY) == [
        date(2026, 4, 13),
        date(2026, 4, 14),
        date(2026, 4, 15),
        date(2026, 4, 16),
        date(2026, 4, 17),
        date(2026, 4, 18),
        date(2026, 4, 19),
    ]


def test_zhege_xingqi_alias_for_this_week():
    assert parse_date_anchors("这个星期干了啥", TODAY) == [
        date(2026, 4, 13),
        date(2026, 4, 14),
        date(2026, 4, 15),
        date(2026, 4, 16),
        date(2026, 4, 17),
        date(2026, 4, 18),
        date(2026, 4, 19),
    ]


def test_last_week_expands_to_seven_days():
    assert parse_date_anchors("上周干了啥", TODAY) == [
        date(2026, 4, 6),
        date(2026, 4, 7),
        date(2026, 4, 8),
        date(2026, 4, 9),
        date(2026, 4, 10),
        date(2026, 4, 11),
        date(2026, 4, 12),
    ]


def test_shangge_xingqi_alias_for_last_week():
    assert parse_date_anchors("上个星期干了啥", TODAY) == [
        date(2026, 4, 6),
        date(2026, 4, 7),
        date(2026, 4, 8),
        date(2026, 4, 9),
        date(2026, 4, 10),
        date(2026, 4, 11),
        date(2026, 4, 12),
    ]


def test_last_month_expands_to_full_previous_calendar_month():
    got = parse_date_anchors("上月的日志", TODAY)
    expected = [date(2026, 3, d) for d in range(1, 32)]
    assert got == expected


def test_shangge_yue_alias_for_last_month():
    got = parse_date_anchors("上个月的日志", TODAY)
    expected = [date(2026, 3, d) for d in range(1, 32)]
    assert got == expected


def test_this_month_runs_from_first_up_to_today():
    got = parse_date_anchors("本月干了啥", TODAY)
    expected = [date(2026, 4, d) for d in range(1, 17)]
    assert got == expected


def test_zhege_yue_alias_for_this_month():
    got = parse_date_anchors("这个月干了啥", TODAY)
    expected = [date(2026, 4, d) for d in range(1, 17)]
    assert got == expected


# ─── weekday-of-week ────────────────────────────────────────────────

def test_last_week_monday_specific_date():
    assert parse_date_anchors("上周一", TODAY) == [date(2026, 4, 6)]


def test_last_week_sunday_chinese_ri():
    assert parse_date_anchors("上周日", TODAY) == [date(2026, 4, 12)]


def test_last_week_numeric_7_is_sunday():
    assert parse_date_anchors("上周7", TODAY) == [date(2026, 4, 12)]


def test_last_week_numeric_1_is_monday():
    assert parse_date_anchors("上周1", TODAY) == [date(2026, 4, 6)]


def test_bare_zhouyi_is_current_week_monday():
    assert parse_date_anchors("周一", TODAY) == [date(2026, 4, 13)]


def test_bare_xingqi_wu_is_current_week_friday():
    assert parse_date_anchors("星期五", TODAY) == [date(2026, 4, 17)]


def test_bare_zhouri_is_current_week_sunday():
    assert parse_date_anchors("周日", TODAY) == [date(2026, 4, 19)]


# ─── explicit YYYY-MM-DD / M月D日 ───────────────────────────────────

def test_iso_date_with_dash():
    assert parse_date_anchors("2026-03-05 的日志", TODAY) == [date(2026, 3, 5)]


def test_iso_date_with_slash():
    assert parse_date_anchors("2026/03/05 的日志", TODAY) == [date(2026, 3, 5)]


def test_iso_date_with_dot():
    assert parse_date_anchors("2026.03.05 的日志", TODAY) == [date(2026, 3, 5)]


def test_month_day_ri_current_year():
    assert parse_date_anchors("3月5日干了啥", TODAY) == [date(2026, 3, 5)]


def test_month_day_hao_current_year():
    assert parse_date_anchors("3月5号干了啥", TODAY) == [date(2026, 3, 5)]


def test_month_day_future_wraps_to_previous_year():
    # 5月1日 is after 2026-04-16 → wrap to 2025-05-01
    assert parse_date_anchors("5月1日干了啥", TODAY) == [date(2025, 5, 1)]


def test_month_day_range_expands_inclusive():
    assert parse_date_anchors("3月5日到8日干了啥", TODAY) == [
        date(2026, 3, 5),
        date(2026, 3, 6),
        date(2026, 3, 7),
        date(2026, 3, 8),
    ]


def test_month_day_range_with_zhi():
    assert parse_date_anchors("3月5日至7日", TODAY) == [
        date(2026, 3, 5),
        date(2026, 3, 6),
        date(2026, 3, 7),
    ]


# ─── no match / empty ───────────────────────────────────────────────

def test_no_date_expression_returns_empty_list():
    assert parse_date_anchors("hello world", TODAY) == []


def test_empty_string_returns_empty_list():
    assert parse_date_anchors("", TODAY) == []


# ─── issue key extraction ──────────────────────────────────────────

def test_extract_issue_keys_two_keys():
    assert extract_issue_keys("帮我看 PDL-42 和 PROJ-1234 的进度") == ["PDL-42", "PROJ-1234"]


def test_extract_issue_keys_strips_trailing_punctuation():
    assert extract_issue_keys("PDL-42.") == ["PDL-42"]


def test_extract_issue_keys_deduped_first_appearance_order():
    assert extract_issue_keys("PDL-42 然后又看 PROJ-1 再回到 PDL-42") == ["PDL-42", "PROJ-1"]


def test_extract_issue_keys_uppercased():
    assert extract_issue_keys("pdl-42") == []  # lowercase not matched — keys are uppercase
    assert extract_issue_keys("PDL-42") == ["PDL-42"]


def test_extract_issue_keys_no_match():
    assert extract_issue_keys("hello") == []


def test_extract_issue_keys_empty():
    assert extract_issue_keys("") == []
