# Auto Daily Log - Design Spec

> 自动日志记录工具：采集桌面活动 + Git commits，LLM 按 Jira issue 汇总，审批后写入 Jira worklog。

## 1. 项目概述

### 1.1 目标

一站式自动工作日志工具，替代手动填写 Jira 工时日志。整合桌面活动采集、Git commit 读取、LLM 智能汇总，通过 Web UI 审批后自动写入 Jira Server worklog。

### 1.2 核心流程

```
monitor（后台持续采集活动 + OCR）→ SQLite
collector（读取当天 Git commits）→ SQLite
          │
      定时触发（默认 18:00，可配置）
          │
summarizer（LLM 按 Jira issue 汇总工时 + 生成摘要）→ worklog_drafts（pending_review）
          │
      用户 Web UI 审批/编辑
          │
jira_client（写入 Jira worklog）→ audit_logs（永久记录）
```

### 1.3 技术栈

- **后端：** Python 3.9+ / FastAPI / SQLite / APScheduler
- **前端：** Vue.js SPA，由 FastAPI 托管静态文件
- **LLM：** 默认 Kimi (Moonshot API)，可配置 OpenAI / Ollama / Claude
- **Jira：** Jira Server / Data Center REST API v2，PAT 认证
- **跨平台：** macOS / Windows / Linux

## 2. 项目结构

```
auto_daily_log/
├── monitor/            # 活动采集（整合自 polars_free_worklog）
│   ├── capture.py      # 前台应用/窗口标题/浏览器URL采集
│   ├── classifier.py   # 活动分类（coding/meeting/communication/...）
│   ├── screenshot.py   # 截图采集
│   ├── ocr.py          # OCR 引擎（按平台自动选择）
│   └── platforms/      # 平台适配层
│       ├── macos.py
│       ├── windows.py
│       └── linux.py
├── collector/          # Git commit 读取
│   └── git_collector.py
├── summarizer/         # LLM 汇总
│   ├── engine.py       # LLM 引擎抽象层
│   ├── kimi.py         # Kimi (Moonshot) 适配
│   ├── openai.py       # OpenAI 适配
│   ├── ollama.py       # Ollama 适配
│   ├── claude.py       # Claude 适配
│   └── prompt.py       # Prompt 模板管理
├── jira_client/        # Jira API 交互
│   └── client.py
├── scheduler/          # 定时任务
│   └── jobs.py
├── web/
│   ├── api/            # FastAPI 路由
│   │   ├── activities.py
│   │   ├── worklogs.py
│   │   ├── issues.py
│   │   ├── settings.py
│   │   └── dashboard.py
│   └── frontend/       # Vue.js SPA
│       ├── views/
│       │   ├── Dashboard.vue
│       │   ├── Worklog.vue
│       │   ├── Issues.vue
│       │   └── Settings.vue
│       └── components/
├── models/             # 数据模型
│   ├── database.py     # SQLite 连接管理
│   └── schemas.py      # Pydantic schemas
├── config.yaml         # 初始配置文件
├── app.py              # 应用入口
└── requirements.txt
```

## 3. 数据模型（SQLite）

### 3.1 activities - 活动采集记录

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| timestamp | TEXT | ISO 8601 时间戳 |
| app_name | TEXT | 前台应用名 |
| window_title | TEXT | 窗口标题 |
| category | TEXT | 分类：coding/meeting/communication/design/writing/reading/research/browsing/other |
| confidence | REAL | 分类置信度 0-1 |
| url | TEXT (nullable) | 浏览器 URL |
| signals | TEXT (JSON) | 扩展信号：browser_url, wecom_group_name, screenshot_path, ocr_text, hints |
| duration_sec | INTEGER | 采样间隔（秒） |

索引：`idx_activities_timestamp` on `timestamp`

### 3.2 git_repos - Git 仓库配置

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| path | TEXT | 仓库本地路径 |
| author_email | TEXT | 过滤用的 author email |
| is_active | BOOLEAN | 是否启用 |
| created_at | TEXT | 创建时间 |

