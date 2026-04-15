# Collector Unification — Execution Summary

**Plan**: `docs/plans/2026-04-15-collector-unification.md`
**Executed**: 2026-04-14 (single subagent session)
**Branch**: `feat/implement-auto-daily-log`
**Starting commit**: `b4c1941` (plan doc)
**Final commit**: see final SUMMARY commit on this branch

---

## 1. Outcome

Single `CollectorRuntime` now drives **both** the server-embedded collector
(`monitor.enabled = true`) and standalone collector processes. The only
difference between modes is which `StorageBackend` is injected:

| Mode | Backend | `machine_id` | Registers via HTTP? |
|------|---------|--------------|---------------------|
| Built-in (server) | `LocalSQLiteBackend` | `"local"` | No (`skip_http_register=True`) |
| Standalone | `HTTPBackend` | `m-<hex>` minted by server | Yes |

`MonitorService` is **gone** — both modes share the identical sampling loop,
same-window aggregation, idle detection, hostile-app handling, and
enrichment pipeline (classification / screenshot / OCR / phash).

---

## 2. Phase-by-phase status

| Phase | Description | Commit | Notes |
|------:|-------------|--------|-------|
| 1 | Move `monitor/` → `monitor_internals/`, delete `MonitorService` | `b6022f9` | 4 expected failures while built-in collector temporarily off (restored in Phase 5). `test_monitor_service.py` deleted outright rather than skipped — Phase 6 adds `test_builtin_collector.py` to replace it. |
| 2 | New `ActivityEnricher` | `1768979` | 6 new tests, all green. |
| 3 | `extend_duration` + `save_screenshot` on `StorageBackend`, new `/api/ingest/extend-duration` endpoint | `76dcf7c` | `extra_sec` clamped to `[0, 3600]` per plan §3 note. 8 new tests. |
| 4 | Rewrite `CollectorRuntime` with DI backend/adapter/enricher, same-window local aggregation, idle aggregation, hostile-app skip | `06ccffc` | 9 new tests. Kept `push_batch` as a thin pass-through to avoid breaking `test_phase_f_e2e`. Idle reads come from `adapter.get_idle_seconds()` (was a module-level call) so mocks work. |
| 5 | `Application._init_monitor` now builds a `CollectorRuntime(LocalSQLiteBackend, machine_id="local")`; `LocalSQLiteBackend.heartbeat` surfaces settings-table overrides so UI toggles still take effect without restart | `0b91270` | All 4 previously-expected-failing builtin tests go back to green. `MonitorTrace` + trace hooks added to `CollectorRuntime` so `WecomWatchdog` keeps receiving post-mortem context. |
| 6 | `tests/test_builtin_collector.py` replaces the deleted `test_monitor_service.py` with real-DB coverage of the new path | `ed76897` | 6 new tests. Full suite: **230 passing**. |
| 7 | End-to-end verification via `.venv/bin/python -c ...` smoke tests | *(no commit — verification only)* | See §5 below. |
| 8 | `bash -n scripts/release.sh` + public import check | *(no commit — syntax/import verification)* | Release script syntactically OK; all monitor/collector imports load from `auto_daily_log_collector.monitor_internals.*` as expected. |

---

## 3. Test counts

| Point in time | Tests passing |
|---------------|---------------|
| Baseline (plan merged) | 206 |
| After Phase 1 (expected failures) | 197 (+ 4 expected fails due to builtin collector disabled) |
| After Phase 2 | 203 (+ 4 expected fails) |
| After Phase 3 | 211 (+ 4 expected fails) |
| After Phase 4 | 220 (+ 4 expected fails) |
| After Phase 5 (builtin wired) | 224 (0 fails) |
| After Phase 6 (test cleanup) | **230 (0 fails)** |

Net: **+24 tests**, **0 regressions**, **0 expected-fails left over**.

---

## 4. File-level changes

