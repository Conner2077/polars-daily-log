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
        db_path = Path.home() / ".auto_daily_log" / "data.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(db_path, embedding_dimensions=self.config.embedding.dimensions)
        await self.db.initialize()

    async def _init_monitor(self) -> None:
        screenshot_dir = Path.home() / ".auto_daily_log" / "screenshots"
        self.monitor = MonitorService(self.db, self.config.monitor, screenshot_dir)

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

        self.scheduler.start()

    async def run(self) -> None:
        await self._init_db()
        await self._init_monitor()
        self._init_scheduler()

        app = create_app(self.db)

        # Attach LLM engine to app state for API use (daily generate)
        try:
            app.state._llm_engine = get_llm_engine(self.config.llm)
        except Exception:
            app.state._llm_engine = None

        # Attach searcher to app state if embedding is enabled
        emb_engine = get_embedding_engine(self.config.llm, self.config.embedding)
        if emb_engine:
            app.state.searcher = Searcher(self.db, emb_engine)

        monitor_task = asyncio.create_task(self.monitor.start())

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
            self.monitor.stop()
            monitor_task.cancel()
            if self.scheduler:
                self.scheduler.shutdown()
            await self.db.close()
