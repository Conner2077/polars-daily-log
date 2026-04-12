# Auto Daily Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained tool that monitors desktop activity, collects git commits, generates Jira worklog drafts via LLM, and submits them after approval — all managed through a web UI.

**Architecture:** Python FastAPI backend with SQLite storage, APScheduler for timed jobs, Vue.js SPA frontend served as static files. Monitor module adapted from polars_free_worklog. LLM engine abstraction supports Kimi/OpenAI/Ollama/Claude.

**Tech Stack:** Python 3.9+, FastAPI, SQLite (aiosqlite), APScheduler, Vue 3 + Vite, httpx (async HTTP), pyobjc (macOS OCR)

**Source reference:** Activity monitor code adapted from `/Users/conner/Zone/code/ai_project/polars_free_worklog/activity_monitor/`

**Design spec:** `docs/superpowers/specs/2026-04-12-auto-daily-log-design.md`

---

## Task 1: Project Skeleton + Dependencies

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `auto_daily_log/__init__.py`
- Create: `auto_daily_log/__main__.py`
- Create: `config.yaml`
- Create: `.gitignore`

- [ ] **Step 1: Create .gitignore**

```gitignore
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.venv/
venv/
*.db
*.sqlite
screenshots/
node_modules/
web/frontend/dist/
.env
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[project]
name = "auto-daily-log"
version = "0.1.0"
description = "Automated Jira worklog tool with activity monitoring and LLM summarization"
requires-python = ">=3.9"
dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "aiosqlite>=0.19.0",
    "httpx>=0.25.0",
    "apscheduler>=3.10.0",
    "pyyaml>=6.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
]

[project.optional-dependencies]
macos = ["pyobjc-framework-Vision>=10.0"]
windows = ["winocr>=0.1.0"]
linux = ["pytesseract>=0.3.10", "Pillow>=10.0"]
dev = ["pytest>=7.4.0", "pytest-asyncio>=0.23.0", "pytest-httpx>=0.28.0"]

[project.scripts]
auto-daily-log = "auto_daily_log.__main__:main"
```

- [ ] **Step 3: Create requirements.txt**

```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
aiosqlite>=0.19.0
httpx>=0.25.0
apscheduler>=3.10.0
pyyaml>=6.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
pytest>=7.4.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 4: Create config.yaml**

```yaml
server:
  port: 8080
  host: "0.0.0.0"

monitor:
  interval_sec: 30
  ocr_enabled: true
  ocr_engine: auto
  screenshot_retention_days: 7
  privacy:
    blocked_apps: []
    blocked_urls: []

git:
  repos: []

jira:
  server_url: ""
  pat: ""

llm:
  engine: kimi
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

- [ ] **Step 5: Create package init and entry point**

`auto_daily_log/__init__.py`:
```python
"""Auto Daily Log - Automated Jira worklog tool."""
__version__ = "0.1.0"
```

`auto_daily_log/__main__.py`:
```python
"""Entry point: python -m auto_daily_log"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Auto Daily Log")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--port", type=int, help="Override server port")
    args = parser.parse_args()
    # Will be wired up in Task 22
    print(f"Auto Daily Log v0.1.0 - config: {args.config}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Set up venv and verify**

Run:
```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,macos]"
python -m auto_daily_log
```
Expected: `Auto Daily Log v0.1.0 - config: config.yaml`

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml requirements.txt config.yaml auto_daily_log/
git commit -m "feat: project skeleton with dependencies and config"
```

---

## Task 2: Database Models + Initialization

**Files:**
- Create: `auto_daily_log/models/__init__.py`
- Create: `auto_daily_log/models/database.py`
- Create: `auto_daily_log/models/schemas.py`
- Create: `tests/__init__.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write failing test for database initialization**

`tests/__init__.py`: empty file

`tests/test_database.py`:
```python
import pytest
import pytest_asyncio
import aiosqlite
from pathlib import Path
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_initialize_creates_all_tables(db):
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = [t["name"] for t in tables]
    assert "activities" in table_names
    assert "git_repos" in table_names
    assert "git_commits" in table_names
    assert "jira_issues" in table_names
    assert "worklog_drafts" in table_names
    assert "audit_logs" in table_names
    assert "settings" in table_names


@pytest.mark.asyncio
async def test_insert_and_fetch_activity(db):
    await db.execute(
        """INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("2026-04-12T10:00:00+08:00", "IntelliJ IDEA", "Main.java", "coding", 0.92, 30),
    )
    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 1
    assert rows[0]["app_name"] == "IntelliJ IDEA"
    assert rows[0]["category"] == "coding"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_daily_log.models'`

- [ ] **Step 3: Implement Database class**

`auto_daily_log/models/__init__.py`: empty file

`auto_daily_log/models/database.py`:
```python
from pathlib import Path
from typing import Any, Optional

import aiosqlite

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    app_name TEXT,
    window_title TEXT,
    category TEXT,
    confidence REAL,
    url TEXT,
    signals TEXT,
    duration_sec INTEGER
);
CREATE INDEX IF NOT EXISTS idx_activities_timestamp ON activities(timestamp);

CREATE TABLE IF NOT EXISTS git_repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    author_email TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS git_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER REFERENCES git_repos(id),
    hash TEXT NOT NULL,
    message TEXT,
    author TEXT,
    committed_at TEXT,
    files_changed TEXT,
    insertions INTEGER DEFAULT 0,
    deletions INTEGER DEFAULT 0,
    date TEXT
);
CREATE INDEX IF NOT EXISTS idx_git_commits_date ON git_commits(date);

CREATE TABLE IF NOT EXISTS jira_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_key TEXT UNIQUE NOT NULL,
    summary TEXT,
    description TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS worklog_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    time_spent_sec INTEGER DEFAULT 0,
    summary TEXT,
    raw_activities TEXT,
    raw_commits TEXT,
    status TEXT DEFAULT 'pending_review',
    user_edited INTEGER DEFAULT 0,
    jira_worklog_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_drafts_date_status ON worklog_drafts(date, status);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES worklog_drafts(id),
    action TEXT NOT NULL,
    before_snapshot TEXT,
    after_snapshot TEXT,
    jira_response TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


class Database:
    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor.lastrowid

    async def execute_many(self, sql: str, params_list: list[tuple]) -> None:
        await self._conn.executemany(sql, params_list)
        await self._conn.commit()

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Implement Pydantic schemas**

`auto_daily_log/models/schemas.py`:
```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ActivityRecord(BaseModel):
    id: Optional[int] = None
    timestamp: str
    app_name: Optional[str] = None
    window_title: Optional[str] = None
    category: Optional[str] = None
    confidence: Optional[float] = None
    url: Optional[str] = None
    signals: Optional[str] = None  # JSON string
    duration_sec: int = 30


class GitRepo(BaseModel):
    id: Optional[int] = None
    path: str
    author_email: Optional[str] = None
    is_active: bool = True


class GitCommit(BaseModel):
    id: Optional[int] = None
    repo_id: int
    hash: str
    message: Optional[str] = None
    author: Optional[str] = None
    committed_at: Optional[str] = None
    files_changed: Optional[str] = None  # JSON string
    insertions: int = 0
    deletions: int = 0
    date: str


class JiraIssue(BaseModel):
    id: Optional[int] = None
    issue_key: str
    summary: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True


class WorklogDraft(BaseModel):
    id: Optional[int] = None
    date: str
    issue_key: str
    time_spent_sec: int = 0
    summary: Optional[str] = None
    raw_activities: Optional[str] = None  # JSON string
    raw_commits: Optional[str] = None  # JSON string
    status: str = "pending_review"
    user_edited: bool = False
    jira_worklog_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AuditLog(BaseModel):
    id: Optional[int] = None
    draft_id: int
    action: str
    before_snapshot: Optional[str] = None
    after_snapshot: Optional[str] = None
    jira_response: Optional[str] = None
    created_at: Optional[str] = None


class SettingItem(BaseModel):
    key: str
    value: str  # JSON string


class WorklogDraftUpdate(BaseModel):
    time_spent_sec: Optional[int] = None
    summary: Optional[str] = None
    issue_key: Optional[str] = None
    status: Optional[str] = None
```

- [ ] **Step 6: Commit**

```bash
git add auto_daily_log/models/ tests/
git commit -m "feat: SQLite database layer with all tables and Pydantic schemas"
```

---

## Task 3: Config Management

**Files:**
- Create: `auto_daily_log/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

`tests/test_config.py`:
```python
import pytest
from pathlib import Path
from auto_daily_log.config import load_config, AppConfig


def test_load_default_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
server:
  port: 9090
  host: "127.0.0.1"
monitor:
  interval_sec: 60
  ocr_enabled: false
llm:
  engine: openai
""")
    config = load_config(str(config_file))
    assert config.server.port == 9090
    assert config.monitor.interval_sec == 60
    assert config.monitor.ocr_enabled is False
    assert config.llm.engine == "openai"


def test_load_config_with_defaults():
    config = load_config(None)
    assert config.server.port == 8080
    assert config.monitor.interval_sec == 30
    assert config.monitor.ocr_enabled is True
    assert config.llm.engine == "kimi"
    assert config.scheduler.trigger_time == "18:00"
    assert config.auto_approve.enabled is True
    assert config.auto_approve.timeout_min == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement config module**

`auto_daily_log/config.py`:
```python
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class ServerConfig(BaseModel):
    port: int = 8080
    host: str = "0.0.0.0"


class PrivacyConfig(BaseModel):
    blocked_apps: list[str] = []
    blocked_urls: list[str] = []


class MonitorConfig(BaseModel):
    interval_sec: int = 30
    ocr_enabled: bool = True
    ocr_engine: str = "auto"
    screenshot_retention_days: int = 7
    privacy: PrivacyConfig = PrivacyConfig()


class GitRepoConfig(BaseModel):
    path: str
    author_email: str = ""


class GitConfig(BaseModel):
    repos: list[GitRepoConfig] = []


class JiraConfig(BaseModel):
    server_url: str = ""
    pat: str = ""


class LLMProviderConfig(BaseModel):
    api_key: str = ""
    model: str = ""
    base_url: str = ""


class LLMConfig(BaseModel):
    engine: str = "kimi"
    kimi: LLMProviderConfig = LLMProviderConfig(
        model="moonshot-v1-8k", base_url="https://api.moonshot.cn/v1"
    )
    openai: LLMProviderConfig = LLMProviderConfig(
        model="gpt-4o", base_url="https://api.openai.com/v1"
    )
    ollama: LLMProviderConfig = LLMProviderConfig(
        model="llama3", base_url="http://localhost:11434"
    )
    claude: LLMProviderConfig = LLMProviderConfig(
        model="claude-sonnet-4-20250514"
    )


class SchedulerConfig(BaseModel):
    enabled: bool = True
    trigger_time: str = "18:00"


class AutoApproveConfig(BaseModel):
    enabled: bool = True
    timeout_min: int = 30


class SystemConfig(BaseModel):
    language: str = "zh"
    data_retention_days: int = 90


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    monitor: MonitorConfig = MonitorConfig()
    git: GitConfig = GitConfig()
    jira: JiraConfig = JiraConfig()
    llm: LLMConfig = LLMConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    auto_approve: AutoApproveConfig = AutoApproveConfig()
    system: SystemConfig = SystemConfig()


def load_config(config_path: Optional[str]) -> AppConfig:
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return AppConfig(**data)
    return AppConfig()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_config.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/config.py tests/test_config.py
git commit -m "feat: YAML config loading with Pydantic validation and defaults"
```

---

## Task 4: Monitor - Platform Abstraction Layer

Adapt from `/Users/conner/Zone/code/ai_project/polars_free_worklog/activity_monitor/`.

**Files:**
- Create: `auto_daily_log/monitor/__init__.py`
- Create: `auto_daily_log/monitor/platforms/__init__.py`
- Create: `auto_daily_log/monitor/platforms/base.py`
- Create: `auto_daily_log/monitor/platforms/macos.py`
- Create: `auto_daily_log/monitor/platforms/windows.py`
- Create: `auto_daily_log/monitor/platforms/linux.py`
- Create: `auto_daily_log/monitor/platforms/detect.py`
- Create: `tests/test_monitor_platform.py`

- [ ] **Step 1: Write failing test for platform detection**

`tests/test_monitor_platform.py`:
```python
import pytest
from unittest.mock import patch
from auto_daily_log.monitor.platforms.detect import get_current_platform, get_platform_module
from auto_daily_log.monitor.platforms.base import PlatformAPI


def test_get_current_platform():
    platform = get_current_platform()
    assert platform in ("macos", "windows", "linux")


def test_get_platform_module_returns_platform_api():
    module = get_platform_module()
    assert isinstance(module, PlatformAPI)


def test_platform_api_has_required_methods():
    module = get_platform_module()
    assert hasattr(module, "get_frontmost_app")
    assert hasattr(module, "get_window_title")
    assert hasattr(module, "get_browser_tab")
    assert hasattr(module, "get_wecom_chat_name")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_monitor_platform.py -v`
Expected: FAIL

- [ ] **Step 3: Implement platform abstraction**

`auto_daily_log/monitor/__init__.py`: empty file

`auto_daily_log/monitor/platforms/__init__.py`: empty file

