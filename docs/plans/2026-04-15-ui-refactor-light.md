# UI Refactor — Light Tier（对齐 landing OpenAI 风格）

**状态**：待执行
**预计工作量**：15-25 小时（约 1 周零散）
**执行方式**：分支开发，逐页验收
**前置依赖**：`docs-site/` landing 已上线（https://conner2077.github.io/polars-daily-log/）

---

## 1. 背景 & 问题

2026-04-15 做 landing 时手画的 Dashboard SVG mock（见 `docs-site/src/pages/index.astro` 的 `.mock` svg 块）远比当前 `web/frontend/` 的实际渲染干净。Conner 原话："你手动生成的，可比咱们现在实际的好看太多了。"

现状：
- Vue 3 + Element Plus 2.13.7，5 个页面共 ~2778 LOC
- 单一 `src/styles/global.css`（440 行，其中 107 条 `!important` 战 Element Plus 默认样式）
- 默认配色是 Apple 风（`--bg #f5f5f7`、`--accent #0071e3` 蓝）
- 图表：Dashboard 用纯 `<div>` + CSS 横向条（没用图表库）
- 无 web font，无暗色模式，无响应式断点

Landing 风格基线（要对齐的目标）：
- `--bg: #ffffff` 纯白
- `--ink: #171717` 近黑
- `--ink-soft: #353740`、`--ink-muted: #6e6e80`
- `--line: #e5e5e5`
- 字体：Geist + Noto Sans SC + JetBrains Mono
- 无蓝色 accent，整体偏中性，OpenAI 官网调性

## 2. 目标

1. **5 个 Vue 页面视觉对齐 landing 风格**（Dashboard / Activities / MyLogs / Issues / Settings）
2. **Dashboard 新增动态时间轴图表**（滚动 12 小时窗口 + 60s 刷新 + 当前时间游标）
3. **不改变任何产品行为**（仅视觉层 + 新增图表组件与后端 endpoint）
4. **提供"一键切回当前 UI"的保障机制**

## 3. 非目标

- ❌ 不重写 Element Plus 或换成 shadcn-vue / Reka UI（那是将来的重档 refactor）
- ❌ 不清理现有 107 条 `!important`（那是中档 refactor）
- ❌ 不加响应式断点 / 移动端适配
- ❌ 不加暗色模式
- ❌ 不动 .vue 组件内的 JavaScript 逻辑（除 Dashboard 装 TimelineChart）
- ❌ 不改后端 API（除新增 timeline endpoint）

## 4. 架构

### 4.1 主题层分离（可回退的核心）

```
web/frontend/src/styles/
├── global.css                    ← 完全不动（当前 UI 的底线）
└── theme-minimal.css             ← 新增：覆盖 global.css 的 CSS vars + 加载 web font
```

`main.js` 入口：

```js
import "./styles/global.css";
import "./styles/theme-minimal.css";   // ← 这一行是开关，注释即回退
```

**回退机制**：注释这一行，整个 UI 秒级恢复到当前状态，应用行为不变。

### 4.2 TimelineChart 架构

```
Dashboard.vue
    │ <TimelineChart :bucket-minutes="15" :window-hours="12" />
    ▼
TimelineChart.vue（web/frontend/src/components/charts/）
    │ 挂载时 fetch + setInterval 60s refetch
    │ fetch("/api/activities/timeline?hours=12&bucket=15m")
    ▼
Backend: GET /api/activities/timeline
    │ 参数：hours（过去 N 小时）, bucket（bucket 粒度，"15m" / "5m"）
    │ SELECT timestamp, category FROM activities
    │   WHERE timestamp > now - hours AND category != 'idle'
    │ 按 bucket 聚合为 active_mins
    │ 返回 [{bucket_start, active_mins, idle_mins, top_app}, ...]
```

