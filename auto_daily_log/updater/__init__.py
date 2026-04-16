"""Self-update system: GitHub-Releases version check, snapshot+rollback,
and detached pip-upgrade orchestration triggered from the Web UI.

Layout:
  version_check.py — query GitHub, cache, compare versions
  backup.py        — VACUUM INTO + tar config + manifest.json
  state.py         — progress/status file shared with the Web UI
  runner.py        — kill server → backup → pip install → restart
  __main__.py      — `python -m auto_daily_log.updater <subcommand>`
"""

GITHUB_REPO = "Conner2077/polars-daily-log"
WHEEL_NAME_TEMPLATE = "auto_daily_log-{version}-py3-none-any.whl"
