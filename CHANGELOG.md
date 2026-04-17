# Changelog

## [0.5.2] — 2026-04-17

安装脚本全面修复 + Windows 一键安装。

### Added
- **Windows bootstrap**：`irm .../bootstrap.ps1 | iex` 一行装，和 bash 版对齐
- **install.sh 28 个分支覆盖测试**：角色/配置/口令/镜像/节号/前端/启动/模式全覆盖
- **install.ps1 15 个 Windows-only 测试**：CI Windows runner 真跑，macOS/Linux skip
- **Landing page 版本号动态化**：从 pyproject.toml 读，不再手动改

### Fixed
- **install.ps1 全面对齐 install.sh**：VERSION 动态化、数据目录创建、builtin LLM 口令解密（Git 自带 openssl）、阿里云 pip 镜像、collector.yaml 用 pyyaml 写、adl.ps1→pdl、安装完自动启动
- **install.sh sed `&` 注入**：collector URL/name 含特殊字符时 sed 替换炸掉；已转义

---

## [0.5.1] — 2026-04-17

Hotfix: install.sh 重写，修复 `curl | bash` 全流程。

### Fixed
- **所有 `read` 改用 `tty_read` helper**：统一走 `/dev/tty`，无 tty 时降级到默认值而不是死循环/崩溃
- **节号重复**：builtin LLM 和 Frontend 都写"7."，现已重排 1-10
- **VERSION 硬编码 0.1.0**：改为动态读 VERSION 文件（release）或 pyproject.toml（dev）
- **`setup_data` 早退 bug**：role=both 且 collector.yaml 已存在时 `return` 跳过整个函数；已删除 early return
- **collector.yaml sed 注入**：URL 含特殊字符时 sed 坏掉；改用 Python yaml.safe_load/dump，sed 仅作 fallback
- **安装完不启动**：新增 "Start now? [Y/n]" 交互，默认 Y 自动拉起

---

## [0.5.0] — 2026-04-17

安装即用 + 自更新 + UI 打磨。新用户口令解密自动配 LLM、Web UI 一键升级、Jira 头像集成、响应式布局修复。

### Added
- **内置 LLM 口令解密**：安装时输入作者分享的口令，自动配好 Kimi；直接回车跳过。密文入库，明文不暴露给 GitHub 扫描器。(`scripts/encrypt-builtin.sh` + `install.sh` 交互)
- **Web UI 自更新**：Settings → 自动更新 tab，检测 / 下载 / 备份 / 升级全流程，三平台支持。
- **Jira 头像缓存**：登录后自动下载 48x48 头像到本地，侧边栏展示真实头像（降级到首字母圆圈）。
- **昵称设置**：Settings → 个人资料 tab，侧边栏显示优先级：昵称 > Jira displayName > 'User'。
- **CodeRabbit**：`.coderabbit.yaml` 按 AGENTS.md 原则配置 per-path review 规则。
- **审计时间轴中文化**：`created→创建`、`auto_approved→自动审批`、`submitted→已提交 Jira` 等。
- **每条 Issue 独立审计**：worklog 提交时按 issue 记录审计日志。
- **Ingest row_ids**：活动上传返回精确行 ID 列表（不再假设连续）。

### Fixed
- **Dashboard 响应式溢出**：`grid-template-columns` 改用 `minmax(0, 1fr)`，窗口缩小时卡片正常收缩。断点提升到 1280px。
- **审计时间显示 UTC**：SQLite `datetime('now')` 存的是 UTC，前端转本地时区显示。
- **Settings select/input 高度不一致**：统一 34px + 同样 padding。
- **Settings tab 滚动条外露**：`scrollbar-width: none` 隐藏。
- **Anthropic 连接检查挂起**：改为 stream 模式避免某些 endpoint 超时。
- **DB 路径硬编码**：`~/.auto_daily_log` 改为从 config 解析。
- **CI flaky tests**：最后一条活动用 `datetime.now()` 锚定 "online"；timeline 断言去时钟依赖。

### Changed
- 侧边栏 logo 点击跳转官网（新标签页 + hover 透明度反馈）。
- Landing page 版本标记更新到 v0.4.0，安装代码块加复制按钮。

---

## [0.4.0] — 2026-04-16

Chat Agent + E2E 测试覆盖。新增基于 LLM 的对话式日志助手，可以查询活动、生成工时、推送 Jira，全部通过自然语言完成。

### Added
- **Chat Agent**：侧边栏新增 Chat 入口，支持与 LLM 对话查询日志数据
  - **Phase 1**：会话持久化（DB 存储） + 中止/重试 + SSE 流式输出
  - **Phase 2**：智能检索 — 日期 NER（"昨天"/"上周"等）+ Issue Key 自动提取 → 携带上下文对话
  - **Phase 3**：从对话中提取 worklog → 推送到 Jira + 引用链接
  - **Phase 4**：代码块高亮 + 导出 .md + 动态建议问题
  - **History drawer**：切换/删除历史会话
  - **Session rename** + 分页 + 搜索 + Markdown 表格/链接渲染
- **E2E 全流程测试**（`tests/test_e2e_full_lifecycle.py`）：12 阶段从空环境 → 安装 → 采集 → 生成 → 审批 → 提交 → 删除，mock LLM + Jira，CI 三平台跑。总测试数 386。

### Fixed
- 无新增 fix（0.3.1 的 fix 已发布）。

---

## [0.3.1] — 2026-04-16

Bug fix + 功能增量，主要解决定时任务不触发的问题。

### Added
- **Scheduler 启动补跑**：server 启动时检查当天的 daily_generate / auto_approve 是否已产出结果，如果已过触发时间但没有输出则立即补跑。解决重启后错过定时任务的问题。
- **misfire_grace_time=7200s**：APScheduler 所有 cron job 增加 2 小时容错窗口，短暂重启后能自动补执行。
- **MyLog 生命周期按钮**：
  - 所有状态增加"删除"按钮（`DELETE /api/worklogs/{id}`）
  - pending_review / approved 增加"驳回"按钮
  - 移除语义模糊的"归档"
- **MyLog 折叠卡片**："过去"模式下卡片默认折叠（header + 摘要预览），点击展开/收起。
- **MyLog "今日/过去"过滤**：hover "过去"横向展开子选项（全部/每日/每周/每月/自定义），stagger 动画。选中后 tab 文字显示选中项。
- **MCP Server**：`pdl mcp` 暴露 activities/worklogs/Jira 给外部 agent。
- **`pdl query` CLI**：命令行直查数据（给脚本和 agent 用）。
- **Scheduler 日志**：所有定时任务加 `[Scheduler]` 前缀日志（触发/完成/失败），方便排查。

### Fixed
- **定时任务静默失败**：daily_generate / auto_approve 的 LLM 异常被 APScheduler 默认吞掉，用户看到 collector 正常但没日报。修：job 函数内 try/except + print 到 server.log。
- **"过去 → 全部"显示空**：`/api/worklogs` 不传 date 时 fallback 到今天（只返回今天数据）。修：不传 date+tag 时返回所有草稿。
- **Classifier "daily" 误判**：`daily` 单独作为会议关键词太宽，"Polars Daily Log" 被标成 meeting。修：限定为 `daily standup/sync/scrum/huddle`。
- **pdl build 残留 dist 目录**：构建前清理 wheel staging 目录。

### Tests
- 新增 `tests/test_scheduler_catchup.py`（11 cases）：覆盖 catch-up 逻辑、misfire、LLM 异常传播、空数据跳过等场景。

---

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