`auto_daily_log/monitor/platforms/base.py`:
```python
from abc import ABC, abstractmethod
from typing import Optional, Tuple


class PlatformAPI(ABC):
    @abstractmethod
    def get_frontmost_app(self) -> Optional[str]:
        ...

    @abstractmethod
    def get_window_title(self, app_name: str) -> Optional[str]:
        ...

    @abstractmethod
    def get_browser_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Returns (title, url)"""
        ...

    @abstractmethod
    def get_wecom_chat_name(self, app_name: str) -> Optional[str]:
        ...
```

`auto_daily_log/monitor/platforms/detect.py`:
```python
import platform
from typing import Literal
from .base import PlatformAPI

PlatformType = Literal["macos", "windows", "linux"]


def get_current_platform() -> PlatformType:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "windows":
        return "windows"
    return "linux"


def get_platform_module() -> PlatformAPI:
    current = get_current_platform()
    if current == "macos":
        from .macos import MacOSAPI
        return MacOSAPI()
    elif current == "windows":
        from .windows import WindowsAPI
        return WindowsAPI()
    else:
        from .linux import LinuxAPI
        return LinuxAPI()
```

- [ ] **Step 4: Implement macOS platform adapter**

Adapt from `polars_free_worklog/activity_monitor/mac_apis.py`.

`auto_daily_log/monitor/platforms/macos.py`:
```python
import subprocess
from typing import Optional, Tuple
from .base import PlatformAPI

_BROWSERS = {"google chrome", "microsoft edge", "brave browser", "arc", "safari"}
_CHROMIUM = {"google chrome", "microsoft edge", "brave browser", "arc"}


def _run_osascript(script: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout.strip()
        return output if output and output != "missing value" else None
    except (subprocess.TimeoutExpired, Exception):
        return None


class MacOSAPI(PlatformAPI):
    def get_frontmost_app(self) -> Optional[str]:
        return _run_osascript(
            'tell application "System Events" to get name of first '
            "application process whose frontmost is true"
        )

    def get_window_title(self, app_name: str) -> Optional[str]:
        return _run_osascript(
            f'tell application "System Events" to tell process "{app_name}" '
            "to get name of front window"
        )

    def get_browser_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        if not app_name:
            return None, None
        app_lower = app_name.lower()
        if app_lower in _CHROMIUM:
            return self._get_chromium_tab(app_name)
        if app_lower == "safari":
            return self._get_safari_tab()
        return None, None

    def get_wecom_chat_name(self, app_name: str) -> Optional[str]:
        if not app_name:
            return None
        lower = app_name.lower()
        if lower not in ("wechat", "wecom", "企业微信", "微信"):
            return None
        title = self.get_window_title(app_name)
        if title and title.lower() not in ("wechat", "wecom", "企业微信", "微信"):
            return title
        return None

    def _get_chromium_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        title = _run_osascript(
            f'tell application "{app_name}" to get title of active tab of front window'
        )
        url = _run_osascript(
            f'tell application "{app_name}" to get URL of active tab of front window'
        )
        return title, url

    def _get_safari_tab(self) -> Tuple[Optional[str], Optional[str]]:
        title = _run_osascript(
            'tell application "Safari" to get name of current tab of front window'
        )
        url = _run_osascript(
            'tell application "Safari" to get URL of current tab of front window'
        )
        return title, url
```

- [ ] **Step 5: Implement Windows platform adapter (stub for non-Windows)**

`auto_daily_log/monitor/platforms/windows.py`:
```python
import subprocess
import platform as _platform
from typing import Optional, Tuple
from .base import PlatformAPI


def _run_powershell(cmd: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["powershell", "-Command", cmd],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        return output if output else None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None


class WindowsAPI(PlatformAPI):
    def get_frontmost_app(self) -> Optional[str]:
        return _run_powershell(
            "(Get-Process | Where-Object {$_.MainWindowHandle -eq "
            "(Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] "
            "public static extern IntPtr GetForegroundWindow();' "
            "-Name Win32 -PassThru)::GetForegroundWindow()}).ProcessName"
        )

    def get_window_title(self, app_name: str) -> Optional[str]:
        return _run_powershell(
            "(Get-Process | Where-Object {$_.MainWindowHandle -eq "
            "(Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] "
            "public static extern IntPtr GetForegroundWindow();' "
            "-Name Win32 -PassThru)::GetForegroundWindow()}).MainWindowTitle"
        )

    def get_browser_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        # Windows: extract URL from window title for Chromium browsers
        title = self.get_window_title(app_name)
        return title, None

    def get_wecom_chat_name(self, app_name: str) -> Optional[str]:
        if not app_name:
            return None
        lower = app_name.lower()
        if lower not in ("wechat", "wecom", "企业微信", "微信"):
            return None
        title = self.get_window_title(app_name)
        if title:
            # Window title format: "chat_name - WeChat"
            parts = title.rsplit(" - ", 1)
            if len(parts) == 2:
                return parts[0].strip()
        return None
```

- [ ] **Step 6: Implement Linux platform adapter**

`auto_daily_log/monitor/platforms/linux.py`:
```python
import subprocess
from typing import Optional, Tuple
from .base import PlatformAPI


def _run_command(cmd: list[str]) -> Optional[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout.strip()
        return output if output else None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None


class LinuxAPI(PlatformAPI):
    def get_frontmost_app(self) -> Optional[str]:
        window_id = _run_command(["xdotool", "getactivewindow"])
        if not window_id:
            return None
        wm_class = _run_command(["xprop", "-id", window_id, "WM_CLASS"])
        if wm_class and "=" in wm_class:
            parts = wm_class.split("=", 1)[1].strip().strip('"').split('", "')
            return parts[-1] if parts else None
        return None

    def get_window_title(self, app_name: str) -> Optional[str]:
        window_id = _run_command(["xdotool", "getactivewindow"])
        if not window_id:
            return None
        return _run_command(["xdotool", "getwindowname", window_id])

    def get_browser_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        title = self.get_window_title(app_name)
        return title, None

    def get_wecom_chat_name(self, app_name: str) -> Optional[str]:
        if not app_name:
            return None
        lower = app_name.lower()
        if lower not in ("wechat", "wecom", "企业微信", "微信"):
            return None
        title = self.get_window_title(app_name)
        if title:
            parts = title.rsplit(" - ", 1)
            if len(parts) == 2:
                return parts[0].strip()
        return None
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_monitor_platform.py -v`
Expected: 3 tests PASS

- [ ] **Step 8: Commit**

```bash
git add auto_daily_log/monitor/ tests/test_monitor_platform.py
git commit -m "feat: cross-platform activity capture with macOS/Windows/Linux adapters"
```

---

## Task 5: Monitor - Activity Classifier

**Files:**
- Create: `auto_daily_log/monitor/classifier.py`
- Create: `tests/test_classifier.py`

- [ ] **Step 1: Write failing tests**

`tests/test_classifier.py`:
```python
import pytest
from auto_daily_log.monitor.classifier import classify_activity


def test_classify_coding_by_app():
    cat, conf, hints = classify_activity("Visual Studio Code", "main.py — project", None)
    assert cat == "coding"
    assert conf >= 0.85
    assert "editor" in hints


def test_classify_meeting_by_app():
    cat, conf, hints = classify_activity("zoom.us", "Sprint Review", None)
    assert cat == "meeting"
    assert conf >= 0.90


def test_classify_research_by_url():
    cat, conf, hints = classify_activity("Google Chrome", "Issues", "https://github.com/org/repo/issues")
    assert cat == "research"


def test_classify_browsing_generic_browser():
    cat, conf, hints = classify_activity("Google Chrome", "Some Page", "https://example.com")
    assert cat == "browsing"
    assert conf >= 0.5


def test_classify_communication():
    cat, conf, hints = classify_activity("Slack", "general - Team", None)
    assert cat == "communication"


def test_classify_unknown():
    cat, conf, hints = classify_activity("SomeRandomApp", "Window", None)
    assert cat == "other"
    assert conf < 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classifier.py -v`
Expected: FAIL

- [ ] **Step 3: Implement classifier**

Adapt from `polars_free_worklog/activity_monitor/classifier.py`.

`auto_daily_log/monitor/classifier.py`:
```python
import re
from typing import List, Optional, Tuple

_APP_CATEGORIES = {
    "meeting": {
        "apps": ["zoom", "teams", "webex", "google meet", "facetime", "腾讯会议", "飞书会议"],
        "confidence": 0.95,
        "hint": "meeting-app",
    },
    "coding": {
        "apps": [
            "visual studio code", "cursor", "pycharm", "intellij", "goland",
            "clion", "xcode", "android studio", "webstorm", "rustrover",
            "sublime text", "vim", "neovim",
        ],
        "confidence": 0.92,
        "hint": "editor",
    },
    "coding_terminal": {
        "apps": ["terminal", "iterm2", "iterm", "warp", "alacritty", "kitty", "hyper", "windows terminal"],
        "confidence": 0.85,
        "hint": "terminal",
    },
    "communication": {
        "apps": [
            "slack", "discord", "telegram", "wechat", "wecom", "企业微信",
            "微信", "mail", "outlook", "thunderbird", "飞书", "钉钉",
        ],
        "confidence": 0.85,
        "hint": "comms",
    },
    "design": {
        "apps": ["figma", "sketch", "adobe xd"],
        "confidence": 0.90,
        "hint": "design-app",
    },
    "writing": {
        "apps": ["notion", "obsidian", "word", "pages", "typora"],
        "confidence": 0.85,
        "hint": "docs",
    },
    "reading": {
        "apps": ["preview", "skim", "adobe acrobat"],
        "confidence": 0.75,
        "hint": "pdf",
    },
}

_DOMAIN_CATEGORIES = [
    (r"(?:^|\.)zoom\.us", "meeting"),
    (r"meet\.google\.com", "meeting"),
    (r"teams\.microsoft\.com", "meeting"),
    (r"webex\.com", "meeting"),
    (r"figma\.com", "design"),
    (r"docs\.google\.com", "writing"),
    (r"notion\.so", "writing"),
    (r"github\.com", "research"),
    (r"gitlab\.com", "research"),
    (r"stackoverflow\.com", "research"),
    (r"stackexchange\.com", "research"),
    (r"kaggle\.com", "reading"),
    (r"arxiv\.org", "reading"),
]

_BROWSERS = {"google chrome", "chrome", "microsoft edge", "brave browser", "arc", "safari", "firefox"}

_CODE_FILE_PATTERNS = re.compile(
    r"\.(py|ts|tsx|js|jsx|java|go|rs|cpp|c|h|rb|php|swift|kt|scala|sql|vue|svelte|ipynb)"
    r"[\s\-—]",
    re.IGNORECASE,
)

_MEETING_KEYWORDS = re.compile(
    r"(meeting|standup|retro|sprint|review|daily|sync|huddle|会议|站会|评审)",
    re.IGNORECASE,
)


def classify_activity(
    app_name: Optional[str],
    window_title: Optional[str],
    url: Optional[str],
) -> Tuple[str, float, List[str]]:
    """Returns (category, confidence, hints)."""
    if not app_name:
        return "other", 0.4, []

    app_lower = app_name.lower()
    hints: List[str] = []

    # 1. Direct app match
    for cat, info in _APP_CATEGORIES.items():
        for app in info["apps"]:
            if app in app_lower:
                real_cat = "coding" if cat == "coding_terminal" else cat
                return real_cat, info["confidence"], [info["hint"]]

    # 2. Browser → check URL domain
    if app_lower in _BROWSERS or "browser" in app_lower:
        hints.append("browser")
        if url:
            for pattern, cat in _DOMAIN_CATEGORIES:
                if re.search(pattern, url):
                    return cat, 0.70, hints

        # 3. Check window title for code files
        if window_title and _CODE_FILE_PATTERNS.search(window_title):
            return "coding", 0.70, hints + ["code-file"]

        # 4. Check window title for meeting keywords
        if window_title and _MEETING_KEYWORDS.search(window_title):
            return "meeting", 0.70, hints + ["meeting-keyword"]

        return "browsing", 0.70, hints

    # 5. Window title fallback for non-browser apps
    if window_title:
        if _CODE_FILE_PATTERNS.search(window_title):
            return "coding", 0.70, ["code-file"]
        if _MEETING_KEYWORDS.search(window_title):
            return "meeting", 0.70, ["meeting-keyword"]

    return "other", 0.40, []
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_classifier.py -v`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/monitor/classifier.py tests/test_classifier.py
git commit -m "feat: activity classifier with app/URL/title-based categorization"
```

---

## Task 6: Monitor - Screenshot + OCR

**Files:**
- Create: `auto_daily_log/monitor/screenshot.py`
- Create: `auto_daily_log/monitor/ocr.py`
- Create: `tests/test_ocr.py`

- [ ] **Step 1: Write test for OCR engine selection**

`tests/test_ocr.py`:
```python
import pytest
from unittest.mock import patch
from auto_daily_log.monitor.ocr import get_ocr_engine


@patch("auto_daily_log.monitor.ocr.get_current_platform", return_value="macos")
def test_auto_selects_vision_on_macos(mock_platform):
    engine = get_ocr_engine("auto")
    assert engine == "vision"


@patch("auto_daily_log.monitor.ocr.get_current_platform", return_value="windows")
def test_auto_selects_winocr_on_windows(mock_platform):
    engine = get_ocr_engine("auto")
    assert engine == "winocr"


@patch("auto_daily_log.monitor.ocr.get_current_platform", return_value="linux")
def test_auto_selects_tesseract_on_linux(mock_platform):
    engine = get_ocr_engine("auto")
    assert engine == "tesseract"


