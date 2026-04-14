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
from .monitor.service import MonitorService
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
        self.monitor: MonitorService = None
        self.scheduler: AsyncIOScheduler = None

    async def _init_db(self) -> None:
        data_dir = self.config.system.resolved_data_dir
        db_path = data_dir / "data.db"
        self.db = Database(db_path, embedding_dimensions=self.config.embedding.dimensions)
        await self.db.initialize()

    async def _init_monitor(self) -> None:
        if not self.config.monitor.enabled:
            # Pure-server mode: no built-in collector
            self.monitor = None
            return
        screenshot_dir = self.config.system.resolved_data_dir / "screenshots"
        self.monitor = MonitorService(self.db, self.config.monitor, screenshot_dir)

    async def _register_builtin_collector(self) -> None:
        """Auto-register the built-in monitor as collector machine_id='local'.

        Idempotent — upserts on each server start with fresh platform detection.
        The token_hash is set to a sentinel that the normal auth path cannot
        match (no collector ever sends this); the built-in monitor writes
        directly to DB, not via the HTTP ingest path.
        """
        import json
        import platform as _platform
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

        existing = await self.db.fetch_one(
            "SELECT id FROM collectors WHERE machine_id = ?", ("local",)
        )
        if existing:
            await self.db.execute(
                """UPDATE collectors
                   SET platform = ?, platform_detail = ?, capabilities = ?,
                       hostname = ?, last_seen = datetime('now'), is_active = 1
                   WHERE machine_id = ?""",
                (platform_id, platform_detail, json.dumps(capabilities),
                 _socket.gethostname(), "local"),
            )
        else:
            await self.db.execute(
                """INSERT INTO collectors
                   (machine_id, name, hostname, platform, platform_detail,
                    capabilities, token_hash, last_seen, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 1)""",
                ("local", "Built-in (this machine)", _socket.gethostname(),
                 platform_id, platform_detail, json.dumps(capabilities),
                 "__builtin_no_http_auth__"),
            )

    def _init_scheduler(self) -> None:
        if not self.config.scheduler.enabled:
            return

        self.scheduler = AsyncIOScheduler()

        # Daily log generation job
        gen_hour, gen_minute = map(int, self.config.scheduler.trigger_time.split(":"))

        async def daily_generate_job():
            engine = get_llm_engine(self.config.llm)
            workflow = DailyWorkflow(self.db, engine, self.config.auto_approve)
            await workflow.run_daily_summary()

            # Index today's worklogs + commits for search
            emb_engine = get_embedding_engine(self.config.llm, self.config.embedding)
            if emb_engine:
                indexer = Indexer(self.db, emb_engine)
                today = datetime.now().strftime("%Y-%m-%d")
                await indexer.index_worklogs(today)
                await indexer.index_commits(today)

        self.scheduler.add_job(
            daily_generate_job, "cron", hour=gen_hour, minute=gen_minute, id="daily_generate"
        )

        # Auto-approve + submit job (separate trigger time)
        if self.config.auto_approve.enabled:
            approve_hour, approve_minute = map(int, self.config.auto_approve.trigger_time.split(":"))

            async def auto_approve_job():
                engine = get_llm_engine(self.config.llm)
                workflow = DailyWorkflow(self.db, engine, self.config.auto_approve)
                today = datetime.now().strftime("%Y-%m-%d")
                await workflow.auto_approve_and_submit(today)

            self.scheduler.add_job(
                auto_approve_job, "cron", hour=approve_hour, minute=approve_minute, id="auto_approve"
            )

        # Activity cleanup job — runs daily at 03:00
        async def activity_cleanup_job():
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
            activity_cleanup_job, "cron", hour=3, minute=0, id="activity_cleanup"
        )

        self.scheduler.start()

    async def run(self) -> None:
        await self._init_db()
        await self._init_monitor()
        self._init_scheduler()

        # Auto-register built-in collector (machine_id='local') iff monitor is on
        if self.monitor is not None:
            await self._register_builtin_collector()

        app = create_app(self.db)
        app.state.config = self.config

        try:
            app.state._llm_engine = get_llm_engine(self.config.llm)
        except Exception:
            app.state._llm_engine = None

        emb_engine = get_embedding_engine(self.config.llm, self.config.embedding)
        if emb_engine:
            app.state.searcher = Searcher(self.db, emb_engine)

        monitor_task = None
        watchdog = None
        watchdog_task = None
        if self.monitor is not None:
            monitor_task = asyncio.create_task(self.monitor.start())
            from .monitor.watchdog import WecomWatchdog
            dump_dir = self.config.system.resolved_data_dir / "watchdog"
            watchdog = WecomWatchdog(self.monitor.trace, dump_dir)
            watchdog_task = asyncio.create_task(watchdog.start())
        else:
            print("[Server] monitor.enabled = false — running in pure-server mode")

        config = uvicorn.Config(
            app,
            host=self.config.server.host,
            port=self.config.server.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        try:
            await server.serve()
        finally:
            if self.monitor is not None:
                self.monitor.stop()
            if monitor_task:
                monitor_task.cancel()
            if watchdog:
                watchdog.stop()
            if watchdog_task:
                watchdog_task.cancel()
            if self.scheduler:
                self.scheduler.shutdown()
            await self.db.close()
