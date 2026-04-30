"""Background worker that fills activities.llm_summary via LLM.

Polls `activities` for rows with NULL or '(failed)' llm_summary (excluding
`category='idle'` and soft-deleted rows), renders the activity-summary
prompt using the current row plus up to PREV_N prior successful summaries
from the same machine, and stores the result back on the row.

Failures are written as the sentinel '(failed)' so the next poll loop
retries them once the LLM recovers.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from ..models.database import Database
from . import core


class ActivitySummarizer:
    """Background worker that fills activities.llm_summary via LLM."""

    POLL_INTERVAL_SEC = 5
    BATCH_SIZE = 10
    PREV_N = 3
    # Previously-failed rows sit out this long before the worker tries them
    # again. Prevents a single row the upstream LLM keeps rejecting from
    # hot-looping and starving the chat endpoint of connections/rate-limit
    # budget. 24h is generous enough that transient upstream outages self-heal
    # without user intervention.
    FAIL_COOLDOWN_HOURS = 24

    def __init__(self, db: Database, get_engine: Callable, get_prompt: Callable):
        """
        get_engine: async () -> LLMEngine | None  (reads settings fresh each call)
        get_prompt: async () -> str               (user-configured or default)
        """
        self._db = db
        self._get_engine = get_engine
        self._get_prompt = get_prompt
        self._running = False

    async def run(self) -> None:
        """Main polling loop. Runs until stop() is called."""
        self._running = True
        while self._running:
            try:
                processed = await self._process_batch()
                if processed == 0:
                    await asyncio.sleep(self.POLL_INTERVAL_SEC)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[ActivitySummarizer] loop error: {e}")
                await asyncio.sleep(self.POLL_INTERVAL_SEC * 3)

    def stop(self) -> None:
        self._running = False

    async def _process_batch(self) -> int:
        """Fetch up to BATCH_SIZE pending rows, summarize each. Returns count processed.

        Selection rules:
          * Never-attempted rows (``llm_summary IS NULL``) always picked up.
          * Previously-failed rows (``llm_summary='(failed)'``) are retried only
            after a cooldown — prevents infinite retry loops on content the
            upstream provider permanently rejects (e.g. risk-control, 4xx,
            model-specific refusals). Cooldown is provider-agnostic: any LLM
            that rejects once gets the same grace period.
        """
        rows = await self._db.fetch_all(
            f"""SELECT id, machine_id, timestamp, app_name, window_title, url, signals
               FROM activities
               WHERE (
                       llm_summary IS NULL
                       OR (
                           llm_summary='(failed)'
                           AND (llm_summary_at IS NULL
                                OR llm_summary_at < datetime('now', '-{self.FAIL_COOLDOWN_HOURS} hours'))
                       )
                     )
                 AND category != 'idle'
                 AND deleted_at IS NULL
               ORDER BY timestamp ASC LIMIT ?""",
            (self.BATCH_SIZE,),
        )
        if not rows:
            return 0

        engine = await self._get_engine()
        if engine is None:
            # LLM not configured — back off (caller sleeps POLL_INTERVAL_SEC)
            return 0

        prompt_template = await self._get_prompt()

        for row in rows:
            await self._summarize_one(row, engine, prompt_template)
        return len(rows)

    async def _summarize_one(self, row: dict, engine: Any, prompt_template: str) -> None:
        prev_summaries = await self._fetch_prev_summaries(
            row["machine_id"], row["timestamp"]
        )

        async def engine_call(prompt: str) -> str:
            return await engine.generate(prompt)

        summary = await core.summarize_activity(
            row, prev_summaries, engine_call, prompt_template
        )

        await self._db.execute(
            "UPDATE activities SET llm_summary=?, llm_summary_at=datetime('now') WHERE id=?",
            (summary, row["id"]),
        )

    async def _fetch_prev_summaries(self, machine_id: str, timestamp: str) -> list[dict]:
        rows = await self._db.fetch_all(
            """SELECT timestamp, app_name, llm_summary FROM activities
               WHERE machine_id=? AND timestamp < ?
                 AND llm_summary IS NOT NULL AND llm_summary != '(failed)'
                 AND deleted_at IS NULL
               ORDER BY timestamp DESC LIMIT ?""",
            (machine_id, timestamp, self.PREV_N),
        )
        # Caller expects chronological (early -> late) order
        return list(reversed(rows))

    async def backfill_for_date(self, target_date: str, timeout_sec: int = 60) -> int:
        """Synchronously process all pending rows for a given date.

        Used by daily summary to catch up before compressing. Returns
        count processed. If the timeout hits, remaining pending rows are
        left for the _compress_activities fallback (OCR truncation).
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_sec
        total = 0
        while loop.time() < deadline:
            pending = await self._db.fetch_one(
                """SELECT COUNT(*) AS n FROM activities
                   WHERE date(timestamp)=? AND deleted_at IS NULL
                     AND (llm_summary IS NULL OR llm_summary='(failed)')
                     AND category != 'idle'""",
                (target_date,),
            )
            if not pending or pending["n"] == 0:
                break
            processed = await self._process_batch()
            total += processed
            if processed == 0:
                # Engine not configured, or no rows matched the batch query.
                break
        return total