def test_explicit_engine_override():
    engine = get_ocr_engine("tesseract")
    assert engine == "tesseract"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ocr.py -v`
Expected: FAIL

- [ ] **Step 3: Implement screenshot module**

`auto_daily_log/monitor/screenshot.py`:
```python
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from .platforms.detect import get_current_platform


def capture_screenshot(output_dir: Path) -> Optional[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    filepath = output_dir / filename

    platform = get_current_platform()
    try:
        if platform == "macos":
            subprocess.run(
                ["screencapture", "-x", str(filepath)],
                timeout=10, capture_output=True,
            )
        elif platform == "windows":
            ps_script = (
                f"Add-Type -AssemblyName System.Windows.Forms;"
                f"$bmp = New-Object System.Drawing.Bitmap("
                f"[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width,"
                f"[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height);"
                f"$g = [System.Drawing.Graphics]::FromImage($bmp);"
                f"$g.CopyFromScreen(0,0,0,0,$bmp.Size);"
                f'$bmp.Save("{filepath}")'
            )
            subprocess.run(
                ["powershell", "-Command", ps_script],
                timeout=30, capture_output=True,
            )
        else:  # linux
            for tool_cmd in [
                ["gnome-screenshot", "-f", str(filepath)],
                ["import", "-window", "root", str(filepath)],
                ["scrot", str(filepath)],
                ["maim", str(filepath)],
            ]:
                try:
                    subprocess.run(tool_cmd, timeout=10, capture_output=True)
                    if filepath.exists():
                        break
                except FileNotFoundError:
                    continue

        return filepath if filepath.exists() else None
    except (subprocess.TimeoutExpired, Exception):
        return None
```

- [ ] **Step 4: Implement OCR module**

`auto_daily_log/monitor/ocr.py`:
```python
import subprocess
from pathlib import Path
from typing import Optional

from .platforms.detect import get_current_platform


def get_ocr_engine(configured: str) -> str:
    if configured != "auto":
        return configured
    platform = get_current_platform()
    return {"macos": "vision", "windows": "winocr", "linux": "tesseract"}[platform]


def ocr_image(image_path: Path, engine: str = "auto") -> Optional[str]:
    resolved_engine = get_ocr_engine(engine)

    if resolved_engine == "vision":
        return _ocr_vision(image_path)
    elif resolved_engine == "winocr":
        return _ocr_winocr(image_path)
    else:
        return _ocr_tesseract(image_path)


def _ocr_vision(image_path: Path) -> Optional[str]:
    """macOS Vision framework OCR."""
    try:
        import objc
        from Quartz import CIImage
        from Foundation import NSURL
        import Vision

        url = NSURL.fileURLWithPath_(str(image_path))
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLanguages_(["zh-Hans", "en"])
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)

        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        handler.performRequests_error_([request], None)

        results = request.results()
        if not results:
            return None
        texts = []
        for obs in results:
            candidate = obs.topCandidates_(1)
            if candidate:
                texts.append(candidate[0].string())
        return "\n".join(texts) if texts else None
    except Exception:
        return _ocr_tesseract(image_path)  # fallback


def _ocr_winocr(image_path: Path) -> Optional[str]:
    """Windows OCR via winocr package."""
    try:
        import winocr
        import asyncio
        result = asyncio.run(winocr.recognize_pil(image_path, lang="zh-Hans-CN"))
        return result.text if result and result.text else None
    except Exception:
        return _ocr_tesseract(image_path)  # fallback


def _ocr_tesseract(image_path: Path) -> Optional[str]:
    """Tesseract OCR fallback."""
    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "-l", "chi_sim+eng"],
            capture_output=True, text=True, timeout=30,
        )
        text = result.stdout.strip()
        return text if text else None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_ocr.py -v`
Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add auto_daily_log/monitor/screenshot.py auto_daily_log/monitor/ocr.py tests/test_ocr.py
git commit -m "feat: cross-platform screenshot capture and OCR (Vision/winocr/Tesseract)"
```

---

## Task 7: Monitor - Background Service

**Files:**
- Create: `auto_daily_log/monitor/service.py`
- Create: `tests/test_monitor_service.py`

- [ ] **Step 1: Write failing test**

`tests/test_monitor_service.py`:
```python
import json
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock
from pathlib import Path
from auto_daily_log.monitor.service import MonitorService
from auto_daily_log.models.database import Database
from auto_daily_log.config import MonitorConfig


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_sample_once_stores_activity(db, tmp_path):
    config = MonitorConfig(ocr_enabled=False, interval_sec=30)
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    with patch.object(service, "_capture_raw") as mock_capture:
        mock_capture.return_value = {
            "app_name": "IntelliJ IDEA",
            "window_title": "Main.java — project",
            "url": None,
            "wecom_group": None,
            "screenshot_path": None,
            "ocr_text": None,
        }
        await service.sample_once()

    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 1
    assert rows[0]["app_name"] == "IntelliJ IDEA"
    assert rows[0]["category"] == "coding"


@pytest.mark.asyncio
async def test_privacy_blocklist_skips_app(db, tmp_path):
    config = MonitorConfig(
        ocr_enabled=False,
        privacy={"blocked_apps": ["WeChat"], "blocked_urls": []},
    )
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    with patch.object(service, "_capture_raw") as mock_capture:
        mock_capture.return_value = {
            "app_name": "WeChat",
            "window_title": "Chat",
            "url": None,
            "wecom_group": None,
            "screenshot_path": None,
            "ocr_text": None,
        }
        await service.sample_once()

    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_merge_consecutive_same_activity(db, tmp_path):
    config = MonitorConfig(ocr_enabled=False, interval_sec=30)
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    raw = {
        "app_name": "IntelliJ IDEA",
        "window_title": "Main.java",
        "url": None,
        "wecom_group": None,
        "screenshot_path": None,
        "ocr_text": None,
    }
    with patch.object(service, "_capture_raw", return_value=raw):
        await service.sample_once()
        await service.sample_once()

    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 1
    assert rows[0]["duration_sec"] == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_monitor_service.py -v`
Expected: FAIL

- [ ] **Step 3: Implement MonitorService**

`auto_daily_log/monitor/service.py`:
```python
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import MonitorConfig
from ..models.database import Database
from .classifier import classify_activity
from .platforms.detect import get_platform_module
from .screenshot import capture_screenshot
from .ocr import ocr_image


class MonitorService:
    def __init__(self, db: Database, config: MonitorConfig, screenshot_dir: Path):
        self._db = db
        self._config = config
        self._screenshot_dir = screenshot_dir
        self._platform = get_platform_module()
        self._last_app: Optional[str] = None
        self._last_title: Optional[str] = None
        self._last_id: Optional[int] = None
        self._running = False

    def _capture_raw(self) -> dict:
        app_name = self._platform.get_frontmost_app()
        window_title = self._platform.get_window_title(app_name) if app_name else None
        tab_title, url = (
            self._platform.get_browser_tab(app_name) if app_name else (None, None)
        )
        wecom_group = self._platform.get_wecom_chat_name(app_name) if app_name else None

        screenshot_path = None
        ocr_text = None
        if self._config.ocr_enabled:
            today_dir = self._screenshot_dir / datetime.now().strftime("%Y-%m-%d")
            screenshot_path = capture_screenshot(today_dir)
            if screenshot_path:
                ocr_text = ocr_image(screenshot_path, self._config.ocr_engine)

        return {
            "app_name": app_name,
            "window_title": tab_title or window_title,
            "url": url,
            "wecom_group": wecom_group,
            "screenshot_path": str(screenshot_path) if screenshot_path else None,
            "ocr_text": ocr_text,
        }

    def _is_blocked(self, raw: dict) -> bool:
        app = raw.get("app_name") or ""
        url = raw.get("url") or ""
        for blocked in self._config.privacy.blocked_apps:
            if blocked.lower() in app.lower():
                return True
        for blocked in self._config.privacy.blocked_urls:
            if blocked.lower() in url.lower():
                return True
        return False

    async def sample_once(self) -> None:
        raw = self._capture_raw()
        if not raw["app_name"] or self._is_blocked(raw):
            return

        app_name = raw["app_name"]
        window_title = raw["window_title"]

        # Merge consecutive same activity
        if app_name == self._last_app and window_title == self._last_title and self._last_id:
            await self._db.execute(
                "UPDATE activities SET duration_sec = duration_sec + ? WHERE id = ?",
                (self._config.interval_sec, self._last_id),
            )
            return

        category, confidence, hints = classify_activity(app_name, window_title, raw["url"])

        signals = {
            "browser_url": raw["url"],
            "wecom_group_name": raw["wecom_group"],
            "screenshot_path": raw["screenshot_path"],
            "ocr_text": raw["ocr_text"],
            "hints": hints,
        }

        row_id = await self._db.execute(
            """INSERT INTO activities
               (timestamp, app_name, window_title, category, confidence, url, signals, duration_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                app_name,
                window_title,
                category,
                confidence,
                raw["url"],
                json.dumps(signals, ensure_ascii=False),
                self._config.interval_sec,
            ),
        )

        self._last_app = app_name
        self._last_title = window_title
        self._last_id = row_id

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.sample_once()
            except Exception as e:
                print(f"[Monitor] Error: {e}")
            await asyncio.sleep(self._config.interval_sec)

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_monitor_service.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/monitor/service.py tests/test_monitor_service.py
git commit -m "feat: monitor background service with activity merging and privacy blocklist"
```

---

## Task 8: Git Collector

**Files:**
- Create: `auto_daily_log/collector/__init__.py`
- Create: `auto_daily_log/collector/git_collector.py`
- Create: `tests/test_git_collector.py`

- [ ] **Step 1: Write failing test**

`tests/test_git_collector.py`:
```python
import json
import pytest
import pytest_asyncio
from pathlib import Path
from auto_daily_log.collector.git_collector import GitCollector
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo with a commit for testing."""
    import subprocess
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo_path, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True
    )
    (repo_path / "hello.py").write_text("print('hello')")
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add hello script"],
        cwd=repo_path, capture_output=True,
    )
    return repo_path


@pytest.mark.asyncio
async def test_collect_todays_commits(db, git_repo):
    # Register the repo
    await db.execute(
        "INSERT INTO git_repos (path, author_email, is_active) VALUES (?, ?, ?)",
        (str(git_repo), "test@example.com", 1),
    )
    collector = GitCollector(db)
    count = await collector.collect_today()
    assert count >= 1

    commits = await db.fetch_all("SELECT * FROM git_commits")
    assert len(commits) >= 1
    assert "hello" in commits[0]["message"]
    assert commits[0]["insertions"] >= 1


@pytest.mark.asyncio
async def test_skip_inactive_repos(db, git_repo):
    await db.execute(
        "INSERT INTO git_repos (path, author_email, is_active) VALUES (?, ?, ?)",
        (str(git_repo), "test@example.com", 0),
    )
    collector = GitCollector(db)
    count = await collector.collect_today()
    assert count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_collector.py -v`
Expected: FAIL

- [ ] **Step 3: Implement GitCollector**

`auto_daily_log/collector/__init__.py`: empty file

`auto_daily_log/collector/git_collector.py`:
```python
import json
import subprocess
from datetime import date, datetime
from typing import Optional

from ..models.database import Database


class GitCollector:
    def __init__(self, db: Database):
        self._db = db

    async def collect_today(self, target_date: Optional[date] = None) -> int:
        target = target_date or date.today()
        date_str = target.isoformat()
        after = f"{date_str} 00:00:00"
        before = f"{date_str} 23:59:59"

        repos = await self._db.fetch_all(
            "SELECT * FROM git_repos WHERE is_active = 1"
        )
        total = 0
        for repo in repos:
            count = await self._collect_repo(repo, after, before, date_str)
            total += count
        return total

    async def _collect_repo(
        self, repo: dict, after: str, before: str, date_str: str
    ) -> int:
        path = repo["path"]
        email = repo["author_email"]
        repo_id = repo["id"]

        # Format: hash|||message|||author|||date|||files
        fmt = "%H|||%s|||%ae|||%aI|||"
        cmd = [
            "git", "-C", path, "log",
            f"--after={after}", f"--before={before}",
            f"--format={fmt}",
        ]
        if email:
            cmd.append(f"--author={email}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return 0

        if result.returncode != 0 or not result.stdout.strip():
            return 0

        count = 0
        for line in result.stdout.strip().split("\n"):
            if "|||" not in line:
                continue
            parts = line.split("|||")
            if len(parts) < 4:
                continue
            commit_hash, message, author, committed_at = parts[0], parts[1], parts[2], parts[3]

            # Check for duplicate
            existing = await self._db.fetch_one(
                "SELECT id FROM git_commits WHERE hash = ? AND repo_id = ?",
                (commit_hash, repo_id),
            )
            if existing:
                continue

            # Get file stats
            stat_cmd = [
                "git", "-C", path, "diff-tree", "--no-commit-id",
                "--numstat", "-r", commit_hash,
            ]
            stat_result = subprocess.run(
                stat_cmd, capture_output=True, text=True, timeout=10
            )
            files = []
            insertions = 0
            deletions = 0
            if stat_result.returncode == 0:
                for stat_line in stat_result.stdout.strip().split("\n"):
                    if not stat_line:
                        continue
                    stat_parts = stat_line.split("\t")
                    if len(stat_parts) >= 3:
                        ins = int(stat_parts[0]) if stat_parts[0] != "-" else 0
                        dels = int(stat_parts[1]) if stat_parts[1] != "-" else 0
                        insertions += ins
                        deletions += dels
                        files.append(stat_parts[2])

            await self._db.execute(
                """INSERT INTO git_commits
                   (repo_id, hash, message, author, committed_at, files_changed, insertions, deletions, date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    repo_id, commit_hash, message, author, committed_at,
                    json.dumps(files), insertions, deletions, date_str,
                ),
            )
            count += 1
        return count
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_git_collector.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/collector/ tests/test_git_collector.py
git commit -m "feat: git commit collector with per-repo filtering and deduplication"
```

