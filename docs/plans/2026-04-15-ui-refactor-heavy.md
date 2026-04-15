# UI Refactor — Heavy Tier（替换组件层 + 响应式 + 暗色）

**状态**：**未启动，备用 plan**（优先跑 [light tier](2026-04-15-ui-refactor-light.md)，看完效果再定）
**预计工作量**：5-8 周（按 full-time 节奏；兼职做 2-3 个月）
**执行方式**：长分支 + 并行新实现，逐页 cutover
**前置依赖**：light tier 已合并并稳定运行 1-2 周，证明"光靠改主题不够"才启动此 plan

---

## 1. 背景 & 和 Light Tier 的关系

### Light Tier 做了什么

- 仅调 `src/styles/theme-minimal.css`（CSS vars + web font）
- 不动 Element Plus，不动 `.vue` 文件，不动 `global.css`
- 5 个页面视觉接近 landing 风格，但：
  - **107 条 `!important`** 仍在
  - **Element Plus 原生 DOM 结构** 决定布局上限（深层嵌套 div、固定间距）
  - 无响应式、无暗色

### Heavy Tier 为什么需要

如果 light tier 跑完后出现以下任一情况，heavy 就有必要：

- Element Plus 某些组件（如 el-table 行高、el-dialog padding、el-timeline 连接线）即使用了 `!important` 也达不到 landing mock 的精度
- 你想加移动端 / 平板适配
- 你想加暗色模式
- 你想重新规划 Dashboard / Activities 的信息架构（不只是换皮）
- 团队 / 社区有新人来提 PR，架构债成为入门门槛

如果 light tier 跑完**体感已经很好**，heavy 可以长期搁置，这份 plan 保留作档案。

## 2. 目标

1. **替换 Element Plus 为 shadcn-vue（基于 Reka UI）**，组件代码 own 在 repo 里可改
2. **引入 Tailwind CSS**，统一 token 系统 + utility-first 开发
3. **5 个页面全量重写**（保留业务逻辑和 API，重写模板层）
4. **响应式**：desktop / laptop / tablet 三断点（移动端低优先）
5. **暗色模式**：`prefers-color-scheme` 检测 + 手动 toggle
6. **清除 107 条 `!important` 债**
7. **组件层拆解**：提取 `StatCard` / `DataTable` / `FormField` / `CodeBlock` 等 primitive
8. **TimelineChart 进化**：可点柱子看详情、类别堆叠、多日对比

## 3. 非目标

- ❌ 不重写后端（API、data model、scheduler、summarizer 全部保留）
- ❌ 不改变业务流程（采集→总结→审批→Jira 推送的 4 阶段保留）
- ❌ 不引入 SSR（产品是本地 SPA，SSR 无收益）
- ❌ 不切 Vue 3 → React / Svelte（切框架收益不抵成本）
- ❌ 不做手机原生 app（web 足够）

## 4. 架构

### 4.1 技术栈替换

| 层 | 当前 | Heavy Tier |
|----|------|-----------|
| UI 组件 | Element Plus 2.13.7 | shadcn-vue（Reka UI 底层）|
| CSS | 单 `global.css` + 107 `!important` | Tailwind CSS + 设计 token |
| 图标 | `@element-plus/icons-vue` + emoji | `lucide-vue-next` 单一来源 |
| 图表 | 手写 SVG（light tier 已有 TimelineChart） | 扩展为自家 `<BarChart>/<LineChart>/<Stacked>` 组件库 |
| 表单验证 | Element Plus 内置 | vee-validate + zod（或 Reka UI 内置） |
| Markdown | 现有 `marked` | 保留 `marked` + 可选加 shiki 代码高亮 |
| 动画 | CSS transition | 继续 CSS transition + Motion One 复杂场景 |
| 路由 | vue-router hash 模式 | vue-router history 模式（需服务端配合 fallback） |

### 4.2 并行 parallel-app 迁移策略

不推荐 big-bang 一次性重写。分支策略：

```
master (light tier 后稳定版)
  │
  ├─ feat/ui-refactor-heavy-spike       # Phase H0 的 POC，一次性分支
  │
  └─ feat/ui-refactor-heavy             # 正式长分支
        └─ web/frontend/                # 保持原实现（渐进替换）
        └─ web/frontend-next/           # 新实现，独立 npm 包 + 独立 vite build
```

`web/frontend-next/` 与 `web/frontend/` 共存期：

