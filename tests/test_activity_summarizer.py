"""Unit tests for ActivitySummarizer background worker.

Uses a real Database fixture plus a MockEngine; no external LLM calls.
"""
import json

import pytest
import pytest_asyncio

from auto_daily_log.models.database import Database
from auto_daily_log.summarizer.activity_summarizer import ActivitySummarizer
from auto_daily_log.summarizer.prompt import DEFAULT_ACTIVITY_SUMMARY_PROMPT


class MockEngine:
    """Mock LLM engine capturing prompts and returning canned responses."""

    def __init__(self, responses=None, fail=False):
        self.responses = list(responses) if responses else []
        self.fail = fail
        self.calls: list[str] = []

    async def generate(self, prompt):
        self.calls.append(prompt)
        if self.fail:
            raise Exception("simulated LLM failure")
        if self.responses:
            return self.responses.pop(0)
        return "mock summary"


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


def _make_summarizer(db, engine, prompt_template=None):
    async def get_engine():
        return engine

    async def get_prompt():
        return prompt_template or DEFAULT_ACTIVITY_SUMMARY_PROMPT

    return ActivitySummarizer(db, get_engine, get_prompt)


async def _insert_activity(db, **overrides):
    row = {
        "timestamp": "2026-04-14T10:00:00",
        "app_name": "VSCode",
        "window_title": "main.py",
        "category": "coding",
        "confidence": 0.9,
        "url": None,
        "signals": None,
        "duration_sec": 60,
        "machine_id": "local",
    }
    row.update(overrides)
    return await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, confidence, url, signals, duration_sec, machine_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["timestamp"],
            row["app_name"],
            row["window_title"],
            row["category"],
            row["confidence"],
            row["url"],
            row["signals"],
            row["duration_sec"],
            row["machine_id"],
        ),
    )


@pytest.mark.asyncio
async def test_process_single_row_success(db):
    act_id = await _insert_activity(db, app_name="VSCode", window_title="debug.py")
    engine = MockEngine(responses=["在调试 debug.py 文件"])
    summarizer = _make_summarizer(db, engine)

    n = await summarizer._process_batch()

    assert n == 1
    row = await db.fetch_one("SELECT llm_summary, llm_summary_at FROM activities WHERE id = ?", (act_id,))
    assert row["llm_summary"] == "在调试 debug.py 文件"
    assert row["llm_summary_at"] is not None
    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_llm_exception_marks_row_failed(db):
    act_id = await _insert_activity(db)
    engine = MockEngine(fail=True)
    summarizer = _make_summarizer(db, engine)

    n = await summarizer._process_batch()

    assert n == 1
    row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (act_id,))
    assert row["llm_summary"] == "(failed)"


@pytest.mark.asyncio
async def test_skips_idle_category(db):
    idle_id = await _insert_activity(db, category="idle", app_name="(idle)")
    work_id = await _insert_activity(db, category="coding", app_name="VSCode", timestamp="2026-04-14T10:01:00")
    engine = MockEngine(responses=["工作中"])
    summarizer = _make_summarizer(db, engine)

    n = await summarizer._process_batch()

    assert n == 1
    idle_row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (idle_id,))
    work_row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (work_id,))
    assert idle_row["llm_summary"] is None
    assert work_row["llm_summary"] == "工作中"


@pytest.mark.asyncio
async def test_skips_soft_deleted(db):
    deleted_id = await _insert_activity(db, app_name="DeletedApp")
    await db.execute("UPDATE activities SET deleted_at = datetime('now') WHERE id = ?", (deleted_id,))
    active_id = await _insert_activity(db, app_name="ActiveApp", timestamp="2026-04-14T10:01:00")
    engine = MockEngine(responses=["正在使用 ActiveApp"])
    summarizer = _make_summarizer(db, engine)

    n = await summarizer._process_batch()

    assert n == 1
    deleted_row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (deleted_id,))
    active_row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (active_id,))
    assert deleted_row["llm_summary"] is None
    assert active_row["llm_summary"] == "正在使用 ActiveApp"


