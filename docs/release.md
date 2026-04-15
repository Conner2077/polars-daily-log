# Release Runbook

> 开发者打包一个新版本发给用户（朋友/同事/试用者）的完整流程。
> 这是个人工具，每个用户装自己的一套，所以 release 的目标是
> "打出一个能直接装、不需要 git/Node 的 tarball"。

---

## 前置

- 本机 clone 仓库，`.venv` 已装好 (`./pdl install` 跑过)
- Node.js 18+（前端构建用）
- 所有改动已 commit 到 main 分支，`git status` 干净

---

## 1. 确认质量

```bash
# 跑全量测试
.venv/bin/python -m pytest tests/ -q

# 预期输出末尾：200+ passed
```

**不过不发**。如果有新失败的测试，先修、commit、再来。

---

## 2. 更新版本号

用 SemVer：
- `0.1.0 → 0.1.1`：bug 修复
- `0.1.0 → 0.2.0`:    新功能，不破坏 API
- `0.1.0 → 1.0.0`：破坏性变更（用户 tarball/config 需要迁移）

改一处：

```toml
# pyproject.toml
version = "0.2.0"
```

同步一处：

```json
# web/frontend/package.json
"version": "0.2.0"
```

（版本不一致不会炸，但 release 体里的 VERSION 文件以 pyproject 为准。）

---

## 3. 写 CHANGELOG

打开 `CHANGELOG.md`（没就新建），顶部追加：

```markdown
## [0.2.0] — 2026-04-20

### Added
- 聊天 Agent（deep-chat 嵌入）
- ...

### Changed
- 日志卡片 issue_key 改为可点跳 Jira

### Fixed
- Jira worklog comment 含 emoji 返回 500 的 bug

### 升级注意
- `config.yaml` 的 `llm.engine` 自动迁移：`claude` → `anthropic`
```

不要用自动工具生成散文，**手写**几行用户看得懂的话。

---

## 4. 打 release

```bash
bash scripts/release.sh
```

会做：
1. `npm ci && npm run build` 前端
2. 把 `dist/` stage 到 `auto_daily_log/frontend_dist/`
3. `python -m build --wheel` 产出 wheel
4. 校验 wheel 含 `frontend_dist/` + 3 个 Python 包
5. 组装 tarball：`release/polars-daily-log-<version>.tar.gz`

产物：
```
release/
├── polars-daily-log-0.2.0.tar.gz       ← 发给用户
└── polars-daily-log-0.2.0/             ← 解压版（方便调试）
    ├── wheels/auto_daily_log-0.2.0-py3-none-any.whl
    ├── install.sh
    ├── install.ps1
    ├── adl
    ├── config.yaml.example
    ├── collector.yaml.example
    ├── README.md
    └── VERSION
```

---

## 5. 自测 tarball

**别跳过这一步**。把 tarball 丢到一个干净目录装一遍，确认端到端通：

```bash
# 找个干净的临时目录
mkdir -p /tmp/adl-release-test && cd /tmp/adl-release-test
tar xzf /path/to/release/polars-daily-log-0.2.0.tar.gz
cd polars-daily-log-0.2.0

# 用 env 非交互模式装 collector
PDL_ROLE=collector \
PDL_SERVER_URL=http://127.0.0.1:8888 \
PDL_COLLECTOR_NAME=release-test \
  bash install.sh

# 预期：Verification 阶段全 ✓
```

验过再往下。失败不要发。

---

## 6. 打 tag

```bash
git tag -a v0.2.0 -m "Release 0.2.0"
git push origin v0.2.0
```

tag 跟仓库绑定，后续看 `git log --oneline v0.1.0..v0.2.0` 能回看一个版本跨度的所有提交。

---

## 7. 发布

手动分发，找个用户能下载的地方：

| 渠道 | 操作 |
|------|------|
| 自己的 OSS / 文件服务器 | `scp release/polars-daily-log-0.2.0.tar.gz user@files:/var/www/releases/` |
| GitHub Release | `gh release create v0.2.0 release/*.tar.gz -F CHANGELOG.md` |
| 飞书 / 微信附件 | 直接拖给朋友 |
| Jira 附件 | 附到跟踪 issue 里 |

---

## 8. 通知用户

发给试用的朋友一条消息：

```
Polars Daily Log v0.2.0

下载：<链接>
升级方式（已经装过的）：
  cd 你解压过的目录
  ./pdl stop
  tar xzf 新版tarball --strip-components=1
  ./pdl build --restart

主要变化：<changelog 前 2-3 点>
```

---

## 故障排查（release 链路本身）

| 症状 | 原因 | 修法 |
|------|------|------|
| `release.sh` 报 `npm ci` 失败 | `web/frontend/package-lock.json` 过期 | `cd web/frontend && npm install && git add package-lock.json && git commit` |
| wheel 里没有 `frontend_dist/` | `auto_daily_log/frontend_dist/` 在 `.gitignore` 里，但 stage 步骤没跑 | 清理 `rm -rf build dist auto_daily_log/frontend_dist` 重跑 |
| 解压后 `bash install.sh` 报 `detect_install_mode: no wheels` | tarball 里 `wheels/` 目录空了 | 重跑 release.sh 看 verify 步骤哪里挂 |
| 装 wheel 报 `ERROR: Could not find a version that satisfies auto-daily-log` | 路径语法问题 | 确认 `pip install /abs/path/to/wheel.whl[macos]`，加引号防 zsh 解读 `[` |

---

## 阶段 2 要做的（当前未做）

- GitHub Actions / GitLab CI 自动化（tag push 触发 release）
- `git-cliff` 按 conventional commits 自动生成 CHANGELOG
- 发布到内部 PyPI（`devpi` 搭一个）
- wheel / tarball 签名 + `sha256sum` 校验文件

现在阶段 1 用不到，别提前工程化。