### 3.3 git_commits - 当天 Git 提交缓存

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| repo_id | INTEGER FK | 关联 git_repos |
| hash | TEXT | commit hash |
| message | TEXT | commit message |
| author | TEXT | 作者 |
| committed_at | TEXT | 提交时间 |
| files_changed | TEXT (JSON) | 变更文件列表 |
| insertions | INTEGER | 新增行数 |
| deletions | INTEGER | 删除行数 |
| date | TEXT | 日期 YYYY-MM-DD |

索引：`idx_git_commits_date` on `date`

### 3.4 jira_issues - 活跃 Jira 任务

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| issue_key | TEXT UNIQUE | 如 PROJ-101 |
| summary | TEXT | issue 标题（从 Jira 拉取） |
| description | TEXT | issue 描述（帮助 LLM 匹配） |
| is_active | BOOLEAN | 是否参与当日匹配 |
| created_at | TEXT | 创建时间 |

### 3.5 worklog_drafts - 工作日志草稿

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| date | TEXT | 日期 YYYY-MM-DD |
| issue_key | TEXT | 关联的 Jira issue |
| time_spent_sec | INTEGER | 工时（秒） |
| summary | TEXT | LLM 生成的工作摘要 |
| raw_activities | TEXT (JSON) | 关联的 activity ID 列表 |
| raw_commits | TEXT (JSON) | 关联的 git_commit ID 列表 |
| status | TEXT | pending_review / approved / auto_approved / submitted / rejected / auto_rejected |
| user_edited | BOOLEAN | 用户是否手动修改过 |
| jira_worklog_id | TEXT (nullable) | Jira 返回的 worklog ID |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

索引：`idx_drafts_date_status` on `(date, status)`

### 3.6 audit_logs - 审计日志

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| draft_id | INTEGER FK | 关联 worklog_drafts |
| action | TEXT | created / edited / approved / submitted / rejected |
| before_snapshot | TEXT (JSON) | 变更前内容 |
| after_snapshot | TEXT (JSON) | 变更后内容 |
| jira_response | TEXT (JSON, nullable) | Jira API 返回（仅 submitted） |
| created_at | TEXT | 操作时间 |

### 3.7 settings - 系统配置

| 字段 | 类型 | 说明 |
|------|------|------|
| key | TEXT PK | 配置键 |
| value | TEXT (JSON) | 配置值 |
| updated_at | TEXT | 更新时间 |

所有可配置项（采集/LLM/Jira/Prompt/定时等）存储在此表，Web UI 直接读写。

## 4. 模块设计

### 4.1 Monitor - 活动采集

**整合自 polars_free_worklog 的核心能力。**

#### 采集内容

| 能力 | macOS | Windows | Linux |
|------|-------|---------|-------|
| 前台应用名 | osascript | PowerShell | xdotool |
| 窗口标题 | osascript | PowerShell | xdotool |
| 浏览器 URL | AppleScript per browser | 读窗口标题 | 读窗口标题 |
| 企微/微信群名 | AppleScript | - | - |
| 截图 | screencapture | PowerShell | scrot / gnome-screenshot |
| OCR | Vision framework (pyobjc) | Windows.Media.Ocr (winocr) | Tesseract + chi_sim |

#### 采集策略

- 默认每 **30 秒** 采样一次（可配置）
- 相邻采样 app_name + window_title 相同时，合并为一条，累加 duration_sec
- 自动分类（复用 polars_free_worklog 的 classifier 逻辑）
- **OCR 默认开启**，用户可在 Web UI 关闭
- 隐私保护：app/URL 黑名单命中时，不记录、不截图、不 OCR
- 截图按天归档，可配置保留天数（默认 7 天自动清理）

#### OCR 引擎选择

```
ocr_engine: auto（默认）
  macOS   → Vision framework (pyobjc)   — 中文识别好，无需额外安装
  Windows → Windows.Media.Ocr (winocr)  — 系统自带，中文支持好
  Linux   → Tesseract + chi_sim         — 需安装 tesseract-ocr

可手动指定：auto | vision | winocr | tesseract
```

### 4.2 Collector - Git Commit 读取

- 用户在 Web UI 配置本地仓库路径列表（支持多个）
- 每个仓库配置 author_email 过滤，只取自己的 commit
- 定时任务触发时，读取当天所有仓库的 commit
- 使用 `git log --after="YYYY-MM-DD 00:00" --before="YYYY-MM-DD 23:59" --author=email --format=...`
- 提取：hash、message、files_changed、insertions、deletions
- 缓存到 git_commits 表

