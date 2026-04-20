# Changelog

## [0.7.4] — 2026-04-20

这次主要收的是 LLM/Jira 配置体验和 summarizer 失败恢复：统一 LLM 配置入口、Jira PAT 可以直接测 + 保存、OCR 识别失败有手动重试按钮、Settings 顶部角标不再对不上内容。

### Changed
- **LLM 配置统一到 `llm_engines` 表**：删除 `builtin_llm.py` 和 `~/.auto_daily_log/builtin.key` 文件。`install.sh` 解密 `builtin_llm.enc` 后直接 UPSERT 到 `llm_engines` 表（标记 is_default=1），`worklogs.py` / `search.py` 都走表查询，不再有两套读路径打架。升级会保留用户已配置的引擎记录，不会被 builtin 覆盖。
- **Collector 启动读 settings 表**：`_make_builtin_collector` 改成 async，先读 `monitor_interval_sec` / `monitor_ocr_enabled` / `monitor_ocr_engine` / `monitor_screenshot_retention_days`，以 `config.yaml` 兜底。Web UI 上调的值首次 tick 就生效，不再等第一次 heartbeat 回灌。
- **`config.yaml` 默认 `ocr_enabled: true`**：匹配 UI 现在会暴露 OCR toggle 的常见用法；已部署的机器保留各自的设置。

### Added
- **Jira PAT「测试连接」按钮**：Settings → Jira 区，PAT 模式下多一个按钮。成功时不只告诉你「通了」，还会自动把你填的 server_url / username / PAT 存进 settings 表、顺手拉一次 avatar —— 一步到位。`/api/settings/jira-test` 为底层端点，复用 `JiraClient.get_myself()`（新加的 helper，返回完整用户 JSON）。
- **LLM 引擎 JSON 导入 / 导出**：Settings → LLM 引擎区加两个按钮（`exportEngines` / `triggerImportFile`）。导出含完整 api_key，方便迁移或手动备份；导入是 upsert（按 name 匹配），已存在的会更新。端点：`GET /api/llm-engines/export` / `POST /api/llm-engines/import`。
- **「重新识别失败项」按钮**：活动记录页在有 `(failed)` 行时右上角出现，点一下把那天所有 `(failed)` 重置为 `NULL` 触发 ActivitySummarizer 5 秒内重跑。以前只能等 24h 冷却（`FAIL_COOLDOWN_HOURS`）或手写 SQL，LLM 临时失效（上游 401、key 过期）场景用户没办法自己救。端点：`POST /api/activities/retry-failed?target_date=...`。

### Fixed
- **`normalize_base_url` 不再偷偷补 `/v1`**：上版本为了"本机网关少填 /v1"用户补了自动 `/v1`，但把用反代自定义路径（如 `http://localhost:3001` 直接暴露 chat/completions 的自研网关）的用户 URL 改花了、而且悄无声息。现在规范化只做「剥尾部 `/chat/completions`、`/messages`、anthropic 多余的 `/v1`」这种非破坏性操作，别的都信用户填的。`/api/llm-engines` create / update / import 也统一调同一个 normalizer，单引擎 & 多引擎入口行为对齐。
- **Jira PAT 认证用 Basic Auth**：fanruan Jira 的 PAT 不认 Bearer header，必须用 `Basic base64(username:token)`（见 prior 0.7.3 range 上的 hotfix 承接；本版把"登录后保存 + 拉 avatar"整条链路补齐）。
- **Jira issue 抓取改走 `JiraClient`**：`/api/issues/fetch/{key}` 原本用 subprocess + curl + cookie。现在改走 `build_jira_client_from_db`，cookie / PAT 两种认证共用一条代码路径，出错类型统一走 `MissingJiraConfig` 异常。
- **Dashboard 侧边栏 MyLog badge 显示 `1` 但页面空白**：`dashboard.py` 的 `pending_review_count` 只查旧的 `worklog_drafts` 表（已经"砍审批流"的遗物），和 MyLog 页面渲染的新 pipeline `summaries` 表完全不对。改为合并两表：旧表过滤掉 `summary='[]'` 空残留、新表按 MyLogs.vue 的 `unpublishedCount` 规则（非空 issue_key、非 `ALL`/`DAILY` 哨兵、output 有 publisher）。badge 真正反映"有能推的事"。
- **`install.sh`**：用 `mktemp` 写中间 plaintext，写完 UPSERT DB 后 `rm -f`，避免 `builtin.key` 遗留在文件系统。

