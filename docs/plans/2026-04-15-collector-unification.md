# Collector 统一重构计划

**状态**：待执行
**预计工作量**：6-8 小时
**执行方式**：subagent 单次任务

---

## 1. 目标

**一份 collector 实现**。消除 `MonitorService`（server 内嵌，功能完整）与
`CollectorRuntime`（standalone，功能残缺）两份并行实现的现状。

完成后的架构：

```
CollectorRuntime  (auto_daily_log_collector/ 内唯一采集器)
├── 采样循环：tick 间隔 / heartbeat / idle 聚合 / 同窗口 duration 累加
├── PlatformAdapter      —— 平台差异只在这一层
├── ActivityEnricher     —— 分类 / 截图 / OCR / phash / hostile apps 反侦测
└── StorageBackend       —— 决定写哪里
    ├── LocalSQLiteBackend  —— server 内嵌模式：直接写本地 DB
    └── HTTPBackend         —— 独立进程模式：POST 到 server /api/ingest/*
```

**`config.yaml` 里的 `monitor.enabled=true` 语义保留**，但实现变为：server
启动时 `asyncio.create_task(CollectorRuntime(backend=LocalSQLiteBackend).run())`，
不再是单独的 MonitorService 类。

---

## 2. 分两层的硬性迁移

### 层 1：CollectorRuntime 能力补齐

当前 `CollectorRuntime.sample_once()` 只拿 `app/title/url`，缺：
- ❌ 分类 (category + confidence + hints)
- ❌ 截图 (screenshot_path)
- ❌ OCR (ocr_text)
- ❌ phash 去重（连续相似窗口不重复 OCR）
- ❌ idle 检测 + idle 连续聚合
- ❌ 同窗口连续聚合（UPDATE last row duration_sec）
- ❌ hostile apps 反侦测（企业微信 frontmost 时跳过 window title probe / 截屏）
- ❌ 企业微信群名提取 (wecom_group_name)
- ❌ Runtime 配置覆盖（settings 表里的 monitor_ocr_enabled 动态生效）

补齐方式：新增 `auto_daily_log_collector/enricher.py` 包装这些。

### 层 2：monitor/ 代码物理搬家

把 `auto_daily_log/monitor/` 目录**整个搬到** `auto_daily_log_collector/` 下。
完成后 `auto_daily_log/` 只剩 server 代码（web API、summarizer、scheduler、
jira_client、models 等）。

搬迁前后：

```
Before:
  auto_daily_log/
  ├── monitor/                 ← 这整个要搬
  │   ├── platforms/
  │   ├── service.py           ← 整体删除（MonitorService 不复存在）
  │   ├── classifier.py
  │   ├── screenshot.py
  │   ├── portal_screencast.py
  │   ├── ocr.py
  │   ├── phash.py
  │   ├── idle.py
  │   └── watchdog.py
  │
  auto_daily_log_collector/
  ├── platforms/               ← PlatformAdapter 层
  ├── runner.py
  ├── config.py
  ├── ...

After:
  auto_daily_log/              ← 纯 server 代码
  ├── web/
  ├── summarizer/
  ├── scheduler/
  ├── jira_client/
  ├── models/
  ├── search/
  ├── collector/               ← 已存在，server 的 git commit collector，留着
  ├── app.py
  └── config.py
  │
  auto_daily_log_collector/
  ├── platforms/               ← PlatformAdapter（高层包装）
  │   ├── base.py              (PlatformAdapter 接口)
  │   ├── factory.py
  │   ├── macos.py / linux.py / windows.py / gnome_wayland.py
  │   └── portal_screencast.py
  ├── monitor_internals/       ← 新目录，装搬过来的底层（PlatformAPI + 工具函数）
  │   ├── platforms/           (PlatformAPI 底层：macos.py / linux.py / windows.py / gnome_wayland.py / base.py / detect.py)
  │   ├── classifier.py
  │   ├── screenshot.py
  │   ├── ocr.py
  │   ├── phash.py
  │   ├── idle.py
  │   └── watchdog.py
  ├── enricher.py              ← 新增
  ├── runner.py
  ├── config.py
  └── __main__.py
```