---

## Task 9: LLM Engine Abstraction + Kimi Adapter

**Files:**
- Create: `auto_daily_log/summarizer/__init__.py`
- Create: `auto_daily_log/summarizer/engine.py`
- Create: `auto_daily_log/summarizer/kimi.py`
- Create: `auto_daily_log/summarizer/openai_engine.py`
- Create: `auto_daily_log/summarizer/ollama.py`
- Create: `auto_daily_log/summarizer/claude_engine.py`
- Create: `tests/test_llm_engine.py`

- [ ] **Step 1: Write failing test**

`tests/test_llm_engine.py`:
```python
import pytest
from auto_daily_log.summarizer.engine import get_llm_engine, LLMEngine
from auto_daily_log.config import LLMConfig, LLMProviderConfig


def test_get_kimi_engine():
    config = LLMConfig(engine="kimi", kimi=LLMProviderConfig(
        api_key="test-key", model="moonshot-v1-8k", base_url="https://api.moonshot.cn/v1"
    ))
    engine = get_llm_engine(config)
    assert isinstance(engine, LLMEngine)
    assert engine.name == "kimi"


def test_get_openai_engine():
    config = LLMConfig(engine="openai", openai=LLMProviderConfig(
        api_key="test-key", model="gpt-4o", base_url="https://api.openai.com/v1"
    ))
    engine = get_llm_engine(config)
    assert engine.name == "openai"


def test_get_ollama_engine():
    config = LLMConfig(engine="ollama", ollama=LLMProviderConfig(
        model="llama3", base_url="http://localhost:11434"
    ))
    engine = get_llm_engine(config)
    assert engine.name == "ollama"


def test_get_claude_engine():
    config = LLMConfig(engine="claude", claude=LLMProviderConfig(
        api_key="test-key", model="claude-sonnet-4-20250514"
    ))
    engine = get_llm_engine(config)
    assert engine.name == "claude"


def test_unknown_engine_raises():
    config = LLMConfig(engine="unknown")
    with pytest.raises(ValueError, match="Unknown LLM engine"):
        get_llm_engine(config)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_engine.py -v`
Expected: FAIL

- [ ] **Step 3: Implement engine abstraction and all adapters**

`auto_daily_log/summarizer/__init__.py`: empty file

`auto_daily_log/summarizer/engine.py`:
```python
from abc import ABC, abstractmethod
from ..config import LLMConfig


class LLMEngine(ABC):
    name: str

    @abstractmethod
    async def generate(self, prompt: str) -> str:
        ...


def get_llm_engine(config: LLMConfig) -> LLMEngine:
    engine_name = config.engine.lower()
    if engine_name == "kimi":
        from .kimi import KimiEngine
        return KimiEngine(config.kimi)
    elif engine_name == "openai":
        from .openai_engine import OpenAIEngine
        return OpenAIEngine(config.openai)
    elif engine_name == "ollama":
        from .ollama import OllamaEngine
        return OllamaEngine(config.ollama)
    elif engine_name == "claude":
        from .claude_engine import ClaudeEngine
        return ClaudeEngine(config.claude)
    else:
        raise ValueError(f"Unknown LLM engine: {engine_name}")
```

`auto_daily_log/summarizer/kimi.py`:
```python
import httpx
from ..config import LLMProviderConfig
from .engine import LLMEngine


class KimiEngine(LLMEngine):
    name = "kimi"

    def __init__(self, config: LLMProviderConfig):
        self._config = config

    async def generate(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
```

`auto_daily_log/summarizer/openai_engine.py`:
```python
import httpx
from ..config import LLMProviderConfig
from .engine import LLMEngine


class OpenAIEngine(LLMEngine):
    name = "openai"

    def __init__(self, config: LLMProviderConfig):
        self._config = config

    async def generate(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
```

`auto_daily_log/summarizer/ollama.py`:
```python
import httpx
from ..config import LLMProviderConfig
from .engine import LLMEngine


class OllamaEngine(LLMEngine):
    name = "ollama"

    def __init__(self, config: LLMProviderConfig):
        self._config = config

    async def generate(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._config.base_url}/api/generate",
                json={
                    "model": self._config.model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["response"]
```

`auto_daily_log/summarizer/claude_engine.py`:
```python
import httpx
from ..config import LLMProviderConfig
from .engine import LLMEngine


class ClaudeEngine(LLMEngine):
    name = "claude"

    def __init__(self, config: LLMProviderConfig):
        self._config = config

    async def generate(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._config.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._config.model,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_engine.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/summarizer/ tests/test_llm_engine.py
git commit -m "feat: LLM engine abstraction with Kimi/OpenAI/Ollama/Claude adapters"
```

---

## Task 10: Summarizer - Prompt + Worklog Generation

**Files:**
- Create: `auto_daily_log/summarizer/prompt.py`
- Create: `auto_daily_log/summarizer/summarizer.py`
- Create: `tests/test_summarizer.py`

- [ ] **Step 1: Write failing test**

`tests/test_summarizer.py`:
```python
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from auto_daily_log.summarizer.summarizer import WorklogSummarizer
from auto_daily_log.summarizer.prompt import DEFAULT_SUMMARIZE_PROMPT, render_prompt
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


def test_default_prompt_has_placeholders():
    assert "{jira_issues}" in DEFAULT_SUMMARIZE_PROMPT
    assert "{git_commits}" in DEFAULT_SUMMARIZE_PROMPT
    assert "{activities}" in DEFAULT_SUMMARIZE_PROMPT
    assert "{date}" in DEFAULT_SUMMARIZE_PROMPT


def test_render_prompt():
    rendered = render_prompt(
        DEFAULT_SUMMARIZE_PROMPT,
        date="2026-04-12",
        jira_issues="- PROJ-101: Fix SQL parser",
        git_commits="- 10:30 fix: resolve JOIN issue",
        activities="- 9:00-11:00 IntelliJ (Main.java) coding",
    )
    assert "PROJ-101" in rendered
    assert "2026-04-12" in rendered
    assert "IntelliJ" in rendered


@pytest.mark.asyncio
async def test_summarizer_generates_drafts(db):
    # Setup: add an issue and some activities
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, description, is_active) VALUES (?, ?, ?, ?)",
        ("PROJ-101", "Fix SQL parser", "Fix JOIN handling in parser", 1),
    )
    await db.execute(
        """INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("2026-04-12T10:00:00", "IntelliJ IDEA", "AstToPlanConverter.java", "coding", 0.92, 3600),
    )

    mock_engine = AsyncMock()
    mock_engine.generate.return_value = json.dumps([
        {"issue_key": "PROJ-101", "time_spent_hours": 1.0, "summary": "修复了SQL解析器的JOIN处理逻辑"}
    ])

    summarizer = WorklogSummarizer(db, mock_engine)
    drafts = await summarizer.generate_drafts("2026-04-12")

    assert len(drafts) == 1
    assert drafts[0]["issue_key"] == "PROJ-101"
    assert drafts[0]["time_spent_sec"] == 3600

    # Verify stored in DB
    rows = await db.fetch_all("SELECT * FROM worklog_drafts WHERE date = '2026-04-12'")
    assert len(rows) == 1
    assert rows[0]["status"] == "pending_review"

    # Verify audit log
    logs = await db.fetch_all("SELECT * FROM audit_logs")
    assert len(logs) == 1
    assert logs[0]["action"] == "created"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_summarizer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement prompt module**

`auto_daily_log/summarizer/prompt.py`:
```python
DEFAULT_SUMMARIZE_PROMPT = """你是工作日志助手。以下是用户今天的工作数据：

【日期】{date}

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
  {{
    "issue_key": "PROJ-101",
    "time_spent_hours": 3.5,
    "summary": "..."
  }}
]"""


DEFAULT_AUTO_APPROVE_PROMPT = """你是工作日志审批助手。请检查以下工作日志草稿：

【日期】{date}
【Jira 任务】{issue_key}: {issue_summary}
【工时】{time_spent_hours} 小时
【日志内容】{summary}
【关联 Git Commits】{git_commits}

请判断：
1. 日志内容是否与 Git commits 和任务描述一致？
2. 工时是否合理？
3. 日志描述是否清晰、具体？

如果合格返回 {{"approved": true}}
如果不合格返回 {{"approved": false, "reason": "不通过原因"}}"""


def render_prompt(template: str, **kwargs) -> str:
    return template.format(**kwargs)
```

- [ ] **Step 4: Implement WorklogSummarizer**

`auto_daily_log/summarizer/summarizer.py`:
```python
import json
import re
from datetime import date
from typing import Optional

from ..models.database import Database
from .engine import LLMEngine
from .prompt import DEFAULT_SUMMARIZE_PROMPT, render_prompt


class WorklogSummarizer:
    def __init__(self, db: Database, engine: LLMEngine):
        self._db = db
        self._engine = engine

    async def generate_drafts(
        self, target_date: str, prompt_template: Optional[str] = None
    ) -> list[dict]:
        template = prompt_template or await self._get_prompt_template()

        issues = await self._db.fetch_all(
            "SELECT * FROM jira_issues WHERE is_active = 1"
        )
        activities = await self._db.fetch_all(
            "SELECT * FROM activities WHERE date(timestamp) = ?", (target_date,)
        )
        commits = await self._db.fetch_all(
            "SELECT * FROM git_commits WHERE date = ?", (target_date,)
        )

        issues_text = "\n".join(
            f"- {i['issue_key']}: {i['summary']} ({i['description'] or ''})"
            for i in issues
        ) or "无"

        commits_text = "\n".join(
            f"- {c['committed_at'][:16]} {c['message']} ({c.get('files_changed', '')})"
            for c in commits
        ) or "无"

        activities_text = "\n".join(
            f"- {a['timestamp'][:16]} {a['app_name']} ({a['window_title']}) "
            f"{a['category']} {a['duration_sec']}s"
            for a in activities
        ) or "无"

        prompt = render_prompt(
            template,
            date=target_date,
            jira_issues=issues_text,
            git_commits=commits_text,
            activities=activities_text,
        )

        raw_response = await self._engine.generate(prompt)
        parsed = self._parse_response(raw_response)

        # Delete old drafts for this date that are still pending
        await self._db.execute(
            "DELETE FROM worklog_drafts WHERE date = ? AND status = 'pending_review'",
            (target_date,),
        )

        drafts = []
        for item in parsed:
            time_spent_sec = int(item["time_spent_hours"] * 3600)

            # Find matching activity and commit IDs
            activity_ids = [
                a["id"] for a in activities
                if self._activity_matches_issue(a, item["issue_key"], issues)
            ]
            commit_ids = [c["id"] for c in commits]

            draft_id = await self._db.execute(
                """INSERT INTO worklog_drafts
                   (date, issue_key, time_spent_sec, summary, raw_activities, raw_commits, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending_review')""",
                (
                    target_date,
                    item["issue_key"],
                    time_spent_sec,
                    item["summary"],
                    json.dumps(activity_ids),
                    json.dumps(commit_ids),
                ),
            )

            await self._db.execute(
                """INSERT INTO audit_logs (draft_id, action, after_snapshot)
                   VALUES (?, 'created', ?)""",
                (draft_id, json.dumps(item, ensure_ascii=False)),
            )

            drafts.append({
                "id": draft_id,
                "issue_key": item["issue_key"],
                "time_spent_sec": time_spent_sec,
                "summary": item["summary"],
            })

        return drafts

    def _parse_response(self, response: str) -> list[dict]:
        # Extract JSON array from response (may contain markdown fences)
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return []

    def _activity_matches_issue(
        self, activity: dict, issue_key: str, issues: list[dict]
    ) -> bool:
        # Simple keyword matching as a heuristic
        issue = next((i for i in issues if i["issue_key"] == issue_key), None)
        if not issue:
            return False
        keywords = (issue.get("summary") or "").lower().split()
        window = (activity.get("window_title") or "").lower()
        return any(k in window for k in keywords if len(k) > 2)

    async def _get_prompt_template(self) -> str:
        setting = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key = 'summarize_prompt'"
        )
        if setting:
            return setting["value"]
        return DEFAULT_SUMMARIZE_PROMPT
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_summarizer.py -v`
Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add auto_daily_log/summarizer/prompt.py auto_daily_log/summarizer/summarizer.py tests/test_summarizer.py
git commit -m "feat: worklog summarizer with LLM-based draft generation and audit logging"
```

---

## Task 11: Jira Client

**Files:**
- Create: `auto_daily_log/jira_client/__init__.py`
- Create: `auto_daily_log/jira_client/client.py`
- Create: `tests/test_jira_client.py`

- [ ] **Step 1: Write failing test**