### 测试
- 新增 30+ 回归 case：
  - `tests/test_api_llm_engines.py` 覆盖 CRUD 规范化 + export/import round-trip
  - `tests/test_api_activities_retry.py` 覆盖 retry-failed 的 scoped/global/soft-deleted/no-match
  - `tests/test_api_dashboard_pending.py` 覆盖 orphan 过滤、legacy+new 求和、date 范围、sentinel key、publisher 要求、empty key
  - `tests/test_url_helper.py` 调整后加了 3 条「保留 bare host 不动」的 case
  - `tests/test_install_real.py` / `test_install_sh.py` 升级为校验 llm_engines 表而不是 `builtin.key` 文件
  - `tests/test_phase_p_protocol.py` 改为从 llm_engines 表读，不再 mock `load_builtin_llm_config`
- 全量 652 passed / 15 skipped。

### 升级注意
- 有 DB / 安装脚本改动（写 `llm_engines` 表），但向前兼容：现有引擎记录不动，仅 builtin 那条会被 install.sh 重新 upsert 一次。
- 用户已经写过配置的 `llm_engines` 不受 builtin upsert 影响（is_default 会被转移给 builtin 那条；如果你有自己的主引擎，升级后去 Settings 改回默认即可）。
- 升级到 0.7.4 后，`~/.auto_daily_log/builtin.key` 文件不再被任何代码读写，可以自己手动删掉（不删也无害）。

---

## [0.7.3] — 2026-04-20

Hotfix：再修两条 updater 上的 UX 问题。

### Fixed
- **刷新页面永远显示"升级失败"**：`updater/state.py` 的 `read_status()` 没有终态清理逻辑，`update_status.json` 里 `phase="failed"` 的记录一旦写入就永久留盘。`/api/updates/status` 每次轮询都返同一条。改：`read_status` 对 `completed`/`failed` 终态加 1 小时 TTL，超过就删文件 + 返回 idle。在制态（installing 等）不清，保留"卡在半路"的信号。
- **外部升级后点"升级"被 409**：`updater/version_check.py` 的 `_read_cache()` 只校验 24h TTL，不比运行时 `__version__` 与缓存里的 `current` 是否一致。手动 `git pull + pip install` 或用 `/rollback` 切过版本后，UI 仍基于旧缓存显示"可升级"，点下去 `install_update` 发现 `target == __version__` 直接 409。改：缓存中的 `current` 与运行时 `__version__` 不一致就当缓存失效，重拉 GitHub。

### 测试
- 新增 6 个回归 case：4 个覆盖 state 终态 TTL 的各分支（stale failed/completed → idle；fresh terminal 保留；in-progress 不动），2 个覆盖 version_check drift（版本漂 → 缓存失效；版本一致 → 走缓存）。

### 升级注意
- 纯运行时修复，无 DB/UI/API schema 变更。
- 升级后下一次 `read_status` / `check()` 调用就会自动应用新逻辑；旧的 `update_status.json` / `update_check.json` 会被自然替换或清除，无需手动删文件。

---

## [0.7.2] — 2026-04-20

Hotfix：修"自动更新"在 uv 创建的虚拟环境下失败。

### Fixed
- **Updater 在 uv venv 里 `No module named pip` 而失败**：`uv venv .venv` 创建的环境默认不装 pip，updater 硬编码 `python -m pip install ...` 直接报错、触发回滚。
  - 改为运行时探测链：`python -m pip` → `python -m ensurepip --upgrade` → `uv pip install --python <python>` → 报错提示手工修复命令。
  - 覆盖场景：标准 python venv（走 pip）、uv venv 但保留了 ensurepip（走 ensurepip 自举）、纯 uv venv（走 uv CLI）、都没有（返回 127 + 日志写明修复方法，触发干净回滚）。
