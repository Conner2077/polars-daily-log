# Per-Activity LLM 摘要（"活动内容猜测"）

**状态**：待执行
**预计工作量**：6-7 小时
**执行方式**：subagent 单次任务
**前置依赖**：`2026-04-15-collector-unify-data-path.md` 已完成（所有 ingest 经 `/api/ingest/*`，数据路径唯一）

---

## 1. 问题

当前 daily 生成的 Stage 2（`_compress_activities`）对 OCR 做**前 100 字截断**，是严重的信息丢失。
每天成百上千的活动被压成稀疏片段，喂给 Step 1 LLM 时语境已残缺，生成的 full_summary 质量天然受限。

## 2. 目标

每条活动入库后，**server 后台异步调 LLM**，给它打一段 ≤100 字的"此刻我在做什么"摘要，
存到 `activities.llm_summary` 列。后续 daily 生成时 `_compress_activities` 聚合的是这些
**语义密度高的摘要**而不是 OCR 片段。

用户可在 Activities 页看到每条活动的 LLM 摘要。Prompt 模板可配置。

## 3. 架构

```
Collector（纯采集，不变）
    │ POST /api/ingest/activities
    ▼
/api/ingest/activities endpoint
    │ INSERT activities (llm_summary=NULL)
    │ 立即 return 200
    ▼
ActivitySummarizer（server 后台 asyncio task）
    while True:
      rows = SELECT ... WHERE (llm_summary IS NULL OR llm_summary='(failed)')
                       AND category != 'idle'
                       ORDER BY timestamp ASC LIMIT 10
      for row in rows:
        prev3 = SELECT llm_summary FROM activities
                 WHERE machine_id=? AND timestamp < ?
                   AND llm_summary IS NOT NULL AND llm_summary != '(failed)'
                 ORDER BY timestamp DESC LIMIT 3
        prompt = render("活动内容猜测 Prompt", prev3, current)
        try:
          summary = await llm.generate(prompt)
          UPDATE activities SET llm_summary=?, llm_summary_at=?
        except:
          UPDATE activities SET llm_summary='(failed)', llm_summary_at=?
      await asyncio.sleep(5)

Daily 生成入口（WorklogSummarizer.generate_drafts）
    │ await activity_summarizer.backfill_for_date(target_date, timeout=60)
    │   （同步等 worker 把 NULL 的都跑一遍，超时就走）
    │ _compress_activities(activities) 改用 a["llm_summary"]
    ▼
    Step 1 LLM → full_summary（现在语境更好）
    Step 2 LLM → per-issue JSON（不变）
```

## 4. 设计决策（用户已拍板）

| 决策 | 值 |
|------|---|
| 位置 | server 端后台 worker，collector 只采集 |
| 调用方式 | 单条调用，带前 3 条已成功的 llm_summary 作 context |
| Prev-N 范围 | 同 machine_id 内，timestamp < 当前，llm_summary 非空非 failed |
| 跳过条件 | 仅 `category='idle'`，其他都处理 |
| DB 列 | 独立列 `llm_summary TEXT` + `llm_summary_at TEXT`，不塞 signals JSON |
| OCR 长度 | prompt 里**不截断**（输出保留原始 OCR 完整性）|
| 失败处理 | 标记 `'(failed)'`，LLM 恢复后 worker 扫到会重试（降级为 NULL 需要 daily 时 fallback）|
| Daily 生成时 | 同步 backfill 当天 NULL/failed 行，60s 超时，未完成的 fallback 到旧 OCR 截断 |
| Prompt 可配置 | 新增"活动内容猜测 Prompt"，settings key，UI 编辑框 |
| LLM Engine | 复用 `_get_llm_engine_from_settings`（跟 daily 一套引擎）|

## 5. 执行步骤

### Phase 1 — Schema 迁移

1. 改 `auto_daily_log/models/database.py`：新增 2 列 + 索引
   ```python
   # 在 _migrate() 里
   act_cols = ...
   if "llm_summary" not in act_col_names:
       await self._conn.execute("ALTER TABLE activities ADD COLUMN llm_summary TEXT")
   if "llm_summary_at" not in act_col_names:
       await self._conn.execute("ALTER TABLE activities ADD COLUMN llm_summary_at TEXT")
   # 索引：worker 要频繁扫 "NULL or failed"，category != idle
   await self._conn.execute(
       "CREATE INDEX IF NOT EXISTS idx_activities_llm_pending "
       "ON activities(timestamp) WHERE llm_summary IS NULL OR llm_summary='(failed)'"
   )
   ```