## 5. 设计决策（已对齐）

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | 图表技术 | 纯 SVG + Vue 组件，不引图表库 | 产品只有 1-2 个图表；ECharts 默认风格与 landing 冲突；包体积 + 离线友好 |
| 2 | 动态刷新频率 | 60 秒 | 够看到变化，数据库压力小 |
| 3 | Timeline 展示范围 | **过去 12 小时**滚动窗口 | 覆盖完整工作日，左侧旧数据淡出有"时间流过"感 |
| 4 | 回退机制 | 单 import 开关 | 不改 .vue 文件，不改 global.css，保证可逆 |
| 5 | 分支策略 | `feat/ui-refactor-light` | 不直接改 master，逐页验收后 squash merge |
| 6 | 字体加载 | Google Fonts CDN（与 landing 一致） | 未来可切自托管，但先与 landing 同源 |
| 7 | CJK fallback | `Noto Sans SC` + 系统 `PingFang SC` | Geist 不含中文，需显式 fallback |
| 8 | Accent 色 | 抛弃 Apple 蓝 `#0071e3`，用 `--ink: #171717` | OpenAI 风是近黑单色，不靠彩色 |

## 6. TimelineChart 交互细节

- **布局**：48 根柱子（12h × 4 bucket/h）占满图表宽度
- **柱子颜色**：`var(--ink)` 实色
- **空闲 bucket**：用 `opacity: 0.25` 显示（像 landing mock 的午休柱子）
- **当前 bucket**：在右边缘，带缓慢 pulse 动画（`opacity` 90%↔100% 循环 2.4s）
- **当前时间游标**：一条细垂直线 `var(--ink-muted)`，实时显示"现在"，每 60s 更新位置
- **柱子新增/消失动画**：
  - 到达新 bucket 边界（每 15 分钟整）时，整个图向左 shift 一格（CSS transform transition 0.6s ease）
  - 最左侧的旧柱子淡出（opacity 1→0）
  - 最右侧出现新空柱子（height 0→real via transition）
- **数据更新动画**：同一柱子的高度变化用 CSS `transition: height 0.4s ease`
- **Hover tooltip**：悬停显示 "09:30-09:45 · 14.3 min 活跃 · VS Code"

## 7. 验收硬指标（每页必过）

### 7.1 视觉
- 打开前先截 baseline（`docs/baselines/<page>-before.png`）
- 重构后对照 baseline，**你明确同意"不比当前差"**
- 3 分辨率都要看：1440、1920、ultrawide

### 7.2 功能（smoke test）
- 所有按钮点击有预期响应
- 所有表单可以提交
- 所有 dialog 能正常打开关闭
- 路由跳转无异常

### 7.3 边界 case（每页必测 3 个）
- **长内容**：超长 OCR（500+ 字）、超长 Markdown（2000+ 字）、超长窗口标题
- **空状态**：该页数据为 0 时渲染正常
- **错误状态**：API 500 时显示合理 fallback

### 7.4 性能
- 切页无感知卡顿
- Timeline 组件每 60s refetch 不阻塞交互

### 7.5 回退验证（每个 Phase 结束前跑一次）
- 注释 `theme-minimal.css` import → 应完全回到 baseline 截图
- 恢复 import → 应回到新风格
- 来回切 3 次，无样式残留

## 8. 分阶段实施

### Phase 1：Foundation（2-3h）
**范围**：搭骨架 + 建分支 + 截 baseline

- 建分支 `feat/ui-refactor-light`
- 逐页截图存 `docs/baselines/`（Dashboard / Activities / MyLogs / Issues / Settings + 每页 1-2 个代表性弹窗）
- 新建 `web/frontend/src/styles/theme-minimal.css`（包含 OpenAI 配色 CSS vars + Google Fonts import）
- 在 `main.js` 加 import，顺序在 global.css 之后
- 跑一次 dev，**每页只看顶部 nav + 字体是否已生效**，不深入调样式
- 验证回退开关：注释 → 看是否完全恢复

**交付**：theme-minimal.css 可开关，字体加载成功，不影响任何功能

---

### Phase 2：Dashboard + TimelineChart（4-6h）
**范围**：Dashboard 视觉对齐 SVG mock + 新增动态时间轴

2.1 **视觉对齐**（1h）
- Dashboard 当前是 3 stat cards + 活动类型占比。对照 SVG mock，调 theme-minimal.css 里卡片圆角、字号、间距
- 类型占比条改用 `var(--ink)` 单色

