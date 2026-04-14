"""Collector runtime — samples activities and pushes to server.

This is the main loop that runs on each collector machine.
"""
import asyncio
import json
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional

from auto_daily_log.models.backends import HTTPBackend
from shared.schemas import ActivityPayload

from .client import RegistrationClient
from .config import CollectorConfig
from .credentials import load_credentials, save_credentials
from .platforms import PlatformAdapter, create_adapter


class CollectorRuntime:
    """Owns adapter + backend + sample loop + heartbeat."""

    HEARTBEAT_INTERVAL_SEC = 30

    # Allow-list of config keys a collector will honor from server override
    HONORED_OVERRIDE_KEYS = {
        "interval_sec", "ocr_enabled", "blocked_apps", "blocked_urls",
    }

    def __init__(self, config: CollectorConfig):
        self._config = config
        self._adapter: PlatformAdapter = create_adapter()
        self._backend: Optional[HTTPBackend] = None
        self._machine_id: Optional[str] = None
        self._running = False
        self._paused = False

    async def ensure_registered(self) -> str:
        """Load credentials or register with server. Returns machine_id."""
        creds = load_credentials(self._config.credentials_file)
        if creds:
            self._machine_id = creds.machine_id
            self._backend = HTTPBackend(
                server_url=self._config.server_url,
                token=creds.token,
                queue_dir=self._config.resolved_data_dir / "queue",
            )
            return creds.machine_id

        # First-time registration
        client = RegistrationClient(self._config.server_url)
        resp = await client.register(
            name=self._config.name,
            hostname=socket.gethostname(),
            platform=self._adapter.platform_id(),
            platform_detail=self._adapter.platform_detail(),
            capabilities=self._adapter.capabilities(),
        )
        save_credentials(
            self._config.credentials_file,
            resp.machine_id,
            resp.token,
        )
        self._machine_id = resp.machine_id
        self._backend = HTTPBackend(
            server_url=self._config.server_url,
            token=resp.token,
            queue_dir=self._config.resolved_data_dir / "queue",
        )
        return resp.machine_id

    async def sample_once(self) -> Optional[ActivityPayload]:
        """Capture one activity snapshot. Returns None if no app in foreground."""
        app = self._adapter.get_frontmost_app()
        if not app:
            return None

        # Privacy filter
        for blocked in self._config.blocked_apps:
            if blocked.lower() in app.lower():
                return None

        title = self._adapter.get_window_title(app)
        tab_title, url = self._adapter.get_browser_tab(app)

        # Blocked URL filter
        if url:
            for blocked in self._config.blocked_urls:
                if blocked.lower() in url.lower():
                    return None

        effective_title = tab_title or title
        return ActivityPayload(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            app_name=app,
            window_title=effective_title,
            url=url,
            duration_sec=self._config.interval_sec,
        )

    async def push_batch(self, batch: list[ActivityPayload]) -> list[int]:
        if not self._backend or not self._machine_id:
            raise RuntimeError("Collector not registered yet")
        return await self._backend.save_activities(self._machine_id, batch)

    async def heartbeat(self) -> Optional[dict]:
        """Send one heartbeat, apply any config override + pause state.

        Returns the full HeartbeatResponse dict on success, or None on
        network failure. Callers can inspect `config_override` and
        `is_paused` fields.
        """
        if not self._backend or not self._machine_id:
            return None
        response = await self._backend.heartbeat(self._machine_id)
        if response is None:
            return None
        override = response.get("config_override")
        if override:
            self._apply_override(override)
        self._paused = bool(response.get("is_paused", False))
        return response

    def _apply_override(self, override: dict) -> None:
        """Merge override into in-memory config. Unknown keys are ignored."""
        for key, value in override.items():
            if key not in self.HONORED_OVERRIDE_KEYS:
                continue
            # Use model_copy to mutate immutably
            self._config = self._config.model_copy(update={key: value})

    def set_paused(self, paused: bool) -> None:
        """Toggle sampling pause (heartbeat continues)."""
        self._paused = paused

    async def run(self) -> None:
        """Main sample loop + heartbeat. Call ensure_registered() first."""
        self._running = True
        pending: list[ActivityPayload] = []
        ticks_since_flush = 0
        seconds_since_heartbeat = 0.0

        while self._running:
            # 1. Heartbeat every HEARTBEAT_INTERVAL_SEC (uses server's
            #    current view to apply config override + pause)
            if seconds_since_heartbeat >= self.HEARTBEAT_INTERVAL_SEC:
                try:
                    await self.heartbeat()
                except Exception as e:
                    print(f"[Collector] heartbeat error: {e}")
                seconds_since_heartbeat = 0.0

            # 2. Sample — unless paused by server
            if not self._paused:
                try:
                    snap = await self.sample_once()
                    if snap:
                        pending.append(snap)

                    ticks_since_flush += 1
                    if pending and (len(pending) >= 10 or ticks_since_flush >= 3):
                        try:
                            await self.push_batch(pending)
                            pending = []
                            ticks_since_flush = 0
                        except Exception as e:
                            print(f"[Collector] push failed (will retry from queue): {e}")
                except Exception as e:
                    print(f"[Collector] sample error: {e}")

            interval = self._config.interval_sec
            await asyncio.sleep(interval)
            seconds_since_heartbeat += interval

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def config(self) -> CollectorConfig:
        return self._config

    def stop(self) -> None:
        self._running = False

    async def close(self) -> None:
        if self._backend:
            await self._backend.close()

    @property
    def machine_id(self) -> Optional[str]:
        return self._machine_id

    @property
    def adapter(self) -> PlatformAdapter:
        return self._adapter
