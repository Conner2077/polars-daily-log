# Changelog

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
