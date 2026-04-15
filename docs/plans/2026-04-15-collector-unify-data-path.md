# Collector 数据路径统一（方案 A：内嵌 collector 走 loopback HTTP）

**状态**：待执行
**预计工作量**：2-3 小时
**执行方式**：subagent 单次任务

---

## 1. 问题定义

Phase 5 重构解决了**代码一份**（只有 `CollectorRuntime`），但**数据路径**依然两份：

```
外部 collector  ─HTTP─► /api/ingest/activities ─► DB
内嵌 collector         ────────────────────────► DB （经 LocalSQLiteBackend 直接写，绕过 HTTP 层）
```

后果：`/api/ingest/*` 的所有 middleware / hook / validation / future
rate-limit / audit 等都**只对外部 collector 生效**。未来任何 ingest 路径的
扩展都要改两处，否则出现"本机能行、远程不行"或反过来的 bug。

## 2. 目标

**一条数据路径**。内嵌 collector 也走 `http://127.0.0.1:{port}/api/ingest/*`，
跟外部 collector 完全等价。

```
外部 collector    ─HTTP─┐
                        ├─► /api/ingest/activities ─► DB
内嵌 collector    ─HTTP─┘        （唯一入口）
（loopback 到本机 uvicorn）
```

删除 `LocalSQLiteBackend`，只留 `HTTPBackend`。

## 3. 核心设计

### 3.1 内嵌 collector 启动流程

```python
# auto_daily_log/app.py 的 run() 方法

async def run(self):
    await self._init_db()
    await self._register_builtin_collector()   # 写 collectors 表 + 分配 token
    self._init_scheduler()

    app = create_app(self.db)
    ...

    # 先起 uvicorn
    server = uvicorn.Server(uvicorn.Config(app, ...))
    server_task = asyncio.create_task(server.serve())

    # 等 uvicorn 就绪（监听端口可连）后再起内嵌 collector
    if self.config.monitor.enabled:
        await self._wait_for_server_ready(self.config.server.port, timeout=10)
        self._builtin_collector = self._make_builtin_collector()
        monitor_task = asyncio.create_task(self._builtin_collector.run())
        # watchdog 照旧

    await server_task
```

`_wait_for_server_ready()`：每 200ms 尝试 `asyncio.open_connection('127.0.0.1', port)`，
成功即返回；超时报错（应不会发生，uvicorn 通常 <1s 就绪）。

### 3.2 Token 自分发

Server 启动时，`_register_builtin_collector()` 同步做两件事：

```python
async def _register_builtin_collector(self):
    import secrets, hashlib

    # 1. UPSERT collectors 表（现有逻辑保留，machine_id='local'）
    # ... 现有 UPSERT ...

    # 2. 生成 / 读取 built-in token（幂等）
    row = await self.db.fetch_one(
        "SELECT value FROM settings WHERE key='builtin_collector_token'"
    )
    if row and row["value"]:
        token = row["value"]
    else:
        token = "tk-builtin-" + secrets.token_urlsafe(24)
        await self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("builtin_collector_token", token),
        )

    # 3. 把 hash 写进 collectors.token_hash（跟外部 collector 注册一样）
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    await self.db.execute(
        "UPDATE collectors SET token_hash = ? WHERE machine_id = 'local'",
        (token_hash,),
    )
    self._builtin_token = token
```

设计要点：
- **明文 token 存 settings 表**：只本机进程能读，跟 LLM API key 同级别处理
- **token_hash 存 collectors 表**：跟外部 collector 注册流程完全一致
- **幂等**：server 重启不重新生成 token（避免 collector 缓存失效）

### 3.3 构造内嵌 HTTPBackend

```python
def _make_builtin_collector(self) -> CollectorRuntime:
    from auto_daily_log.models.backends import HTTPBackend
    from auto_daily_log_collector.config import CollectorConfig
    from auto_daily_log_collector.enricher import ActivityEnricher
    from auto_daily_log_collector.platforms import create_adapter
    from auto_daily_log_collector.runner import CollectorRuntime

    m = self.config.monitor
    data_dir = self.config.system.resolved_data_dir
    screenshot_dir = data_dir / "screenshots"

    backend = HTTPBackend(
        server_url=f"http://127.0.0.1:{self.config.server.port}",
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
        server_url=f"http://127.0.0.1:{self.config.server.port}",
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
        skip_http_register=True,   # 已在 _register_builtin_collector 写 DB
    )
```