2.2 **后端 endpoint**（1-1.5h）
- `auto_daily_log/web/api/activities.py` 新增 `GET /api/activities/timeline`
- 参数：`hours` (int, default 12), `bucket` (str, "15m"/"5m"/"1m")
- 实现按 bucket 聚合，返回 `[{bucket_start, active_mins, idle_mins, top_app}, ...]`
- 测试：`tests/test_api_timeline.py` 含 3 个 case（基础 / 跨日 / 空库）

2.3 **TimelineChart.vue**（2-3h）
- `web/frontend/src/components/charts/TimelineChart.vue`
- Props: `hours`, `bucket-minutes`
- SVG 渲染 + 60s 轮询 + 当前时间游标 + bucket 边界滚动动画 + Hover tooltip
- Dashboard.vue 中引入：`<TimelineChart :hours="12" :bucket-minutes="15" />`

2.4 **验收**（0.5h）
- 真实数据跑满 1 小时（观察 bucket 切换、时间游标移动）
- 边界 case：活动为 0 的 bucket、跨 12h 窗口、空数据
- 回退验证

**交付**：Dashboard 视觉对齐 + 动态时间轴可用

---

### Phase 3：Activities（3-4h）
**范围**：最复杂的页面（8 列表格 + 预览对话框 + timeline 视图）

- 调 theme-minimal.css 覆盖 `.el-table`、`.el-table-column`（border、row hover、header 字体）
- 覆盖 `.el-dialog`（preview 弹窗）圆角、字号、padding
- OCR `<pre>` 用 `var(--bg-soft)` 底色 + JetBrains Mono
- Tag pill（类型标签）去掉 Element Plus 原生色，用 ink 灰度
- Timeline 视图（`.el-timeline`）connector 颜色 → `var(--line)`
- **边界 case 必测**：500+ 字 OCR、100+ 字窗口标题、多行 LLM summary

**交付**：Activities 页视觉一致 + 长内容不炸

---

### Phase 4：MyLogs（3-4h）
**范围**：Markdown 渲染、嵌套卡片、audit trail

- log-card 结构样式：卡片圆角 / 阴影 / 间距
- Markdown `:deep()` 穿透：headings / code / lists 全部继承 CSS vars
- 代码块字体 → JetBrains Mono
- 状态 pill、issue-section 间距、生成中 overlay 改成中性色
- audit trail dialog 的嵌套 el-timeline → Phase 3 同步 tweak 复用
- **边界 case**：2000+ 字 Markdown、含 code block、生成中状态、超长 full_summary

**交付**：MyLogs 所有嵌套层级一致

---

### Phase 5：Issues（1.5-2h）
**范围**：最简单的表格页

- el-table 覆盖（与 Phase 3 一致，大部分 vars 已就位）
- add-issue dialog 风格
- el-switch 颜色（active 从蓝色 → ink）
- **边界 case**：长 description、空列表

**交付**：Issues 页视觉对齐

---

### Phase 6：Settings（3-4h）
**范围**：7 tabs，大量表单 + textarea + tables

- el-tabs 下划线 / active 色
- 所有 form 组件（el-input / el-input-number / el-select / el-date-picker / el-time-picker / el-switch）
- 4 个 12 行 textarea（prompt 模板）→ 等宽字体、ink 底色、padding 加大
- collectors 表 + 动态 tag
- Jira SSO 嵌套 form
- alert banner
- **边界 case**：超长 prompt 模板、无 collector、Jira 登录失败

**交付**：Settings 全部 tabs 视觉一致

---

### Phase 7：整体走查 + 合并（1.5-2h）
**范围**：最终验证 + merge

- 并排对比：dev server 切换 theme-minimal import，看 5 个页面前后对比
- 全量 smoke test（按 README 关键路径走一遍）
- 回退演练：3 次切换无副作用
- baseline 截图对比确认每页"不差"
- 合并到 master（squash merge）
- bump 到 `0.3.0`（minor，因为没破坏变更但有明显视觉变更）
- 写 CHANGELOG
- Release workflow 自动跑，出 v0.3.0 tarball