- 同一个后端两个前端；环境变量切换 serve 哪个（`PDL_FRONTEND=next` → next，默认旧）
- 每页迁移完，路由级切换（`/dashboard` 由 next 提供，其他仍旧）
- 全部迁完 → 删 `web/frontend/`，`web/frontend-next/` 改名 `web/frontend/`

好处：
- **随时能回旧版**（环境变量切换）
- **逐页 cutover**，每页完成即可验收
- **新旧共存期不互相污染**（两套独立 vite + CSS）
- **可以做 A/B 切换**让你长时间对比

### 4.3 设计 token 层

```
web/frontend-next/src/styles/
├── tokens.css              # :root + [data-theme="dark"] CSS vars
├── tailwind.config.ts      # 从 tokens.css 导入 vars，不重复定义
└── app.css                 # 极少量 global reset + font import
```

token 值来自 landing（light tier 已验证），包括暗色 set：

```css
:root {
  --bg: #ffffff;
  --ink: #171717;
  --ink-muted: #6e6e80;
  --line: #e5e5e5;
  /* ... */
}

[data-theme="dark"] {
  --bg: #07080a;
  --ink: #f4f5f7;
  --ink-muted: #9aa4b4;
  --line: #1d222c;
  /* ... */
}
```

## 5. 设计决策（**开工前要重新确认**，ecosystem 变化快）

| # | 决策 | 当前倾向 | 启动前要验证的 |
|---|------|---------|---------------|
| 1 | 组件库 | shadcn-vue | 启动时看 Reka UI / shadcn-vue 成熟度（issue 数、文档、组件齐全度）|
| 2 | CSS 框架 | Tailwind CSS | 启动时看 Tailwind 版本（v4 可能已稳定） |
| 3 | 图标库 | lucide-vue-next | 看图标丰富度，中文场景特殊图标（如微信）需不需要 fallback |
| 4 | 状态管理 | composable + provide/inject（不引 Pinia） | 迁移中发现跨页复杂状态再引 Pinia |
| 5 | 路由模式 | history（需 server fallback） | 确认 FastAPI 能加 catch-all 路由 fallback 到 index.html |
| 6 | 动画库 | CSS + Motion One（按需） | Timeline 扩展时评估是否需要 Motion One |
| 7 | 虚拟列表 | @tanstack/vue-virtual | Activities 表如果行数 500+ 才需要 |
| 8 | 表单 | vee-validate + zod | 对比 Reka UI 内置表单原语 |
| 9 | 类型系统 | 全量 TypeScript | light tier 后可先以 `.vue` JSDoc + `//@ts-check` 渐进 |
| 10 | 测试 | Vitest + @testing-library/vue + Playwright | 当前是 pytest backend only，需补前端测试栈 |

## 6. 分阶段实施

### Phase H0：Spike / POC（4-8h）

**目标**：在扔掉 Element Plus 前，用一个隔离 PoC 验证可行性。

- 建 `web/frontend-spike/`，独立 package.json
- 装 Vue 3 + Vite + Tailwind + Reka UI + shadcn-vue CLI
- 实现一个"复杂场景"：Activities 表的核心部分（el-table 替换为 shadcn-vue table 组件 + 8 列 + `show-overflow-tooltip` 等价物 + popconfirm 删除）
- 对比：bundle size、代码量、可读性、可维护性
- **产出**：一页 spike 报告（走 / 不走 heavy 的推荐），删掉 spike 分支

**验收**：能 npm run dev + 表格可用 + 报告结论清晰

---

### Phase H1：Foundation（2-3 天）

**目标**：建好 `web/frontend-next/` 空壳，和 `web/frontend/` 切换机制打通。

- 创建 `web/frontend-next/` 目录结构（vite + Vue 3 + Tailwind + TS）
- 拷贝 `tokens.css` 从 light tier
- 配置 Tailwind（从 tokens 读 CSS vars，不重复定义）
- 装 shadcn-vue CLI + 拉取基础组件：`Button` / `Input` / `Dialog` / `Popover` / `Tabs` / `Card` / `Switch` / `Badge` / `Separator`
- 装 `lucide-vue-next`
- 配 Google Fonts / 自托管 Geist + JetBrains Mono + Noto Sans SC
- 写 `App.vue` 外壳（nav + 路由出口 + 暗色 toggle）
- **后端侧**：在 `auto_daily_log/web/app.py` 加环境变量切换 frontend：`PDL_FRONTEND=next` serve `frontend-next/dist/`，默认 serve `frontend/dist/`
- **构建**：`./pdl build` 识别 `PDL_FRONTEND_NEXT_ONLY=1` 开关一次构建两端