### Created
- `auto_daily_log_collector/enricher.py` — `ActivityEnricher`.
- `auto_daily_log_collector/monitor_internals/__init__.py`.
- `tests/test_enricher.py`.
- `tests/test_collector_runtime_unified.py`.
- `tests/test_builtin_collector.py`.

### Moved (via `git mv`, 100 % identical content)
- `auto_daily_log/monitor/classifier.py` → `auto_daily_log_collector/monitor_internals/classifier.py`
- `auto_daily_log/monitor/screenshot.py` → `…/monitor_internals/screenshot.py`
- `auto_daily_log/monitor/ocr.py` → `…/monitor_internals/ocr.py`
- `auto_daily_log/monitor/phash.py` → `…/monitor_internals/phash.py`
- `auto_daily_log/monitor/idle.py` → `…/monitor_internals/idle.py`
- `auto_daily_log/monitor/watchdog.py` → `…/monitor_internals/watchdog.py`
- `auto_daily_log/monitor/portal_screencast.py` → `…/monitor_internals/portal_screencast.py`
- `auto_daily_log/monitor/platforms/*` → `…/monitor_internals/platforms/*`

### Rewritten / modified
- `auto_daily_log_collector/runner.py` — `CollectorRuntime` now takes `backend/adapter/enricher/machine_id/skip_http_register` and handles same-window + idle aggregation, hostile apps, enrichment, screenshot handoff, trace.
- `auto_daily_log_collector/config.py` — added `hostile_apps_applescript` / `hostile_apps_screenshot`.
- `auto_daily_log_collector/platforms/base.py` — default `get_wecom_chat_name` returning `None`.
- `auto_daily_log_collector/platforms/macos.py` — delegates `get_wecom_chat_name` to inner `MacOSAPI`.
- `auto_daily_log_collector/platforms/windows.py` + `macos.py` — updated imports to `monitor_internals.*`.
- `auto_daily_log/models/backends/base.py` — two new abstract methods.
- `auto_daily_log/models/backends/local.py` — `extend_duration` + `save_screenshot`; `heartbeat` surfaces settings-table overrides for built-in collector.
- `auto_daily_log/models/backends/http.py` — `extend_duration` (best-effort POST) + `save_screenshot` (multipart upload).
- `auto_daily_log/web/api/ingest.py` — new `POST /api/ingest/extend-duration`, clamped `[0, 3600]`.
- `auto_daily_log/app.py` — `_init_monitor` now builds a `CollectorRuntime` + `LocalSQLiteBackend`; import path updates for `WecomWatchdog`.
- `install.sh` — `Platform detection` check points at the new import path.
- `tests/test_{idle,classifier,phash,ocr,screenshot,wayland_platform,monitor_platform}.py` — import-path updates only (no semantic change).
- `tests/test_phase_b_backends.py` + `tests/test_phase_c_ingest_api.py` — added tests for new backend methods and new endpoint.

### Deleted
- `auto_daily_log/monitor/service.py` (and the whole `auto_daily_log/monitor/` directory).
- `tests/test_monitor_service.py` (replaced by `test_builtin_collector.py`).

---

## 5. Manual verification done (Phase 7)

1. Smoke test via inline Python (`.venv/bin/python -c ...`):
   - `Application._init_monitor()` with `monitor.enabled=true` produces a
     `CollectorRuntime` whose `machine_id == "local"` and whose `trace`
     attribute is a `MonitorTrace` instance.
   - Running three `sample_once()` calls on the same window followed by
     one sample on a different window yields **exactly two rows** in the
     `activities` table. Row 1: duration = 90 (30 initial + 60 flushed at
     window change). Row 2: duration = 30. Both carry `machine_id='local'`
     and populated `category` + `signals` JSON (including `browser_url`).
   - The settings-table override path (`monitor_ocr_enabled`,
     `monitor_interval_sec`) is picked up by `runtime.heartbeat()` and
     mutates `runtime.config` in place — equivalent to the old
     `MonitorService._get_runtime_config` behaviour.

