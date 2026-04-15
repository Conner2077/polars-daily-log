# Auto Daily Log — 项目核心原则

> 本文面向参与本项目的 AI 助手（Claude / Cursor / Codex / Copilot CLI / Gemini CLI 等）与开发者。所有设计决策、prompt 改动、UI 流程改动都应遵守这些原则。

---

## 🎯 核心原则：原汁原味记录，二次加工按需输出

**每天总结的内容要原汁原味，后续根据目的进行二次加工。**

这是本项目最重要的设计准则，适用于所有数据流。

### 为什么

- **原始记录是唯一真相源**。一旦在采集/总结环节做了筛选，被丢弃的信息就永远找不回来了。
- **不同用途需要不同加工方式**：提交 Jira 要筛掉娱乐活动，写周报要按主题聚合，回顾反思要看完整时间线。如果源头就是"清洗过"的数据，各种下游场景都只能二次凑合。
- **用户信任需要完整记录**。用户事后想查"我那天到底干了什么"时，看到的应该是事实，不是被 AI 预判过的版本。

### 具体含义

| 数据层 | 应该做 | 不应该做 |
|--------|--------|----------|
| **Monitor 采集** | 记录所有前台活动：app、窗口标题、URL、OCR | 丢弃"看起来没意义"的活动 |
| **每日总结（SUMMARIZE_PROMPT）** | 如实汇总当天所有活动，包括看视频、闲聊、摸鱼 | 只挑工作相关的写进去 |
| **自动审批（AUTO_APPROVE_PROMPT）** | 根据"是否适合提交 Jira"判断，必要时标注原因 | 删改已经生成的日志 |
| **周报/月报（PERIOD_SUMMARY_PROMPT）** | 从完整日志里按主题二次聚合 | 假设每日日志已经筛过 |
| **Jira 提交** | 用自动审批筛过的版本提交 | 直接把原始日志推给 Jira |
| **回收站** | 软删除保留 N 天才永久清理 | 直接硬删除 |

### 生命周期体现

```
采集所有活动 (原始层)
    ↓
每日总结：如实记录 (完整层)
    ↓
    ├──→ 自动审批：筛出工作相关 ──→ 提交 Jira (合规层)
    ├──→ 周报/月报：按主题聚合 ──→ 归档 (分析层)
    └──→ 历史回看：原文呈现 (反思层)
```

每一层的责任都不同，但**底层永远保留最原始的数据**。

### 对开发者/AI 的指导

- 改 prompt 时，**"每日总结"prompt 偏保守**（记录为主），**下游用途的 prompt 自行筛选**
- 加新功能时，优先从已有的完整日志里取数据，而不是要求 Monitor 采集新维度
- 当某个功能需要"筛过的数据"，不要回头改 Monitor 或 SUMMARIZE_PROMPT，而是在该功能的 prompt 里做筛选
- 数据删除用软删除（回收站/归档），硬删除仅限最终清理环节

---

## 其他约定（次要）

### 跨平台（两层结构，别搞混）

Server 核心必须跨平台，不含任何平台专属系统调用。平台代码分两层：

- **底层** `auto_daily_log/monitor/`：raw OS API 封装（AppleScript / xdotool / Atspi / gdbus / screencapture 等），实现 `PlatformAPI` 接口
- **Adapter 层** `auto_daily_log_collector/platforms/`：包装底层 + 声明 `capabilities()` + 向 `factory.py` 注册，实现 `PlatformAdapter` 契约

**加新平台 = 两层必须一起动**。只加底层不做 adapter，分布式 collector 用户拿不到；只做 adapter 不动底层，没有 raw API 可调。

详见 `auto_daily_log_collector/DEVELOPMENT.md` §1 架构图。

Collector 与 Server 之间通过 HTTP 协议通信，任意平台都能做 Collector。

### 安装与环境

- 首次运行**必须**跑 `bash install.sh`，不要直接 `pip install .` 或 `pip install -r requirements.txt`
- `install.sh` 第 8 步 verify 会逐项 import 核心依赖（`aiosqlite` / `sqlite_vec` / FastAPI 等），任何一项缺失立即打印修复命令
- 服务器如果报 `No module named aiosqlite` 之类的 ImportError，**不要**改 `database.py` 去绕开，正确做法是 `pip install -e ".[linux|macos|windows]"`

### 测试规范

- 所有测试必须用**精确值断言**（`assert x == 'expected'`）
- 严禁 `assert x` / `assert len(x) > 0` 之类的笼统断言
- 参考 `auto_daily_log_collector/DEVELOPMENT.md` 的测试章节

### UI 一致性

- 删除/破坏性操作统一用 `class="danger-btn"`（软色调），不可逆操作用 `danger-btn-strong`
- 确认弹窗用 Element Plus 的 `el-popconfirm`，Toast 用 `ElMessage`（1.5s 自动消失 + 点击关闭）
- 页面顶部只放"这个页面干什么"的一句话，工具栏/过滤器放在页面次级位置

### 隐私

- `monitor.privacy.blocked_apps` / `blocked_urls` 必须严格尊重
- 反侦测 app（如企业微信）要在 `hostile_apps_applescript` 配置里避开深度 introspection（`_HOSTILE_APPS` 白名单）
- OCR 中若出现敏感信息（密码、银行卡），用户删除后回收站也要删干净