**验收**：`PDL_FRONTEND=next pdl start` 能打开一个空壳页（只有 nav），切回 `PDL_FRONTEND=default` 即回旧版

---

### Phase H2：Shared primitives（2-3 天）

**目标**：提取所有页面都要用的 primitive 组件，避免重复造轮子。

- `StatCard`：数字 + 标签 + 副文本（Dashboard / Settings 用）
- `DataTable`：shadcn-vue Table 上二次封装，支持 truncate + tooltip + 排序 + 分页
- `FormField`：Label + Input/Select/Textarea/Switch 组合（Settings 大量用）
- `CodeBlock`：等宽 + 行号 + 复制按钮（OCR、Prompt 模板、bootstrap 命令用）
- `StatusPill`：带颜色的小徽章（active / idle / pending / failed）
- `Timeline`：替换 el-timeline（MyLogs audit / Activities 视图用）
- `EmptyState`：空列表插画 + 文案
- `LoadingSkeleton`：骨架屏

每个 primitive 附 Vitest 单测（props、slot、事件）

**验收**：所有 primitive 在独立 storybook 式 demo 页可视；单测 90%+ 覆盖

---

### Phase H3：Dashboard 迁移（1-1.5 天）

**路由级切换**：`/dashboard` 由 next 提供，其他路由保持旧版。

- 组合 StatCard + CategoryBreakdown + TimelineChart
- TimelineChart 从 light tier 迁移 + 扩展：
  - 点击柱子弹 popover 显示该 bucket 的活动详情
  - 类别堆叠选项（按 category 分色堆）
  - 日期切换（可看历史某日）
- 暗色模式下重新调色 + 阴影
- 响应式：laptop 窄屏下卡片堆成 1 列

**验收**：对照 light tier 的 Dashboard 并排看，heavy 版本**精度更高 / 间距更一致 / 无 Element Plus 蓝色残影**

---

### Phase H4：Activities 迁移（3-4 天）

**最复杂，留足时间。**

- DataTable 渲染 8 列 + 每行 250+ 字 OCR 处理
- Preview Dialog：截图 lightbox + OCR 全文
- 左侧日期列表（移动端变 drawer）
- Timeline 视图切换
- 搜索 UI（shadcn-vue Command 或 Combobox）
- 虚拟滚动（如果某天活动数 > 500）

**边界 case 重点**：
- 超长 OCR（模拟 1000+ 字）
- 超长窗口标题（200+ 字符）
- 截图 4K vs 低分辨率
- 空数据
- 加载 error

**验收**：迁移期 next `/activities` + 旧 `/dashboard` 并存无异常

---

### Phase H5：MyLogs 迁移（2-3 天）

- 卡片嵌套结构
- Markdown 渲染（marked + 自家 CSS，或切 shiki 做代码高亮）
- 编辑模式 in-place
- Audit trail 重新用 Timeline primitive
- "生成中" overlay 用 LoadingSkeleton primitive

**边界 case**：
- 2000+ 字 Markdown
- 含大量代码块的 full_summary
- 单 draft 10+ issue

---

### Phase H6：Issues 迁移（0.5-1 天）

- DataTable 复用
- Add dialog（shadcn-vue Dialog + FormField）

---

### Phase H7：Settings 迁移（2-3 天）

- Tabs primitive（shadcn-vue 有对应）
- 7 tab 的 FormField 组合
- 4 个 12 行 textarea（prompt 编辑）→ CodeBlock 变体（可编辑）
- collectors 表（DataTable）
- Jira SSO 嵌套 form
- Alert banner

---

### Phase H8：响应式 + 暗色（1-2 天）

- 断点走查所有页面：`sm/md/lg/xl`
- 暗色 token set 验证
- 手动 toggle + `prefers-color-scheme` 监听
- 持久化到 localStorage

---

### Phase H9：Testing, a11y, performance, cutover（2-3 天）

- Playwright E2E：关键路径（打开 → 查活动 → 改 draft → 推 Jira → 验结果）
- axe-core a11y 扫描
- Lighthouse 性能：目标 LH > 95（桌面）
- 包体积对比：heavy vs 当前
- 最终 cutover：默认 `PDL_FRONTEND=next`，旧版保留 1 个版本作 escape hatch，再下一版本删

---

## 7. 风险 & 缓解