2. 单测：`tests/test_phase_a_schema.py` 或新增测试验证两列存在 + 可读可写 + index 存在

### Phase 2 — Prompt 模板

改 `auto_daily_log/summarizer/prompt.py`：

```python
DEFAULT_ACTIVITY_SUMMARY_PROMPT = """你是活动识别助手。根据用户当前的桌面活动片段，**猜测**此刻在做什么，输出一句 ≤100 字的中文描述。

【最近活动（由早到晚）】
{prev_summaries}

【此刻】
时间：{timestamp}
前台应用：{app_name}
窗口标题：{window_title}
URL：{url}
浏览器标签：{tab_title}
OCR 识别文字：{ocr_text}
企业微信群名：{wecom_group}

要求：
- 结合"最近活动"推测意图（例如"继续上一步的调试"），不要孤立描述
- 只猜测具体在做什么，不评价
- ≤100 字中文，**一句话**，不要标题、不要列表
- 如果"最近活动"为空（第一条活动），直接根据"此刻"信息猜测
"""
```

- 导出到 `__init__.py` / `web/api/settings.py:get_default_prompts` 返回的 dict 里加 `activity_summary_prompt`

### Phase 3 — ActivitySummarizer 类

新文件 `auto_daily_log/summarizer/activity_summarizer.py`：

```python
class ActivitySummarizer:
    """Background worker that fills activities.llm_summary via LLM."""

    POLL_INTERVAL_SEC = 5
    BATCH_SIZE = 10
    PREV_N = 3

    def __init__(self, db: Database, get_engine, get_prompt):
        """
        get_engine: async () -> LLMEngine | None  # reads settings table fresh each call
        get_prompt: async () -> str                # user-configured or default
        """
        self._db = db
        self._get_engine = get_engine
        self._get_prompt = get_prompt
        self._running = False
        self._loop_task = None

    async def run(self) -> None:
        """Main polling loop. Runs until stop()."""
        self._running = True
        while self._running:
            try:
                processed = await self._process_batch()
                if processed == 0:
                    await asyncio.sleep(self.POLL_INTERVAL_SEC)
            except Exception as e:
                print(f"[ActivitySummarizer] loop error: {e}")
                await asyncio.sleep(self.POLL_INTERVAL_SEC * 3)

    def stop(self) -> None:
        self._running = False

    async def _process_batch(self) -> int:
        """Fetch up to BATCH_SIZE pending rows, summarize each. Returns count processed."""
        rows = await self._db.fetch_all(
            """SELECT id, machine_id, timestamp, app_name, window_title, url, signals
               FROM activities
               WHERE (llm_summary IS NULL OR llm_summary='(failed)')
                 AND category != 'idle'
                 AND deleted_at IS NULL
               ORDER BY timestamp ASC LIMIT ?""",
            (self.BATCH_SIZE,),
        )
        if not rows:
            return 0

        engine = await self._get_engine()
        if engine is None:
            # LLM not configured — sleep longer, no point spinning
            return 0

        prompt_template = await self._get_prompt()

        for row in rows:
            await self._summarize_one(row, engine, prompt_template)
        return len(rows)

    async def _summarize_one(self, row, engine, prompt_template) -> None:
        prev_summaries = await self._fetch_prev_summaries(
            row["machine_id"], row["timestamp"]
        )
        prev_text = self._format_prev(prev_summaries)

        signals = {}
        try:
            if row["signals"]:
                signals = json.loads(row["signals"])
        except Exception:
            pass

        prompt = render_prompt(
            prompt_template,
            prev_summaries=prev_text,
            timestamp=row["timestamp"],
            app_name=row["app_name"] or "",
            window_title=row["window_title"] or "",
            url=row["url"] or "",
            tab_title=signals.get("tab_title") or "",
            ocr_text=signals.get("ocr_text") or "",
            wecom_group=signals.get("wecom_group_name") or "",
        )

        try:
            summary = (await engine.generate(prompt)).strip()
            if not summary:
                summary = "(failed)"
            elif len(summary) > 200:
                summary = summary[:200]  # safety clip in case LLM ignores 100-char rule
        except Exception as e:
            print(f"[ActivitySummarizer] LLM failed for row {row['id']}: {e}")
            summary = "(failed)"

        await self._db.execute(
            "UPDATE activities SET llm_summary=?, llm_summary_at=datetime('now') WHERE id=?",
            (summary, row["id"]),
        )

    async def _fetch_prev_summaries(self, machine_id, timestamp):
        rows = await self._db.fetch_all(
            """SELECT timestamp, app_name, llm_summary FROM activities
               WHERE machine_id=? AND timestamp < ?
                 AND llm_summary IS NOT NULL AND llm_summary != '(failed)'
                 AND deleted_at IS NULL
               ORDER BY timestamp DESC LIMIT ?""",
            (machine_id, timestamp, self.PREV_N),
        )
        return list(reversed(rows))

    def _format_prev(self, prev_rows) -> str:
        if not prev_rows:
            return "（无）"
        lines = []
        for r in prev_rows:
            ts = r["timestamp"][11:16] if len(r["timestamp"]) >= 16 else r["timestamp"]
            lines.append(f"- {ts} [{r['app_name']}] {r['llm_summary']}")
        return "\n".join(lines)

    async def backfill_for_date(self, target_date: str, timeout_sec: int = 60) -> int:
        """Synchronously process all pending rows for a given date.

        Used by daily summary to catch up before compressing. Returns
        count processed; remaining pending (if timeout) get fallback
        treatment in _compress_activities.
        """
        deadline = asyncio.get_event_loop().time() + timeout_sec
        total = 0
        while asyncio.get_event_loop().time() < deadline:
            pending = await self._db.fetch_one(
                """SELECT COUNT(*) AS n FROM activities
                   WHERE date(timestamp)=? AND deleted_at IS NULL
                     AND (llm_summary IS NULL OR llm_summary='(failed)')
                     AND category != 'idle'""",
                (target_date,),
            )
            if not pending or pending["n"] == 0:
                break
            processed = await self._process_batch()
            total += processed
            if processed == 0:
                break  # engine not configured or no rows matched filter
        return total
```