**交付**：master 已更新，v0.3.0 已发布

---

## 9. 风险 & 缓解

| 风险 | 缓解 |
|------|------|
| Element Plus 内部 class `!important` 斗不过我们的覆盖 | 在 theme-minimal.css 里也用 `!important`（单项目作用域可接受；重档 refactor 时再清） |
| Markdown 渲染 `:deep()` 穿透失效 | MyLogs 专项测试 —— 用真实长 Markdown 验证每种元素 |
| 字体加载失败（Google Fonts 被墙等）| CJK 系统 fallback `PingFang SC` 兜底，不会完全裸；生产可切自托管 |
| Timeline 组件在切页时 interval 泄漏 | `onUnmounted` 里 `clearInterval` + fetch abort controller |
| 新视觉 Conner 看完觉得不如旧 | 每 Phase 单独 merge；不合意的 Phase 可单独 revert；最终 revert = 注释一行 import |
| 图表时间 / 时区 bug | timeline API 以 server 时区为准；前端 `new Date()` 显式处理 offset；测试 case 覆盖 |

## 10. 执行运行手册（Execution Runbook）

**并行策略**：独立且不互相修改的工作放 subagent 并行；共享文件 `theme-minimal.css` 由不同章节区块切片给不同 subagent 避免 merge 冲突。

### Wave 1（主进程同步，~15 min）
- 建分支 `feat/ui-refactor-light`
- 创建 `web/frontend/src/styles/theme-minimal.css` 骨架：`:root { ... }` 加 landing token + `@import` Google Fonts（Geist / Noto Sans SC / JetBrains Mono）
- 编辑 `main.js` 加 `import "./styles/theme-minimal.css"`（在 global.css 之后）
- `cd web/frontend && npm run dev` 后台起 dev server（port 5173）
- 肉眼验证：nav 的字体变了（Geist 代替原系统 font），整体背景从 `#f5f5f7` 变 `#ffffff`
- **注释 import** → 应完全回旧；**恢复 import** → 应回新。连切 2 次无残留

### Wave 2（3 subagent 并行，~45-60 min，见下表）

| Task | Subagent type | 输入契约 | 输出契约 |
|------|---------------|---------|---------|
| 2a Timeline API | general-purpose | FastAPI 端加 `GET /api/activities/timeline?hours=12&bucket=15m`。从 `activities` 表按 bucket 聚合 `active_mins`（非 idle）、`idle_mins`（idle）、`top_app`（出现最多的 app_name）。参数校验：`hours in [1,72]`, `bucket in ["5m","15m","1h"]`。时区按服务器 local。Python 测试 3 个 case：空库 / 正常 12h / 跨日 | `auto_daily_log/web/api/activities.py` 新增 endpoint；`tests/test_api_timeline.py` 全绿；返回 JSON shape：`{"buckets":[{"bucket_start":"2026-04-15T09:00:00","active_mins":14.3,"idle_mins":0.7,"top_app":"VS Code"},...]}` |
| 2b TimelineChart.vue | general-purpose | 新建 `web/frontend/src/components/charts/TimelineChart.vue`。Props: `hours` (default 12), `bucketMinutes` (default 15)。挂载时 fetch + `setInterval(60_000)` 刷新；卸载时 `clearInterval`。SVG 渲染 48 柱 + 当前时间游标（垂直线 + 小圆点）。Hover 柱子显示 tooltip `HH:MM-HH:MM · X.X min · top_app`。柱子颜色 `var(--ink)`，空闲 `opacity 0.25`；当前 bucket `pulse` 动画。过 bucket 边界（每 15 分钟整）整图左移一格，CSS transform 0.6s ease。新柱子高度 transition 0.4s。响应 CSS 变量（暗色扩展时零改动）| 独立可挂载的 Vue 组件；传入假 props 也能渲染 skeleton；不依赖 Element Plus |
| 2c 全局 EP 覆盖 | general-purpose | 在 `theme-minimal.css` 的**第 2 节（通用组件）**内补：`.el-button` / `.el-button--primary`（黑 primary）/ `.el-input__wrapper`（去蓝色 focus 环，改 ink-soft）/ `.el-dialog`（rounded 12px, `--ink` header）/ `.el-tag`（中性 ink-muted）/ `.el-switch` active（`--ink` 代替蓝）/ `.el-table__row hover`（`--bg-soft` 代替 EP 默认）/ `.el-timeline-item__node`（`--ink` 代替 EP 蓝）。允许用 `!important` 对抗现有 107 条（重档清）。每条覆盖列明 before/after 值 | theme-minimal.css 的通用章节填完；dev server 看 Settings 页（组件最杂）立刻肉眼变化 |

