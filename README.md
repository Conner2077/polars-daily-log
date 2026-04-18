<div align="center">

<img alt="Polars Daily Log" src="docs/banner.svg" width="100%"/>

### Your day, automatically — from activity to worklog.

A personal, local-first work-activity aggregator. It silently tracks foreground activity and Git commits across all your machines, asks an LLM to summarize the day, and pushes the result to Jira as worklogs with one click.

[中文](README_zh.md) / English

<a href="https://github.com/Conner2077/polars-daily-log/issues">Report Issues</a> · <a href="https://github.com/Conner2077/polars-daily-log/issues/new?labels=feedback">Feedback</a> · <a href="CHANGELOG.md">Changelog</a> · <a href="docs/release.md">Release Guide</a>

[![Release][release-shield]][release-link]
[![Stars][stars-shield]][stars-link]
[![Issues][issues-shield]][issues-link]
[![License][license-shield]][license-link]
[![Last commit][last-commit-shield]][last-commit-link]  
[![Python][python-shield]][python-link]
[![Platform][platform-shield]][platform-link]
[![Downloads][downloads-shield]][downloads-link]

🔒 **100% local** — one install per person, your data never leaves your own machines.

</div>

## How many machines do you use?

| Your situation | How to install |
|---|---|
| **Just one computer** (most common) | Install `both` on that one machine — server + collector in one |
| **Multiple machines to aggregate** (MacBook + work desktop + Linux box etc.) | Install `both` on the machine that's always on as the hub; install `collector` only on the others and point them at the hub |
| **I want to hack on the code** | [Jump to §Developers](#developers) |

---

## Quick Start

### Prerequisites

| | macOS | Linux | Windows |
|---|-------|-------|---------|
| Python 3.9+ | built-in | built-in | `winget install Python.Python.3.12` |
| git | `xcode-select --install` | `apt install git` | `winget install Git.Git` |

### Install

#### One-liner (recommended, macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/Conner2077/polars-daily-log/master/bootstrap.sh | bash
```

This pulls the latest release, extracts it to `~/.polars-daily-log`, and runs `install.sh` interactively.

Non-interactive variant (e.g. installing a collector on a second machine):

```bash
curl -fsSL https://raw.githubusercontent.com/Conner2077/polars-daily-log/master/bootstrap.sh | \
  PDL_ROLE=collector \
  PDL_SERVER_URL=http://your-hub-ip:8888 \
  PDL_COLLECTOR_NAME=my-laptop \
  bash
```

Optional env vars: `PDL_VERSION` (pin a version, default `latest`), `PDL_INSTALL_DIR` (default `~/.polars-daily-log`).

#### Manual install (Windows, or offline handoff)

1. **Download the tarball** from the [Releases page](https://github.com/Conner2077/polars-daily-log/releases)
2. **Extract**

   ```bash
   tar xzf polars-daily-log-0.2.0.tar.gz
   cd polars-daily-log-0.2.0
   ```

3. **Run the installer**

   ```bash
   # macOS / Linux
   bash install.sh

   # Windows (PowerShell)
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

4. **Follow the prompts**

   **Scenario A — only using this one machine**:

   ```
   0. What are you installing?
     1) server      — ...
     2) collector   — ...
     3) both        — ...
     Choose: 3          ← pick both
   ```

   **Scenario B — this is an additional machine, just push data to your hub**:

   ```
     Choose: 2          ← pick collector
     Server URL: http://your-hub-ip:8888
     Collector name [this-host]: ← press Enter for default
   ```

5. **Start it**

   ```bash
   ./pdl server start            # scenario A or the hub
   ./pdl collector start         # scenario B
   ```

6. **Open `http://127.0.0.1:8888`** (or the hub machine's IP). In Settings, configure:
   - **LLM**: choose Kimi / OpenAI / Claude, paste your API key (or leave blank to use the built-in Kimi default)
   - **Jira** (optional): if you want daily logs to sync as Jira worklogs, scan the QR to sign into Jira SSO

---

## Daily use

### Start / stop

| What | Command |
|---|---|
| Start everything | `./pdl start` (server + collector together) |
| Start server only | `./pdl server start` |
| Start collector only | `./pdl collector start` |
| Status | `./pdl status` |
| Stop | `./pdl stop` |
| Restart | `./pdl restart` |

### Logs / debug

```bash
./pdl server logs 100         # server backend log
./pdl server logs -f          # follow live
./pdl collector logs 50       # collector log
```

### Windows equivalents

On Windows, autostart goes through Scheduled Tasks (the installer asks whether to enable login autostart):

```powershell
Start-ScheduledTask -TaskName AutoDailyLogServer
Stop-ScheduledTask -TaskName AutoDailyLogServer
Get-ScheduledTaskInfo -TaskName AutoDailyLogCollector
```

### Where your data lives

| Thing | Path |
|---|---|
| Database (activities, logs, config) | `~/.auto_daily_log/data.db` |
| Screenshots | `~/.auto_daily_log/screenshots/YYYY-MM-DD/` |
| Server log | `~/.auto_daily_log/logs/server.log` |
| Collector log | `~/.auto_daily_log_collector/logs/collector.log` (standalone collector) |
| Config files | `<install-dir>/config.yaml`, `<install-dir>/collector.yaml` |

When you overwrite the tarball to upgrade, nothing under `~/` is touched — upgrades don't lose data.

### Push to messaging platforms (Webhook)

Besides Jira, you can push daily logs to group chats via webhook — WeChat Work, Feishu, Slack, or any HTTP endpoint.

1. **Get the webhook URL** from your group chat bot settings
2. **Open Settings** > "Scopes & Outputs" > add or edit an output
3. Set **Push Platform** to "Webhook", paste the URL, pick **Message Format** (WeChat Work / Feishu / Slack / Generic JSON)
4. Set **Push Mode** to "Manual" or "Auto-push after scheduled generation"

| Format | Body sent |
|---|---|
| WeChat Work | `{"msgtype":"markdown","markdown":{"content":"..."}}` |
| Feishu | `{"msg_type":"text","content":{"text":"..."}}` |
| Slack | `{"text":"..."}` |
| Generic JSON | `{"issue_key":"...","time_spent_sec":...,"comment":"...","started":"..."}` |

> **Manual vs auto**: "Auto-push after scheduled generation" only triggers when the scheduler generates logs on its cron schedule. Manually clicking "Generate" in the UI will **not** auto-push — use the push button on the generated summary instead.

### Report a bug

Click the 💡 button in the top-right of the Web UI. Write a few words; it automatically attaches the current page + UA and sends it to the developer.

---

## Upgrade

**Installed via the one-liner**: rerun the same curl command. It upgrades in place (runs `./pdl stop` first; your data under `~/` is untouched).

**Installed manually**:
```bash
# Stop services
./pdl stop

# Overwrite the install dir with the new tarball (venv is kept, data under ~/ is kept)
tar xzf polars-daily-log-0.2.0.tar.gz --strip-components=1

# Reinstall Python from the new wheel; the frontend dist ships inside the wheel
./pdl build --restart
```

If a release requires config migration, it'll be called out at the top of `CHANGELOG.md`.

---

## Uninstall

```bash
./pdl stop
cd ..
rm -rf ~/.polars-daily-log            # or your manual install dir
rm -rf ~/.auto_daily_log              # data + logs (skip if you want to keep them)
rm -rf ~/.auto_daily_log_collector    # standalone collector credentials + offline queue
```

Windows extras:
```powershell
Unregister-ScheduledTask -TaskName AutoDailyLogServer -Confirm:$false
Unregister-ScheduledTask -TaskName AutoDailyLogCollector -Confirm:$false
```

---

## Troubleshooting

| Symptom | What to check |
|---|---|
| `No module named aiosqlite` on startup | venv not activated / `install.sh` was skipped. Rerun `bash install.sh` |
| Daily summary contains only `Activity summary: ...` lines | LLM call failed → in Web UI Settings, verify engine / URL / API key match |
| Submitting to Jira returns 500 "Internal Server Error" | The comment contained an emoji. The latest version scrubs these automatically — upgrade |
| WeCom auto-exits after 2–4 minutes | Make sure `monitor.hostile_apps_applescript` in `config.yaml` includes `wechat/wecom/企业微信` |
| Frontend blank page | `./pdl server logs 50` to check the backend; hard-reload the browser (Cmd/Ctrl+Shift+R) |
| Windows collector is idle | Check `%USERPROFILE%\.auto_daily_log_collector\logs\collector.log` and the Scheduled Task status |

---

## Developers

If you cloned the repo instead of unpacking a tarball:

### Prerequisites
- Python 3.9+, Node.js 18+, git

### Getting started

```bash
git clone https://github.com/Conner2077/polars-daily-log.git
cd polars-daily-log
bash install.sh              # auto-detects no wheel → dev mode (pip install -e . + frontend source build)
./pdl server start
```

### Day-to-day

| What | Command |
|---|---|
| Rebuild after pull | `./pdl build --restart` |
| Frontend only | `./pdl build --no-python` |
| Run tests | `.venv/bin/python -m pytest tests/ -q` |
| Frontend hot-reload dev | `cd web/frontend && npm run dev`, open `localhost:5173` |

### Cutting a release

See [`docs/release.md`](docs/release.md).

### Project principles

See [AGENTS.md](AGENTS.md) (Claude Code loads it via `CLAUDE.md → @AGENTS.md`). The core rules:

- **Keep the raw record**: daily summaries don't filter; downstream consumers (Jira submission uses `AUTO_APPROVE_PROMPT`) do the filtering
- **Two-layer platform code**: raw OS APIs live in `auto_daily_log/monitor/`; adapters live in `auto_daily_log_collector/platforms/`
- **Single Jira entry point**: `jira_client.client.build_jira_client_from_db`, which handles emoji / 4-byte UTF-8 scrubbing for you

---

## License

Apache 2.0. See [LICENSE](LICENSE).

<!-- Badge references -->
[release-shield]: https://img.shields.io/github/v/release/Conner2077/polars-daily-log?style=flat-square&color=brightgreen&label=release
[release-link]: https://github.com/Conner2077/polars-daily-log/releases
[stars-shield]: https://img.shields.io/github/stars/Conner2077/polars-daily-log?style=flat-square&color=yellow
[stars-link]: https://github.com/Conner2077/polars-daily-log/stargazers
[issues-shield]: https://img.shields.io/github/issues/Conner2077/polars-daily-log?style=flat-square&color=orange
[issues-link]: https://github.com/Conner2077/polars-daily-log/issues
[license-shield]: https://img.shields.io/github/license/Conner2077/polars-daily-log?style=flat-square&color=blue
[license-link]: https://github.com/Conner2077/polars-daily-log/blob/master/LICENSE
[last-commit-shield]: https://img.shields.io/github/last-commit/Conner2077/polars-daily-log?style=flat-square
[last-commit-link]: https://github.com/Conner2077/polars-daily-log/commits/master
[python-shield]: https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square&logo=python&logoColor=white
[python-link]: https://www.python.org/downloads/
[platform-shield]: https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square
[platform-link]: #prerequisites
[downloads-shield]: https://img.shields.io/github/downloads/Conner2077/polars-daily-log/total?style=flat-square&color=success
[downloads-link]: https://github.com/Conner2077/polars-daily-log/releases