- 单测 `tests/test_activity_summarizer.py`：mock engine + db fixture，至少 8 个 case：
  - 处理一条成功
  - LLM 异常时写入 `'(failed)'`
  - 跳过 `category='idle'`
  - 跳过 `deleted_at` 非 NULL
  - 重试 `'(failed)'` 行
  - prev3 只来自同 machine_id
  - prev3 不含 NULL/failed 行
  - backfill_for_date 空 table 立即返回
  - backfill_for_date 对 failed 行也处理

### Phase 4 — Worker 接入 Application

改 `auto_daily_log/app.py`：

```python
# run() 里，uvicorn 就绪 + 内嵌 collector 启动之后，加：
from .summarizer.activity_summarizer import ActivitySummarizer

async def _get_engine():
    from .web.api.worklogs import _get_llm_engine_from_settings
    try:
        return await _get_llm_engine_from_settings(self.db)
    except Exception:
        return None

async def _get_prompt():
    from .summarizer.prompt import DEFAULT_ACTIVITY_SUMMARY_PROMPT
    row = await self.db.fetch_one(
        "SELECT value FROM settings WHERE key='activity_summary_prompt'"
    )
    if row and row["value"] and row["value"].strip():
        return row["value"]
    return DEFAULT_ACTIVITY_SUMMARY_PROMPT

self._activity_summarizer = ActivitySummarizer(self.db, _get_engine, _get_prompt)
summarizer_task = asyncio.create_task(self._activity_summarizer.run())

# 把 summarizer_task 和其他 task 一起放进 try/finally 清理
# 在 finally 里：
#   self._activity_summarizer.stop()
#   summarizer_task.cancel()
```

**把 `ActivitySummarizer` 实例暴露为 `app.state.activity_summarizer`** 这样 daily 路径能调用 backfill。

### Phase 5 — Daily 同步 backfill

改 `auto_daily_log/summarizer/summarizer.py:WorklogSummarizer.generate_drafts`：

```python
async def generate_drafts(self, target_date, prompt_template=None):
    # ... 现有 fetch jira_issues 之前/之后 ...
    # 在 activities fetch 之前加：
    if self._activity_summarizer is not None:
        try:
            await self._activity_summarizer.backfill_for_date(target_date, timeout_sec=60)
        except Exception as e:
            print(f"[Summarizer] activity backfill failed (non-fatal): {e}")
    # ... 继续 fetch activities ...
```

WorklogSummarizer 构造函数接受可选的 `activity_summarizer` 参数。
调用方（`web/api/worklogs.py:_generate_daily` / `scheduler/jobs.py:DailyWorkflow`）传进去。

### Phase 6 — `_compress_activities` 改用 llm_summary

改 `auto_daily_log/summarizer/summarizer.py:_compress_activities`：

