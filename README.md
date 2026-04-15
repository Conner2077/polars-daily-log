# Auto Daily Log

自动抓取工作活动 + LLM 总结 + Jira 工时提交的分布式工具。

## 🚀 首次运行必须跑 install.sh

```bash
bash install.sh
```

**不要**直接 `pip install .` 或 `pip install -r requirements.txt`，会跳过：
- 系统依赖检测（git / xdotool / screenshot 工具 / tesseract）
- venv 创建
- 前端构建
- 导入验证（aiosqlite / sqlite_vec / FastAPI 等核心包）

`install.sh` 最后一步会逐项 import 关键运行时依赖，任何一项缺失立即报出来，不会让服务"装上了但起不来"。

## 日常使用

```bash
./adl server start      # 启动服务（默认端口 8888）
./adl server status
./adl server restart
./adl server logs 100   # 最近 100 行日志
./adl collector start   # 如果你用独立 collector 模式
```

打开 http://127.0.0.1:8888 使用 Web UI。

## 架构

- **Server**（本仓 `auto_daily_log/`）：Web UI + SQLite 中心库 + LLM + Jira 提交，跨平台
- **Collector**（本仓 `auto_daily_log_collector/`）：采集节点，跨平台独立运行，通过 HTTP 推给 server

平台专属监控逻辑**必须**放在 `auto_daily_log_collector/platforms/<os>.py`，不能放到 server 侧。契约见 `auto_daily_log_collector/DEVELOPMENT.md`。

## 核心原则

见 [CLAUDE.md](CLAUDE.md)：
- 每日总结"原汁原味"，不做筛选；筛选留给下游（Jira 提交用 AUTO_APPROVE_PROMPT）
- 删除用软删除（回收站）
- 反侦测 app（企业微信等）走 `hostile_apps_*` 白名单，不做深度 introspection

## 故障排查

| 症状 | 先检查 |
|------|--------|
| 启动报 `No module named aiosqlite` | 没激活 venv 或没跑 `install.sh` |
| 当日总结只有 "Activity summary: ..." | LLM 调用失败，检查 Settings 里 engine/URL/api_key 是否匹配 |
| 企业微信频繁自退 | 确认 `hostile_apps_applescript` 配置里有 `企业微信/wechat/wecom` |
| 前端看不到日志 | `./adl server logs 50` 看后端报错，再刷新 |