**`monitor_internals/` 命名**：避开跟 `platforms/`（adapter 层）冲突，
表明"这里是 collector 内部实现细节"，外界不该直接用。

---

## 3. StorageBackend 接口扩展

当前 `StorageBackend` 只支持 `save_activities` / `save_commits` / `heartbeat`。
同窗口聚合和截图保存需要新增：

```python
# auto_daily_log/models/backends/base.py
class StorageBackend(ABC):
    # existing ...

    @abstractmethod
    async def extend_duration(self, machine_id: str, row_id: int, extra_sec: int) -> None:
        """Add extra_sec to the existing row's duration_sec.

        Used for same-window aggregation: when the current window matches
        the last sample, don't insert a new row, just extend."""

    @abstractmethod
    async def save_screenshot(self, local_path: Path) -> str:
        """Persist a screenshot and return the path to store in activity signals.

        - LocalSQLiteBackend: return str(local_path) as-is (file already on disk).
        - HTTPBackend: POST multipart to /api/ingest/screenshot, return
          server-side path from response."""
```

**HTTP backend 的 extend_duration**：需要 server 新增 endpoint
`POST /api/ingest/extend-duration`（body: `{row_id, extra_sec}`，带 machine_id
auth），在 `auto_daily_log/web/api/ingest.py` 加。

**本地同窗口聚合优化**：CollectorRuntime 在本地缓存 `last_row_id` + `last_app`
+ `last_title`，同窗口时**不调 backend**（既不 save 也不 extend），直到窗口变
才调 extend_duration 把累计的 duration 一次推过去。好处：standalone 时少网
络调用。

> **注意**：HTTP 层的 extend_duration 最好有个窗口限制（比如 5 分钟内才
> extend，超过强制新起一条），防止网络抖动导致 `row_id` 错位后一直改错行。
> 这个细节在实现时决定。

---

## 4. 执行步骤（严格顺序）

### Phase 1 — 物理搬家（机械操作，不改逻辑）

**P1.1** `git mv auto_daily_log/monitor/platforms/ auto_daily_log_collector/monitor_internals/platforms/`

**P1.2** 把 `auto_daily_log/monitor/` 里以下文件搬到 `auto_daily_log_collector/monitor_internals/`：
- `classifier.py`、`screenshot.py`、`portal_screencast.py`、`ocr.py`、`phash.py`、`idle.py`、`watchdog.py`

**P1.3** 搬完后 `auto_daily_log/monitor/` 应该只剩 `__init__.py`（或整个目录删）+ `service.py`（下一步删）

**P1.4** 删 `auto_daily_log/monitor/service.py`（MonitorService 整体废弃）

**P1.5** 删 `auto_daily_log/monitor/` 空目录

**P1.6** 全局替换 import：
- `from auto_daily_log.monitor.classifier` → `from auto_daily_log_collector.monitor_internals.classifier`
- `from auto_daily_log.monitor.screenshot` → `from auto_daily_log_collector.monitor_internals.screenshot`
- `from auto_daily_log.monitor.ocr` → `from auto_daily_log_collector.monitor_internals.ocr`
- `from auto_daily_log.monitor.phash` → `from auto_daily_log_collector.monitor_internals.phash`
- `from auto_daily_log.monitor.idle` → `from auto_daily_log_collector.monitor_internals.idle`
- `from auto_daily_log.monitor.watchdog` → `from auto_daily_log_collector.monitor_internals.watchdog`
- `from auto_daily_log.monitor.platforms` → `from auto_daily_log_collector.monitor_internals.platforms`
- `from auto_daily_log.monitor.portal_screencast` → `from auto_daily_log_collector.monitor_internals.portal_screencast`
- `from .platforms.detect` / `from .idle` 等**相对导入**（搬家后在同一包内）：无需改

**P1.7** 特别注意：`auto_daily_log/app.py` 还 import 了 `MonitorService` 和 `WecomWatchdog`：
- `MonitorService` 的引用整体删（phase 5 会换成 CollectorRuntime）
- `WecomWatchdog` 保留（它现在位于 watchdog.py，被搬家后 import 路径改一下就行）