```python
def _compress_activities(self, activities):
    if not activities:
        return "无"

    groups = defaultdict(lambda: {
        "duration": 0,
        "titles": set(),
        "llm_summaries": [],       # NEW
        "ocr_fallback": [],        # 只在 llm 不可用时用
    })

    for a in activities:
        key = (a.get("category", "other"), a.get("app_name", "Unknown"))
        groups[key]["duration"] += a.get("duration_sec", 0)
        title = a.get("window_title")
        if title:
            groups[key]["titles"].add(title[:60])

        # 优先用 llm_summary；跳过 None / '(failed)'
        llm_sum = a.get("llm_summary")
        if llm_sum and llm_sum != "(failed)":
            if llm_sum not in groups[key]["llm_summaries"]:  # 去重
                groups[key]["llm_summaries"].append(llm_sum)
        else:
            # Fallback：旧 OCR 逻辑（取前 100 字，用户不希望但 LLM 失败时总比空强）
            if a.get("signals"):
                try:
                    signals = json.loads(a["signals"])
                    ocr = (signals.get("ocr_text") or "")[:100]
                    if ocr and len(groups[key]["ocr_fallback"]) < 3:
                        groups[key]["ocr_fallback"].append(ocr)
                except (json.JSONDecodeError, TypeError):
                    pass

    lines = []
    for (cat, app), info in sorted(groups.items(), key=lambda x: -x[1]["duration"]):
        hours = round(info["duration"] / 3600, 1)
        if hours < 0.1:
            continue
        titles = list(info["titles"])[:5]
        title_str = ", ".join(titles) if titles else ""
        line = f"- [{cat}] {app} ({hours}h): {title_str}"
        # 优先用 llm_summary 汇总
        if info["llm_summaries"]:
            summaries = "；".join(info["llm_summaries"][:8])  # 最多 8 条避免 prompt 爆炸
            line += f" | 内容: {summaries}"
        elif info["ocr_fallback"]:
            line += f" | OCR: {'; '.join(info['ocr_fallback'][:2])}"
        lines.append(line)

    return "\n".join(lines) or "无"
```

**注意**：`fetch activities` 的 SQL 要 SELECT 出 `llm_summary` 列。
现在 `SELECT *` 会自动包括（数据库加列后 SELECT \* 会返回），但显式加上更清晰。

### Phase 7 — 前端 UI

**`/api/activities` 响应加 `llm_summary` 字段**（`auto_daily_log/web/api/activities.py`）：
- 现在返回什么字段查一下；如果是 `SELECT * FROM activities` 就已自动包含，验证 response 带上 `llm_summary`

**`Activities.vue` 加列 "LLM 摘要"**：
- 表格 `<el-table-column>` 新增一列，column-key `llm_summary`
- Cell 显示 llm_summary 内容；`(failed)` 显示为灰色 "—（识别失败）"；NULL 显示为 "—"
- 列宽 200-300px，text-overflow ellipsis，hover 或点击展开

**活动详情弹窗/展开**（如果已经有 OCR 展示的详情面板）加一行 "LLM 摘要"：
- 检查 `Activities.vue` 是否有 `row.signals.ocr_text` 展示逻辑，旁边加 `row.llm_summary`

### Phase 8 — Settings UI 加 Prompt 编辑框

改 `web/frontend/src/views/Settings.vue`：

1. Settings 里 "Prompt 模板" 区域加第 4 个 textarea "活动内容猜测 Prompt"
2. 绑定到 `settings.activity_summary_prompt`
3. "恢复默认" 按钮调 `/api/settings/default-prompts` 取 `activity_summary_prompt`
4. 保存时 `api.putSetting('activity_summary_prompt', value)`
5. `Settings.vue:settings` 的 ref 初始值加 `activity_summary_prompt: ''`

改 `auto_daily_log/web/api/settings.py:get_default_prompts`：
```python
return {
    "summarize_prompt": DEFAULT_SUMMARIZE_PROMPT,
    "auto_approve_prompt": DEFAULT_AUTO_APPROVE_PROMPT,
    "period_summary_prompt": DEFAULT_PERIOD_SUMMARY_PROMPT,
    "activity_summary_prompt": DEFAULT_ACTIVITY_SUMMARY_PROMPT,  # NEW
}
```

### Phase 9 — 端到端验证