- 新增 5 个测试覆盖 4 条分支 + 错误日志路径（`tests/test_updater_runner.py`）。

### 升级注意
- 纯运行时修复，无 API / 数据库改动。
- 如果你用的是 `python -m venv` 创建的 `.venv`（installer 默认路径），升级无需任何额外操作。
- 如果你之前手工跑过 `uv venv`，升级后"自动更新"就能正常用；无需再 `uv pip install pip`。

---

## [0.7.1] — 2026-04-20

Hotfix：修 Linux 用户的"定时任务不跑"回归。

### Fixed
- **`time_scopes.daily.schedule_rule` 被意外 NULL 导致 scheduler 零 job 注册**：旧版迁移用"整表为空才迁"的条件，用户通过中间版本升级时可能留下 `time_scopes.daily` 行但 `schedule_rule` 为 NULL，从此再也不触发迁移。scheduler 的 `WHERE schedule_rule IS NOT NULL` 过滤掉这行 → 定时任务全部不跑。
  - 迁移改成**per-row 幂等**：每行单独检查，缺失就插入，存在但 `schedule_rule` 为 NULL 而 `summary_types` 有值就回填（保留用户自定义的时间）。
  - builtin-ensure 循环给 `daily` 带默认 `{"time":"18:00"}`。
  - 启动兜底：`daily` 仍为 NULL 时最终 UPDATE 一次默认值。
- 涉及测试：`tests/test_migration_pipeline.py` 新增 3 个回归 case 覆盖漂移修复、硬编码兜底、manual 类型保持 NULL。

### 升级注意
- 纯数据库层修复，无 API / UI 改动。
- 升级后首次启动会自动检测并修复 `time_scopes.daily.schedule_rule`（日志无输出，静默修复）。
- 如果你此前手动改过 `daily` 的触发时间，修复会优先从 `summary_types` 回填，不会被 `18:00` 默认值覆盖。

---

## [0.7.0] — 2026-04-20

CoDaily 日报广场接入、MyLog 动作语义化、多实例测试工具。

### Added
- **CoDailyPublisher**：新的推送通道，把 PDL 的 summary 按 CoDaily push-contract v1.0 推到日报广场（`POST /api/v1/push`）。Settings → 输出 → 推送平台新增 "CoDaily（日报广场）" 选项，配置 URL / PDL Token / Scope 后即可自动推送。独立于 Jira/Webhook，不与现有路径冲突。
- **`./pdl test-users`**：多实例测试命令，一键起 N 个 PDL 实例（端口 8900+），可选择同时在 CoDaily DB 里创建对应用户 + PDL Token，自动复用主实例的默认 LLM 引擎配置。`setup / start / stop / status / clean` 五个子命令。
- **MyLog 生成按钮加图标 + 提示**：每日/周/月/季 chip 前缀 ✨ MagicStick 图标 + "生成<scope>" tooltip，动作语义明显。

### Fixed
- **Settings 深度链接 bug**：MyLog "+" 跳转 `/settings?tab=scopes` 时，若用户已在 /settings 页面，activeTab 不会跟着更新（因为 `URLSearchParams(window.location.search)` 只在 setup 时读一次）。改成 `useRoute().query` 响应式 + watch。

### Internal
- `.claude/` 目录加到 `.gitignore`，避免 tooling 临时状态污染提交

### 升级注意
- 无破坏性变更。新配置项均可选，已有 scope_outputs 不受影响。
- 首次使用 CoDaily 推送需先在 CoDaily 侧生成 PDL Token，再在 PDL Settings 填入。

---

## [0.6.0] — 2026-04-19

多引擎 LLM、Webhook 推送、季报、文档全面升级。

