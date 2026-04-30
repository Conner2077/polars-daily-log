"""Smoke tests for the offline eval harness.

Verifies the wiring (load dataset → run pipeline → score) works
end-to-end against the deterministic mock engine. These tests do NOT
measure LLM accuracy — that's what the harness is for, with a real engine.
"""
import json
from pathlib import Path

import pytest

from auto_daily_log import eval_summarizer
from auto_daily_log.eval_summarizer import (
    DEFAULT_DATASET,
    MockEngine,
    evaluate_activity_case,
    evaluate_daily_case,
    evaluate_dataset,
    evaluate_issues_case,
)


def _load_baseline() -> dict:
    return json.loads(Path(DEFAULT_DATASET).read_text(encoding="utf-8"))


def test_default_dataset_exists_and_parses():
    assert DEFAULT_DATASET.exists(), f"baseline dataset missing at {DEFAULT_DATASET}"
    data = _load_baseline()
    assert data["activities"], "dataset should have at least one activity case"
    assert data["dailies"], "dataset should have at least one daily case"
    assert data["issues"], "dataset should have at least one issues case"


@pytest.mark.asyncio
async def test_mock_engine_passes_activity_cases():
    """Mock engine returns canned content shaped to satisfy baseline.json.
    This proves the harness can run end-to-end."""
    data = _load_baseline()
    engine = MockEngine()
    for case in data["activities"]:
        result = await evaluate_activity_case(
            case, engine, eval_summarizer.DEFAULT_ACTIVITY_SUMMARY_PROMPT,
        )
        assert result.passed, f"{case['id']} failed: {result.failures}"


@pytest.mark.asyncio
async def test_mock_engine_passes_daily_case():
    data = _load_baseline()
    engine = MockEngine()
    case = data["dailies"][0]
    result = await evaluate_daily_case(
        case, engine, eval_summarizer.DEFAULT_SUMMARIZE_PROMPT,
    )
    assert result.passed, f"{case['id']} failed: {result.failures}"


@pytest.mark.asyncio
async def test_mock_engine_passes_issues_case():
    data = _load_baseline()
    engine = MockEngine()
    case = data["issues"][0]
    result = await evaluate_issues_case(
        case, engine, eval_summarizer.DEFAULT_AUTO_APPROVE_PROMPT,
    )
    assert result.passed, f"{case['id']} failed: {result.failures}"


@pytest.mark.asyncio
async def test_evaluate_dataset_returns_aggregated_report():
    data = _load_baseline()
    report = await evaluate_dataset(data, MockEngine())
    assert len(report.stages) == 3
    stage_names = [s.stage for s in report.stages]
    assert stage_names == ["activity", "daily", "issues"]
    assert report.passed == report.total
    assert report.pass_rate == 1.0


@pytest.mark.asyncio
async def test_evaluate_dataset_filters_stages():
    data = _load_baseline()
    report = await evaluate_dataset(data, MockEngine(), stages=["daily"])
    assert len(report.stages) == 1
    assert report.stages[0].stage == "daily"


@pytest.mark.asyncio
async def test_failing_assertion_marks_case_failed():
    """Inject a case the mock can't satisfy — confirms scoring catches it."""
    bad_case = {
        "id": "intentionally-impossible",
        "record": {"timestamp": "2026-04-14T10:00:00", "app_name": "Unknown"},
        "prev_summaries": [],
        "expected": {"must_contain_any_of": ["这个字符串不会出现"]},
    }
    result = await evaluate_activity_case(
        bad_case, MockEngine(), eval_summarizer.DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    )
    assert not result.passed
    assert any("missing all of" in f for f in result.failures)


@pytest.mark.asyncio
async def test_issues_case_catches_extra_forbidden_key():
    """Engine returns key X, dataset forbids X — must fail."""
    case = {
        "id": "forbidden-key-test",
        "date": "2026-04-14",
        "full_summary": "",
        "commits_text": "",
        "issues": [{"issue_key": "DROP-ME", "summary": "x", "description": ""}],
        "expected": {"must_not_have_keys": ["DROP-ME"]},
    }
    result = await evaluate_issues_case(
        case, MockEngine(), eval_summarizer.DEFAULT_AUTO_APPROVE_PROMPT,
    )
    assert not result.passed
    assert any("DROP-ME" in f for f in result.failures)