**P1.8** 确认测试还能 import 到位，跑一遍 `.venv/bin/python -m pytest tests/ -q`。
**测试应该大批量失败**（MonitorService 没了、monitor.* 路径失效），这是**预期**。
先记录失败数，后续 phase 修。

**验证**：
```bash
.venv/bin/python -c "from auto_daily_log_collector.monitor_internals.classifier import classify_activity; print('OK')"
.venv/bin/python -c "from auto_daily_log_collector.monitor_internals.platforms.detect import get_current_platform; print('OK')"
```

### Phase 2 — 新增 ActivityEnricher

**P2.1** 新文件 `auto_daily_log_collector/enricher.py`：

```python
"""Activity enricher — adds category, screenshot, OCR, phash, hostile-app
handling, and wecom_group_name to raw activity samples.

Used by CollectorRuntime. Keeps enrichment logic separate from sampling
loop + backend choice, so the same enricher runs identically whether
the collector writes to local SQLite or pushes over HTTP.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .monitor_internals.classifier import classify_activity
from .monitor_internals.screenshot import capture_screenshot
from .monitor_internals.ocr import ocr_image
from .monitor_internals.phash import compute_phash, is_similar


class ActivityEnricher:
    def __init__(
        self,
        screenshot_dir: Path,
        hostile_apps_applescript: list[str],
        hostile_apps_screenshot: list[str],
        phash_enabled: bool = True,
        phash_threshold: int = 20,
    ):
        self._screenshot_dir = screenshot_dir
        self._hostile_as = {s.lower() for s in hostile_apps_applescript}
        self._hostile_ss = {s.lower() for s in hostile_apps_screenshot}
        self._phash_enabled = phash_enabled
        self._phash_threshold = phash_threshold

        # State for similarity-based OCR reuse
        self._last_phash = None
        self._last_ocr_text: Optional[str] = None
        self._last_app: Optional[str] = None
        self._last_title: Optional[str] = None

    def is_hostile_applescript(self, app_name: Optional[str]) -> bool:
        """Whether this app breaks when probed via AppleScript/UI APIs
        (e.g. WeChat Work self-exits). Caller should skip window title
        and browser tab probing."""
        return (app_name or "").lower() in self._hostile_as

    def enrich(
        self,
        app_name: str,
        window_title: Optional[str],
        url: Optional[str],
        wecom_group: Optional[str],
        ocr_enabled: bool,
        ocr_engine: str,
    ) -> dict:
        """Classify the activity and optionally take + OCR a screenshot.

        Returns a dict with keys: category, confidence, signals (json str).
        """
        screenshot_path: Optional[Path] = None
        ocr_text: Optional[str] = None

        same_window = (
            app_name == self._last_app
            and window_title == self._last_title
            and self._last_app is not None
        )

        app_lower = (app_name or "").lower()
        _debug_no_skip = os.environ.get("PDL_DEBUG_NO_SKIP") == "1"
        skip_screenshot = (not _debug_no_skip) and (app_lower in self._hostile_ss)

        if ocr_enabled and not same_window and not skip_screenshot:
            today_dir = self._screenshot_dir / datetime.now().strftime("%Y-%m-%d")
            screenshot_path = capture_screenshot(today_dir)
            if screenshot_path:
                if self._phash_enabled:
                    current_hash = compute_phash(screenshot_path)
                    if is_similar(current_hash, self._last_phash, self._phash_threshold):
                        ocr_text = self._last_ocr_text
                        try:
                            screenshot_path.unlink()
                        except OSError:
                            pass
                        screenshot_path = None
                    else:
                        ocr_text = ocr_image(screenshot_path, ocr_engine)
                        self._last_phash = current_hash
                        self._last_ocr_text = ocr_text
                else:
                    ocr_text = ocr_image(screenshot_path, ocr_engine)
        elif same_window:
            ocr_text = self._last_ocr_text

        self._last_app = app_name
        self._last_title = window_title

        category, confidence, hints = classify_activity(app_name, window_title, url)

        signals = {
            "browser_url": url,
            "wecom_group_name": wecom_group,
            "screenshot_path": str(screenshot_path) if screenshot_path else None,
            "ocr_text": ocr_text,
            "hints": hints,
        }

        return {
            "category": category,
            "confidence": confidence,
            "signals_json": json.dumps(signals, ensure_ascii=False),
            "screenshot_local_path": screenshot_path,  # for backend.save_screenshot later
        }
```