### 4.3 Summarizer - LLM 汇总

#### LLM 引擎

抽象层设计，所有引擎实现统一接口：

```python
class LLMEngine:
    async def generate(self, prompt: str) -> str: ...
```

支持引擎：
- **Kimi (Moonshot)**：默认，`https://api.moonshot.cn/v1`
- **OpenAI**：兼容 OpenAI API 格式
- **Ollama**：本地部署
- **Claude**：Anthropic API

#### Prompt 模板

默认 Prompt 存储在 settings 表，用户可在 Web UI 编辑：

```
你是工作日志助手。以下是用户今天的工作数据：

【活跃 Jira 任务】
{jira_issues}

【Git Commits】
{git_commits}

【活动记录】
{activities}

请为每个 Jira 任务生成：
1. 工时（小时，精确到 0.5h）
2. 工作日志摘要（中文，50-100字，描述具体做了什么）

无法匹配到任何 Jira 任务的活动，归入"未分类"。

以 JSON 格式返回：
[
  {
    "issue_key": "PROJ-101",
    "time_spent_hours": 3.5,
    "summary": "..."
  }
]
```

模板支持变量占位符：`{jira_issues}`, `{git_commits}`, `{activities}`, `{date}`

#### 匹配策略

LLM 根据以下信息推断活动归属：
1. Git commit 改的文件 + issue 描述关键词
2. 活动中的窗口标题/URL 与 issue 关联
3. OCR 文本中的关键词
4. 无法匹配的归入"未分类"，用户审批时手动分配

### 4.4 Jira Client

#### 认证

- Jira Server / Data Center REST API v2
- 认证方式：Personal Access Token (PAT)
- Header: `Authorization: Bearer <PAT>`

#### Worklog 写入

```
POST /rest/api/2/issue/{issueKey}/worklog

{
  "timeSpentSeconds": 12600,
  "started": "2026-04-12T09:00:00.000+0800",
  "comment": "工作日志摘要内容"
}
```

#### Issue 信息拉取

添加 issue 时自动拉取标题和描述：

```
GET /rest/api/2/issue/{issueKey}?fields=summary,description
```

### 4.5 Scheduler - 定时任务

- 使用 APScheduler 实现
- 默认任务：每天 18:00 触发汇总流程
- 触发时间可在 Web UI 配置
- 支持开关控制
- 汇总流程：Collector 读取 Git commits → Summarizer 生成草稿 → 通知用户审批

### 4.6 Web UI

#### 页面结构

**Dashboard（首页）**
- 今日活动时间线概览
- 待审批日志提醒（数量 badge）
- 今日已记录工时汇总
- 采集状态指示（运行中/已暂停）

**Worklog（工作日志）**
- 按日期查看，默认今天
- 每个 issue 一张卡片：
  - issue key + 标题
  - 工时（可编辑）
  - 摘要内容（富文本编辑）
  - 关联的 git commits 和活动记录（可展开查看）
- 操作按钮：
  - 编辑 → 记录 audit_log
  - 通过 → 状态改为 approved
  - 驳回 → 状态改为 rejected，可重新生成
  - 一键全部通过
  - 确认提交到 Jira → 调 API，状态改为 submitted
- 底部：当天活动时间线（帮助判断工时是否合理）
- 历史标签页：查看已提交的日志和审计轨迹

**Issues（Jira 任务管理）**
- 添加 issue（输入 key，自动拉取标题描述）
- 启用/停用 issue
- 删除 issue
- 显示每个 issue 的最近工时记录

**Settings（设置）**
- 采集配置：采样间隔、OCR 开关/引擎、截图保留天数、隐私黑名单
- Git 仓库：路径列表、author email
- Jira 连接：Server 地址、PAT、连接测试
- LLM 配置：引擎选择、API Key、端点地址、模型名称
- Prompt 编辑器：支持变量高亮的文本编辑器
- 定时任务：触发时间、开关
- 自动审批：开关、超时时间、审批 Prompt 编辑器
- 系统：服务端口、语言、数据保留策略