### Added
- **多引擎 LLM + per-output 路由**：不同输出可使用不同 LLM 引擎（Kimi/OpenAI/Claude/自定义），砍掉 OTHER 分类桶
- **Webhook 推送**：支持企业微信（markdown 格式）、飞书、Slack、通用 JSON，Settings 新增消息格式选择器
- **季报 + scope 去重**：新增 quarterly 周期，同周期重复生成自动覆盖
- **使用指南**：新增 `docs/usage-guide.md`，含全页面截图（demo 数据）和详细操作说明
- **README 重写**：功能表格 + Dashboard 截图 + 核心功能说明，中英文同步

### Changed
- **推送方式语义**："生成后自动推送"改为"定时生成后自动推送"——手动生成不再触发自动推送，仅 scheduler 定时生成时触发
- **推送逻辑统一**：手动推送和自动推送合并为 `_publish_summary()` 单一实现

### Fixed
- **Webhook errcode 误判**：企微/飞书 API 返回 HTTP 200 但 body 含 errcode，现在正确检测并报错
- **single 模式推送失败**：无 issue_key 的日志（原汁原味日志等）推送时被 issue_key 校验拦截，现在仅 Jira 推送才校验
- **stream-only 代理兼容**：LLM generate() 统一用 stream 模式，兼容只支持 streaming 的代理
- **pdl build release 早退**：release 模式下 `pdl build` 不再尝试前端构建，直接提示 `pdl server start`
- **scope 排序**：scope 列表按 day→week→month→quarter 排序
- **0h 隐藏**：summary 无 time_spent_sec 时不显示 "0h"

---

## [0.5.6] — 2026-04-18

安装脚本最终稳定 + 前端修复。

### Fixed
- **install.sh set -e 杀脚本**：`[[ false ]] && cmd` 和 `[[ -z non-empty ]] && cmd` 在 set -e 下返回 1 直接退出。选 server/collector 必崩，tty_read 读到输入也崩。全部改为 if/then
- **MyLogs "全部" 显示空**：切到全部时 selectedDate 没清空，API 带着今天日期查 → 0 条
- **scopeLabel 乱码**：monthly 的 emoji 被编码损坏（📅→◆◆），改为纯文本"每月"
- **collector.yaml 生成静默失败**：去掉 2>/dev/null，加文件存在验证

---

## [0.5.5] — 2026-04-17

Hotfix: `curl | bash` 交互 prompt 修复。

### Fixed
- **bootstrap.sh**: 用 `bash install.sh < /dev/tty` 把终端接给 install.sh，解决 `curl | bash` 下用户键入无响应
- **install.sh tty_read**: 优先检查 `[[ -t 0 ]]`（stdin 已是 tty），再 fallback `< /dev/tty`，再 fallback 默认值

### Added
- **真实安装测试** (`test_install_real.py`, @slow): 从零装 server/both/collector + 升级保留 settings/activities/worklogs/config，共 9 个用例，真 wheel 真 pip
- **测试覆盖矩阵** (`docs/test-coverage.md`): 全场景清单 + 新功能测试要求写入 AGENTS.md

---

## [0.5.4] — 2026-04-17

安装全流程修复（server/both/collector 三角色验证通过）。

### Fixed
- **缺 packaging 依赖**：updater 模块 import packaging 但 pyproject.toml 未声明 → 新装用户 server 启动秒退
- **tty_read stderr 噪音**：管道环境下 /dev/tty 不可用时打印 "Device not configured"，已 2>/dev/null 抑制
- **定时任务表名错误**：scheduler 查 time_scopes 表但实际叫 summary_types → 定时任务不注册
- **Settings UI 触发时间无效**：scheduler 不读 settings 表的 scheduler_trigger_time → 用户改时间没用；已加 settings override
- **Settings 保存按钮**：无改动时灰化不可点；有未保存改动时浏览器/路由切换弹确认

---

## [0.5.3] — 2026-04-17

Hotfix: Windows 安装崩溃 + CI 修复。

### Fixed
- **PowerShell 窗口闪退**：install.ps1 / bootstrap.ps1 用 try/catch/finally 包裹 Main，异常时显示错误信息 + "Press Enter to exit" 保持窗口
- **Windows CI 32 个 bash 测试误跑**：test_install_sh.py 加 skipif Windows，bash 测试只在 macOS/Linux 跑

---

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