**P2.2** 单测 `tests/test_enricher.py`：
- 纯分类（app=IntelliJ IDEA → category=coding）
- hostile_apps_applescript 过滤
- phash 相似时复用上次 OCR
- 不同窗口强制重新截屏

**验证**：新单测应该全绿。

### Phase 3 — 扩展 StorageBackend

**P3.1** 改 `auto_daily_log/models/backends/base.py`：加 `extend_duration` + `save_screenshot` 抽象方法。

**P3.2** 改 `LocalSQLiteBackend`（`local.py`）：
```python
async def extend_duration(self, machine_id, row_id, extra_sec):
    await self._db.execute(
        "UPDATE activities SET duration_sec = duration_sec + ? WHERE id = ? AND machine_id = ?",
        (extra_sec, row_id, machine_id),
    )

async def save_screenshot(self, local_path):
    # Local file, no relocation needed.
    return str(local_path)
```

**P3.3** 改 `HTTPBackend`（`http.py`）：
```python
async def extend_duration(self, machine_id, row_id, extra_sec):
    await self._client.post(
        f"{self._server_url}/api/ingest/extend-duration",
        headers=self._auth_headers(machine_id),
        json={"row_id": row_id, "extra_sec": extra_sec},
    )

async def save_screenshot(self, local_path):
    with open(local_path, "rb") as f:
        files = {"file": (local_path.name, f, "image/png")}
        resp = await self._client.post(
            f"{self._server_url}/api/ingest/screenshot",
            headers=self._auth_headers(self._machine_id),  # from last set
            files=files,
        )
    return resp.json()["path"]
```

**P3.4** 新 server endpoint `POST /api/ingest/extend-duration`（`auto_daily_log/web/api/ingest.py`）：
```python
class ExtendDurationRequest(BaseModel):
    row_id: int
    extra_sec: int = Field(..., ge=0, le=3600)

@router.post("/ingest/extend-duration")
async def extend_duration(body: ExtendDurationRequest, request: Request, collector=Depends(_authenticate_collector)):
    db = request.app.state.db
    await db.execute(
        "UPDATE activities SET duration_sec = duration_sec + ? WHERE id = ? AND machine_id = ?",
        (body.extra_sec, body.row_id, collector["machine_id"]),
    )
    return {"ok": True}
```

### Phase 4 — 重写 CollectorRuntime

**P4.1** 改 `auto_daily_log_collector/runner.py`：
- 构造函数接受 `backend: StorageBackend`（现在硬编码 HTTPBackend，去掉）、`enricher: ActivityEnricher`
- 增加 `machine_id` 可传入（server 内嵌模式传 `"local"`，跳过 HTTP 注册）
- 新方法：`register_as_builtin(machine_id="local", name="Built-in (this machine)")` 给 server 用（直接写 `collectors` 表，跳过网络）—— 或者让 CollectorRuntime 接受 `skip_http_register: bool` 构造参数
- 重写 `sample_once`：
  1. `idle_sec = adapter.get_idle_seconds()`，判断是否 idle
  2. Idle：若上条也是 idle → `backend.extend_duration(last_idle_row_id, interval)`；否则 insert new idle row
  3. Not idle：`app = adapter.get_frontmost_app()`，None 或 hostile_applescript 时跳过 title/tab/wecom probe
  4. 同窗口？local 缓存比对 → `backend.extend_duration(last_row_id, interval)`
  5. 新窗口？enricher.enrich(...) → 如果有 screenshot_local_path，调 `backend.save_screenshot(...)` 拿到最终路径覆盖进 signals → `backend.save_activities([payload])`，记新 row_id 到缓存