`tests/test_jira_client.py`:
```python
import pytest
import httpx
from auto_daily_log.jira_client.client import JiraClient
from auto_daily_log.config import JiraConfig


@pytest.fixture
def jira_client():
    config = JiraConfig(server_url="https://jira.example.com", pat="test-token")
    return JiraClient(config)


def test_build_worklog_payload(jira_client):
    payload = jira_client._build_worklog_payload(
        time_spent_sec=3600,
        comment="Did some work",
        started="2026-04-12T09:00:00.000+0800",
    )
    assert payload["timeSpentSeconds"] == 3600
    assert payload["comment"] == "Did some work"
    assert payload["started"] == "2026-04-12T09:00:00.000+0800"


def test_build_auth_headers(jira_client):
    headers = jira_client._headers()
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_fetch_issue_info(jira_client, httpx_mock):
    httpx_mock.add_response(
        url="https://jira.example.com/rest/api/2/issue/PROJ-101?fields=summary,description",
        json={
            "key": "PROJ-101",
            "fields": {
                "summary": "Fix SQL parser",
                "description": "Fix JOIN handling",
            },
        },
    )
    info = await jira_client.fetch_issue("PROJ-101")
    assert info["key"] == "PROJ-101"
    assert info["summary"] == "Fix SQL parser"


@pytest.mark.asyncio
async def test_submit_worklog(jira_client, httpx_mock):
    httpx_mock.add_response(
        url="https://jira.example.com/rest/api/2/issue/PROJ-101/worklog",
        json={"id": "12345"},
        status_code=201,
    )
    result = await jira_client.submit_worklog(
        issue_key="PROJ-101",
        time_spent_sec=3600,
        comment="Fixed bug",
        started="2026-04-12T09:00:00.000+0800",
    )
    assert result["id"] == "12345"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jira_client.py -v`
Expected: FAIL

- [ ] **Step 3: Implement JiraClient**

`auto_daily_log/jira_client/__init__.py`: empty file

`auto_daily_log/jira_client/client.py`:
```python
from typing import Optional

import httpx

from ..config import JiraConfig


class JiraClient:
    def __init__(self, config: JiraConfig):
        self._config = config

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._config.pat}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = self._config.server_url.rstrip("/")
        return f"{base}{path}"

    def _build_worklog_payload(
        self, time_spent_sec: int, comment: str, started: str
    ) -> dict:
        return {
            "timeSpentSeconds": time_spent_sec,
            "started": started,
            "comment": comment,
        }

    async def fetch_issue(self, issue_key: str) -> dict:
        url = self._url(f"/rest/api/2/issue/{issue_key}?fields=summary,description")
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            return {
                "key": data["key"],
                "summary": data["fields"].get("summary", ""),
                "description": data["fields"].get("description", ""),
            }

    async def submit_worklog(
        self,
        issue_key: str,
        time_spent_sec: int,
        comment: str,
        started: str,
    ) -> dict:
        url = self._url(f"/rest/api/2/issue/{issue_key}/worklog")
        payload = self._build_worklog_payload(time_spent_sec, comment, started)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            return response.json()

    async def test_connection(self) -> bool:
        try:
            url = self._url("/rest/api/2/myself")
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self._headers())
                return response.status_code == 200
        except Exception:
            return False
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_jira_client.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/jira_client/ tests/test_jira_client.py
git commit -m "feat: Jira Server REST API client with worklog submission and issue fetching"
```

---

## Task 12: Scheduler + Auto-Approve Workflow

**Files:**
- Create: `auto_daily_log/scheduler/__init__.py`
- Create: `auto_daily_log/scheduler/jobs.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing test**

`tests/test_scheduler.py`:
```python
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from auto_daily_log.scheduler.jobs import DailyWorkflow
from auto_daily_log.models.database import Database
from auto_daily_log.config import AutoApproveConfig


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_auto_approve_approves_good_draft(db):
    # Insert a pending draft
    draft_id = await db.execute(
        """INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status)
           VALUES ('2026-04-12', 'PROJ-101', 3600, '修复了SQL解析', 'pending_review')"""
    )
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, is_active) VALUES ('PROJ-101', 'Fix SQL', 1)"
    )

    mock_engine = AsyncMock()
    mock_engine.generate.return_value = json.dumps({"approved": True})

    config = AutoApproveConfig(enabled=True, timeout_min=30)
    workflow = DailyWorkflow(db, mock_engine, config)

    await workflow.auto_approve_pending("2026-04-12")

    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    assert draft["status"] == "auto_approved"

    logs = await db.fetch_all("SELECT * FROM audit_logs WHERE draft_id = ?", (draft_id,))
    assert any(l["action"] == "auto_approved" for l in logs)