1. 全量测试：`.venv/bin/python -m pytest tests/ -q`，目标 ≥233 + 8 = 241
2. 跑前端 build：`cd web/frontend && npm run build`（不要用 `./pdl build` 因为会动 Python 环境）
3. 假数据验证：
   ```bash
   # 从旧 DB 里清掉所有 llm_summary 标记（模拟一次性全 backfill 场景）
   sqlite3 ~/.auto_daily_log/data.db "UPDATE activities SET llm_summary=NULL, llm_summary_at=NULL WHERE machine_id='local' AND category != 'idle' LIMIT 20;"
   # 等 30s, 再查
   sqlite3 ~/.auto_daily_log/data.db "SELECT COUNT(*), COUNT(llm_summary) FROM activities WHERE machine_id='local' AND category != 'idle';"
   ```
   （这一步不能跑，因为计划 §约束不让启动 server。改成 SUMMARY 里描述 "若手动启动 server，预期 worker 30s 内填满"）

## 6. 约束

1. **Collector 不加任何逻辑**——LLM 相关全部放 server 侧
2. **LLM 调用必须异步**，不阻塞 ingest 路径
3. **失败行可重试**（下一轮 worker 扫到 `'(failed)'` 会重试）
4. **不改 `/api/ingest/*` 签名**
5. **不改 AGENTS.md / CLAUDE.md**（可新增 AGENTS.md 的一条 tip 关于 llm_summary 用途）
6. **不升级依赖**
7. **prev-N 严格同 machine_id**（跨机污染 context）
8. **每 phase 结束打一个 commit**，前缀 `feat: per-activity llm summary — phase N — ...`
9. **测试精确值断言**（AGENTS.md §测试规范）
10. **daily generate 路径在 backfill 超时后必须继续执行**（不阻塞），用 fallback OCR 兜底

## 7. 风险

1. **冷启动成本**：第一次装完、或长时间没 LLM 的情况下，数据库累积了几千条 NULL 行。
   Worker 慢慢处理（每批 10 条 × 每次 2-3 秒 LLM 延迟 × 每轮 sleep 5s = ~30 分钟才能
   处理 200 条）。用户感知是"新装的第一天 Activities 页大部分行 LLM 摘要是空的"。
   **缓解**：Activities 页显示 "—" 而不是错误；daily 生成 backfill 时同步跑一批；
   用户的容忍阈值是"开始用一两天后稳定了就好"。

2. **LLM API key 没配置**：`_get_engine` 返回 None → worker 每轮 sleep，不忙等。
   用户配好后 worker 自动恢复。

3. **失败行 `'(failed)'` 永远得不到处理**：如果 LLM 一直挂、用户不修，就永远失败。
   但每次 worker 扫都会再试，所以一旦 LLM 恢复就会逐渐清光。文档化此行为。

4. **Prompt 爆炸**：某条活动 OCR 极长（几千字）× prev3 各有 100 字 = 整个 prompt
   5000+ tokens。Kimi 8k context 够，但速度 + 费用偏高。
   **缓解**：先观察，有问题再加 OCR 截断。用户明确说"不截断"，遵守。

5. **Daily 生成延迟**：60s backfill 超时前，用户感知 "生成按钮一直转"。
   UI 上 `MyLogs.vue` 已有进度步骤提示，backfill 阶段新增一个 "AI 正在理解活动..." 
   能让用户感知到在做事。这是 phase 7 的可选 polish。

6. **活动数据增长**：`llm_summary` 列每行 100 字 × 每天 200-500 行 × 一年 = 几 MB。
   可忽略。

## 8. 完成标志

- [ ] 所有 phase commit 按顺序打好
- [ ] 测试 ≥241
- [ ] `activities` 表有 `llm_summary` + `llm_summary_at` 列 + pending index
- [ ] `ActivitySummarizer` 类在 `auto_daily_log/summarizer/activity_summarizer.py`
- [ ] `DEFAULT_ACTIVITY_SUMMARY_PROMPT` 在 `prompt.py`
- [ ] `/api/settings/default-prompts` 返回 `activity_summary_prompt`
- [ ] `Activities.vue` 新增 "LLM 摘要" 列（显示 llm_summary；failed / null 友好降级）
- [ ] `Settings.vue` 新增"活动内容猜测 Prompt"编辑框 + 恢复默认按钮
- [ ] `_compress_activities` 优先用 llm_summary，fallback OCR
- [ ] `WorklogSummarizer.generate_drafts` 开头 await `activity_summarizer.backfill_for_date`
- [ ] `docs/plans/2026-04-15-per-activity-llm-summary-SUMMARY.md` 写完

## 9. 不做的事（delayed）

这些 MVP 不做，未来可加：
- OCR 片段的语义哈希缓存（同 (app, title, ocr_hash) N 分钟内复用结果，省 LLM 调用）
- 活动详情页 "重新识别" 按钮
- Worker 处理进度 UI 实时显示（pending 数、当前处理到哪条）
- Prompt A/B 对比工具
- LLM 费用统计
