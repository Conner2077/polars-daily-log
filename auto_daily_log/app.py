import asyncio
from datetime import datetime
from pathlib import Path

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import AppConfig, load_config
from .models.database import Database
from .monitor.service import MonitorService
from .scheduler.jobs import DailyWorkflow
from .summarizer.engine import get_llm_engine
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
        self.db = Database(db_path)
        await self.db.initialize()

    async def _init_monitor(self) -> None:
        screenshot_dir = Path.home() / ".auto_daily_log" / "screenshots"
        self.monitor = MonitorService(self.db, self.config.monitor, screenshot_dir)

    def _init_scheduler(self) -> None:
        if not self.config.scheduler.enabled:
            return

        self.scheduler = AsyncIOScheduler()
        hour, minute = map(int, self.config.scheduler.trigger_time.split(":"))

        async def daily_job():
            engine = get_llm_engine(self.config.llm)
            workflow = DailyWorkflow(self.db, engine, self.config.auto_approve)
            await workflow.run_daily_summary()

            if self.config.auto_approve.enabled:
                timeout = self.config.auto_approve.timeout_min * 60
                await asyncio.sleep(timeout)
                today = datetime.now().strftime("%Y-%m-%d")
                await workflow.auto_approve_pending(today)

        self.scheduler.add_job(
            daily_job, "cron", hour=hour, minute=minute, id="daily_summary"
        )
        self.scheduler.start()

    async def run(self) -> None:
        await self._init_db()
        await self._init_monitor()
        self._init_scheduler()

        app = create_app(self.db)

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
