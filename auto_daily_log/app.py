import asyncio
import os
from datetime import datetime
from pathlib import Path

# Clear proxy env vars BEFORE any httpx import to prevent proxy interference
# Jira SSO, LLM API calls must go direct, not through local proxy (e.g. Clash)
_PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "no_proxy", "NO_PROXY")
for _pv in _PROXY_VARS:
    os.environ.pop(_pv, None)

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import AppConfig, load_config
from .models.database import Database
from .scheduler.jobs import DailyWorkflow
from .summarizer.engine import get_llm_engine
from .search.embedding import get_embedding_engine
from .search.searcher import Searcher
from .search.indexer import Indexer
from .web.app import create_app


class Application:
    def __init__(self, config: AppConfig):
        self.config = config
        self.db: Database = None
        # Replaced by CollectorRuntime in phase 5; kept as attribute for
        # start/stop lifecycle management.
        self.monitor = None
        self.scheduler: AsyncIOScheduler = None
        # Plaintext token the built-in collector uses when calling
        # /api/ingest/* over loopback HTTP. Generated once and persisted
        # to the settings table; survives restarts.
        self._builtin_token: str = None
        # Per-activity LLM summary background worker. Created in run()
        # once the DB is ready; exposed via app.state and carried into
        # DailyWorkflow so daily-generate can await a backfill first.
        self._activity_summarizer = None

    async def _init_db(self) -> None:
        data_dir = self.config.system.resolved_data_dir
        db_path = data_dir / "data.db"
        self.db = Database(db_path, embedding_dimensions=self.config.embedding.dimensions)
        await self.db.initialize()

    def _make_builtin_collector(self):
        """Build the in-process CollectorRuntime backed by HTTPBackend.

        Called after the uvicorn server is listening on the loopback port
        so the collector's first request (heartbeat / activity POST)
        succeeds immediately.
        """
        from auto_daily_log.models.backends import HTTPBackend
        from auto_daily_log_collector.config import CollectorConfig
        from auto_daily_log_collector.enricher import ActivityEnricher
        from auto_daily_log_collector.platforms import create_adapter
        from auto_daily_log_collector.runner import CollectorRuntime

        m = self.config.monitor
        data_dir = self.config.system.resolved_data_dir
        screenshot_dir = data_dir / "screenshots"
        server_url = f"http://127.0.0.1:{self.config.server.port}"

        if not self._builtin_token:
            raise RuntimeError(
                "Built-in collector token missing — "
                "_register_builtin_collector must run before _make_builtin_collector"
            )

        backend = HTTPBackend(
            server_url=server_url,
            token=self._builtin_token,
            queue_dir=data_dir / "queue-local",
        )
        adapter = create_adapter()
        enricher = ActivityEnricher(
            screenshot_dir=screenshot_dir,
            hostile_apps_applescript=m.hostile_apps_applescript,
            hostile_apps_screenshot=m.hostile_apps_screenshot,
            phash_enabled=m.phash_enabled,
            phash_threshold=m.phash_threshold,
        )

        collector_config = CollectorConfig(
            server_url=server_url,
            name="Built-in (this machine)",
            interval_sec=m.interval_sec,
            ocr_enabled=m.ocr_enabled,
            ocr_engine=m.ocr_engine,
            screenshot_retention_days=m.screenshot_retention_days,
            idle_threshold_sec=m.idle_threshold_sec,
            phash_enabled=m.phash_enabled,
            phash_threshold=m.phash_threshold,
            blocked_apps=list(m.privacy.blocked_apps),
            blocked_urls=list(m.privacy.blocked_urls),
            hostile_apps_applescript=list(m.hostile_apps_applescript),
            hostile_apps_screenshot=list(m.hostile_apps_screenshot),
            data_dir=str(data_dir),
        )

        return CollectorRuntime(
            config=collector_config,
            backend=backend,
            adapter=adapter,
            enricher=enricher,
            machine_id="local",
            # DB row already UPSERTed in _register_builtin_collector with
            # the correct token_hash; skip the HTTP /collectors/register
            # round-trip (which would rotate the token and break auth).
            skip_http_register=True,
        )

    async def _wait_for_server_ready(self, port: int, timeout: float = 10.0) -> bool:
        """Poll loopback TCP until uvicorn is accepting connections.

        200ms tick; returns True once a connection succeeds, False on
        timeout. Caller decides what to do on failure (typically: warn
        and skip the built-in collector rather than hang the server).
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.2)
        return False

    async def _register_builtin_collector(self) -> None:
        """Auto-register the built-in monitor as collector machine_id='local'.

        Idempotent — upserts on each server start with fresh platform detection.
        Also mints/reads a plaintext token (stored in ``settings``) and writes
        its sha256 hash onto ``collectors.token_hash`` so the built-in
        collector can authenticate against ``/api/ingest/*`` over loopback HTTP
        just like any external collector.
        """
        import hashlib
        import json
        import platform as _platform
        import secrets
        import socket as _socket

        try:
            from auto_daily_log_collector.platforms.factory import detect_platform_id
            from auto_daily_log_collector.platforms import create_adapter
            platform_id = detect_platform_id()
            adapter = create_adapter(platform_id)
            platform_detail = adapter.platform_detail()
            capabilities = sorted(adapter.capabilities())
        except Exception:
            # Fallback to basic detection if collector package fails
            platform_id = _platform.system().lower()
            platform_detail = f"{_platform.system()} {_platform.release()}"
            capabilities = []

        # Step 1: mint / load the built-in token (idempotent across restarts)
        token_row = await self.db.fetch_one(
            "SELECT value FROM settings WHERE key = ?",
            ("builtin_collector_token",),
        )
        if token_row and token_row["value"]:
            token = token_row["value"]
        else:
            token = "tk-builtin-" + secrets.token_urlsafe(24)
            await self.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("builtin_collector_token", token),
            )
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self._builtin_token = token

        # Step 2: UPSERT the collectors row with current platform info +
        # the freshly-computed token_hash (so rotating the settings value
        # automatically rotates auth).
        existing = await self.db.fetch_one(
            "SELECT id FROM collectors WHERE machine_id = ?", ("local",)
        )
        if existing:
            await self.db.execute(
                """UPDATE collectors
                   SET platform = ?, platform_detail = ?, capabilities = ?,
                       hostname = ?, token_hash = ?,
                       last_seen = datetime('now'), is_active = 1
                   WHERE machine_id = ?""",
                (platform_id, platform_detail, json.dumps(capabilities),
                 _socket.gethostname(), token_hash, "local"),
            )
        else:
            await self.db.execute(
                """INSERT INTO collectors
                   (machine_id, name, hostname, platform, platform_detail,
                    capabilities, token_hash, last_seen, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 1)""",
                ("local", "Built-in (this machine)", _socket.gethostname(),
                 platform_id, platform_detail, json.dumps(capabilities),
                 token_hash),
            )

    def _init_scheduler(self) -> None:
        if not self.config.scheduler.enabled:
            return

        import logging
        _sched_tag = "[Scheduler]"

        # misfire_grace_time: if the server was down when the job should have
        # fired, APScheduler will still run it within this window (seconds).
        MISFIRE_GRACE = 2 * 60 * 60  # 2 hours

        self.scheduler = AsyncIOScheduler()

        # Daily log generation job
        gen_hour, gen_minute = map(int, self.config.scheduler.trigger_time.split(":"))

        async def daily_generate_job():
            print("[Scheduler] daily_generate triggered")
            try:
                engine = get_llm_engine(self.config.llm)
                workflow = DailyWorkflow(
                    self.db,
                    engine,
                    self.config.auto_approve,
                    activity_summarizer=self._activity_summarizer,
                )
                drafts = await workflow.run_daily_summary()
                print(f"[Scheduler] daily_generate completed: {len(drafts)} draft(s)")

                # Index today's worklogs + commits for search
                emb_engine = get_embedding_engine(self.config.llm, self.config.embedding)
                if emb_engine:
                    indexer = Indexer(self.db, emb_engine)
                    today = datetime.now().strftime("%Y-%m-%d")
                    await indexer.index_worklogs(today)
                    await indexer.index_commits(today)
            except Exception as e:
                print(f"[Scheduler] daily_generate FAILED: {type(e).__name__}: {e}")

        self.scheduler.add_job(
            daily_generate_job, "cron", hour=gen_hour, minute=gen_minute,
            id="daily_generate", misfire_grace_time=MISFIRE_GRACE,
        )

        # Auto-approve + submit job (separate trigger time)
        if self.config.auto_approve.enabled:
            approve_hour, approve_minute = map(int, self.config.auto_approve.trigger_time.split(":"))

            async def auto_approve_job():
                print("[Scheduler] auto_approve triggered")
                try:
                    engine = get_llm_engine(self.config.llm)
                    workflow = DailyWorkflow(
                        self.db,
                        engine,
                        self.config.auto_approve,
                        activity_summarizer=self._activity_summarizer,
                    )
                    today = datetime.now().strftime("%Y-%m-%d")
                    await workflow.auto_approve_and_submit(today)
                    print("[Scheduler] auto_approve completed")
                except Exception as e:
                    print(f"[Scheduler] auto_approve FAILED: {type(e).__name__}: {e}")

            self.scheduler.add_job(
                auto_approve_job, "cron", hour=approve_hour, minute=approve_minute,
                id="auto_approve", misfire_grace_time=MISFIRE_GRACE,
            )

        # Activity cleanup job — runs daily at 03:00
        async def activity_cleanup_job():
            print("activity_cleanup triggered")
            retention = self.config.system.activity_retention_days
            recycle = self.config.system.recycle_retention_days
            # Soft-delete activities older than retention days
            await self.db.execute(
                "UPDATE activities SET deleted_at = datetime('now') "
                "WHERE deleted_at IS NULL AND date(timestamp) < date('now', ?)",
                (f"-{retention} days",),
            )
            # Permanently delete recycled activities older than recycle retention
            await self.db.execute(
                "DELETE FROM activities WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)",
                (f"-{recycle} days",),
            )

        self.scheduler.add_job(
            activity_cleanup_job, "cron", hour=3, minute=0,
            id="activity_cleanup", misfire_grace_time=MISFIRE_GRACE,
        )

        self.scheduler.start()
        approve_str = f"{approve_hour}:{approve_minute:02d}" if self.config.auto_approve.enabled else "disabled"
        print(f"[Scheduler] Started: daily_generate={gen_hour}:{gen_minute:02d}, auto_approve={approve_str}, cleanup=03:00, misfire_grace={MISFIRE_GRACE}s")

        # ── Startup catch-up: if we missed today's jobs, run them now ──
        import asyncio
        asyncio.ensure_future(self._scheduler_catchup(gen_hour, gen_minute, daily_generate_job,
                                                       auto_approve_job if self.config.auto_approve.enabled else None,
                                                       approve_hour if self.config.auto_approve.enabled else None,
                                                       approve_minute if self.config.auto_approve.enabled else None))

    async def _scheduler_catchup(self, gen_h, gen_m, gen_fn, approve_fn, approve_h, approve_m):
        """If the server starts after a scheduled time and today's job hasn't
        produced output yet, run it immediately as catch-up."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        existing = await self.db.fetch_one(
            "SELECT id FROM worklog_drafts WHERE date = ? AND tag = 'daily'", (today,)
        )

        gen_time = now.replace(hour=gen_h, minute=gen_m, second=0, microsecond=0)
        if now > gen_time and not existing:
            print(f"[Scheduler:Catchup] daily_generate missed for {today}, running now")
            try:
                await gen_fn()
                print(f"[Scheduler:Catchup] daily_generate catch-up completed")
            except Exception as e:
                print(f"[Scheduler:Catchup] daily_generate failed: {e}")

        if approve_fn and approve_h is not None:
            approve_time = now.replace(hour=approve_h, minute=approve_m, second=0, microsecond=0)
            auto_approved = await self.db.fetch_one(
                "SELECT id FROM worklog_drafts WHERE date = ? AND status IN ('auto_approved', 'submitted')", (today,)
            )
            if now > approve_time and not auto_approved:
                print(f"[Scheduler:Catchup] auto_approve missed for {today}, running now")
                try:
                    await approve_fn()
                    print(f"[Scheduler:Catchup] auto_approve catch-up completed")
                except Exception as e:
                    print(f"[Scheduler:Catchup] auto_approve failed: {e}")

    async def run(self) -> None:
        await self._init_db()

        # Mint/load built-in token + UPSERT collectors row BEFORE the
        # uvicorn loop starts so /api/ingest/* authentication already
        # recognises the in-process collector on its very first request.
        if self.config.monitor.enabled:
            await self._register_builtin_collector()

        # Build the per-activity LLM summariser with late-binding engine
        # and prompt resolvers so users can rotate LLM keys / prompt text
        # at runtime without restarting the server.
        from .summarizer.activity_summarizer import ActivitySummarizer
        from .summarizer.prompt import DEFAULT_ACTIVITY_SUMMARY_PROMPT

        async def _summarizer_get_engine():
            try:
                from .web.api.worklogs import _get_llm_engine_from_settings
                return await _get_llm_engine_from_settings(self.db)
            except Exception as e:
                print(f"[ActivitySummarizer] engine lookup failed: {e}")
                return None

        async def _summarizer_get_prompt():
            row = await self.db.fetch_one(
                "SELECT value FROM settings WHERE key='activity_summary_prompt'"
            )
            if row and row["value"] and row["value"].strip():
                return row["value"]
            return DEFAULT_ACTIVITY_SUMMARY_PROMPT

        self._activity_summarizer = ActivitySummarizer(
            self.db, _summarizer_get_engine, _summarizer_get_prompt
        )

        self._init_scheduler()

        app = create_app(self.db)
        app.state.config = self.config
        # Expose so request handlers (e.g. /api/worklogs/generate) can
        # trigger a synchronous backfill before the daily LLM step.
        app.state.activity_summarizer = self._activity_summarizer

        try:
            app.state._llm_engine = get_llm_engine(self.config.llm)
        except Exception:
            app.state._llm_engine = None

        emb_engine = get_embedding_engine(self.config.llm, self.config.embedding)
        if emb_engine:
            app.state.searcher = Searcher(self.db, emb_engine)

        config = uvicorn.Config(
            app,
            host=self.config.server.host,
            port=self.config.server.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve())

        # Fire up the background activity-summary worker right after
        # uvicorn is scheduled; it polls its own loop and won't block the
        # server if the LLM engine is unreachable.
        summarizer_task = asyncio.create_task(self._activity_summarizer.run())

        monitor_task = None
        watchdog = None
        watchdog_task = None

        if self.config.monitor.enabled:
            # Wait for uvicorn to bind the loopback port before firing up
            # the in-process collector — otherwise its first POST races
            # with bind() and gets ECONNREFUSED.
            ready = await self._wait_for_server_ready(
                self.config.server.port, timeout=10.0
            )
            if not ready:
                print(
                    "[Server] uvicorn did not accept connections within 10s — "
                    "skipping built-in collector startup (server continues)"
                )
            else:
                self.monitor = self._make_builtin_collector()
                monitor_task = asyncio.create_task(self.monitor.run())
                from auto_daily_log_collector.monitor_internals.watchdog import WecomWatchdog
                dump_dir = self.config.system.resolved_data_dir / "watchdog"
                watchdog = WecomWatchdog(self.monitor.trace, dump_dir)
                watchdog_task = asyncio.create_task(watchdog.start())
        else:
            print("[Server] monitor.enabled = false — running in pure-server mode")

        try:
            await server_task
        finally:
            if self._activity_summarizer is not None:
                self._activity_summarizer.stop()
            summarizer_task.cancel()
            if self.monitor is not None:
                self.monitor.stop()
            if monitor_task:
                monitor_task.cancel()
            # Flush in-progress same-window aggregate to DB before tearing down,
            # so the last window's tail duration isn't lost on `pdl server stop`.
            if self.monitor is not None:
                try:
                    await self.monitor.close()
                except Exception as e:
                    print(f"[Server] monitor close error (non-fatal): {e}")
            if watchdog:
                watchdog.stop()
            if watchdog_task:
                watchdog_task.cancel()
            if self.scheduler:
                self.scheduler.shutdown()
            await self.db.close()
