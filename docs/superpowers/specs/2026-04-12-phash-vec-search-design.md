# pHash 截图去重 + sqlite-vec 向量搜索 Design Spec

> 在现有 auto_daily_log 项目上增加两个功能：pHash 截图去重减少 OCR 开销，sqlite-vec 向量搜索支持语义检索历史活动和日志。同时增加空闲检测，区分"专注工作"和"人不在"。

## 1. pHash 截图去重

### 1.1 原理

每次截图后计算感知哈希（perceptual hash），与上一张截图的 pHash 比较汉明距离。距离小于阈值说明截图内容相似，跳过 OCR 处理，删除截图文件。

### 1.2 改动位置

`auto_daily_log/monitor/service.py` 的 `_capture_raw` 方法。

### 1.3 流程

```
截图完成
  │
  ├── 计算 pHash
  ├── 与 self._last_phash 比较汉明距离
  │
  ├── 距离 ≤ threshold → 截图相似
  │     ├── 跳过 OCR
  │     ├── 删除截图文件（节省磁盘）
  │     └── ocr_text 复用上一次的值
  │
  └── 距离 > threshold → 截图不同
        ├── 正常 OCR
        ├── 更新 self._last_phash
        └── 更新 self._last_ocr_text
```

注意：pHash 去重只影响 OCR 是否执行，**活动记录本身照常写入 DB**，不中断采集。

### 1.4 新增依赖

- `imagehash>=4.3.0`
- `Pillow>=10.0`（已在 linux optional-deps，提升为核心依赖）

### 1.5 配置项

| Key | 默认值 | 说明 |
|-----|--------|------|
| `monitor.phash_enabled` | `true` | 是否启用 pHash 去重 |
| `monitor.phash_threshold` | `10` | 汉明距离阈值（0-64，越小越严格） |

### 1.6 新增文件

- `auto_daily_log/monitor/phash.py` — pHash 计算和比较工具函数

## 2. 空闲检测

### 2.1 原理

读取操作系统的键鼠空闲时长，超过阈值标记为 idle。

### 2.2 平台实现

| 平台 | 方法 |
|------|------|
| macOS | `ioreg -c IOHIDSystem` 读取 HIDIdleTime（纳秒） |
| Windows | PowerShell 调用 `GetLastInputInfo` |
| Linux | `xprintidle`（毫秒） |

### 2.3 对采集的影响

```
每次采样时
  │
  ├── 检查系统空闲时长
  │
  ├── 空闲 ≤ 阈值 (180s) → active
  │     ├── 正常截图 + pHash 判断 + OCR
  │     └── 正常分类和记录
  │
  └── 空闲 > 阈值 → idle
        ├── 不截图、不 OCR
        ├── category = "idle"
        └── 连续 idle 合并为一条，累加 duration_sec
```

### 2.4 配置项

| Key | 默认值 | 说明 |
|-----|--------|------|
| `monitor.idle_threshold_sec` | `180` | 空闲阈值（秒），默认 3 分钟 |

### 2.5 新增文件

- `auto_daily_log/monitor/idle.py` — 跨平台空闲时长检测

### 2.6 改动位置

- `auto_daily_log/monitor/platforms/base.py` — PlatformAPI 新增 `get_idle_seconds()` 方法
- `auto_daily_log/monitor/platforms/macos.py` / `windows.py` / `linux.py` — 各平台实现
- `auto_daily_log/monitor/service.py` — `sample_once` 增加空闲判断

## 3. sqlite-vec 向量搜索

### 3.1 新增依赖

- `sqlite-vec>=0.1.0`

### 3.2 数据库变更