@pytest.mark.asyncio
async def test_auto_approve_rejects_bad_draft(db):
    draft_id = await db.execute(
        """INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status)
           VALUES ('2026-04-12', 'PROJ-101', 3600, '做了一些事情', 'pending_review')"""
    )
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, is_active) VALUES ('PROJ-101', 'Fix SQL', 1)"
    )

    mock_engine = AsyncMock()
    mock_engine.generate.return_value = json.dumps(
        {"approved": False, "reason": "日志内容过于笼统"}
    )

    config = AutoApproveConfig(enabled=True, timeout_min=30)
    workflow = DailyWorkflow(db, mock_engine, config)

    await workflow.auto_approve_pending("2026-04-12")

    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    assert draft["status"] == "pending_review"  # stays pending

    logs = await db.fetch_all("SELECT * FROM audit_logs WHERE draft_id = ?", (draft_id,))
    assert any(l["action"] == "auto_rejected" for l in logs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement DailyWorkflow**

`auto_daily_log/scheduler/__init__.py`: empty file

`auto_daily_log/scheduler/jobs.py`:
```python
import json
import re
from datetime import date, datetime
from typing import Optional

from ..config import AutoApproveConfig
from ..models.database import Database
from ..summarizer.engine import LLMEngine
from ..summarizer.prompt import DEFAULT_AUTO_APPROVE_PROMPT, render_prompt


class DailyWorkflow:
    def __init__(
        self, db: Database, engine: LLMEngine, auto_approve_config: AutoApproveConfig
    ):
        self._db = db
        self._engine = engine
        self._auto_approve_config = auto_approve_config

    async def run_daily_summary(
        self, target_date: Optional[str] = None
    ) -> list[dict]:
        """Full daily workflow: collect git → summarize → schedule auto-approve."""
        from ..collector.git_collector import GitCollector
        from ..summarizer.summarizer import WorklogSummarizer

        target = target_date or date.today().isoformat()

        # Step 1: Collect git commits
        collector = GitCollector(self._db)
        await collector.collect_today()

        # Step 2: Generate drafts
        summarizer = WorklogSummarizer(self._db, self._engine)
        drafts = await summarizer.generate_drafts(target)

        return drafts

    async def auto_approve_pending(self, target_date: str) -> None:
        """LLM-based auto-approval for pending drafts."""
        if not self._auto_approve_config.enabled:
            return

        drafts = await self._db.fetch_all(
            "SELECT * FROM worklog_drafts WHERE date = ? AND status = 'pending_review'",
            (target_date,),
        )

        prompt_template = await self._get_auto_approve_prompt()

        for draft in drafts:
            issue = await self._db.fetch_one(
                "SELECT * FROM jira_issues WHERE issue_key = ?",
                (draft["issue_key"],),
            )

            commits = await self._db.fetch_all(
                "SELECT * FROM git_commits WHERE date = ?", (target_date,)
            )
            commits_text = "\n".join(
                f"- {c['message']}" for c in commits
            ) or "无"

            prompt = render_prompt(
                prompt_template,
                date=target_date,
                issue_key=draft["issue_key"],
                issue_summary=issue["summary"] if issue else "",
                time_spent_hours=round(draft["time_spent_sec"] / 3600, 1),
                summary=draft["summary"],
                git_commits=commits_text,
            )

            raw_response = await self._engine.generate(prompt)
            result = self._parse_approval(raw_response)

            if result.get("approved"):
                await self._db.execute(
                    "UPDATE worklog_drafts SET status = 'auto_approved', updated_at = datetime('now') WHERE id = ?",
                    (draft["id"],),
                )
                await self._db.execute(
                    "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'auto_approved', ?)",
                    (draft["id"], raw_response),
                )
            else:
                await self._db.execute(
                    "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'auto_rejected', ?)",
                    (draft["id"], raw_response),
                )

    def _parse_approval(self, response: str) -> dict:
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {"approved": False, "reason": "Failed to parse LLM response"}

    async def _get_auto_approve_prompt(self) -> str:
        setting = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key = 'auto_approve_prompt'"
        )
        if setting:
            return setting["value"]
        return DEFAULT_AUTO_APPROVE_PROMPT
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_scheduler.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/scheduler/ tests/test_scheduler.py
git commit -m "feat: daily workflow with auto-approve via LLM evaluation"
```

---

## Task 13: Web API - Settings + Issues + Activities

**Files:**
- Create: `auto_daily_log/web/__init__.py`
- Create: `auto_daily_log/web/api/__init__.py`
- Create: `auto_daily_log/web/api/settings.py`
- Create: `auto_daily_log/web/api/issues.py`
- Create: `auto_daily_log/web/api/activities.py`
- Create: `auto_daily_log/web/app.py`
- Create: `tests/test_api_settings.py`
- Create: `tests/test_api_issues.py`

- [ ] **Step 1: Write failing tests for settings API**

`tests/conftest.py`:
```python
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from auto_daily_log.web.app import create_app
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def app_client(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.initialize()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()
```

`tests/test_api_settings.py`:
```python
import pytest


@pytest.mark.asyncio
async def test_get_settings(app_client):
    response = await app_client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_put_setting(app_client):
    response = await app_client.put(
        "/api/settings/monitor.interval_sec",
        json={"value": "60"},
    )
    assert response.status_code == 200

    response = await app_client.get("/api/settings")
    data = response.json()
    found = [s for s in data if s["key"] == "monitor.interval_sec"]
    assert len(found) == 1
    assert found[0]["value"] == "60"
```

`tests/test_api_issues.py`:
```python
import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_list_issues_empty(app_client):
    response = await app_client.get("/api/issues")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_add_issue(app_client):
    response = await app_client.post(
        "/api/issues",
        json={"issue_key": "PROJ-101", "summary": "Fix bug", "description": "Fix it"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["issue_key"] == "PROJ-101"

    response = await app_client.get("/api/issues")
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_toggle_issue_active(app_client):
    await app_client.post(
        "/api/issues",
        json={"issue_key": "PROJ-102", "summary": "Task", "description": ""},
    )
    response = await app_client.patch(
        "/api/issues/PROJ-102",
        json={"is_active": False},
    )
    assert response.status_code == 200

    response = await app_client.get("/api/issues")
    issue = [i for i in response.json() if i["issue_key"] == "PROJ-102"][0]
    assert issue["is_active"] is False


@pytest.mark.asyncio
async def test_delete_issue(app_client):
    await app_client.post(
        "/api/issues",
        json={"issue_key": "PROJ-103", "summary": "Delete me", "description": ""},
    )
    response = await app_client.delete("/api/issues/PROJ-103")
    assert response.status_code == 200

    response = await app_client.get("/api/issues")
    assert len(response.json()) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_settings.py tests/test_api_issues.py -v`
Expected: FAIL

- [ ] **Step 3: Implement FastAPI app factory and routes**

`auto_daily_log/web/__init__.py`: empty file

`auto_daily_log/web/api/__init__.py`: empty file

`auto_daily_log/web/app.py`:
```python
from fastapi import FastAPI
from ..models.database import Database
from .api import settings, issues, activities, worklogs, dashboard, git_repos


def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Auto Daily Log", version="0.1.0")

    # Attach db to app state
    app.state.db = db

    # Register routes
    app.include_router(settings.router, prefix="/api")
    app.include_router(issues.router, prefix="/api")
    app.include_router(activities.router, prefix="/api")
    app.include_router(worklogs.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(git_repos.router, prefix="/api")

    return app
```

`auto_daily_log/web/api/settings.py`:
```python
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["settings"])


class SettingUpdate(BaseModel):
    value: str


@router.get("/settings")
async def list_settings(request: Request):
    db = request.app.state.db
    rows = await db.fetch_all("SELECT key, value, updated_at FROM settings")
    return rows


@router.get("/settings/{key}")
async def get_setting(key: str, request: Request):
    db = request.app.state.db
    row = await db.fetch_one("SELECT * FROM settings WHERE key = ?", (key,))
    if not row:
        return {"key": key, "value": None}
    return row


@router.put("/settings/{key}")
async def put_setting(key: str, body: SettingUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT key FROM settings WHERE key = ?", (key,))
    if existing:
        await db.execute(
            "UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?",
            (body.value, key),
        )
    else:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (key, body.value),
        )
    return {"key": key, "value": body.value}
```

`auto_daily_log/web/api/issues.py`:
```python
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["issues"])


class IssueCreate(BaseModel):
    issue_key: str
    summary: str = ""
    description: str = ""


class IssueUpdate(BaseModel):
    is_active: Optional[bool] = None
    summary: Optional[str] = None
    description: Optional[str] = None


@router.get("/issues")
async def list_issues(request: Request):
    db = request.app.state.db
    rows = await db.fetch_all("SELECT * FROM jira_issues ORDER BY created_at DESC")
    # Convert is_active from int to bool
    return [{**r, "is_active": bool(r["is_active"])} for r in rows]


@router.post("/issues", status_code=201)
async def add_issue(body: IssueCreate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one(
        "SELECT id FROM jira_issues WHERE issue_key = ?", (body.issue_key,)
    )
    if existing:
        raise HTTPException(400, f"Issue {body.issue_key} already exists")
    await db.execute(
        "INSERT INTO jira_issues (issue_key, summary, description) VALUES (?, ?, ?)",
        (body.issue_key, body.summary, body.description),
    )
    return {"issue_key": body.issue_key, "summary": body.summary, "is_active": True}


@router.patch("/issues/{issue_key}")
async def update_issue(issue_key: str, body: IssueUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one(
        "SELECT * FROM jira_issues WHERE issue_key = ?", (issue_key,)
    )
    if not existing:
        raise HTTPException(404, f"Issue {issue_key} not found")

    updates = []
    params = []
    if body.is_active is not None:
        updates.append("is_active = ?")
        params.append(int(body.is_active))
    if body.summary is not None:
        updates.append("summary = ?")
        params.append(body.summary)
    if body.description is not None:
        updates.append("description = ?")
        params.append(body.description)

    if updates:
        params.append(issue_key)
        await db.execute(
            f"UPDATE jira_issues SET {', '.join(updates)} WHERE issue_key = ?",
            tuple(params),
        )
    return {"status": "updated"}


@router.delete("/issues/{issue_key}")
async def delete_issue(issue_key: str, request: Request):
    db = request.app.state.db
    await db.execute("DELETE FROM jira_issues WHERE issue_key = ?", (issue_key,))
    return {"status": "deleted"}
```

`auto_daily_log/web/api/activities.py`:
```python
from fastapi import APIRouter, Request, Query
from datetime import date

router = APIRouter(tags=["activities"])


@router.get("/activities")
async def list_activities(
    request: Request,
    target_date: str = Query(default=None, description="YYYY-MM-DD"),
):
    db = request.app.state.db
    target = target_date or date.today().isoformat()
    rows = await db.fetch_all(
        "SELECT * FROM activities WHERE date(timestamp) = ? ORDER BY timestamp",
        (target,),
    )
    return rows
```

`auto_daily_log/web/api/git_repos.py`:
```python
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["git_repos"])


class GitRepoCreate(BaseModel):
    path: str
    author_email: str = ""


class GitRepoUpdate(BaseModel):
    is_active: Optional[bool] = None
    author_email: Optional[str] = None


@router.get("/git-repos")
async def list_repos(request: Request):
    db = request.app.state.db
    rows = await db.fetch_all("SELECT * FROM git_repos ORDER BY created_at DESC")
    return [{**r, "is_active": bool(r["is_active"])} for r in rows]


@router.post("/git-repos", status_code=201)
async def add_repo(body: GitRepoCreate, request: Request):
    db = request.app.state.db
    repo_id = await db.execute(
        "INSERT INTO git_repos (path, author_email) VALUES (?, ?)",
        (body.path, body.author_email),
    )
    return {"id": repo_id, "path": body.path, "author_email": body.author_email}


@router.patch("/git-repos/{repo_id}")
async def update_repo(repo_id: int, body: GitRepoUpdate, request: Request):
    db = request.app.state.db
    updates, params = [], []
    if body.is_active is not None:
        updates.append("is_active = ?")
        params.append(int(body.is_active))
    if body.author_email is not None:
        updates.append("author_email = ?")
        params.append(body.author_email)
    if updates:
        params.append(repo_id)
        await db.execute(f"UPDATE git_repos SET {', '.join(updates)} WHERE id = ?", tuple(params))
    return {"status": "updated"}


@router.delete("/git-repos/{repo_id}")
async def delete_repo(repo_id: int, request: Request):
    db = request.app.state.db
    await db.execute("DELETE FROM git_repos WHERE id = ?", (repo_id,))
    return {"status": "deleted"}
```

- [ ] **Step 4: Create stub routers for worklogs and dashboard (implemented next task)**

`auto_daily_log/web/api/worklogs.py`:
```python
from fastapi import APIRouter

router = APIRouter(tags=["worklogs"])
# Endpoints implemented in Task 14
```

`auto_daily_log/web/api/dashboard.py`:
```python
from fastapi import APIRouter

router = APIRouter(tags=["dashboard"])
# Endpoints implemented in Task 14
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_api_settings.py tests/test_api_issues.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add auto_daily_log/web/ tests/conftest.py tests/test_api_settings.py tests/test_api_issues.py
git commit -m "feat: FastAPI app with settings, issues, and activities API routes"
```

---

## Task 14: Web API - Worklogs + Dashboard

**Files:**
- Modify: `auto_daily_log/web/api/worklogs.py`
- Modify: `auto_daily_log/web/api/dashboard.py`
- Create: `tests/test_api_worklogs.py`

- [ ] **Step 1: Write failing test**

`tests/test_api_worklogs.py`:
```python
import pytest


@pytest_asyncio.fixture
async def seeded_client(app_client):
    """Seed database with a draft for testing."""
    # We need direct DB access, so we'll seed via API and direct DB calls
    # First add an issue
    await app_client.post(
        "/api/issues",
        json={"issue_key": "PROJ-101", "summary": "Fix SQL", "description": ""},
    )
    # Trigger manual draft creation (via API)
    # For now, seed directly
    return app_client


import pytest_asyncio


@pytest.mark.asyncio
async def test_list_drafts_empty(app_client):
    response = await app_client.get("/api/worklogs?date=2026-04-12")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_update_draft(app_client):
    # Seed a draft directly via the worklogs API trigger endpoint
    response = await app_client.post(
        "/api/worklogs/seed",
        json={
            "date": "2026-04-12",
            "issue_key": "PROJ-101",
            "time_spent_sec": 3600,
            "summary": "Fixed SQL",
        },
    )
    assert response.status_code == 201
    draft_id = response.json()["id"]

    # Update the draft
    response = await app_client.patch(
        f"/api/worklogs/{draft_id}",
        json={"summary": "Fixed SQL parser JOIN handling", "time_spent_sec": 7200},
    )
    assert response.status_code == 200

    # Verify update
    response = await app_client.get("/api/worklogs?date=2026-04-12")
    drafts = response.json()
    assert len(drafts) == 1
    assert drafts[0]["summary"] == "Fixed SQL parser JOIN handling"
    assert drafts[0]["time_spent_sec"] == 7200
    assert drafts[0]["user_edited"] == 1


@pytest.mark.asyncio
async def test_approve_draft(app_client):
    response = await app_client.post(
        "/api/worklogs/seed",
        json={
            "date": "2026-04-12",
            "issue_key": "PROJ-102",
            "time_spent_sec": 1800,
            "summary": "Review PR",
        },
    )
    draft_id = response.json()["id"]

    response = await app_client.post(f"/api/worklogs/{draft_id}/approve")
    assert response.status_code == 200

    response = await app_client.get("/api/worklogs?date=2026-04-12")
    draft = [d for d in response.json() if d["id"] == draft_id][0]
    assert draft["status"] == "approved"


@pytest.mark.asyncio
async def test_reject_draft(app_client):
    response = await app_client.post(
        "/api/worklogs/seed",
        json={
            "date": "2026-04-12",
            "issue_key": "PROJ-103",
            "time_spent_sec": 900,
            "summary": "Meeting",
        },
    )
    draft_id = response.json()["id"]

    response = await app_client.post(f"/api/worklogs/{draft_id}/reject")
    assert response.status_code == 200

    response = await app_client.get("/api/worklogs?date=2026-04-12")
    draft = [d for d in response.json() if d["id"] == draft_id][0]
    assert draft["status"] == "rejected"


@pytest.mark.asyncio
async def test_approve_all(app_client):
    for key in ["PROJ-201", "PROJ-202"]:
        await app_client.post(
            "/api/worklogs/seed",
            json={"date": "2026-04-13", "issue_key": key, "time_spent_sec": 1800, "summary": "Work"},
        )

    response = await app_client.post("/api/worklogs/approve-all?date=2026-04-13")
    assert response.status_code == 200

    response = await app_client.get("/api/worklogs?date=2026-04-13")
    assert all(d["status"] == "approved" for d in response.json())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_worklogs.py -v`
Expected: FAIL

- [ ] **Step 3: Implement worklogs API**

`auto_daily_log/web/api/worklogs.py`:
```python
import json
from datetime import date
from fastapi import APIRouter, Request, Query, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["worklogs"])


class DraftSeed(BaseModel):
    date: str
    issue_key: str
    time_spent_sec: int
    summary: str


class DraftUpdate(BaseModel):
    time_spent_sec: Optional[int] = None
    summary: Optional[str] = None
    issue_key: Optional[str] = None


@router.get("/worklogs")
async def list_drafts(request: Request, date: str = Query(default=None)):
    db = request.app.state.db
    target = date or __import__("datetime").date.today().isoformat()
    rows = await db.fetch_all(
        "SELECT * FROM worklog_drafts WHERE date = ? ORDER BY created_at",
        (target,),
    )
    return rows


@router.post("/worklogs/seed", status_code=201)
async def seed_draft(body: DraftSeed, request: Request):
    """Create a draft manually (for testing and manual entry)."""
    db = request.app.state.db
    draft_id = await db.execute(
        """INSERT INTO worklog_drafts (date, issue_key, time_spent_sec, summary, status)
           VALUES (?, ?, ?, ?, 'pending_review')""",
        (body.date, body.issue_key, body.time_spent_sec, body.summary),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action, after_snapshot) VALUES (?, 'created', ?)",
        (draft_id, json.dumps(body.model_dump(), ensure_ascii=False)),
    )
    return {"id": draft_id}


@router.patch("/worklogs/{draft_id}")
async def update_draft(draft_id: int, body: DraftUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one(
        "SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,)
    )
    if not existing:
        raise HTTPException(404, "Draft not found")

    before = json.dumps(dict(existing), ensure_ascii=False, default=str)

    updates = ["user_edited = 1", "updated_at = datetime('now')"]
    params = []
    if body.time_spent_sec is not None:
        updates.append("time_spent_sec = ?")
        params.append(body.time_spent_sec)
    if body.summary is not None:
        updates.append("summary = ?")
        params.append(body.summary)
    if body.issue_key is not None:
        updates.append("issue_key = ?")
        params.append(body.issue_key)

    params.append(draft_id)
    await db.execute(
        f"UPDATE worklog_drafts SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )

    updated = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    after = json.dumps(dict(updated), ensure_ascii=False, default=str)

    await db.execute(
        "INSERT INTO audit_logs (draft_id, action, before_snapshot, after_snapshot) VALUES (?, 'edited', ?, ?)",
        (draft_id, before, after),
    )
    return {"status": "updated"}


@router.post("/worklogs/{draft_id}/approve")
async def approve_draft(draft_id: int, request: Request):
    db = request.app.state.db
    await db.execute(
        "UPDATE worklog_drafts SET status = 'approved', updated_at = datetime('now') WHERE id = ?",
        (draft_id,),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action) VALUES (?, 'approved')",
        (draft_id,),
    )
    return {"status": "approved"}


@router.post("/worklogs/{draft_id}/reject")
async def reject_draft(draft_id: int, request: Request):
    db = request.app.state.db
    await db.execute(
        "UPDATE worklog_drafts SET status = 'rejected', updated_at = datetime('now') WHERE id = ?",
        (draft_id,),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action) VALUES (?, 'rejected')",
        (draft_id,),
    )
    return {"status": "rejected"}


@router.post("/worklogs/approve-all")
async def approve_all(request: Request, date: str = Query(default=None)):
    db = request.app.state.db
    target = date or __import__("datetime").date.today().isoformat()
    await db.execute(
        "UPDATE worklog_drafts SET status = 'approved', updated_at = datetime('now') WHERE date = ? AND status = 'pending_review'",
        (target,),
    )
    drafts = await db.fetch_all(
        "SELECT id FROM worklog_drafts WHERE date = ? AND status = 'approved'",
        (target,),
    )
    for d in drafts:
        await db.execute(
            "INSERT INTO audit_logs (draft_id, action) VALUES (?, 'approved')",
            (d["id"],),
        )
    return {"status": "all_approved", "count": len(drafts)}


@router.post("/worklogs/{draft_id}/submit")
async def submit_to_jira(draft_id: int, request: Request):
    """Submit an approved draft to Jira."""
    db = request.app.state.db
    draft = await db.fetch_one("SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,))
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft["status"] not in ("approved", "auto_approved"):
        raise HTTPException(400, f"Draft status is '{draft['status']}', must be approved first")

    # Load Jira config from settings
    jira_url = await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_server_url'")
    jira_pat = await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_pat'")
    if not jira_url or not jira_pat or not jira_url["value"] or not jira_pat["value"]:
        raise HTTPException(400, "Jira not configured. Set server URL and PAT in Settings.")

    from ...config import JiraConfig
    from ...jira_client.client import JiraClient

    jira_config = JiraConfig(server_url=jira_url["value"], pat=jira_pat["value"])
    jira = JiraClient(jira_config)

    started = f"{draft['date']}T09:00:00.000+0800"
    try:
        result = await jira.submit_worklog(
            issue_key=draft["issue_key"],
            time_spent_sec=draft["time_spent_sec"],
            comment=draft["summary"],
            started=started,
        )
        jira_worklog_id = result.get("id", "")
    except Exception as e:
        raise HTTPException(502, f"Jira API error: {str(e)}")

    await db.execute(
        "UPDATE worklog_drafts SET status = 'submitted', jira_worklog_id = ?, updated_at = datetime('now') WHERE id = ?",
        (str(jira_worklog_id), draft_id),
    )
    await db.execute(
        "INSERT INTO audit_logs (draft_id, action, jira_response) VALUES (?, 'submitted', ?)",
        (draft_id, json.dumps(result, ensure_ascii=False)),
    )
    return {"status": "submitted", "jira_worklog_id": jira_worklog_id}


@router.get("/worklogs/{draft_id}/audit")
async def get_audit_trail(draft_id: int, request: Request):
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT * FROM audit_logs WHERE draft_id = ? ORDER BY created_at",
        (draft_id,),
    )
    return rows
```

- [ ] **Step 4: Implement dashboard API**

`auto_daily_log/web/api/dashboard.py`:
```python
from datetime import date
from fastapi import APIRouter, Request, Query

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
async def get_dashboard(request: Request, target_date: str = Query(default=None)):
    db = request.app.state.db
    target = target_date or date.today().isoformat()

    activities = await db.fetch_all(
        "SELECT category, SUM(duration_sec) as total_sec FROM activities "
        "WHERE date(timestamp) = ? GROUP BY category",
        (target,),
    )

    pending = await db.fetch_all(
        "SELECT COUNT(*) as count FROM worklog_drafts WHERE date = ? AND status = 'pending_review'",
        (target,),
    )

    submitted = await db.fetch_all(
        "SELECT SUM(time_spent_sec) as total FROM worklog_drafts WHERE date = ? AND status = 'submitted'",
        (target,),
    )

    return {
        "date": target,
        "activity_summary": activities,
        "pending_review_count": pending[0]["count"] if pending else 0,
        "submitted_hours": round((submitted[0]["total"] or 0) / 3600, 1) if submitted else 0,
    }
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_api_worklogs.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add auto_daily_log/web/api/worklogs.py auto_daily_log/web/api/dashboard.py tests/test_api_worklogs.py
git commit -m "feat: worklogs API with CRUD, approve/reject, submit, and dashboard endpoint"
```

---

## Task 15: App Entry Point + Full Integration

**Files:**
- Modify: `auto_daily_log/__main__.py`
- Create: `auto_daily_log/app.py`

- [ ] **Step 1: Implement application entry point**

`auto_daily_log/app.py`:
```python
import asyncio
from datetime import datetime
from pathlib import Path

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import AppConfig, load_config
from .models.database import Database
from .monitor.service import MonitorService
from .scheduler.jobs import DailyWorkflow
from .summarizer.engine import get_llm_engine
from .web.app import create_app


class Application:
    def __init__(self, config: AppConfig):
        self.config = config
        self.db: Database = None
        self.monitor: MonitorService = None
        self.scheduler: AsyncIOScheduler = None

    async def _init_db(self) -> None:
        db_path = Path.home() / ".auto_daily_log" / "data.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(db_path)
        await self.db.initialize()

    async def _init_monitor(self) -> None:
        screenshot_dir = Path.home() / ".auto_daily_log" / "screenshots"
        self.monitor = MonitorService(self.db, self.config.monitor, screenshot_dir)

    def _init_scheduler(self) -> None:
        if not self.config.scheduler.enabled:
            return

        self.scheduler = AsyncIOScheduler()
        hour, minute = map(int, self.config.scheduler.trigger_time.split(":"))

        async def daily_job():
            engine = get_llm_engine(self.config.llm)
            workflow = DailyWorkflow(self.db, engine, self.config.auto_approve)
            await workflow.run_daily_summary()

            if self.config.auto_approve.enabled:
                # Schedule auto-approve after timeout
                timeout = self.config.auto_approve.timeout_min * 60
                await asyncio.sleep(timeout)
                today = datetime.now().strftime("%Y-%m-%d")
                await workflow.auto_approve_pending(today)

        self.scheduler.add_job(
            daily_job, "cron", hour=hour, minute=minute, id="daily_summary"
        )
        self.scheduler.start()

    async def run(self) -> None:
        await self._init_db()
        await self._init_monitor()
        self._init_scheduler()

        app = create_app(self.db)

        # Start monitor in background
        monitor_task = asyncio.create_task(self.monitor.start())

        config = uvicorn.Config(
            app,
            host=self.config.server.host,
            port=self.config.server.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        try:
            await server.serve()
        finally:
            self.monitor.stop()
            monitor_task.cancel()
            if self.scheduler:
                self.scheduler.shutdown()
            await self.db.close()
```

- [ ] **Step 2: Update __main__.py**

`auto_daily_log/__main__.py`:
```python
"""Entry point: python -m auto_daily_log"""
import argparse
import asyncio

from .config import load_config
from .app import Application


def main():
    parser = argparse.ArgumentParser(description="Auto Daily Log")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--port", type=int, help="Override server port")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.port:
        config.server.port = args.port

    app = Application(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify app starts**

Run:
```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log
python -m auto_daily_log --port 8080
```
Expected: Server starts, visit `http://localhost:8080/docs` shows FastAPI Swagger UI

- [ ] **Step 4: Commit**

```bash
git add auto_daily_log/app.py auto_daily_log/__main__.py
git commit -m "feat: application entry point with monitor, scheduler, and web server integration"
```

---

## Task 16: Vue.js Frontend Setup

**Files:**
- Create: `web/frontend/` (Vue.js project via Vite)
- Modify: `auto_daily_log/web/app.py` (serve static files)

- [ ] **Step 1: Initialize Vue.js project**

```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log
npm create vite@latest web/frontend -- --template vue
cd web/frontend
npm install
npm install vue-router@4 axios element-plus @element-plus/icons-vue
```

- [ ] **Step 2: Configure Vite proxy for development**

`web/frontend/vite.config.js`:
```javascript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: 'dist',
  }
})
```

- [ ] **Step 3: Set up app shell with Element Plus and Vue Router**

`web/frontend/src/main.js`:
```javascript
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(ElementPlus)
app.use(router)
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}
app.mount('#app')
```

`web/frontend/src/router/index.js`:
```javascript
import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/worklogs', name: 'Worklogs', component: () => import('../views/Worklogs.vue') },
  { path: '/issues', name: 'Issues', component: () => import('../views/Issues.vue') },
  { path: '/settings', name: 'Settings', component: () => import('../views/Settings.vue') },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
```

`web/frontend/src/api/index.js`:
```javascript
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export default {
  // Dashboard
  getDashboard: (date) => api.get('/dashboard', { params: { target_date: date } }),

  // Activities
  getActivities: (date) => api.get('/activities', { params: { target_date: date } }),

  // Worklogs
  getWorklogs: (date) => api.get('/worklogs', { params: { date } }),
  updateDraft: (id, data) => api.patch(`/worklogs/${id}`, data),
  approveDraft: (id) => api.post(`/worklogs/${id}/approve`),
  rejectDraft: (id) => api.post(`/worklogs/${id}/reject`),
  approveAll: (date) => api.post('/worklogs/approve-all', null, { params: { date } }),
  submitDraft: (id) => api.post(`/worklogs/${id}/submit`),
  getAuditTrail: (id) => api.get(`/worklogs/${id}/audit`),

  // Issues
  getIssues: () => api.get('/issues'),
  addIssue: (data) => api.post('/issues', data),
  updateIssue: (key, data) => api.patch(`/issues/${key}`, data),
  deleteIssue: (key) => api.delete(`/issues/${key}`),

  // Settings
  getSettings: () => api.get('/settings'),
  getSetting: (key) => api.get(`/settings/${key}`),
  putSetting: (key, value) => api.put(`/settings/${key}`, { value }),
}
```

`web/frontend/src/App.vue`:
```vue
<template>
  <el-container style="min-height: 100vh">
    <el-aside width="200px" style="background: #304156">
      <div style="padding: 20px; color: #fff; font-size: 16px; font-weight: bold">
        Auto Daily Log
      </div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="#304156"
        text-color="#bfcbd9"
        active-text-color="#409EFF"
      >
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon>
          <span>Dashboard</span>
        </el-menu-item>
        <el-menu-item index="/worklogs">
          <el-icon><Document /></el-icon>
          <span>Worklogs</span>
        </el-menu-item>
        <el-menu-item index="/issues">
          <el-icon><Tickets /></el-icon>
          <span>Issues</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>Settings</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>
```

- [ ] **Step 4: Create placeholder views**

`web/frontend/src/views/Dashboard.vue`:
```vue
<template>
  <div>
    <h2>Dashboard</h2>
    <p>Placeholder - implemented in Task 17</p>
  </div>
</template>
```

`web/frontend/src/views/Worklogs.vue`:
```vue
<template>
  <div>
    <h2>Worklogs</h2>
    <p>Placeholder - implemented in Task 18</p>
  </div>
</template>
```

`web/frontend/src/views/Issues.vue`:
```vue
<template>
  <div>
    <h2>Issues</h2>
    <p>Placeholder - implemented in Task 19</p>
  </div>
</template>
```

`web/frontend/src/views/Settings.vue`:
```vue
<template>
  <div>
    <h2>Settings</h2>
    <p>Placeholder - implemented in Task 20</p>
  </div>
</template>
```

- [ ] **Step 5: Verify dev server starts**

```bash
cd web/frontend && npm run dev
```
Expected: Opens on `http://localhost:5173`, sidebar navigation works, API calls proxy to backend

- [ ] **Step 6: Add static file serving to FastAPI**

Add to `auto_daily_log/web/app.py` after router registration:

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# After all router includes:
frontend_dist = Path(__file__).parent.parent.parent / "web" / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
```

- [ ] **Step 7: Commit**

```bash
git add web/frontend/ auto_daily_log/web/app.py
git commit -m "feat: Vue.js frontend scaffold with Element Plus, routing, and API client"
```

---

## Task 17: Frontend - Dashboard Page

**Files:**
- Modify: `web/frontend/src/views/Dashboard.vue`

- [ ] **Step 1: Implement Dashboard view**

`web/frontend/src/views/Dashboard.vue`:
```vue
<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px">
      <h2>Dashboard</h2>
      <el-date-picker v-model="selectedDate" type="date" value-format="YYYY-MM-DD" @change="loadData" />
    </div>

    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="8">
        <el-card>
          <template #header>Pending Review</template>
          <div style="font-size: 36px; text-align: center; color: #E6A23C">
            {{ dashboard.pending_review_count }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <template #header>Submitted Hours</template>
          <div style="font-size: 36px; text-align: center; color: #67C23A">
            {{ dashboard.submitted_hours }}h
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <template #header>Total Activity</template>
          <div style="font-size: 36px; text-align: center; color: #409EFF">
            {{ totalActivityHours }}h
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card>
      <template #header>Activity Breakdown</template>
      <el-table :data="dashboard.activity_summary" stripe>
        <el-table-column prop="category" label="Category" />
        <el-table-column label="Duration">
          <template #default="{ row }">
            {{ (row.total_sec / 3600).toFixed(1) }}h
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'

const selectedDate = ref(new Date().toISOString().split('T')[0])
const dashboard = ref({ pending_review_count: 0, submitted_hours: 0, activity_summary: [] })

const totalActivityHours = computed(() => {
  const total = (dashboard.value.activity_summary || []).reduce((s, a) => s + a.total_sec, 0)
  return (total / 3600).toFixed(1)
})

async function loadData() {
  const res = await api.getDashboard(selectedDate.value)
  dashboard.value = res.data
}

onMounted(loadData)
</script>
```

- [ ] **Step 2: Verify in browser**

Run backend (`python -m auto_daily_log`) and frontend (`npm run dev`), verify Dashboard loads.

- [ ] **Step 3: Commit**

```bash
git add web/frontend/src/views/Dashboard.vue
git commit -m "feat: dashboard page with pending count, submitted hours, and activity breakdown"
```

---

## Task 18: Frontend - Worklogs Page

**Files:**
- Modify: `web/frontend/src/views/Worklogs.vue`

- [ ] **Step 1: Implement Worklogs view**

`web/frontend/src/views/Worklogs.vue`:
```vue
<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px">
      <h2>Work Logs</h2>
      <div>
        <el-date-picker v-model="selectedDate" type="date" value-format="YYYY-MM-DD" @change="loadDrafts" style="margin-right: 10px" />
        <el-button type="warning" @click="approveAll" :disabled="!hasPending">Approve All</el-button>
      </div>
    </div>

    <div v-if="drafts.length === 0">
      <el-empty description="No worklogs for this date" />
    </div>

    <el-card v-for="draft in drafts" :key="draft.id" style="margin-bottom: 16px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <div>
            <el-tag :type="statusType(draft.status)" style="margin-right: 8px">{{ draft.status }}</el-tag>
            <strong>{{ draft.issue_key }}</strong>
            <span v-if="draft.user_edited" style="margin-left: 8px; color: #909399; font-size: 12px">(edited)</span>
          </div>
          <div>
            <span style="font-size: 18px; font-weight: bold; margin-right: 16px">
              {{ (draft.time_spent_sec / 3600).toFixed(1) }}h
            </span>
          </div>
        </div>
      </template>

      <div v-if="editingId === draft.id">
        <el-form label-width="80px">
          <el-form-item label="Hours">
            <el-input-number v-model="editForm.hours" :min="0" :step="0.5" :precision="1" />
          </el-form-item>
          <el-form-item label="Summary">
            <el-input v-model="editForm.summary" type="textarea" :rows="3" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveEdit(draft.id)">Save</el-button>
            <el-button @click="editingId = null">Cancel</el-button>
          </el-form-item>
        </el-form>
      </div>
      <div v-else>
        <p>{{ draft.summary }}</p>
      </div>

      <template #footer v-if="draft.status === 'pending_review'">
        <el-button type="primary" size="small" @click="startEdit(draft)">Edit</el-button>
        <el-button type="success" size="small" @click="approve(draft.id)">Approve</el-button>
        <el-button type="danger" size="small" @click="reject(draft.id)">Reject</el-button>
      </template>
      <template #footer v-else-if="draft.status === 'approved' || draft.status === 'auto_approved'">
        <el-button type="primary" size="small" @click="submit(draft.id)">Submit to Jira</el-button>
      </template>
      <template #footer v-else-if="draft.status === 'submitted'">
        <el-button size="small" @click="showAudit(draft.id)">View Audit Trail</el-button>
      </template>
    </el-card>

    <el-dialog v-model="auditVisible" title="Audit Trail" width="600px">
      <el-timeline>
        <el-timeline-item v-for="log in auditLogs" :key="log.id" :timestamp="log.created_at">
          <strong>{{ log.action }}</strong>
          <pre v-if="log.after_snapshot" style="font-size: 12px; max-height: 200px; overflow: auto">{{ log.after_snapshot }}</pre>
        </el-timeline-item>
      </el-timeline>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const selectedDate = ref(new Date().toISOString().split('T')[0])
const drafts = ref([])
const editingId = ref(null)
const editForm = ref({ hours: 0, summary: '' })
const auditVisible = ref(false)
const auditLogs = ref([])

const hasPending = computed(() => drafts.value.some(d => d.status === 'pending_review'))

function statusType(status) {
  const map = {
    pending_review: 'warning',
    approved: 'success',
    auto_approved: 'success',
    submitted: 'info',
    rejected: 'danger',
    auto_rejected: 'danger',
  }
  return map[status] || ''
}

async function loadDrafts() {
  const res = await api.getWorklogs(selectedDate.value)
  drafts.value = res.data
}

function startEdit(draft) {
  editingId.value = draft.id
  editForm.value = {
    hours: draft.time_spent_sec / 3600,
    summary: draft.summary,
  }
}

async function saveEdit(id) {
  await api.updateDraft(id, {
    time_spent_sec: Math.round(editForm.value.hours * 3600),
    summary: editForm.value.summary,
  })
  editingId.value = null
  ElMessage.success('Draft updated')
  await loadDrafts()
}

async function approve(id) {
  await api.approveDraft(id)
  ElMessage.success('Approved')
  await loadDrafts()
}

async function reject(id) {
  await api.rejectDraft(id)
  ElMessage.warning('Rejected')
  await loadDrafts()
}

async function approveAll() {
  await api.approveAll(selectedDate.value)
  ElMessage.success('All approved')
  await loadDrafts()
}

async function submit(id) {
  try {
    await api.submitDraft(id)
    ElMessage.success('Submitted to Jira')
    await loadDrafts()
  } catch (e) {
    ElMessage.error('Submit failed: ' + (e.response?.data?.detail || e.message))
  }
}

async function showAudit(id) {
  const res = await api.getAuditTrail(id)
  auditLogs.value = res.data
  auditVisible.value = true
}

onMounted(loadDrafts)
</script>
```

- [ ] **Step 2: Verify in browser**

Test: date picker, edit/approve/reject workflow, audit trail dialog.

- [ ] **Step 3: Commit**

```bash
git add web/frontend/src/views/Worklogs.vue
git commit -m "feat: worklogs page with edit, approve, reject, submit, and audit trail"
```

---

## Task 19: Frontend - Issues Page

**Files:**
- Modify: `web/frontend/src/views/Issues.vue`

- [ ] **Step 1: Implement Issues view**

`web/frontend/src/views/Issues.vue`:
```vue
<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px">
      <h2>Jira Issues</h2>
      <el-button type="primary" @click="dialogVisible = true">Add Issue</el-button>
    </div>

    <el-table :data="issues" stripe>
      <el-table-column prop="issue_key" label="Key" width="150" />
      <el-table-column prop="summary" label="Summary" />
      <el-table-column label="Active" width="100">
        <template #default="{ row }">
          <el-switch v-model="row.is_active" @change="toggleActive(row)" />
        </template>
      </el-table-column>
      <el-table-column label="Actions" width="120">
        <template #default="{ row }">
          <el-button type="danger" size="small" text @click="deleteIssue(row.issue_key)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="Add Jira Issue" width="500px">
      <el-form :model="newIssue" label-width="120px">
        <el-form-item label="Issue Key" required>
          <el-input v-model="newIssue.issue_key" placeholder="e.g. PROJ-101" />
        </el-form-item>
        <el-form-item label="Summary">
          <el-input v-model="newIssue.summary" placeholder="Issue title" />
        </el-form-item>
        <el-form-item label="Description">
          <el-input v-model="newIssue.description" type="textarea" :rows="3" placeholder="Issue description (helps LLM match activities)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">Cancel</el-button>
        <el-button type="primary" @click="addIssue">Add</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const issues = ref([])
const dialogVisible = ref(false)
const newIssue = ref({ issue_key: '', summary: '', description: '' })

async function loadIssues() {
  const res = await api.getIssues()
  issues.value = res.data
}

async function addIssue() {
  if (!newIssue.value.issue_key) {
    ElMessage.warning('Issue key is required')
    return
  }
  try {
    await api.addIssue(newIssue.value)
    ElMessage.success('Issue added')
    dialogVisible.value = false
    newIssue.value = { issue_key: '', summary: '', description: '' }
    await loadIssues()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || 'Failed to add issue')
  }
}

async function toggleActive(row) {
  await api.updateIssue(row.issue_key, { is_active: row.is_active })
}

async function deleteIssue(key) {
  await ElMessageBox.confirm(`Delete issue ${key}?`, 'Confirm')
  await api.deleteIssue(key)
  ElMessage.success('Deleted')
  await loadIssues()
}

onMounted(loadIssues)
</script>
```

- [ ] **Step 2: Verify in browser**

Test: add issue, toggle active, delete with confirmation.

- [ ] **Step 3: Commit**

```bash
git add web/frontend/src/views/Issues.vue
git commit -m "feat: issues management page with add, toggle, and delete"
```

---

## Task 20: Frontend - Settings Page

**Files:**
- Modify: `web/frontend/src/views/Settings.vue`

- [ ] **Step 1: Implement Settings view**

`web/frontend/src/views/Settings.vue`:
```vue
<template>
  <div>
    <h2 style="margin-bottom: 20px">Settings</h2>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="Monitor" name="monitor">
        <el-form label-width="200px" style="max-width: 600px">
          <el-form-item label="Sampling Interval (sec)">
            <el-input-number v-model="settings.monitor_interval_sec" :min="10" :max="300" />
          </el-form-item>
          <el-form-item label="OCR Enabled">
            <el-switch v-model="settings.monitor_ocr_enabled" />
          </el-form-item>
          <el-form-item label="OCR Engine">
            <el-select v-model="settings.monitor_ocr_engine">
              <el-option label="Auto" value="auto" />
              <el-option label="Vision (macOS)" value="vision" />
              <el-option label="WinOCR (Windows)" value="winocr" />
              <el-option label="Tesseract" value="tesseract" />
            </el-select>
          </el-form-item>
          <el-form-item label="Screenshot Retention (days)">
            <el-input-number v-model="settings.monitor_screenshot_retention_days" :min="1" :max="90" />
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="Git Repos" name="git">
        <p style="color: #909399; margin-bottom: 16px">Configure git repositories to track commits from.</p>
        <el-table :data="gitRepos" stripe style="margin-bottom: 16px">
          <el-table-column prop="path" label="Path" />
          <el-table-column prop="author_email" label="Author Email" />
          <el-table-column label="Actions" width="80">
            <template #default="{ $index }">
              <el-button text type="danger" @click="gitRepos.splice($index, 1)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-form inline>
          <el-form-item>
            <el-input v-model="newRepo.path" placeholder="/path/to/repo" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="newRepo.author_email" placeholder="email@example.com" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="addRepo">Add</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="Jira" name="jira">
        <el-form label-width="160px" style="max-width: 600px">
          <el-form-item label="Server URL">
            <el-input v-model="settings.jira_server_url" placeholder="https://jira.example.com" />
          </el-form-item>
          <el-form-item label="Personal Access Token">
            <el-input v-model="settings.jira_pat" type="password" show-password />
          </el-form-item>
          <el-form-item>
            <el-button @click="testJiraConnection">Test Connection</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="LLM" name="llm">
        <el-form label-width="160px" style="max-width: 600px">
          <el-form-item label="Engine">
            <el-select v-model="settings.llm_engine">
              <el-option label="Kimi (Moonshot)" value="kimi" />
              <el-option label="OpenAI" value="openai" />
              <el-option label="Ollama" value="ollama" />
              <el-option label="Claude" value="claude" />
            </el-select>
          </el-form-item>
          <el-form-item label="API Key">
            <el-input v-model="settings.llm_api_key" type="password" show-password />
          </el-form-item>
          <el-form-item label="Model">
            <el-input v-model="settings.llm_model" />
          </el-form-item>
          <el-form-item label="Base URL">
            <el-input v-model="settings.llm_base_url" />
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="Prompts" name="prompts">
        <h4>Summarize Prompt</h4>
        <p style="color: #909399; font-size: 12px; margin-bottom: 8px">
          Variables: {date}, {jira_issues}, {git_commits}, {activities}
        </p>
        <el-input v-model="settings.summarize_prompt" type="textarea" :rows="12" />
        <h4 style="margin-top: 20px">Auto-Approve Prompt</h4>
        <p style="color: #909399; font-size: 12px; margin-bottom: 8px">
          Variables: {date}, {issue_key}, {issue_summary}, {time_spent_hours}, {summary}, {git_commits}
        </p>
        <el-input v-model="settings.auto_approve_prompt" type="textarea" :rows="12" />
      </el-tab-pane>

      <el-tab-pane label="Scheduler" name="scheduler">
        <el-form label-width="200px" style="max-width: 600px">
          <el-form-item label="Daily Trigger Enabled">
            <el-switch v-model="settings.scheduler_enabled" />
          </el-form-item>
          <el-form-item label="Trigger Time">
            <el-time-picker v-model="settings.scheduler_trigger_time" format="HH:mm" value-format="HH:mm" />
          </el-form-item>
          <el-form-item label="Auto-Approve Enabled">
            <el-switch v-model="settings.auto_approve_enabled" />
          </el-form-item>
          <el-form-item label="Auto-Approve Timeout (min)">
            <el-input-number v-model="settings.auto_approve_timeout_min" :min="5" :max="120" />
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <div style="margin-top: 20px">
      <el-button type="primary" @click="saveAll">Save All Settings</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const activeTab = ref('monitor')
const settings = ref({
  monitor_interval_sec: 30,
  monitor_ocr_enabled: true,
  monitor_ocr_engine: 'auto',
  monitor_screenshot_retention_days: 7,
  jira_server_url: '',
  jira_pat: '',
  llm_engine: 'kimi',
  llm_api_key: '',
  llm_model: '',
  llm_base_url: '',
  summarize_prompt: '',
  auto_approve_prompt: '',
  scheduler_enabled: true,
  scheduler_trigger_time: '18:00',
  auto_approve_enabled: true,
  auto_approve_timeout_min: 30,
})
const gitRepos = ref([])
const newRepo = ref({ path: '', author_email: '' })

async function loadSettings() {
  const res = await api.getSettings()
  for (const item of res.data) {
    if (item.key in settings.value) {
      const val = item.value
      // Parse booleans and numbers
      if (val === 'true') settings.value[item.key] = true
      else if (val === 'false') settings.value[item.key] = false
      else if (!isNaN(Number(val)) && val !== '') settings.value[item.key] = Number(val)
      else settings.value[item.key] = val
    }
  }
}

function addRepo() {
  if (!newRepo.value.path) return
  gitRepos.value.push({ ...newRepo.value })
  newRepo.value = { path: '', author_email: '' }
}

async function saveAll() {
  for (const [key, value] of Object.entries(settings.value)) {
    await api.putSetting(key, String(value))
  }
  ElMessage.success('Settings saved')
}

function testJiraConnection() {
  ElMessage.info('Connection test not yet implemented')
}

onMounted(loadSettings)
</script>
```

- [ ] **Step 2: Verify in browser**

Test: all tabs render, save settings works, values persist after reload.

- [ ] **Step 3: Commit**

```bash
git add web/frontend/src/views/Settings.vue
git commit -m "feat: settings page with monitor, git, jira, LLM, prompts, and scheduler config"
```

---

## Task 21: Build Frontend + Final Integration Test

**Files:**
- Modify: `auto_daily_log/web/app.py`

- [ ] **Step 1: Build Vue.js frontend for production**

```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log/web/frontend
npm run build
```
Expected: `dist/` directory created with static files

- [ ] **Step 2: Update FastAPI to serve static files**

Ensure `auto_daily_log/web/app.py` has the static file mount (added in Task 16 Step 6). Verify it works:

```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log
python -m auto_daily_log --port 8080
```

Open `http://localhost:8080` — should load the Vue.js app from FastAPI.

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log
pytest tests/ -v
```
Expected: All tests pass

- [ ] **Step 4: End-to-end smoke test**

Manual verification checklist:
1. Start app: `python -m auto_daily_log`
2. Open `http://localhost:8080`
3. Settings: configure LLM API key, Jira URL/PAT
4. Issues: add a Jira issue
5. Dashboard: shows activity stats (monitor running)
6. Worklogs: manually trigger summary or wait for scheduled time

- [ ] **Step 5: Final commit**

```bash
git add .
git commit -m "feat: production build and full integration"
```