## 5. 审批流程

```
定时触发 (18:00)
    │
    ▼
LLM 生成草稿 → worklog_drafts (status: pending_review)
    │                          → audit_logs (action: created)
    ▼
Web UI 显示待审批通知，启动自动审批倒计时
    │
    ├── 用户在超时前操作 ──┬── 编辑摘要/工时  → audit_logs (action: edited, before/after snapshot)
    │                      ├── 通过           → status: approved, audit_logs (action: approved)
    │                      ├── 驳回           → status: rejected, audit_logs (action: rejected)
    │                      │                    可手动触发重新生成
    │                      └── 一键全部通过   → 批量 approved
    │
    └── 超时无人操作（默认 30 分钟，可配置）
        │
        ▼
      LLM 自动审批：调用 LLM 评估草稿质量
        ├── 通过 → status: auto_approved, audit_logs (action: auto_approved, llm_review)
        └── 不通过 → 保持 pending_review, audit_logs (action: auto_rejected, llm_review)
                     等待用户手动处理
    │
    ▼
确认提交到 Jira（approved 或 auto_approved）
    │
    ▼
调用 Jira API 写入 worklog
    │
    ├── 成功 → status: submitted, 记录 jira_worklog_id
    │         → audit_logs (action: submitted, jira_response)
    └── 失败 → 保持 approved, 显示错误信息, 可重试
```

### 5.1 自动审批配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| auto_approve_enabled | true | 是否启用自动审批 |
| auto_approve_timeout_min | 30 | 超时时间（分钟） |
| auto_approve_prompt | (见下方) | LLM 审批 Prompt，可在 Web UI 编辑 |

**自动审批 Prompt 模板（默认）：**

```
你是工作日志审批助手。请检查以下工作日志草稿：

【日期】{date}
【Jira 任务】{issue_key}: {issue_summary}
【工时】{time_spent_hours} 小时
【日志内容】{summary}
【关联 Git Commits】{git_commits}

请判断：
1. 日志内容是否与 Git commits 和任务描述一致？
2. 工时是否合理？
3. 日志描述是否清晰、具体？

如果合格返回 {"approved": true}
如果不合格返回 {"approved": false, "reason": "不通过原因"}
```

## 6. 配置文件（config.yaml 初始模板）

```yaml
server:
  port: 8080
  host: "0.0.0.0"

monitor:
  interval_sec: 30
  ocr_enabled: true
  ocr_engine: auto  # auto | vision | winocr | tesseract
  screenshot_retention_days: 7
  privacy:
    blocked_apps: []
    blocked_urls: []

git:
  repos: []
  # - path: /path/to/repo
  #   author_email: user@example.com

jira:
  server_url: ""
  pat: ""

llm:
  engine: kimi  # kimi | openai | ollama | claude
  kimi:
    api_key: ""
    model: "moonshot-v1-8k"
    base_url: "https://api.moonshot.cn/v1"
  openai:
    api_key: ""
    model: "gpt-4o"
    base_url: "https://api.openai.com/v1"
  ollama:
    model: "llama3"
    base_url: "http://localhost:11434"
  claude:
    api_key: ""
    model: "claude-sonnet-4-20250514"

scheduler:
  enabled: true
  trigger_time: "18:00"

auto_approve:
  enabled: true
  timeout_min: 30

system:
  language: "zh"
  data_retention_days: 90
```

## 7. 启动方式

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务（采集 + Web UI + 定时任务 一起启动）
python -m auto_daily_log

# 或指定配置文件
python -m auto_daily_log --config /path/to/config.yaml

# 浏览器访问
open http://localhost:8080
```

## 8. 非功能需求

- **隐私：** 所有数据本地存储，截图不上传。LLM API 调用时发送聚合后的活动摘要和 OCR 文本片段（用于 issue 匹配），不发送截图文件。隐私黑名单命中的内容不会出现在 LLM 请求中。
- **安全：** Jira PAT 和 LLM API Key 加密存储，Web UI 仅监听本地（可配置）
- **性能：** 采集模块低 CPU 占用，OCR 异步处理不阻塞采集
- **容错：** Jira API 失败可重试，LLM 失败保留原始数据可手动填写