`skip_http_register=True` 是唯一的一点"内嵌例外"——因为 server 启动时已经
UPSERT 了 `collectors` 行（它**就是 DB 的拥有者**，直接写更简洁、避免鸡生
蛋）。认证和 ingest 路径依然走正常 HTTP 流程。

### 3.4 认证层完全不特殊对待

`/api/ingest/*` 现有的 `_authenticate_collector` 依赖：
- `Authorization: Bearer <token>` header
- 查 `collectors.token_hash` 比对

**不需要改**。内嵌 collector 带同样的 header，进同样的验证分支，放行。

### 3.5 删除 LocalSQLiteBackend

- 删 `auto_daily_log/models/backends/local.py`
- `auto_daily_log/models/backends/__init__.py` 去掉 `LocalSQLiteBackend` 导出
- 删 `tests/test_database_async_backend.py`（或重写成测 HTTPBackend 经 FastAPI TestClient 的 integration 测试）
- 改 `tests/test_builtin_collector.py`：用 `httpx.AsyncClient` 打 TestClient，验证内嵌路径跟外部路径产出相同 row

## 4. 执行步骤

### Phase 1 — 准备

1. 跑 `.venv/bin/python -m pytest tests/ -q` 确认 230 个测试当前绿的
2. 读 `auto_daily_log/web/api/ingest.py` 的 `_authenticate_collector` 确认认证逻辑
3. 读 `auto_daily_log/models/backends/http.py` 的现有实现（extend_duration / save_screenshot 等都应在）

### Phase 2 — Token 自分发

1. 改 `auto_daily_log/app.py:_register_builtin_collector`：加 token 生成 + token_hash 更新逻辑
2. Application 类新增 `self._builtin_token` 属性
3. 加单测 `tests/test_builtin_token.py`：
   - server 首次启动生成新 token（存在 settings 表）
   - 第二次启动读回同一个 token（幂等）
   - token_hash 跟明文 match

### Phase 3 — 内嵌 CollectorRuntime 改用 HTTPBackend

1. 新方法 `Application._make_builtin_collector()` 返回 CollectorRuntime（backend=HTTPBackend）
2. 新方法 `Application._wait_for_server_ready(port, timeout)`：asyncio socket connect 轮询
3. 改 `Application.run()` 启动顺序：
   - register_builtin_collector 先（写 DB）
   - uvicorn 起在 task
   - wait_for_server_ready
   - _make_builtin_collector + create_task
4. `self.monitor` 还是指向 CollectorRuntime 实例（watchdog 访问 `self.monitor.trace` 不用改）

### Phase 4 — 删除 LocalSQLiteBackend

1. 删 `auto_daily_log/models/backends/local.py`
2. 改 `__init__.py` 去除导出
3. 全局 grep `LocalSQLiteBackend` 应该只剩在历史文档/注释里
4. `tests/test_database_async_backend.py` 删除或重写
5. `tests/test_builtin_collector.py` 重写：用 FastAPI TestClient 模拟 server，CollectorRuntime + HTTPBackend 打 TestClient，验证 activities 行入库、字段齐全

### Phase 5 — 测试回归

1. 跑 `.venv/bin/python -m pytest tests/ -q`
2. 目标：**230+ 测试全绿**（新增 token 测试 / 重写的 builtin 测试后，数字可能 +2-3）

### Phase 6 — 端到端手工验证

用 shell 脚本或 subagent 自己验证：