@pytest.mark.asyncio
async def test_retries_failed_row(db):
    act_id = await _insert_activity(db)
    # Pre-mark as failed (llm_summary_at left NULL — never previously attempted)
    await db.execute("UPDATE activities SET llm_summary = '(failed)' WHERE id = ?", (act_id,))
    engine = MockEngine(responses=["成功识别"])
    summarizer = _make_summarizer(db, engine)

    n = await summarizer._process_batch()

    assert n == 1
    row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (act_id,))
    assert row["llm_summary"] == "成功识别"


@pytest.mark.asyncio
async def test_skips_recently_failed_row_during_cooldown(db):
    """A row that failed within FAIL_COOLDOWN_HOURS must NOT be retried.

    Prevents the infinite retry loop observed when the upstream LLM
    permanently rejects certain content (risk-control, persistent 4xx).
    Provider-agnostic — same behaviour protects every configured engine.
    """
    act_id = await _insert_activity(db)
    # Failed 1 hour ago — well inside the 24h cooldown.
    await db.execute(
        "UPDATE activities SET llm_summary='(failed)', "
        "llm_summary_at=datetime('now', '-1 hours') WHERE id = ?",
        (act_id,),
    )
    engine = MockEngine(responses=["不该被调用"])
    summarizer = _make_summarizer(db, engine)

    n = await summarizer._process_batch()

    assert n == 0
    assert engine.calls == []  # never hit the LLM
    row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (act_id,))
    assert row["llm_summary"] == "(failed)"  # unchanged


@pytest.mark.asyncio
async def test_retries_failed_row_after_cooldown(db):
    """Once FAIL_COOLDOWN_HOURS elapses, the row becomes eligible again."""
    act_id = await _insert_activity(db)
    # Failed 25 hours ago — cooldown expired.
    await db.execute(
        "UPDATE activities SET llm_summary='(failed)', "
        "llm_summary_at=datetime('now', '-25 hours') WHERE id = ?",
        (act_id,),
    )
    engine = MockEngine(responses=["二次尝试成功"])
    summarizer = _make_summarizer(db, engine)

    n = await summarizer._process_batch()

    assert n == 1
    row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (act_id,))
    assert row["llm_summary"] == "二次尝试成功"


@pytest.mark.asyncio
async def test_prev_summaries_same_machine_only(db):
    # Prior summary on DIFFERENT machine (should NOT appear in prev context)
    await _insert_activity(
        db,
        timestamp="2026-04-14T09:00:00",
        app_name="OtherMachineApp",
        machine_id="remote-mac",
    )
    await db.execute(
        "UPDATE activities SET llm_summary = ? WHERE app_name = ?",
        ("来自远程机器的摘要", "OtherMachineApp"),
    )

    # Prior summary on SAME machine (should appear)
    await _insert_activity(
        db,
        timestamp="2026-04-14T09:30:00",
        app_name="LocalEarlier",
        machine_id="local",
    )
    await db.execute(
        "UPDATE activities SET llm_summary = ? WHERE app_name = ?",
        ("本机器早先摘要", "LocalEarlier"),
    )

    # Current row
    current_id = await _insert_activity(
        db,
        timestamp="2026-04-14T10:00:00",
        app_name="CurrentApp",
        machine_id="local",
    )

    engine = MockEngine(responses=["当前活动摘要"])
    summarizer = _make_summarizer(db, engine)

    await summarizer._process_batch()

    # Verify the prompt that was sent included only the same-machine prior summary
    assert len(engine.calls) == 1
    prompt_sent = engine.calls[0]
    assert "本机器早先摘要" in prompt_sent
    assert "来自远程机器的摘要" not in prompt_sent


