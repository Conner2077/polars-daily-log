# Changelog

## [0.3.0] — 2026-04-16

整体 UI 重构，对齐 landing page 的 OpenAI 风格（白底 + 暖墨 `#171717` + Geist 字体 + 左侧 sidebar 导航），并新增动态时间轴、设备在线状态、MyLog 过滤器等交互能力。

### Added
- **左侧 sidebar 导航**（220px）：品牌 / 带图标的导航项（带角标） / DEVICES 在线状态（绿色呼吸灯）/ 底部用户块。点击设备卡片直跳 `/activities?machine=xxx` 按机器筛选。
- **Dashboard 动态时间轴**：SVG 柱状图，滚动 12 小时窗口，60s 自动刷新；当前时间游标；跨零点显示虚线 + `MM-DD` 标签；3 条水平网格线；idle 占比 >50% 的柱子显示为灰色。
- **Dashboard 四张 stat cards**：工作时长（带日环比）/ 活动记录（附 LLM 摘要数）/ MyLog 草稿 / 已推 Jira。
- **Dashboard 左右分栏**：活动时间轴 + 待审批 MyLog 草稿预览；下方"最近活动"表格 5 列。
- **MyLog "今日 / 过去"双级过滤**：过去 hover 时横向展开子选项（全部 / 每日 / 每周 / 每月 / 自定义），stagger 动画；选中子项后 tab 文字直接显示选中项。
- **新后端 endpoints**：
  - `GET /api/activities/timeline` — 滚动窗口按 bucket 聚合
  - `GET /api/activities/recent` — 最近 N 条活动（含 LLM 摘要）
  - `GET /api/dashboard/extended` — 工时 / 草稿 / Jira 统计，含日环比
  - `GET /api/worklogs/drafts/preview` — 待审批草稿展平到 issue 粒度
  - `GET /api/machines/status` — 设备在线状态（用 activities 表的最新 timestamp 而不是 collectors.last_seen，避免 ingest 不更新 last_seen 的误差）

### Changed
- **产品 UI 全站改版**：`src/styles/theme-minimal.css`（新增）在 `global.css` 之后加载，统一 CSS vars（暖墨 / 白底 / Geist / JetBrains Mono），去掉 Apple 蓝色。Element Plus 组件 12 类（button / input / dialog / tag / switch / table / timeline / popover / tabs / card / empty / message）统一覆盖。
- **5 个页面 template 层重构**：Dashboard / Activities / MyLogs / Issues / Settings 全部对齐新风格（flat cards / 行高 / 字号 / 状态 pill 色值等）。
- **Activities / MyLogs 默认日期**：改用本地日期（`getFullYear/Month/Date`），修复 UTC 0 时区附近用户看到昨天数据的问题。
- **MyLog 命名**：侧边栏、Dashboard 卡片、页面标题、空状态统一从 "Worklog 草稿" 改为 "MyLog"。
- **Sidebar 配色**：背景 `#f3f3f3`，主内容区 `#fafafa`，卡片 `#ffffff`，三层色阶让卡片"浮"出来。

### Fixed
- **Activities 页卡死**：`el-tag` 的 `type` prop 不允许空字符串，但 `categoryType()` 对 design/research/browsing/idle 返回了 `""` 触发每行 Vue validation warning × 600 行 → 浏览器冻结。修：fallback 到 `'info'`。
- **Settings 页白屏**：模板里用 `location.port`，Vue 把它当组件作用域变量 → undefined.port 崩溃。修：script 里声明 `windowPort` 常量。
- **Classifier 误判**：`daily` 单独作为会议关键词太宽，"Polars Daily Log" 被标成 meeting。修：限定为 `daily standup/sync/scrum/huddle`；`sprint` 限定为 `sprint planning/review`。
- **设备全显示离线**：`collectors.last_seen` 只在握手时写一次，健康的 collector 也会显示 8 小时前。修：endpoint 改用 `MAX(activities.timestamp)` 作为 last_seen。

### 升级注意
- UI 变化大但产品行为不变；所有 API 契约向后兼容。
- 回退机制：`feat/ui-refactor-light` 分支整段可 `git revert`；仅 CSS 部分可通过注释 `main.js` 里的 `import "./styles/theme-minimal.css"` 秒级切回。
- 历史数据的 activity 分类不会重算；新数据用修正后的 classifier。

---

## [0.2.0] — 2026-04-15

首个可发布 tarball 版本。核心是把 collector 架构拉直、在活动粒度上引入 LLM 语义压缩，并把 CLI/品牌统一到 `pdl` / Polars Daily Log。

### Added
- **Per-activity LLM 摘要**：每条活动单独过一次 LLM 做语义压缩，替代之前对 OCR 原文的简单截断。新增 `activities.llm_summary` 字段、`ActivitySummarizer` 后台 worker、每日总结优先使用 summary、前端 Activities 页多出 LLM 摘要列、Settings 页可编辑活动级 prompt 模板。
- **Release tarball pipeline**：`scripts/release.sh` + `install.sh` / `install.ps1`，可直接打出无需 Node.js 的安装包交付给用户；`docs/release.md` 给出完整 runbook。
- **`pdl build` 子命令** + Windows `install.ps1`，开发者一条命令重建前端 + wheel。
- **安装 verification**：`install.sh` / `install.ps1` 末尾逐项 import 核心依赖，缺失立即打印修复命令。

### Changed
- **Collector 架构统一**：`monitor/` 移入 `auto_daily_log_collector/monitor_internals/`，新增 `ActivityEnricher`；内置 collector 与独立 collector 走同一 `CollectorRuntime`，server 侧内嵌 collector 也复用同一条代码路径。
- **数据路径统一**：内置 collector 改走 loopback HTTP + `HTTPBackend`，删掉 `LocalSQLiteBackend`；内置 token 自分发。
- **CLI 统一为 `pdl`**，环境变量前缀统一为 `PDL_*`（原 `adl` / `ADL_*` 已废弃）。
- **默认端口统一 8888**（config / collector / docs 全部对齐）。
- **品牌与文档定位**：整体重命名为 Polars Daily Log，`AGENTS.md` 明确"个人多设备工具，不是团队协作软件"的产品边界。

### Fixed
- **Idle 后截图恢复**：唤醒后首帧截图链路补上，且 idle 时间不再计入当日工时总和。
- **Jira worklog emoji → HTTP 500**：`_build_worklog_payload` 统一做 4-byte UTF-8 scrub，所有 worklog 必须走 `build_jira_client_from_db` + `JiraClient.submit_worklog`，不要再直接 POST。

### 升级注意
- 从 0.1.0 升级需要重启 server 与 collector；activity 表新增 `llm_summary` 字段由迁移自动补齐。
- 原 `adl` / `ADL_*` 环境变量改为 `pdl` / `PDL_*`，systemd / launchd / 脚本里的启动命令需要同步改。
- 默认端口改到 8888，如果之前显式指定过其他端口请检查 `config.yaml`。
