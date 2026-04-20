"""Collector runtime — samples activities and pushes to any StorageBackend.

This is the single loop that drives both the built-in collector (when
the server runs with ``monitor.enabled = true``) and standalone collector
processes. Both paths use the same ``HTTPBackend`` against ``/api/ingest/*``
— the built-in variant just talks to ``127.0.0.1:<server.port>`` over
loopback and skips HTTP registration because the server wrote its own
row directly.

The loop itself — same-window aggregation, idle detection, hostile-app
handling, enrichment — is shared.
"""
import asyncio
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional

from auto_daily_log.models.backends import HTTPBackend
from auto_daily_log.models.backends.base import StorageBackend
from shared.schemas import ActivityPayload

from .client import RegistrationClient
from .config import CollectorConfig
from .credentials import load_credentials, save_credentials
from .enricher import ActivityEnricher
from .monitor_internals.watchdog import MonitorTrace
from .platforms import PlatformAdapter, create_adapter


class CollectorRuntime:
    """Owns adapter + enricher + backend + sample loop + heartbeat."""

    HEARTBEAT_INTERVAL_SEC = 30

    # Allow-list of config keys honored from server override
    HONORED_OVERRIDE_KEYS = {
        "interval_sec", "ocr_enabled", "ocr_engine",
        "screenshot_retention_days",
        "blocked_apps", "blocked_urls",
    }

    def __init__(
        self,
        config: CollectorConfig,
        *,
        backend: Optional[StorageBackend] = None,
        adapter: Optional[PlatformAdapter] = None,
        enricher: Optional[ActivityEnricher] = None,
        machine_id: Optional[str] = None,
        skip_http_register: bool = False,
    ):
        """Create a runtime.

        Parameters
        ----------
        config
            Parsed collector config (see :class:`CollectorConfig`).
        backend
            Where activities/commits go. If omitted, an ``HTTPBackend``
            is lazily created inside :meth:`ensure_registered` — the
            standalone path.
        adapter
            Platform adapter (window probing, screenshot, idle). If
            omitted, :func:`create_adapter` picks one.
        enricher
            Activity enricher (classification, OCR, phash). If omitted,
            one is built from config (screenshot dir under data_dir).
        machine_id
            Pre-set machine_id, skipping HTTP registration. Used by the
            server's built-in collector (``"local"``).
        skip_http_register
            When True, :meth:`ensure_registered` returns immediately
            after confirming backend + machine_id are present. Used by
            the server's built-in collector (which UPSERTs its own
            collectors row directly in-process before starting the
            runtime).
        """
        self._config = config
        self._adapter: PlatformAdapter = adapter or create_adapter()
        self._backend: Optional[StorageBackend] = backend
        self._enricher: ActivityEnricher = enricher or self._build_default_enricher()
        self._machine_id: Optional[str] = machine_id
        self._skip_http_register: bool = skip_http_register
        self._trace: MonitorTrace = MonitorTrace()
        self._running = False
        self._paused = False

        # Same-window aggregation state: cache last row + its duration so we
        # can batch ``extend_duration`` calls at window boundaries instead of
        # spamming the backend on every tick.
        self._last_app: Optional[str] = None
        self._last_title: Optional[str] = None
        self._last_row_id: Optional[int] = None
        self._pending_extend_sec: int = 0

        # Idle aggregation state
        self._last_was_idle: bool = False
        self._last_idle_row_id: Optional[int] = None

    def _build_default_enricher(self) -> ActivityEnricher:
        screenshot_dir = self._config.resolved_data_dir / "screenshots"
        return ActivityEnricher(
            screenshot_dir=screenshot_dir,
            hostile_apps_applescript=self._config.hostile_apps_applescript,
            hostile_apps_screenshot=self._config.hostile_apps_screenshot,
            phash_enabled=self._config.phash_enabled,
            phash_threshold=self._config.phash_threshold,
        )

    # ─── Registration ──────────────────────────────────────────────────

    async def ensure_registered(self) -> str:
        """Resolve ``machine_id`` + backend. Returns the machine_id."""
        if self._skip_http_register:
            if self._machine_id is None:
                raise RuntimeError(
                    "skip_http_register=True requires machine_id to be "
                    "set in the constructor"
                )
            if self._backend is None:
                raise RuntimeError(
                    "skip_http_register=True requires a backend "
                    "(HTTPBackend with a pre-provisioned token) in the "
                    "constructor"
                )
            return self._machine_id

        creds = load_credentials(self._config.credentials_file)
        if creds:
            self._machine_id = creds.machine_id
            if self._backend is None:
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

    # ─── Sampling ──────────────────────────────────────────────────────

    def _is_blocked(self, app: Optional[str], url: Optional[str]) -> bool:
        if app:
            for blocked in self._config.blocked_apps:
                if blocked.lower() in app.lower():
                    return True
        if url:
            for blocked in self._config.blocked_urls:
                if blocked.lower() in url.lower():
                    return True
        return False

    async def _flush_pending_extend(self) -> None:
        """Push accumulated same-window duration to the backend."""
        if self._pending_extend_sec > 0 and self._last_row_id is not None:
            await self._backend.extend_duration(
                self._machine_id, self._last_row_id, self._pending_extend_sec
            )
        self._pending_extend_sec = 0

    async def sample_once(self) -> Optional[int]:
        """Take one sample. Returns the new row_id on insert, else None.

        Handles idle detection, same-window aggregation, hostile-app
        probe skipping, privacy filters, and enrichment before
        delegating to the injected backend.
        """
        if not self._backend or not self._machine_id:
            raise RuntimeError("Collector not registered yet — call ensure_registered()")

        interval_sec = self._config.interval_sec

        idle_sec = self._adapter.get_idle_seconds()
        is_idle = idle_sec >= self._config.idle_threshold_sec

        if is_idle:
            # Idle wins — but first bank any pending same-window extend for
            # the active row we were following.
            await self._flush_pending_extend()
            self._last_app = None
            self._last_title = None
            self._last_row_id = None
            # Enricher has its own same-window dedup state; clear it too so
            # returning from idle to the same app+title gets a fresh
            # screenshot + OCR rather than reusing stale pre-idle data.
            self._enricher.reset_window_state()

            if self._last_was_idle and self._last_idle_row_id is not None:
                await self._backend.extend_duration(
                    self._machine_id, self._last_idle_row_id, interval_sec
                )
                return None

            idle_payload = ActivityPayload(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                app_name="System",
                window_title="Idle",
                category="idle",
                confidence=0.99,
                duration_sec=interval_sec,
            )
            ids = await self._backend.save_activities(self._machine_id, [idle_payload])
            self._last_idle_row_id = ids[0] if ids else None
            self._last_was_idle = True
            return self._last_idle_row_id

        # Not idle — leaving idle aggregation
        self._last_was_idle = False
        self._last_idle_row_id = None

        self._trace.log("get_frontmost_app")
        app = self._adapter.get_frontmost_app()
        self._trace.log("got_frontmost", app=app)
        if not app:
            return None

        is_hostile = self._enricher.is_hostile_applescript(app)

        if is_hostile:
            self._trace.log("skip_probe_hostile", app=app)
            title = None
            url = None
            wecom_group = None
        else:
            self._trace.log("get_window_title", app=app)
            title = self._adapter.get_window_title(app)
            self._trace.log("got_window_title", app=app, title=title)
            self._trace.log("get_browser_tab", app=app)
            tab_title, url = self._adapter.get_browser_tab(app)
            self._trace.log("got_browser_tab", app=app, tab=tab_title, url=url)
            title = tab_title or title
            self._trace.log("get_wecom_chat", app=app)
            wecom_group = self._adapter.get_wecom_chat_name(app)
            self._trace.log("got_wecom_chat", app=app, group=wecom_group)

        if self._is_blocked(app, url):
            return None

        # Same-window aggregation: accumulate duration locally; flush only
        # at window boundaries so we save on network chatter when the user
        # stays on one window for a long time.
        if (
            app == self._last_app
            and title == self._last_title
            and self._last_row_id is not None
        ):
            self._pending_extend_sec += interval_sec
            return None

        # Window changed — flush outstanding aggregate
        await self._flush_pending_extend()

        self._trace.log("enrich_start", app=app, title=title)
        enriched = self._enricher.enrich(
            app_name=app,
            window_title=title,
            url=url,
            wecom_group=wecom_group,
            ocr_enabled=self._config.ocr_enabled,
            ocr_engine=self._config.ocr_engine,
        )
        self._trace.log(
            "enrich_done",
            app=app,
            category=enriched["category"],
            has_screenshot=enriched["screenshot_local_path"] is not None,
        )

        screenshot_local = enriched["screenshot_local_path"]
        if screenshot_local is not None:
            # Let the backend relocate/upload the file and give us the
            # canonical path to bake into signals_json.
            stored_path = await self._backend.save_screenshot(
                self._machine_id, screenshot_local
            )
            if stored_path and stored_path != str(screenshot_local):
                import json
                signals = json.loads(enriched["signals_json"])
                signals["screenshot_path"] = stored_path
                enriched["signals_json"] = json.dumps(signals, ensure_ascii=False)

        payload = ActivityPayload(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            app_name=app,
            window_title=title,
            category=enriched["category"],
            confidence=enriched["confidence"],
            url=url,
            signals=enriched["signals_json"],
            duration_sec=interval_sec,
        )
        ids = await self._backend.save_activities(self._machine_id, [payload])
        row_id = ids[0] if ids else None

        self._last_app = app
        self._last_title = title
        self._last_row_id = row_id
        return row_id

    # ─── Batch push (retained for direct callers / tests) ─────────────

    async def push_batch(self, batch: list[ActivityPayload]) -> list[int]:
        """Push a batch of activities directly. Thin pass-through to the
        backend; primarily used by tests and git-commit collector.
        """
        if not self._backend or not self._machine_id:
            raise RuntimeError("Collector not registered yet")
        return await self._backend.save_activities(self._machine_id, batch)

    # ─── Heartbeat + override ──────────────────────────────────────────

    async def heartbeat(self) -> Optional[dict]:
        """Ping the server; apply config override + pause state."""
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
            self._config = self._config.model_copy(update={key: value})

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    # ─── Main loop ─────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main sample loop + periodic heartbeat."""
        self._running = True
        seconds_since_heartbeat = 0.0

        while self._running:
            if seconds_since_heartbeat >= self.HEARTBEAT_INTERVAL_SEC:
                try:
                    await self.heartbeat()
                except Exception as e:
                    print(f"[Collector] heartbeat error: {e}")
                seconds_since_heartbeat = 0.0

            if not self._paused:
                try:
                    await self.sample_once()
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
        # Flush any dangling aggregate before closing the backend.
        if self._backend and self._machine_id:
            try:
                await self._flush_pending_extend()
            except Exception:
                pass
        if self._backend:
            await self._backend.close()

    @property
    def machine_id(self) -> Optional[str]:
        return self._machine_id

    @property
    def adapter(self) -> PlatformAdapter:
        return self._adapter

    @property
    def backend(self) -> Optional[StorageBackend]:
        return self._backend

    @property
    def trace(self) -> MonitorTrace:
        """Ring-buffer trace used by WecomWatchdog for post-mortem dumps."""
        return self._trace