```bash
# 1. Server 启动
./pdl server restart

# 2. 等 5 秒，验证本机 collector 注册 + heartbeat 路径
sqlite3 ~/.auto_daily_log/data.db \
  "SELECT machine_id, name, last_seen, token_hash FROM collectors WHERE machine_id='local';"
# 预期：token_hash 非空，last_seen 近几秒

# 3. 验证有新活动进来（走了 HTTP /ingest 路径）
sqlite3 ~/.auto_daily_log/data.db \
  "SELECT COUNT(*) FROM activities WHERE machine_id='local' AND timestamp > datetime('now','-1 minute');"
# 预期：≥ 1

# 4. 看 server log，应该有 POST /api/ingest/activities 的访问记录
./pdl server logs 50 | grep "POST /api/ingest"
# 预期：来自 127.0.0.1 的 POST 成功

# 5. 取本机 token 手动用 curl 打一条 activity，验证外部 collector 同款路径可行
token=$(sqlite3 ~/.auto_daily_log/data.db "SELECT value FROM settings WHERE key='builtin_collector_token';")
curl -sSf "http://127.0.0.1:8888/api/ingest/activities" \
  -H "Authorization: Bearer $token" \
  -H "X-Machine-ID: local" \
  -H "Content-Type: application/json" \
  -d '{"activities":[{"timestamp":"2026-04-15T99:99:99","app_name":"CurlTest","window_title":"Manual","duration_sec":1}]}'
# 预期：200 OK，DB 里有这条记录
```

## 5. 风险

1. **启动时序竞态**：`_wait_for_server_ready` 若 timeout（网络异常、端口被抢）
   → 内嵌 collector 起不来。缓解：timeout 设 10s，失败后打 warning 但不挂 server。

2. **Loopback HTTP 性能**：每 30s 一次采样 + 可能的截图 multipart 上传。
   单机 localhost 绰绰有余（<10ms/请求），personal 工具无感知。

3. **Screenshot 二次 I/O**：HTTPBackend 的 save_screenshot 上传文件 → server 端
   写到同一个 screenshots 目录。本机场景这是"文件写一次、又读一次上传、又写一次"，
   浪费但功能正确。不优化（未来如需，加"源和目的同目录时 rename"特殊路径）。

4. **Offline queue 对本机无用**：HTTPBackend 有 queue_dir 机制给网络失败时用。
   本机 loopback 基本不会失败，queue_dir 会永远空。**留着无害，不特殊处理**。

5. **Settings 表里的 plaintext token**：跟现有 LLM API key 同级别风险。个人工具
   可接受。权限上 DB 文件本来就是 ~/.auto_daily_log/data.db 用户专属。

6. **现有外部 collector 已注册的 token 不受影响**：本次改动只加了"本机 collector
   也用 HTTP"，现有 `m-7b7304d976b04c96` (摸鱼查看器) 之类的记录和 token
   不变。

## 6. 约束

1. **不改 `/api/ingest/*` endpoint 的认证 / schema**——如果改了就不是"统一"，是"新协议"
2. **不升级依赖**（pyproject.toml 不动）
3. **不改 AGENTS.md / CLAUDE.md 核心原则**
4. **每 phase 打一个 commit**，message 前缀 `refactor: unify data path — phase N`
5. **保留 machine_id='local' 语义**（不把内嵌 collector 改成 UUID machine_id——前端 UI 和历史数据都依赖这个字符串）
6. **测试精确值断言**（AGENTS.md §测试规范）

## 7. 完成标志

- [ ] 全量测试通过（≥230 + 新增约 2-3）
- [ ] `auto_daily_log/models/backends/local.py` 已删
- [ ] server log 里 `local` collector 的 ingest 显示为 `POST /api/ingest/activities` 成功（走 HTTP）
- [ ] `collectors.token_hash WHERE machine_id='local'` 非空
- [ ] `settings.builtin_collector_token` 存在
- [ ] 端到端手工 curl 能用同一个 token 打 ingest，行入库
- [ ] 写 SUMMARY 到 `docs/plans/2026-04-15-collector-unify-data-path-SUMMARY.md`：列出所有 commits、测试数量变化、验证结果、任何折中决策

## 8. 完成后衔接

这份完成后，下一个计划会是 `llm_summary` 特性——用 per-activity LLM 摘要替代
本地 OCR 截断。**那份计划依赖本计划的"唯一数据路径"**（llm_summary 触发的
worker 只需监听 DB INSERT，不需要区分路径来源）。