在 `database.py` 的 schema 中新增 embeddings 虚拟表：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS embeddings USING vec0(
    source_type TEXT,
    source_id INTEGER,
    text_content TEXT,
    embedding FLOAT[1536]
);
```

注意：`vec0` 虚拟表需要先加载 sqlite-vec 扩展，在 `Database.initialize()` 中执行 `conn.enable_load_extension(True)` 后加载。

### 3.3 Embedding 生成

#### 引擎抽象

在 `auto_daily_log/summarizer/` 下新增 embedding 能力：

```python
class EmbeddingEngine(ABC):
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
```

#### 各引擎的 Embedding API

| 引擎 | API | 默认模型 |
|------|-----|----------|
| Kimi | `POST /v1/embeddings` | `moonshot-v1-embedding` |
| OpenAI | `POST /v1/embeddings` | `text-embedding-3-small` |
| Ollama | `POST /api/embeddings` | `nomic-embed-text` |
| Claude | 不支持 embedding，fallback 到 Ollama 或报错 |

#### Embedding 时机

| 数据源 | 文本内容 | 时机 |
|--------|----------|------|
| activities | OCR 文本，或拼接 `app_name + window_title + url` | 写入 activities 后异步生成 |
| git_commits | `message + files_changed` | GitCollector 采集完成后 |
| worklog_drafts | `issue_key + summary` | 草稿提交到 Jira 后 |

#### Embedding 服务

新建 `auto_daily_log/search/` 模块：

```
auto_daily_log/search/
├── __init__.py
├── embedding.py      # EmbeddingEngine 抽象 + 各引擎适配
├── indexer.py        # 从 DB 读取记录 → 生成 embedding → 写入 vec0 表
└── searcher.py       # 接收查询 → 生成 query embedding → vec0 相似度搜索
```

### 3.4 搜索 API

```
GET /api/search?q=SQL解析器&limit=20&source_type=activity

Response:
[
  {
    "source_type": "activity",
    "source_id": 123,
    "text_content": "IntelliJ IDEA - AstToPlanConverter.java ...",
    "distance": 0.15,
    "timestamp": "2026-04-12T10:00:00"
  }
]
```

参数：
- `q` (required) — 搜索查询文本
- `limit` (optional, default 20) — 返回结果数
- `source_type` (optional) — 过滤：`activity` / `git_commit` / `worklog`

### 3.5 前端

在 Dashboard 页面顶部加一个全局搜索框：

- 输入框 + 搜索按钮
- 结果以列表形式展示，每条显示：类型图标、文本摘要、时间、相似度
- 点击结果跳转到对应页面（Activity 详情 / Worklog 等）

### 3.6 配置项

| Key | 默认值 | 说明 |
|-----|--------|------|
| `embedding.enabled` | `true` | 是否启用向量索引 |
| `embedding.model` | 跟随 LLM 引擎 | Embedding 模型名 |
| `embedding.dimensions` | `1536` | 向量维度 |

## 4. 配置变更汇总

`config.yaml` 新增：

```yaml
monitor:
  # ...existing...
  phash_enabled: true
  phash_threshold: 10
  idle_threshold_sec: 180

embedding:
  enabled: true
  model: ""           # 空则跟随 LLM 引擎默认值
  dimensions: 1536
```

## 5. 新增/改动文件汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `auto_daily_log/monitor/phash.py` | 新建 | pHash 计算和比较 |
| `auto_daily_log/monitor/idle.py` | 新建 | 跨平台空闲检测 |
| `auto_daily_log/monitor/platforms/base.py` | 修改 | 新增 `get_idle_seconds()` |
| `auto_daily_log/monitor/platforms/macos.py` | 修改 | 实现 idle 检测 |
| `auto_daily_log/monitor/platforms/windows.py` | 修改 | 实现 idle 检测 |
| `auto_daily_log/monitor/platforms/linux.py` | 修改 | 实现 idle 检测 |
| `auto_daily_log/monitor/service.py` | 修改 | 集成 pHash + idle |
| `auto_daily_log/search/__init__.py` | 新建 | search 模块 |
| `auto_daily_log/search/embedding.py` | 新建 | Embedding 引擎抽象 + 适配 |
| `auto_daily_log/search/indexer.py` | 新建 | Embedding 生成 + 写入 |
| `auto_daily_log/search/searcher.py` | 新建 | 向量相似度搜索 |
| `auto_daily_log/models/database.py` | 修改 | 加载 sqlite-vec 扩展 + embeddings 表 |
| `auto_daily_log/web/api/search.py` | 新建 | 搜索 API 路由 |
| `auto_daily_log/web/app.py` | 修改 | 注册 search 路由 |
| `auto_daily_log/config.py` | 修改 | 新增配置类 |
| `web/frontend/src/api/index.js` | 修改 | 新增 search API |
| `web/frontend/src/views/Dashboard.vue` | 修改 | 新增搜索框 |
| `pyproject.toml` | 修改 | 新增 imagehash, sqlite-vec 依赖 |