### Wave 3（主进程，~30 min）

- 拉三个 subagent 结果，解冲突（仅可能在 theme-minimal.css 多章节合并时）
- Dashboard.vue 手动 import TimelineChart：
  ```vue
  <TimelineChart :hours="12" :bucket-minutes="15" />
  ```
  放在 stat cards 下方、活动类型占比上方
- 验收 Phase 2 硬指标（§7 全套）
- **给 Conner 看 Dashboard 效果**（截图或 live dev server）
- 通过 → Wave 4；不通过 → iterate 或 revert

### Wave 4（4 subagent 并行，~60 min）

| Phase | Task | Subagent | 切片 |
|-------|------|----------|------|
| 3 | Activities 页专项 | general-purpose | theme-minimal.css **第 3 节（Activities）**：`.activities-page` scope 下调表格行高、OCR `<pre>` 样式、preview dialog 内部、timeline 视图节点、截图缩略图边框 |
| 4 | MyLogs 页专项 | general-purpose | theme-minimal.css **第 4 节（MyLogs）**：`.my-logs-page` scope 下的 `.log-card` / `.issue-section` / Markdown `:deep()` 下的 h1-h3/code/lists、audit trail、generating overlay |
| 5 | Issues 页专项 | general-purpose | theme-minimal.css **第 5 节（Issues）**：`.issues-page` scope 表头、active switch、delete popconfirm |
| 6 | Settings 页专项 | general-purpose | theme-minimal.css **第 6 节（Settings）**：`.settings-page` scope tabs、forms、textarea（JetBrains Mono）、collectors table、Jira SSO form、alert banner |

每个 subagent 收到：
- 输入：landing 截图 + 当前页截图 + 我画的 SVG mock 风格指南（颜色 / 间距 / 圆角 / 字号）
- 产出：theme-minimal.css 对应章节代码块 + 该章节的 acceptance checklist

### Wave 5（主进程，~30 min）

- 合并 4 个子章节到 theme-minimal.css
- 顺次过 5 个页面 smoke test
- 边界 case 测试（超长 OCR / Markdown / 空状态）
- 最后演练一次回退：注释 → 看旧 → 恢复 → 看新，无残留

### Wave 6（Phase 7 合并，~30 min）

- Squash merge `feat/ui-refactor-light` → master
- 写 CHANGELOG 0.3.0
- bump `pyproject.toml` + `web/frontend/package.json` 到 0.3.0
- push master + tag v0.3.0 → release workflow 自动出 tarball

### 总时长估算

**约 3-4 小时** Wave 1-6 顺序跑完（Wave 2 和 Wave 4 并行节约 ~60 min）。与原估 15-25h 相比，大幅压缩因为：
- subagent 并行 4-6 倍
- 跳过 baseline 截图（git 回退足够）
- 跳过 vitest 前端单测（视觉组件人工验收）
- 每页"专项章节"切片避免文件冲突

---

## 11. 后续（不在本 plan 范围）

本 plan 跑完后的可选接续：

- **中档 refactor**：清 107 条 `!important`，把 `global.css` + `theme-minimal.css` 合并成一个 token 驱动的系统
- **重档 refactor**：换成 shadcn-vue / Reka UI，完全重建组件层
- **Timeline 增强**：
  - 点击柱子弹出该 bucket 的活动详情
  - 类别分层（按 app 分组的堆叠柱）
  - 多日对比视图
- **响应式 & 暗色**：移动端适配 + dark mode token set