| 风险 | 影响 | 缓解 |
|------|-----|------|
| 开发周期长，中途失焦 | 半途而废 | 严格按 Phase 交付；Phase 未完成不开下一个；每周 checkpoint |
| shadcn-vue / Reka UI ecosystem 在开工时仍不成熟 | 卡在组件缺失 | Phase H0 spike 就要验证；不行就换 headless-ui-vue + Tailwind |
| Tailwind v4 重大变更 | 文档 / 生态对不上 | 选稳定版本 + 生产验证，不追最新 |
| Markdown / 代码块 `:deep()` 行为变化 | MyLogs 渲染崩 | Phase H5 专项边界测试 |
| 暗色模式下某些第三方组件色彩硬编码 | 暗色下有白色方块 | 逐组件 audit；用 `color-scheme: dark` + Tailwind variant |
| TypeScript 增量迁移摩擦 | 开发慢 | 允许 `.vue` 混用 TS + JSDoc；不追求 100% TS |
| 并行 frontend 期间后端 session cookie 冲突 | 登录状态丢 | 两端共享 cookie path / domain，测试覆盖 |
| vue-router history 模式需后端 fallback | 硬刷新 404 | FastAPI 加 catch-all 路由返回 `index.html` |
| 图标迁移遗漏 | UI 半英文半 emoji | H1 期间建立图标映射表；CI 加 grep 检查 |
| Timeline 扩展功能超预算 | 拖 Dashboard Phase | 点击 / 堆叠可选；复杂需求拆独立 phase |

## 8. 回退策略（heavy 特有）

因为 heavy 是**新建并行 frontend**，回退非常干净：

### 回退级别

1. **页面级**：把该路由从 next 切回 default（改 `main.js` 里路由表）
2. **整站级**：`PDL_FRONTEND=default pdl restart`，旧前端秒级恢复
3. **版本级**：release 出带 heavy 的版本后，旧 frontend 保留 **2 个 release** 时间，用户可装旧 tarball

### 不可回退的边界

- 新 API endpoint（如 Timeline 扩展）会被两个 frontend 共用，后端侧不回退
- Tailwind / shadcn-vue 是 frontend-next 独有，不会污染旧 frontend

## 9. 跨 plan 的复用

从 light tier 继承进 heavy 的东西：

- ✅ `theme-minimal.css` 的 CSS var 值（直接做 `tokens.css`）
- ✅ `TimelineChart.vue` 的交互逻辑（可重写成 TS + shadcn-vue primitive 组合）
- ✅ Google Fonts 配置（切自托管即可）
- ✅ Timeline API endpoint（后端不变）
- ✅ baseline 截图（validation 参考）

## 10. 触发条件（什么时候启动 heavy）

**硬性触发**（任一满足）：

- light tier 跑完 2 周后，Conner 确认"还不够，想要更干净"
- 要加移动端 / 平板适配
- 要加暗色模式
- 社区 / 同事开始提 PR，有人嫌组件结构难改
- Element Plus 出现 security issue 或跟不上 Vue 4

**软性触发**（锦上添花）：

- 想玩 shadcn-vue / Reka UI 新特性
- 想拉响应式给朋友在 iPad 看日志

无触发 → heavy 长期搁置是正确决策。本 plan 保留作档案。

## 11. 估算总表

| Phase | 名称 | 估时 | 累计 |
|-------|-----|-----|-----|
| H0 | Spike / POC | 4-8h | 1 天 |
| H1 | Foundation | 2-3 天 | 4 天 |
| H2 | Shared primitives | 2-3 天 | 7 天 |
| H3 | Dashboard | 1-1.5 天 | 8.5 天 |
| H4 | Activities | 3-4 天 | 12.5 天 |
| H5 | MyLogs | 2-3 天 | 15.5 天 |
| H6 | Issues | 0.5-1 天 | 16.5 天 |
| H7 | Settings | 2-3 天 | 19.5 天 |
| H8 | 响应式 + 暗色 | 1-2 天 | 21.5 天 |
| H9 | Test / a11y / perf / cutover | 2-3 天 | 24.5 天 |

**总工时：约 24-30 全职工作日** ≈ 5-6 周 full-time，或 2-3 个月兼职周末。

## 12. 后续演化

heavy 完成后开的路：

- 组件库抽离成独立 npm package（给其他个人工具复用）
- 后端侧 API 类型生成（OpenAPI → TS types）
- 多语言 i18n（除简体外加英文 / 繁体）
- 移动端单独 app（Capacitor 包装）
- Electron / Tauri 桌面壳
- 协作层（多机共享 dashboard，**注意**：这会违反"一人一套"定位，慎重）