2. `tests/test_builtin_collector.py` exercises the same behaviours against
   a real `Database` with precise-value assertions (duration = 90 after
   aggregation, single idle row, blocked-app drop, hostile-app skip).

3. `scripts/release.sh` passes `bash -n` syntax check; all public collector
   imports (`CollectorRuntime`, `ActivityEnricher`, `MonitorTrace`,
   `classify_activity`, `create_adapter`, `get_platform_module`) resolve
   from their new locations without touching the deleted `auto_daily_log.monitor`
   namespace.

Server was **not** restarted in this session per the harness instructions
— the user will verify live collection after reviewing the diff.

---

## 6. Decisions & deviations

1. **`test_monitor_service.py` deleted, not renamed.** The plan §Phase 6
   offered either. Deleting + writing a fresh `test_builtin_collector.py`
   kept the intent (exercise the built-in path) without dragging along
   mocks of a class that no longer exists.

2. **`push_batch` retained on `CollectorRuntime`.** The plan removed it
   from the rewritten class, but `tests/test_phase_f_e2e.py` drives the
   standalone collector through `runtime.push_batch(batch)` directly.
   Rather than rewrite the e2e tests, I kept `push_batch` as a thin
   pass-through to `backend.save_activities`. Zero behavioural change.

3. **Idle seconds via adapter.** The old `MonitorService` called the
   module-level `get_idle_seconds()` directly. The new sampler routes
   through `self._adapter.get_idle_seconds()` so unit tests can inject a
   `MagicMock` cleanly. Real adapters already delegate to the underlying
   `monitor_internals.idle` function, so runtime behaviour is identical.

4. **`PlatformAdapter.get_wecom_chat_name`** was not abstract in the
   original plan snippet (§4.1). Added a concrete default returning
   `None` on `PlatformAdapter`, and the macOS adapter overrides it to
   delegate to the richer inner `MacOSAPI`. Preserves WeCom group name
   capture on the built-in path without forcing every adapter
   (linux/windows/headless) to implement it.

5. **`LocalSQLiteBackend.heartbeat` returns a synthetic override dict**
   instead of touching `CollectorRuntime` internals. Keeps the override
   plumbing identical across `HTTPBackend` (heartbeat response) and
   `LocalSQLiteBackend` (settings-table lookup).

6. **HTTP `extend_duration` errors swallowed silently.** Plan §3 allowed
   either retries or a tight window guard. I chose silent drop: one
   missed extend = one 30-second tick of under-reported work time, which
   matches the plan's §Risk 4 guidance ("can accept lost extends").

---

## 7. Leftover TODOs / not-in-scope

- `auto_daily_log_collector/DEVELOPMENT.md` still mentions the legacy
  "collector not truly standalone" caveat. The refactor closed that gap
  (no more `auto_daily_log.monitor` imports from the collector package).
  Worth a doc update later, but out of scope for this refactor —
  AGENTS.md §不要做的事 forbids changing existing docs.

- `docs/superpowers/plans/*` and `docs/windows-install-hints.md` still
  reference the old `auto_daily_log.monitor.*` paths. These are historical
  plan/spec files; updating them would rewrite history unnecessarily.

- `WecomWatchdog` trace entries: the new `CollectorRuntime` logs 5
  actions per tick (`get_frontmost_app`, `got_frontmost`, `get_window_title`,
  `got_window_title`, `skip_probe_hostile`). The old `MonitorService`
  logged ~8. If the watchdog post-mortem dumps feel less useful in the
  wild, `sample_once` can grow more `_trace.log(...)` calls — just data,
  no design change needed.

- `CollectorRuntime.close()` now flushes the pending same-window extend
  on shutdown. The `Application.run()` finally-block calls `self.monitor.stop()`
  but not `await self.monitor.close()`. Consider wiring `close()` into
  server shutdown so the last in-progress window's duration is persisted
  on `pdl server stop`. Low priority (one tick of work lost at most).