**P4.2** ActivityPayload schema 可能需要扩展确认 `signals: str`（JSON）字段已存在 —— 检查 `shared/schemas.py`。

**P4.3** 单测 `tests/test_collector_runtime_unified.py`：
- 用 Mock adapter + Mock backend 验证 idle 聚合、同窗口聚合、新窗口插入、hostile skip
- 至少 5 个 case

### Phase 5 — Server 内嵌改用 CollectorRuntime

**P5.1** 改 `auto_daily_log/app.py`：
```python
# 删这块老逻辑
# self.monitor = MonitorService(...)
# monitor_task = asyncio.create_task(self.monitor.start())

# 换成
if self.config.monitor.enabled:
    from auto_daily_log_collector.runner import CollectorRuntime
    from auto_daily_log_collector.enricher import ActivityEnricher
    from auto_daily_log_collector.platforms.factory import create_adapter
    from auto_daily_log.models.backends import LocalSQLiteBackend

    adapter = create_adapter()
    screenshot_dir = self.config.system.resolved_data_dir / "screenshots"
    enricher = ActivityEnricher(
        screenshot_dir=screenshot_dir,
        hostile_apps_applescript=self.config.monitor.hostile_apps_applescript,
        hostile_apps_screenshot=self.config.monitor.hostile_apps_screenshot,
        phash_enabled=self.config.monitor.phash_enabled,
        phash_threshold=self.config.monitor.phash_threshold,
    )
    backend = LocalSQLiteBackend(self.db)
    collector = CollectorRuntime(
        config=self.config.monitor,  # 适配一下 or 造一个小 adapter
        backend=backend,
        adapter=adapter,
        enricher=enricher,
        machine_id="local",
        skip_http_register=True,
    )
    monitor_task = asyncio.create_task(collector.run())
    self._builtin_collector = collector

# watchdog 逻辑也保留
```

**P5.2** watchdog 仍然需要拿到 collector 的 trace 信息：
- 当前 `WecomWatchdog` 取的是 `MonitorService.trace`
- `CollectorRuntime` 要有相应的 `trace` 属性（MonitorTrace 实例）或者 watchdog 改成不依赖 trace（更简单）
- 具体看 watchdog.py 现状

**P5.3** 确保 `config.yaml` 的所有 `monitor.*` 字段（`ocr_enabled` / `interval_sec` / `hostile_apps_*` / `phash_*` / `privacy.*`）都被正确传给 CollectorRuntime。

### Phase 6 — 测试与清理

**P6.1** 迁移 `tests/test_monitor_service.py`：
- 删除（MonitorService 没了）
- 或者改名成 `test_builtin_collector.py` 测新路径（用 CollectorRuntime + LocalBackend）

**P6.2** 修所有因 import 路径变动炸掉的测试：
- `tests/test_classifier.py`、`tests/test_idle.py`、`tests/test_ocr.py`、`tests/test_phash.py`、`tests/test_monitor_platform.py`、`tests/test_screenshot.py`、`tests/test_wayland_platform.py`、`tests/test_collector_linux_adapter.py` 等

**P6.3** 跑全量测试：`.venv/bin/python -m pytest tests/ -q`
目标：**所有原本绿的测试依然绿，新增的 enricher + collector_runtime 测试也绿**。

### Phase 7 — 端到端验证

**P7.1** 清理之前的双 collector 数据：
```bash
sqlite3 ~/.auto_daily_log/data.db "DELETE FROM collectors WHERE machine_id NOT IN ('local');"
sqlite3 ~/.auto_daily_log/data.db "DELETE FROM activities WHERE machine_id NOT IN ('local');"
```

**P7.2** 启动 server（`config.yaml` 里 `monitor.enabled: true`）：
```bash
./pdl server restart
```

**P7.3** 在 web UI `Activities` 页验证：
- `local` 机器有新采集
- Category 列有值（coding/browsing/meeting/other 等）
- Screenshot 列能看到图标（可点击预览）
- 5 分钟后回来看：连续看同一个窗口应该**只新增一行**并且 duration 累加