@pytest.mark.asyncio
async def test_prev_summaries_excludes_null_and_failed(db):
    # Three priors on same machine: one OK, one NULL, one '(failed)'
    await _insert_activity(
        db, timestamp="2026-04-14T09:00:00", app_name="GoodPrior", machine_id="local"
    )
    await db.execute(
        "UPDATE activities SET llm_summary = ? WHERE app_name = ?",
        ("好的先前摘要", "GoodPrior"),
    )
    # NULL llm_summary prior — automatically NULL from insert
    await _insert_activity(
        db, timestamp="2026-04-14T09:15:00", app_name="NullPrior", machine_id="local"
    )
    # Failed prior
    await _insert_activity(
        db, timestamp="2026-04-14T09:30:00", app_name="FailedPrior", machine_id="local"
    )
    await db.execute(
        "UPDATE activities SET llm_summary = '(failed)' WHERE app_name = ?",
        ("FailedPrior",),
    )

    # Current row
    await _insert_activity(
        db, timestamp="2026-04-14T10:00:00", app_name="CurrentApp", machine_id="local"
    )

    summarizer = _make_summarizer(db, MockEngine())
    prevs = await summarizer._fetch_prev_summaries("local", "2026-04-14T10:00:00")

    assert len(prevs) == 1
    assert prevs[0]["llm_summary"] == "好的先前摘要"
    assert prevs[0]["app_name"] == "GoodPrior"


@pytest.mark.asyncio
async def test_backfill_empty_db_returns_zero(db):
    engine = MockEngine()
    summarizer = _make_summarizer(db, engine)
    n = await summarizer.backfill_for_date("2026-04-14", timeout_sec=5)
    assert n == 0
    assert len(engine.calls) == 0


@pytest.mark.asyncio
async def test_backfill_processes_failed_rows_too(db):
    # Two rows on target date: one NULL, one failed
    null_id = await _insert_activity(db, timestamp="2026-04-14T10:00:00", app_name="A")
    failed_id = await _insert_activity(db, timestamp="2026-04-14T10:01:00", app_name="B")
    await db.execute(
        "UPDATE activities SET llm_summary = '(failed)' WHERE id = ?", (failed_id,)
    )
    engine = MockEngine(responses=["A summary", "B summary"])
    summarizer = _make_summarizer(db, engine)

    n = await summarizer.backfill_for_date("2026-04-14", timeout_sec=5)

    assert n == 2
    a_row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (null_id,))
    b_row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (failed_id,))
    assert a_row["llm_summary"] == "A summary"
    assert b_row["llm_summary"] == "B summary"


@pytest.mark.asyncio
async def test_signals_json_extracted_into_prompt(db):
    signals = json.dumps({
        "ocr_text": "print('hello world')",
        "tab_title": "GitHub - example repo",
        "wecom_group_name": "研发小组",
    })
    await _insert_activity(db, app_name="Chrome", signals=signals, url="https://github.com")
    engine = MockEngine(responses=["在浏览 GitHub"])
    summarizer = _make_summarizer(db, engine)

    await summarizer._process_batch()

    assert len(engine.calls) == 1
    prompt_sent = engine.calls[0]
    assert "print('hello world')" in prompt_sent
    assert "GitHub - example repo" in prompt_sent
    assert "研发小组" in prompt_sent
    assert "https://github.com" in prompt_sent


@pytest.mark.asyncio
async def test_empty_llm_response_marked_failed(db):
    act_id = await _insert_activity(db)
    engine = MockEngine(responses=["   "])  # whitespace-only -> failed
    summarizer = _make_summarizer(db, engine)

    await summarizer._process_batch()

    row = await db.fetch_one("SELECT llm_summary FROM activities WHERE id = ?", (act_id,))
    assert row["llm_summary"] == "(failed)"


@pytest.mark.asyncio
async def test_engine_none_returns_zero(db):
    await _insert_activity(db)

    async def get_engine():
        return None

    async def get_prompt():
        return DEFAULT_ACTIVITY_SUMMARY_PROMPT

    summarizer = ActivitySummarizer(db, get_engine, get_prompt)
    n = await summarizer._process_batch()

    assert n == 0
