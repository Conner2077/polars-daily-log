"""
WeCom watchdog — detect 企业微信/WeChat unexpected exit and log context.

Polls the process list every second; when a monitored app disappears,
writes the last N monitor actions (ring buffer) to a dump file so we
can diagnose what triggered the self-exit.
"""
import asyncio
import logging
import subprocess
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

_log = logging.getLogger("auto_daily_log.watchdog")


_WATCHED_APPS = (
    "WeChat",
    "WeCom",
    "企业微信",
    "wxwork",
    "WeChatWork",
    "WeChatAppEx",
)


class MonitorTrace:
    """Ring buffer of monitor activity entries for post-mortem analysis."""

    def __init__(self, capacity: int = 200):
        self._buf: deque = deque(maxlen=capacity)

    def log(self, action: str, **kv) -> None:
        entry = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "action": action,
            **kv,
        }
        self._buf.append(entry)

    def snapshot(self) -> list[dict]:
        return list(self._buf)


def _list_running_apps() -> dict[str, int]:
    """Return map of {lowercase_process_name: pid} for top-level processes.

    Uses `ps -axco pid,command` to match short command names (strips paths
    and args). When multiple processes share the same command name (e.g.
    helpers), returns the lowest PID which is typically the parent.
    """
    try:
        result = subprocess.run(
            ["ps", "-axco", "pid,command"],
            capture_output=True, text=True, timeout=3,
        )
        out: dict[str, int] = {}
        for line in result.stdout.splitlines()[1:]:  # skip header
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            name = parts[1].strip().lower()
            if name and name not in out:
                out[name] = pid
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return {}


class WecomWatchdog:
    """Background task that detects WeCom exit and dumps trace."""

    def __init__(
        self,
        trace: MonitorTrace,
        dump_dir: Path,
        watched: Iterable[str] = _WATCHED_APPS,
        interval_sec: float = 1.0,
    ):
        self._trace = trace
        self._dump_dir = dump_dir
        self._watched_lower = {w.lower() for w in watched}
        self._interval = interval_sec
        self._running = False
        self._last_pids: dict[str, int] = {}  # name -> PID

    async def start(self) -> None:
        self._running = True
        self._dump_dir.mkdir(parents=True, exist_ok=True)

        # File handler for watchdog events — separate from app log
        watchdog_log = self._dump_dir / "watchdog.log"
        fh = logging.FileHandler(watchdog_log, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(fh)
        _log.setLevel(logging.INFO)
        _log.propagate = True  # also show in console

        self._last_pids = self._currently_running()
        _log.info(f"Watchdog started. Watched={sorted(self._watched_lower)} initial={self._last_pids}")
        print(f"[Watchdog] Started. Watching={sorted(self._watched_lower)} Initial PIDs={self._last_pids}", flush=True)

        tick = 0
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                tick += 1
                current = self._currently_running()

                # Heartbeat every 30s
                if tick % 30 == 0:
                    _log.info(f"Heartbeat: tick={tick} pids={current}")

                # Detect disappearance (was seen, now gone)
                for app, old_pid in self._last_pids.items():
                    if app not in current:
                        _log.warning(f"APP EXITED: {app} (was PID {old_pid})")
                        self._dump(app, reason="exited", old_pid=old_pid, new_pid=None)

                # Detect appearance (new) or PID change (restart)
                for app, new_pid in current.items():
                    old_pid = self._last_pids.get(app)
                    if old_pid is None:
                        _log.info(f"App started: {app} (PID {new_pid})")
                        print(f"[Watchdog] {app} started PID={new_pid}", flush=True)
                    elif old_pid != new_pid:
                        _log.warning(f"APP RESTARTED: {app} (PID {old_pid} → {new_pid})")
                        self._dump(app, reason="restarted", old_pid=old_pid, new_pid=new_pid)

                self._last_pids = current
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log.exception(f"Watchdog loop error: {e}")

    def stop(self) -> None:
        self._running = False

    def _currently_running(self) -> dict[str, int]:
        procs = _list_running_apps()
        return {w: procs[w] for w in self._watched_lower if w in procs}

    def _dump(self, app: str, reason: str = "exited", old_pid: Optional[int] = None, new_pid: Optional[int] = None) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_app = app.replace(" ", "_").replace("/", "_")
        dump_file = self._dump_dir / f"{reason}_{safe_app}_{ts}.log"
        snapshot = self._trace.snapshot()

        lines = [
            f"=== {app} {reason.upper()} at {datetime.now().isoformat()} ===",
            f"old_pid={old_pid} new_pid={new_pid}",
            f"Last {len(snapshot)} monitor actions (oldest first):",
            "",
        ]
        for entry in snapshot:
            kv = " ".join(f"{k}={v!r}" for k, v in entry.items() if k not in ("ts", "action"))
            lines.append(f"{entry['ts']}  {entry['action']:<24} {kv}")

        dump_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        msg = f"[Watchdog] {app} {reason}! Dump saved: {dump_file}"
        print(msg, flush=True)
        _log.warning(msg)