**P7.4** 另起 standalone collector 验证跨机模式：
```bash
./pdl collector start
```
- 注册成功（新 machine_id 出现在 `collectors` 表）
- 采集数据字段齐全（category + screenshot + ocr_text）

**P7.5** 全量回归：跑一次 `./pdl server logs 200` 确认没有报错刷屏。

### Phase 8 — Release 验证

**P8.1** 跑 `bash scripts/release.sh`，确认 wheel 生成无误。

**P8.2** 解压到 /tmp 全新装一次 collector，验证独立 collector 功能对等。

---

## 5. 风险与回退

### 已知风险

1. **Circular import**：monitor_internals 依赖什么、被什么依赖。搬家后可能出现循环导入。
   **缓解**：搬家顺序按依赖树 bottom-up（platforms → idle/phash → screenshot → ocr → classifier → watchdog）。

2. **WecomWatchdog 的 trace 观察机制**：如果 CollectorRuntime 不方便暴露 trace，考虑将 watchdog 改为独立定时器，只轮询进程存在性即可。

3. **Settings runtime override 消失**：MonitorService 从 DB settings 表读 ocr_enabled 等。CollectorRuntime 在内嵌模式同样能直接读 DB（有 db handle），standalone 模式走 heartbeat override（已存在）。**不能遗漏这个迁移**。

4. **同窗口聚合 + HTTP 的一致性问题**：网络失败时 extend_duration 可能丢；但因为采样每 30s 一次，丢一两次 extend 最多少算 30s 工时，**可接受**。不做重试队列。

5. **测试大量失败** in Phase 1：这是预期。别被 pytest 的红色吓到，按 phase 推进。

### 回退策略

如果 phase 5/6 后发现大坑：
```bash
git checkout HEAD~N   # N 取决于已经推了多少
```

每个 phase 结束打一个 commit，好定位回退点。

---

## 6. 执行 checklist（subagent 打勾）

- [ ] Phase 1.1-1.8 搬家完成，全量测试记录失败 count
- [ ] Phase 2 enricher 新测试全绿
- [ ] Phase 3 backend 扩展完成，旧测试不回归
- [ ] Phase 4 CollectorRuntime 重写 + 新测试全绿
- [ ] Phase 5 server 内嵌改用 CollectorRuntime，启动无报错
- [ ] Phase 6 全量测试恢复全绿
- [ ] Phase 7 端到端验证：两种模式数据字段齐全
- [ ] Phase 8 release tarball 依然能装能跑
- [ ] 最后：git log 看 6-8 个有意义的 commit（按 phase）
- [ ] 向主对话汇报：改动 summary + 测试结果 + 异常/决策

---

## 7. 约束（必须遵守）

1. **不删用户数据**：`~/.auto_daily_log/` 下的 data.db 和 screenshots 不能动
2. **不改 AGENTS.md / CLAUDE.md 的核心原则**：原汁原味、两层平台、Jira emoji scrub 这些规则不变
3. **每个 phase 结束打 commit**，commit message 前缀用 `refactor: unify collector ...`
4. **测试必须精确值断言**（AGENTS.md §测试规范）
5. **不改变已有公开行为**：
   - `monitor.enabled=true/false` 的含义不变
   - 现有 `~/.auto_daily_log/data.db` 读写兼容
   - `/api/collectors` / `/api/ingest/*` 现有 endpoint 签名不变（只新增 `/api/ingest/extend-duration`）
6. **Server 启动自动注册 `local` collector** 行为保留
7. **不升级依赖**：保持当前 pyproject.toml 不动
8. **遇到模糊决策时**：优先选"跟现状行为一致"的路径，不自创新行为

---

## 8. 完成后写一份 summary

在 `docs/plans/2026-04-15-collector-unification-SUMMARY.md` 里写：
- 最终文件变动列表
- phase 完成情况（有没有跳过或折中）
- 遗留 TODO（如果有）
- 测试数量变化（从 N 变 M）
- 任何需要后续跟进的事项
