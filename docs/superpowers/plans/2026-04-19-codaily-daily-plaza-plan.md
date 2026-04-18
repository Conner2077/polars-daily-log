# CoDaily / 日报广场 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a closed-group daily-log subscription SaaS (CoDaily) as a brand-new repo `polars-daily-plaza`, fully independent from PDL, talking only over a versioned HTTP push-contract (v1.0).

**Architecture:** FastAPI + SQLite(WAL) backend + Vue 3 / Element Plus frontend, deployed as a Docker stack behind Caddy on a single VPS. Jira acts as Identity Provider (verify user credentials against `jira.fanruan.com`); invites whitelist gates signup; unilateral follow model with follower visibility.

**Tech Stack:** Python 3.11+, FastAPI, aiosqlite, httpx, pytest + pytest-asyncio + pytest-httpx, slowapi; Vue 3, Element Plus, Vite, Pinia, axios; Docker, Caddy.

**Design spec reference:** `docs/superpowers/specs/2026-04-19-codaily-daily-plaza-design.md` (commit `0a4eb82` in the PDL repo). Plan authors / executors should read spec sections when architectural context is needed; this plan does NOT re-derive design decisions.

**Working directory convention:** All file paths in this plan are **relative to the new repo root** `/Users/conner/Zone/code/ai_project/polars-daily-plaza/` (created in Task 0.1). The plan file itself temporarily lives in the PDL repo until plaza exists — it will be copied over in Task 0.1.

---

## File Structure

```
polars-daily-plaza/
├── codaily/                      # Python backend package
│   ├── __init__.py               # version string
│   ├── app.py                    # FastAPI app factory; router wiring; startup/shutdown
│   ├── config.py                 # env-var config (CODAILY_DB, CODAILY_JIRA_BASE, ...)
│   ├── db.py                     # aiosqlite connection manager + schema init + migrations
│   ├── models.py                 # Pydantic v2 request/response schemas
│   ├── audit.py                  # log_action() helper
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── jira.py               # HTTP client that validates credentials via Jira
│   │   ├── session.py            # token gen, hashing (SHA-256), session CRUD
│   │   ├── deps.py               # FastAPI dependencies: current_user, require_admin
│   │   └── routes.py             # /auth/login, /auth/logout, /auth/me, /sessions/*
│   └── api/
│       ├── __init__.py
│       ├── push.py               # POST /push (publisher entry)
│       ├── posts.py              # posts list/get/delete/restore/hide/unhide
│       ├── follows.py            # follow/unfollow + list
│       ├── users.py              # user directory + profile
│       ├── feed.py               # aggregated feed for current user
│       └── admin.py              # invites CRUD + audit log query
├── web/frontend/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── main.js               # Vue mount + Pinia + Element Plus + router
│       ├── App.vue               # app shell (header + router-view)
│       ├── router.js             # route table
│       ├── api.js                # axios client wrapping /api/v1/*
│       ├── stores/auth.js        # Pinia store for session state
│       ├── views/
│       │   ├── Login.vue
│       │   ├── Feed.vue
│       │   ├── UserProfile.vue
│       │   ├── Settings.vue      # Sessions / Recycle / Followers tabs
│       │   └── AdminPanel.vue    # Invites / Audit tabs
│       └── components/
│           └── PostCard.vue      # one entry in the feed
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # shared fixtures: in-memory DB, test client, mocked Jira
│   ├── test_auth.py
│   ├── test_push.py
│   ├── test_posts.py
│   ├── test_follow.py
│   ├── test_feed.py
│   ├── test_admin.py
│   └── test_e2e.py
├── docs/
│   ├── push-contract-v1.md       # normative spec for publishers
│   ├── deployment.md
│   └── specs/
│       └── 2026-04-19-codaily-daily-plaza-design.md  # copied from PDL repo
├── Dockerfile                    # multi-stage: frontend build + python runtime
├── docker-compose.yml
├── Caddyfile
├── pyproject.toml
├── .gitignore
├── .dockerignore
├── AGENTS.md                     # project principles (like PDL's)
├── README.md
└── Makefile                      # dev convenience: make test / make run / make fmt
```

## Phases Overview

| Phase | Scope | Tasks |
|-------|-------|-------|
| 0 | Repo scaffolding, pytest, FastAPI + Vue skeletons | 0.1 – 0.4 |
| 1 | Config, DB schema, audit helper | 1.1 – 1.3 |
| 2 | Auth (Jira proxy, sessions, rate limit, endpoints) | 2.1 – 2.6 |
| 3 | Posts + Push API | 3.1 – 3.4 |
| 4 | Follows + Users + Feed | 4.1 – 4.3 |
| 5 | Admin (invites + audit query) | 5.1 – 5.2 |
| 6 | Frontend core (shell, login, feed, profile) | 6.1 – 6.4 |
| 7 | Frontend Settings + Admin | 7.1 – 7.3 |
| 8 | Deployment + E2E + docs | 8.1 – 8.3 |

Each task is a single commit. Tests always go first. Precise-value assertions only — no `assert x`, `assert len(x) > 0`, etc.

---

## Phase 0: Repo Scaffolding

### Task 0.1: Create new repo + project files

**Files:**
- Create: `/Users/conner/Zone/code/ai_project/polars-daily-plaza/.git/`
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.dockerignore`
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `Makefile`
- Create: `codaily/__init__.py`
- Create: `tests/__init__.py`
- Create: `docs/specs/2026-04-19-codaily-daily-plaza-design.md` (copied from PDL)

- [ ] **Step 1: Initialize the new repo**

```bash
cd /Users/conner/Zone/code/ai_project
mkdir polars-daily-plaza
cd polars-daily-plaza
git init
mkdir -p codaily/auth codaily/api web/frontend/src/views web/frontend/src/components web/frontend/src/stores tests docs/specs
```

- [ ] **Step 2: Write pyproject.toml**

```toml
[project]
name = "codaily"
version = "0.1.0"
description = "CoDaily / 日报广场 — daily-log subscription platform"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "aiosqlite>=0.19",
    "httpx>=0.27",
    "pydantic>=2.6",
    "python-multipart>=0.0.9",
    "slowapi>=0.1.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-asyncio>=0.23",
    "pytest-httpx>=0.28",
    "respx>=0.21",
]

[build-system]
requires = ["setuptools>=64", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["codaily*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Write .gitignore, .dockerignore, codaily/__init__.py, tests/__init__.py**

`.gitignore`:
```
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/
dist/
build/
node_modules/
web/frontend/dist/
.DS_Store
*.db
*.db-journal
*.db-wal
*.db-shm
/data/
```

`.dockerignore`:
```
.git
.venv
__pycache__
*.pyc
tests/
docs/
node_modules
web/frontend/node_modules
```

`codaily/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`: (empty file)

- [ ] **Step 4: Write README.md, AGENTS.md, Makefile**

`README.md` (minimal, full version landed in Task 8.3):
```markdown
# CoDaily / 日报广场

Daily-log exchange platform. Users subscribe to colleagues' daily reports
pushed by publishers (e.g. PDL). Closed-invite group, Jira authentication.

See `docs/specs/2026-04-19-codaily-daily-plaza-design.md` for design.

## Dev quickstart
    pip install -e ".[dev]"
    pytest
```

`AGENTS.md` (project principles — adapt from PDL's style):
```markdown
# CoDaily — Project principles

## Core tenets

1. **Independent from PDL**. No code imports from auto_daily_log. Sole contract
   is the push-contract v1.x documented in `docs/push-contract-v1.md`.
2. **Format-neutral protocol**. Publishers decide what shape to push; CoDaily
   faithfully stores content + metadata, extra fields preserved verbatim.
3. **Unknown metadata versions are kept, not rejected**. Store them; dashboard
   degrades gracefully and tells the user to upgrade.
4. **Precise-value test assertions**. `assert resp.status_code == 201`, never
   `assert resp.ok`. No `assert len(x) > 0` — assert the exact count.
5. **SHA-256 hash tokens before persisting**. Session DB leak should not reveal
   live bearer tokens.
6. **Author soft-deletes, followers hide**. Author delete goes to recycle bin
   (`posts.deleted_at`); follower "delete" writes `post_hides` only.

## PR checks

- tests green (`pytest`)
- `codaily/` runs without import errors
- when adding a metadata field, bump `schema_version` and update
  `docs/push-contract-v1.md`
```

`Makefile`:
```make
.PHONY: test run fmt lint install

install:
	pip install -e ".[dev]"

test:
	pytest -v

run:
	uvicorn codaily.app:create_app --factory --reload --port 8000

fmt:
	python -m ruff format codaily tests

lint:
	python -m ruff check codaily tests
```

- [ ] **Step 5: Copy design spec from PDL repo**

```bash
cp /Users/conner/Zone/code/ai_project/auto_daily_log/docs/superpowers/specs/2026-04-19-codaily-daily-plaza-design.md docs/specs/
```

- [ ] **Step 6: Copy this plan file from PDL repo**

```bash
mkdir -p docs/plans
cp /Users/conner/Zone/code/ai_project/auto_daily_log/docs/superpowers/plans/2026-04-19-codaily-daily-plaza-plan.md docs/plans/
```

- [ ] **Step 7: Initial install + smoke check**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -c "import codaily; print(codaily.__version__)"
```

Expected output: `0.1.0`

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: scaffold new repo for CoDaily / 日报广场"
```

---

### Task 0.2: Pytest harness with smoke test

**Files:**
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write failing smoke test**

`tests/test_smoke.py`:
```python
import codaily


def test_version_is_pep440():
    v = codaily.__version__
    parts = v.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
    assert v == "0.1.0"
```

- [ ] **Step 2: Run — expected PASS (version already set in 0.1)**

```bash
pytest tests/test_smoke.py -v
```

Expected: `1 passed`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_smoke.py
git commit -m "test: smoke check for package version"
```

---

### Task 0.3: FastAPI skeleton with /health

**Files:**
- Create: `codaily/app.py`
- Create: `tests/conftest.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: Write failing test**

`tests/conftest.py`:
```python
import pytest
from httpx import AsyncClient, ASGITransport

from codaily.app import create_app


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
```

`tests/test_health.py`:
```python
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
```

- [ ] **Step 2: Run — expected FAIL (no create_app yet)**

```bash
pytest tests/test_health.py -v
```

Expected: `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Write codaily/app.py**

```python
from fastapi import FastAPI

from . import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="CoDaily", version=__version__)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    return app
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_health.py -v
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/app.py tests/conftest.py tests/test_health.py
git commit -m "feat: FastAPI skeleton with /health"
```

---

### Task 0.4: Frontend Vue 3 + Element Plus skeleton

**Files:**
- Create: `web/frontend/package.json`
- Create: `web/frontend/vite.config.js`
- Create: `web/frontend/index.html`
- Create: `web/frontend/src/main.js`
- Create: `web/frontend/src/App.vue`
- Create: `web/frontend/src/router.js`

- [ ] **Step 1: Initialize package.json**

```bash
cd web/frontend
cat > package.json <<'EOF'
{
  "name": "codaily-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "vue": "^3.4.0",
    "vue-router": "^4.3.0",
    "pinia": "^2.1.7",
    "element-plus": "^2.7.0",
    "@element-plus/icons-vue": "^2.3.1",
    "axios": "^1.6.0"
  },
  "devDependencies": {
    "vite": "^5.2.0",
    "@vitejs/plugin-vue": "^5.0.0"
  }
}
EOF
npm install
```

- [ ] **Step 2: Write vite.config.js, index.html, main.js, App.vue, router.js**

`web/frontend/vite.config.js`:
```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
    port: 5173
  },
  build: { outDir: 'dist', emptyOutDir: true }
})
```

`web/frontend/index.html`:
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>CoDaily — 日报广场</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

`web/frontend/src/main.js`:
```js
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.mount('#app')
```

`web/frontend/src/App.vue`:
```vue
<template>
  <el-container style="height: 100vh">
    <el-header>CoDaily — 日报广场</el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>
```

`web/frontend/src/router.js`:
```js
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', component: { template: '<div>Feed (coming soon)</div>' } }
]

export default createRouter({ history: createWebHistory(), routes })
```

- [ ] **Step 3: Smoke build**

```bash
cd web/frontend
npm run build
```

Expected: `dist/index.html` and `dist/assets/*.js` exist; no errors.

- [ ] **Step 4: Commit**

```bash
cd ../..
git add web/frontend/
git commit -m "feat: Vue 3 + Element Plus frontend skeleton"
```

---

## Phase 1: Database + Core Infrastructure

### Task 1.1: Config module

**Files:**
- Create: `codaily/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

`tests/test_config.py`:
```python
import os
from codaily.config import Settings


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("CODAILY_DB", "/tmp/x.db")
    monkeypatch.setenv("CODAILY_JIRA_BASE", "https://jira.example.com")
    monkeypatch.setenv("CODAILY_ADMIN", "alice")
    s = Settings.from_env()
    assert s.db_path == "/tmp/x.db"
    assert s.jira_base == "https://jira.example.com"
    assert s.admin == "alice"
    assert s.log_level == "INFO"  # default


def test_settings_missing_admin_raises(monkeypatch):
    monkeypatch.delenv("CODAILY_ADMIN", raising=False)
    monkeypatch.setenv("CODAILY_DB", "/tmp/x.db")
    monkeypatch.setenv("CODAILY_JIRA_BASE", "https://jira.example.com")
    import pytest
    with pytest.raises(RuntimeError, match="CODAILY_ADMIN"):
        Settings.from_env()
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_config.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement config.py**

```python
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    jira_base: str
    admin: str
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        db = os.environ.get("CODAILY_DB")
        jira = os.environ.get("CODAILY_JIRA_BASE")
        admin = os.environ.get("CODAILY_ADMIN")
        if not db:
            raise RuntimeError("CODAILY_DB env var is required")
        if not jira:
            raise RuntimeError("CODAILY_JIRA_BASE env var is required")
        if not admin:
            raise RuntimeError("CODAILY_ADMIN env var is required (first admin username)")
        return cls(
            db_path=db,
            jira_base=jira.rstrip("/"),
            admin=admin,
            log_level=os.environ.get("CODAILY_LOG_LEVEL", "INFO"),
        )
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_config.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/config.py tests/test_config.py
git commit -m "feat: env-var Settings with required-field validation"
```

---

### Task 1.2: Database module (connection + schema init)

**Files:**
- Create: `codaily/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing test**

`tests/test_db.py`:
```python
import pytest
from codaily.db import Database


async def test_schema_creates_all_tables():
    db = Database(":memory:")
    await db.connect()
    await db.init_schema()
    rows = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = [r["name"] for r in rows]
    assert names == [
        "audit_log",
        "follows",
        "invites",
        "post_hides",
        "posts",
        "sessions",
        "users",
    ]
    await db.close()


async def test_init_schema_is_idempotent():
    db = Database(":memory:")
    await db.connect()
    await db.init_schema()
    await db.init_schema()  # second call must not raise
    await db.close()


async def test_admin_bootstrap_creates_admin_user():
    db = Database(":memory:")
    await db.connect()
    await db.init_schema()
    await db.bootstrap_admin("alice")
    row = await db.fetch_one("SELECT jira_username, is_admin FROM users WHERE jira_username=?", ("alice",))
    assert row["jira_username"] == "alice"
    assert row["is_admin"] == 1
    invite = await db.fetch_one("SELECT jira_username, invited_by FROM invites WHERE jira_username=?", ("alice",))
    assert invite["jira_username"] == "alice"
    assert invite["invited_by"] == "alice"  # self-invited
    await db.close()
```

- [ ] **Step 2: Run — expected FAIL (module missing)**

```bash
pytest tests/test_db.py -v
```

- [ ] **Step 3: Implement codaily/db.py**

```python
from __future__ import annotations
import aiosqlite
from typing import Any, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    jira_username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    email TEXT,
    avatar_url TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invites (
    jira_username TEXT PRIMARY KEY,
    invited_by TEXT NOT NULL,
    invited_at TEXT DEFAULT (datetime('now')),
    consumed_at TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    jira_username TEXT NOT NULL,
    client_kind TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    last_used_at TEXT,
    expires_at TEXT,
    revoked_at TEXT,
    label TEXT
);

CREATE TABLE IF NOT EXISTS follows (
    follower TEXT NOT NULL,
    followee TEXT NOT NULL,
    followed_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (follower, followee)
);
CREATE INDEX IF NOT EXISTS idx_follows_followee ON follows(followee);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    post_date TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'day',
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'markdown',
    metadata TEXT DEFAULT '{}',
    source TEXT,
    pushed_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT,
    deleted_at TEXT,
    UNIQUE(author, post_date, scope)
);
CREATE INDEX IF NOT EXISTS idx_posts_author_date ON posts(author, post_date DESC);

CREATE TABLE IF NOT EXISTS post_hides (
    follower TEXT NOT NULL,
    post_id INTEGER NOT NULL,
    hidden_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (follower, post_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    ip TEXT,
    user_agent TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor, created_at DESC);
"""


class Database:
    def __init__(self, path: str):
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def bootstrap_admin(self, jira_username: str) -> None:
        """Ensure the configured admin exists as (users, invites) self-invited."""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)",
            (jira_username, jira_username),
        )
        await self._conn.execute(
            "INSERT INTO users (jira_username, display_name, is_admin) VALUES (?, ?, 1) "
            "ON CONFLICT(jira_username) DO UPDATE SET is_admin=1",
            (jira_username, jira_username),
        )
        await self._conn.commit()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur.lastrowid

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_db.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/db.py tests/test_db.py
git commit -m "feat: Database class with schema init + admin bootstrap"
```

---

### Task 1.3: Audit log helper

**Files:**
- Create: `codaily/audit.py`
- Create: `tests/test_audit.py`

- [ ] **Step 1: Write failing test**

`tests/test_audit.py`:
```python
import json
from codaily.db import Database
from codaily.audit import log_action


async def test_log_action_inserts_row():
    db = Database(":memory:")
    await db.connect()
    await db.init_schema()
    await log_action(db, actor="alice", action="login", target=None, detail={"from": "ip"}, ip="10.0.0.1", ua="curl/8")
    row = await db.fetch_one("SELECT actor, action, target, detail, ip, user_agent FROM audit_log")
    assert row["actor"] == "alice"
    assert row["action"] == "login"
    assert row["target"] is None
    assert json.loads(row["detail"]) == {"from": "ip"}
    assert row["ip"] == "10.0.0.1"
    assert row["user_agent"] == "curl/8"
    await db.close()


async def test_log_action_detail_none_stores_null():
    db = Database(":memory:")
    await db.connect()
    await db.init_schema()
    await log_action(db, actor="bob", action="push", target="42")
    row = await db.fetch_one("SELECT detail FROM audit_log")
    assert row["detail"] is None
    await db.close()
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_audit.py -v
```

- [ ] **Step 3: Implement codaily/audit.py**

```python
from __future__ import annotations
import json
from typing import Any, Optional

from .db import Database


async def log_action(
    db: Database,
    *,
    actor: str,
    action: str,
    target: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
    ua: Optional[str] = None,
) -> None:
    detail_json = json.dumps(detail) if detail is not None else None
    await db.execute(
        "INSERT INTO audit_log (actor, action, target, detail, ip, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (actor, action, target, detail_json, ip, ua),
    )
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_audit.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/audit.py tests/test_audit.py
git commit -m "feat: audit log helper log_action()"
```

---

## Phase 2: Auth

### Task 2.1: Jira verification client

**Files:**
- Create: `codaily/auth/__init__.py` (empty)
- Create: `codaily/auth/jira.py`
- Create: `tests/test_jira_auth.py`

- [ ] **Step 1: Write failing test (mock Jira responses)**

`tests/test_jira_auth.py`:
```python
import httpx
import pytest
from pytest_httpx import HTTPXMock

from codaily.auth.jira import JiraAuth, JiraProfile


async def test_verify_returns_profile_on_200(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://jira.example.com/rest/api/2/myself",
        match_headers={"Authorization": "Basic YWxpY2U6cHc="},  # alice:pw
        json={
            "name": "alice",
            "displayName": "Alice Smith",
            "emailAddress": "alice@x.com",
            "avatarUrls": {"48x48": "https://jira.example.com/a/48.png"},
        },
    )
    ja = JiraAuth(base="https://jira.example.com")
    profile = await ja.verify("alice", "pw")
    assert isinstance(profile, JiraProfile)
    assert profile.username == "alice"
    assert profile.display_name == "Alice Smith"
    assert profile.email == "alice@x.com"
    assert profile.avatar_url == "https://jira.example.com/a/48.png"


async def test_verify_returns_none_on_401(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://jira.example.com/rest/api/2/myself",
        status_code=401,
    )
    ja = JiraAuth(base="https://jira.example.com")
    profile = await ja.verify("alice", "wrongpw")
    assert profile is None


async def test_verify_raises_on_5xx(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://jira.example.com/rest/api/2/myself",
        status_code=503,
    )
    ja = JiraAuth(base="https://jira.example.com")
    with pytest.raises(httpx.HTTPStatusError):
        await ja.verify("alice", "pw")
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_jira_auth.py -v
```

- [ ] **Step 3: Implement codaily/auth/jira.py**

```python
from __future__ import annotations
import base64
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass(frozen=True)
class JiraProfile:
    username: str
    display_name: str
    email: Optional[str]
    avatar_url: Optional[str]


class JiraAuth:
    """Verifies a user's (username, password) against Jira's /myself endpoint."""

    def __init__(self, base: str, timeout: float = 8.0):
        self._base = base.rstrip("/")
        self._timeout = timeout

    async def verify(self, username: str, password: str) -> Optional[JiraProfile]:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.get(
                f"{self._base}/rest/api/2/myself",
                headers={"Authorization": f"Basic {token}"},
            )
        if resp.status_code == 401 or resp.status_code == 403:
            return None
        resp.raise_for_status()
        data = resp.json()
        avatars = data.get("avatarUrls") or {}
        return JiraProfile(
            username=data["name"],
            display_name=data.get("displayName") or data["name"],
            email=data.get("emailAddress"),
            avatar_url=avatars.get("48x48"),
        )
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_jira_auth.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/auth/__init__.py codaily/auth/jira.py tests/test_jira_auth.py
git commit -m "feat: Jira verification client with profile extraction"
```

---

### Task 2.2: Session token module

**Files:**
- Create: `codaily/auth/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Write failing test**

`tests/test_session.py`:
```python
import hashlib
from codaily.db import Database
from codaily.auth.session import (
    generate_token, hash_token, create_session, find_session, revoke_session,
)


async def test_generate_token_is_hex_and_unique():
    t1 = generate_token()
    t2 = generate_token()
    assert len(t1) == 64
    assert all(c in "0123456789abcdef" for c in t1)
    assert t1 != t2


async def test_hash_token_is_sha256_hex():
    t = "abc"
    assert hash_token(t) == hashlib.sha256(t.encode()).hexdigest()


async def test_create_and_find_session():
    db = Database(":memory:"); await db.connect(); await db.init_schema()
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("alice", "A"))
    token = await create_session(db, jira_username="alice", client_kind="browser", expires_in_days=30)
    row = await find_session(db, token)
    assert row["jira_username"] == "alice"
    assert row["client_kind"] == "browser"
    assert row["revoked_at"] is None
    await db.close()


async def test_find_session_returns_none_for_unknown_token():
    db = Database(":memory:"); await db.connect(); await db.init_schema()
    row = await find_session(db, "nonexistent-token")
    assert row is None
    await db.close()


async def test_revoke_session_sets_revoked_at():
    db = Database(":memory:"); await db.connect(); await db.init_schema()
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("alice", "A"))
    token = await create_session(db, jira_username="alice", client_kind="browser", expires_in_days=30)
    await revoke_session(db, hash_token(token))
    row = await find_session(db, token)
    assert row is None  # revoked → not findable
    await db.close()


async def test_pdl_publisher_token_never_expires():
    db = Database(":memory:"); await db.connect(); await db.init_schema()
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("alice", "A"))
    token = await create_session(db, jira_username="alice", client_kind="pdl-publisher", expires_in_days=None, label="MacBook")
    row = await find_session(db, token)
    assert row["expires_at"] is None
    assert row["label"] == "MacBook"
    await db.close()
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_session.py -v
```

- [ ] **Step 3: Implement codaily/auth/session.py**

```python
from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..db import Database


def generate_token() -> str:
    """Return 64 hex chars of cryptographic randomness (32 raw bytes)."""
    return secrets.token_hex(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(
    db: Database,
    *,
    jira_username: str,
    client_kind: str,
    expires_in_days: Optional[int],
    label: Optional[str] = None,
) -> str:
    """Create a session row; return the **plaintext** token (only chance to see it)."""
    token = generate_token()
    th = hash_token(token)
    expires_at: Optional[str] = None
    if expires_in_days is not None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        ).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    await db.execute(
        "INSERT INTO sessions (token_hash, jira_username, client_kind, expires_at, label) "
        "VALUES (?, ?, ?, ?, ?)",
        (th, jira_username, client_kind, expires_at, label),
    )
    return token


async def find_session(db: Database, token: str) -> Optional[dict]:
    """Return session row if token is valid, not revoked, not expired."""
    th = hash_token(token)
    row = await db.fetch_one(
        "SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > datetime('now'))",
        (th,),
    )
    return row


async def revoke_session(db: Database, token_hash: str) -> None:
    await db.execute(
        "UPDATE sessions SET revoked_at = datetime('now') WHERE token_hash = ?",
        (token_hash,),
    )


async def touch_session(db: Database, token_hash: str) -> None:
    await db.execute(
        "UPDATE sessions SET last_used_at = datetime('now') WHERE token_hash = ?",
        (token_hash,),
    )
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_session.py -v
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/auth/session.py tests/test_session.py
git commit -m "feat: session token create/find/revoke with SHA-256 hashing"
```

---

### Task 2.3: FastAPI auth dependencies

**Files:**
- Create: `codaily/auth/deps.py`
- Modify: `codaily/app.py` (register app.state.db on startup)
- Create: `tests/test_deps.py`

- [ ] **Step 1: Write failing test**

`tests/test_deps.py`:
```python
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, Depends

from codaily.db import Database
from codaily.auth.deps import current_user, require_admin
from codaily.auth.session import create_session


@pytest.fixture
async def app_with_db():
    app = FastAPI()
    db = Database(":memory:")
    await db.connect()
    await db.init_schema()
    await db.execute("INSERT INTO users (jira_username, display_name, is_admin) VALUES (?, ?, ?)", ("alice", "A", 0))
    await db.execute("INSERT INTO users (jira_username, display_name, is_admin) VALUES (?, ?, ?)", ("root", "R", 1))
    app.state.db = db

    @app.get("/me")
    async def me(user: dict = Depends(current_user)):
        return {"username": user["jira_username"]}

    @app.get("/admin")
    async def admin(user: dict = Depends(require_admin)):
        return {"ok": True}

    yield app
    await db.close()


async def test_current_user_returns_401_without_token(app_with_db):
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/me")
        assert r.status_code == 401


async def test_current_user_returns_401_for_bogus_token(app_with_db):
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/me", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401


async def test_current_user_returns_200_for_valid_bearer(app_with_db):
    db = app_with_db.state.db
    token = await create_session(db, jira_username="alice", client_kind="pdl-publisher", expires_in_days=None)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json() == {"username": "alice"}


async def test_current_user_accepts_cookie(app_with_db):
    db = app_with_db.state.db
    token = await create_session(db, jira_username="alice", client_kind="browser", expires_in_days=30)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/me", cookies={"codaily_session": token})
        assert r.status_code == 200
        assert r.json() == {"username": "alice"}


async def test_require_admin_403_for_non_admin(app_with_db):
    db = app_with_db.state.db
    token = await create_session(db, jira_username="alice", client_kind="browser", expires_in_days=30)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin", cookies={"codaily_session": token})
        assert r.status_code == 403


async def test_require_admin_200_for_admin(app_with_db):
    db = app_with_db.state.db
    token = await create_session(db, jira_username="root", client_kind="browser", expires_in_days=30)
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/admin", cookies={"codaily_session": token})
        assert r.status_code == 200
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_deps.py -v
```

- [ ] **Step 3: Implement codaily/auth/deps.py**

```python
from __future__ import annotations
from fastapi import Depends, HTTPException, Request

from ..db import Database
from .session import find_session, touch_session, hash_token


COOKIE_NAME = "codaily_session"


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip() or None
    return request.cookies.get(COOKIE_NAME)


async def current_user(request: Request) -> dict:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="missing credentials")
    db: Database = request.app.state.db
    sess = await find_session(db, token)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    user = await db.fetch_one(
        "SELECT * FROM users WHERE jira_username = ?", (sess["jira_username"],)
    )
    if not user:
        raise HTTPException(status_code=401, detail="user missing")
    await touch_session(db, hash_token(token))
    # Stash the session kind onto the user dict for downstream handlers.
    user["_session_kind"] = sess["client_kind"]
    user["_token_hash"] = hash_token(token)
    return user


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="admin only")
    return user
```

- [ ] **Step 4: Modify codaily/app.py — open DB on startup**

Replace entire file:
```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .config import Settings
from .db import Database


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = Settings.from_env()
    db = Database(settings.db_path)
    await db.connect()
    await db.init_schema()
    await db.bootstrap_admin(settings.admin)
    app.state.db = db
    app.state.settings = settings
    try:
        yield
    finally:
        await db.close()


def create_app() -> FastAPI:
    app = FastAPI(title="CoDaily", version=__version__, lifespan=_lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    return app
```

- [ ] **Step 5: Run — expected PASS**

```bash
pytest tests/test_deps.py -v
```

Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add codaily/auth/deps.py codaily/app.py tests/test_deps.py
git commit -m "feat: current_user / require_admin FastAPI dependencies"
```

---

### Task 2.4: Login / Logout / Me routes

**Files:**
- Create: `codaily/auth/routes.py`
- Modify: `codaily/app.py` (mount auth router; make JiraAuth/DB injectable via state)
- Modify: `tests/conftest.py` (in-memory DB + mocked Jira fixture for route tests)
- Create: `tests/test_auth_routes.py`

- [ ] **Step 1: Expand tests/conftest.py**

Replace `tests/conftest.py`:
```python
import pytest
from httpx import AsyncClient, ASGITransport

from codaily.app import create_app
from codaily.db import Database
from codaily.auth.jira import JiraAuth, JiraProfile


class FakeJira(JiraAuth):
    """Swap-in that returns a fixed profile for specific (user, pw) pairs."""
    def __init__(self):
        self._creds: dict[tuple[str, str], JiraProfile] = {}

    def add(self, username: str, password: str, profile: JiraProfile):
        self._creds[(username, password)] = profile

    async def verify(self, username: str, password: str):
        return self._creds.get((username, password))


@pytest.fixture
async def app_and_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "codaily.db")
    monkeypatch.setenv("CODAILY_DB", db_path)
    monkeypatch.setenv("CODAILY_JIRA_BASE", "https://jira.example.com")
    monkeypatch.setenv("CODAILY_ADMIN", "root")

    app = create_app()
    fake_jira = FakeJira()

    # Swap the real Jira client on the app after lifespan sets it.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        # Trigger startup by making one request
        r = await client.get("/health")
        assert r.status_code == 200
        app.state.jira = fake_jira  # override whatever lifespan set
        yield app, app.state.db, fake_jira, client


@pytest.fixture
async def client(app_and_db):
    _app, _db, _jira, c = app_and_db
    yield c
```

- [ ] **Step 2: Write failing tests**

`tests/test_auth_routes.py`:
```python
from codaily.auth.jira import JiraProfile


async def test_login_success_invited_user_returns_cookie_and_creates_user(app_and_db):
    app, db, jira, client = app_and_db
    jira.add("alice", "pw", JiraProfile(username="alice", display_name="Alice Smith", email="a@x.com", avatar_url="https://x/a.png"))
    await db.execute("INSERT INTO invites (jira_username, invited_by) VALUES (?, ?)", ("alice", "root"))

    r = await client.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["jira_username"] == "alice"
    assert body["user"]["display_name"] == "Alice Smith"
    assert body["user"]["is_admin"] == 0
    assert "codaily_session" in r.cookies

    # users row created with profile from Jira
    u = await db.fetch_one("SELECT * FROM users WHERE jira_username=?", ("alice",))
    assert u["display_name"] == "Alice Smith"
    assert u["email"] == "a@x.com"
    assert u["avatar_url"] == "https://x/a.png"

    # invite consumed_at stamped
    inv = await db.fetch_one("SELECT consumed_at FROM invites WHERE jira_username=?", ("alice",))
    assert inv["consumed_at"] is not None


async def test_login_wrong_password_returns_401(app_and_db):
    _app, _db, jira, client = app_and_db
    # no cred registered in fake_jira
    r = await client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Jira 验证失败"


async def test_login_not_invited_returns_403(app_and_db):
    _app, _db, jira, client = app_and_db
    jira.add("ghost", "pw", JiraProfile(username="ghost", display_name="G", email=None, avatar_url=None))
    # no invite added for "ghost"
    r = await client.post("/api/v1/auth/login", json={"username": "ghost", "password": "pw"})
    assert r.status_code == 403
    assert r.json()["detail"] == "未被邀请，请联系管理员"


async def test_me_returns_current_user(app_and_db):
    app, db, jira, client = app_and_db
    jira.add("alice", "pw", JiraProfile(username="alice", display_name="A", email=None, avatar_url=None))
    await db.execute("INSERT INTO invites (jira_username, invited_by) VALUES (?, ?)", ("alice", "root"))
    await client.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})

    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["jira_username"] == "alice"


async def test_logout_revokes_and_subsequent_requests_401(app_and_db):
    app, db, jira, client = app_and_db
    jira.add("alice", "pw", JiraProfile(username="alice", display_name="A", email=None, avatar_url=None))
    await db.execute("INSERT INTO invites (jira_username, invited_by) VALUES (?, ?)", ("alice", "root"))
    await client.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})

    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 204

    r2 = await client.get("/api/v1/auth/me")
    assert r2.status_code == 401
```

- [ ] **Step 3: Run — expected FAIL**

```bash
pytest tests/test_auth_routes.py -v
```

- [ ] **Step 4: Implement codaily/auth/routes.py**

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..audit import log_action
from .deps import current_user, COOKIE_NAME
from .session import create_session, revoke_session


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    db = request.app.state.db
    jira = request.app.state.jira
    profile = await jira.verify(body.username, body.password)
    if profile is None:
        raise HTTPException(status_code=401, detail="Jira 验证失败")

    invite = await db.fetch_one(
        "SELECT jira_username, consumed_at FROM invites WHERE jira_username = ?",
        (profile.username,),
    )
    if invite is None:
        raise HTTPException(status_code=403, detail="未被邀请，请联系管理员")

    # Upsert user with latest Jira profile.
    await db.execute(
        "INSERT INTO users (jira_username, display_name, email, avatar_url) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(jira_username) DO UPDATE SET "
        "display_name=excluded.display_name, email=excluded.email, avatar_url=excluded.avatar_url",
        (profile.username, profile.display_name, profile.email, profile.avatar_url),
    )
    # Stamp invite consumed_at on first login.
    if invite["consumed_at"] is None:
        await db.execute(
            "UPDATE invites SET consumed_at = datetime('now') WHERE jira_username = ?",
            (profile.username,),
        )

    token = await create_session(db, jira_username=profile.username, client_kind="browser", expires_in_days=30)
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True, secure=True, samesite="strict", path="/",
        max_age=30 * 24 * 3600,
    )
    user = await db.fetch_one("SELECT * FROM users WHERE jira_username = ?", (profile.username,))
    await log_action(db, actor=profile.username, action="login",
                     ip=request.client.host if request.client else None,
                     ua=request.headers.get("user-agent"))
    return {"user": user}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    db = request.app.state.db
    await revoke_session(db, user["_token_hash"])
    response.delete_cookie(COOKIE_NAME, path="/")
    await log_action(db, actor=user["jira_username"], action="logout")


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    # Strip private underscored fields before returning.
    return {k: v for k, v in user.items() if not k.startswith("_")}
```

- [ ] **Step 5: Wire router + Jira into app.py**

Replace `codaily/app.py`:
```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .config import Settings
from .db import Database
from .auth.jira import JiraAuth
from .auth.routes import router as auth_router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = Settings.from_env()
    db = Database(settings.db_path)
    await db.connect()
    await db.init_schema()
    await db.bootstrap_admin(settings.admin)
    app.state.db = db
    app.state.settings = settings
    app.state.jira = JiraAuth(settings.jira_base)
    try:
        yield
    finally:
        await db.close()


def create_app() -> FastAPI:
    app = FastAPI(title="CoDaily", version=__version__, lifespan=_lifespan)
    app.include_router(auth_router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    return app
```

- [ ] **Step 6: Run — expected PASS**

```bash
pytest tests/test_auth_routes.py -v
```

Expected: `5 passed`.

- [ ] **Step 7: Commit**

```bash
git add codaily/auth/routes.py codaily/app.py tests/conftest.py tests/test_auth_routes.py
git commit -m "feat: /auth/login /logout /me with Jira IDP + invites whitelist"
```

---

### Task 2.5: Sessions management endpoints

**Files:**
- Modify: `codaily/auth/routes.py` (add sessions sub-routes)
- Create: `tests/test_sessions_routes.py`

- [ ] **Step 1: Write failing test**

`tests/test_sessions_routes.py`:
```python
from codaily.auth.jira import JiraProfile


async def _login(client, db, jira, username="alice"):
    jira.add(username, "pw", JiraProfile(username=username, display_name=username, email=None, avatar_url=None))
    await db.execute(
        "INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)",
        (username, "root"),
    )
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": "pw"})
    assert r.status_code == 200


async def test_list_sessions_returns_only_mine(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    r = await client.get("/api/v1/sessions")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["jira_username"] == "alice"
    assert body[0]["client_kind"] == "browser"
    assert "token" not in body[0]
    assert "token_hash" not in body[0]


async def test_generate_pdl_token_returns_plaintext_once(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    r = await client.post("/api/v1/sessions/pdl-token", json={"label": "MacBook"})
    assert r.status_code == 201
    body = r.json()
    assert isinstance(body["token"], str)
    assert len(body["token"]) == 64
    assert body["client_kind"] == "pdl-publisher"
    assert body["label"] == "MacBook"

    # List sessions now shows 2 (browser + pdl-publisher), no plaintext
    r2 = await client.get("/api/v1/sessions")
    assert len(r2.json()) == 2
    for s in r2.json():
        assert "token" not in s


async def test_revoke_other_session(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    gen = await client.post("/api/v1/sessions/pdl-token", json={"label": "lab"})
    # find the pdl session id
    sessions = (await client.get("/api/v1/sessions")).json()
    pdl = next(s for s in sessions if s["client_kind"] == "pdl-publisher")

    r = await client.delete(f"/api/v1/sessions/{pdl['id']}")
    assert r.status_code == 204

    remaining = (await client.get("/api/v1/sessions")).json()
    assert len(remaining) == 1
    assert remaining[0]["client_kind"] == "browser"


async def test_revoke_nonexistent_returns_404(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    r = await client.delete("/api/v1/sessions/99999")
    assert r.status_code == 404


async def test_cannot_revoke_other_users_session(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    # Manually insert a session belonging to bob
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    await db.execute(
        "INSERT INTO sessions (token_hash, jira_username, client_kind) VALUES (?, ?, ?)",
        ("deadbeef", "bob", "browser"),
    )
    other = await db.fetch_one("SELECT id FROM sessions WHERE jira_username='bob'")
    r = await client.delete(f"/api/v1/sessions/{other['id']}")
    assert r.status_code == 404  # "not your session" surfaces as 404 not 403 to avoid enumeration
```

Note: the `sessions` table in Task 1.2 has `token_hash` as PK, not an `id`. We must add an integer `id` AUTOINCREMENT. Fix the schema.

- [ ] **Step 2: Migrate schema to add sessions.id**

Edit `codaily/db.py` — update `sessions` table definition:
```sql
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    jira_username TEXT NOT NULL,
    client_kind TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    last_used_at TEXT,
    expires_at TEXT,
    revoked_at TEXT,
    label TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(jira_username);
```

Update the design spec mentally — `sessions.id` is the user-facing handle, `token_hash` is the lookup key. No runtime users yet, so we don't need a migration script.

Also update the expected table ordering in `tests/test_db.py` (names list stays the same — no new table). Confirm `test_session.py` still passes; `create_session` / `find_session` unchanged.

- [ ] **Step 3: Run — expected FAIL for new tests**

```bash
pytest tests/test_sessions_routes.py -v
```

- [ ] **Step 4: Implement session routes**

Append to `codaily/auth/routes.py`:
```python
from pydantic import Field


class PdlTokenBody(BaseModel):
    label: str = Field(min_length=1, max_length=100)


sessions_router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def _scrub(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("token_hash",)}


@sessions_router.get("")
async def list_my_sessions(request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT id, jira_username, client_kind, created_at, last_used_at, expires_at, revoked_at, label "
        "FROM sessions WHERE jira_username = ? AND revoked_at IS NULL ORDER BY created_at DESC",
        (user["jira_username"],),
    )
    return [_scrub(r) for r in rows]


@sessions_router.post("/pdl-token", status_code=201)
async def create_pdl_token(body: PdlTokenBody, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    token = await create_session(
        db,
        jira_username=user["jira_username"],
        client_kind="pdl-publisher",
        expires_in_days=None,
        label=body.label,
    )
    await log_action(db, actor=user["jira_username"], action="create-pdl-token", target=body.label)
    return {"token": token, "client_kind": "pdl-publisher", "label": body.label}


@sessions_router.delete("/{session_id}", status_code=204)
async def revoke(session_id: int, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    row = await db.fetch_one(
        "SELECT id, token_hash FROM sessions WHERE id = ? AND jira_username = ? AND revoked_at IS NULL",
        (session_id, user["jira_username"]),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    from .session import revoke_session as _revoke
    await _revoke(db, row["token_hash"])
    await log_action(db, actor=user["jira_username"], action="revoke-session", target=str(session_id))
```

- [ ] **Step 5: Mount sessions_router in app.py**

In `codaily/app.py`, add:
```python
from .auth.routes import router as auth_router, sessions_router

# inside create_app:
app.include_router(sessions_router)
```

- [ ] **Step 6: Run — expected PASS**

```bash
pytest tests/test_sessions_routes.py tests/test_db.py tests/test_session.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add codaily/auth/routes.py codaily/app.py codaily/db.py tests/test_sessions_routes.py
git commit -m "feat: sessions management endpoints (list/create-pdl-token/revoke)"
```

---

### Task 2.6: Rate limiting on /auth/login

**Files:**
- Modify: `codaily/app.py` (add SlowAPI limiter)
- Modify: `codaily/auth/routes.py` (decorate /login)
- Create: `tests/test_rate_limit.py`

- [ ] **Step 1: Write failing test**

`tests/test_rate_limit.py`:
```python
async def test_login_rate_limit_triggers_after_10_per_minute(app_and_db):
    app, db, jira, client = app_and_db
    for _ in range(10):
        r = await client.post("/api/v1/auth/login", json={"username": "x", "password": "y"})
        assert r.status_code == 401  # wrong creds; allowed
    r = await client.post("/api/v1/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 429
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_rate_limit.py -v
```

- [ ] **Step 3: Wire slowapi**

In `codaily/app.py`, add:
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])

def create_app() -> FastAPI:
    app = FastAPI(title="CoDaily", version=__version__, lifespan=_lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth_router)
    app.include_router(sessions_router)
    ...
```

In `codaily/auth/routes.py`, decorate login (and only login):
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

_login_limiter = Limiter(key_func=get_remote_address)

@router.post("/login")
@_login_limiter.limit("10/minute")
async def login(body: LoginBody, request: Request, response: Response):
    ...
```

Note: slowapi requires `request` as a param in decorated functions — we already have it.

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_rate_limit.py -v
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/app.py codaily/auth/routes.py tests/test_rate_limit.py
git commit -m "feat: rate-limit /auth/login to 10/min per IP"
```

---

## Phase 3: Posts + Push API

### Task 3.1: Pydantic models + metadata validator

**Files:**
- Create: `codaily/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing test**

`tests/test_models.py`:
```python
import pytest
from pydantic import ValidationError

from codaily.models import PushBody, MetadataV1


def test_push_body_minimal():
    b = PushBody(post_date="2026-04-19", scope="day", content="hi")
    assert b.post_date == "2026-04-19"
    assert b.scope == "day"
    assert b.content == "hi"
    assert b.content_type == "markdown"
    assert b.metadata is None
    assert b.source is None


def test_push_body_rejects_bad_date():
    with pytest.raises(ValidationError):
        PushBody(post_date="not-a-date", scope="day", content="x")


def test_push_body_accepts_arbitrary_scope_string():
    # spec: scope is an open string, we preserve whatever publisher sends
    b = PushBody(post_date="2026-04-19", scope="my-custom-scope", content="x")
    assert b.scope == "my-custom-scope"


def test_metadata_v1_requires_schema_version():
    with pytest.raises(ValidationError, match="schema_version"):
        MetadataV1.model_validate({"issue_keys": ["X-1"]})


def test_metadata_v1_full():
    m = MetadataV1.model_validate({
        "schema_version": "1.0",
        "issue_keys": ["X-1", "X-2"],
        "time_spent_sec": 3600,
        "entries": [{"issue_key": "X-1", "hours": 1.0, "summary": "did a thing"}],
        "tags": ["backend"],
    })
    assert m.schema_version == "1.0"
    assert m.issue_keys == ["X-1", "X-2"]
    assert m.time_spent_sec == 3600
    assert m.entries[0].issue_key == "X-1"
    assert m.entries[0].hours == 1.0


def test_metadata_preserves_unknown_fields():
    m = MetadataV1.model_validate({
        "schema_version": "1.0",
        "future_field": "should be kept",
    })
    # model_extra holds unknowns
    assert m.model_extra == {"future_field": "should be kept"}
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_models.py -v
```

- [ ] **Step 3: Implement codaily/models.py**

```python
from __future__ import annotations
from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MetadataEntry(BaseModel):
    issue_key: str
    hours: float
    summary: str


class MetadataV1(BaseModel):
    """v1.0 metadata schema. Unknown fields are preserved via model_extra."""
    model_config = ConfigDict(extra="allow")

    schema_version: str
    issue_keys: Optional[list[str]] = None
    time_spent_sec: Optional[int] = None
    entries: Optional[list[MetadataEntry]] = None
    tags: Optional[list[str]] = None


class PushBody(BaseModel):
    post_date: str = Field(..., description="YYYY-MM-DD")
    scope: str = Field(..., min_length=1, max_length=64,
                       description="Open string; publisher's choice")
    content: str
    content_type: Literal["markdown", "json", "text"] = "markdown"
    metadata: Optional[dict[str, Any]] = None  # raw dict; validated separately
    source: Optional[str] = Field(default=None, max_length=64)

    @field_validator("post_date")
    @classmethod
    def _check_date(cls, v: str) -> str:
        date.fromisoformat(v)  # raises ValueError → ValidationError
        return v


class PostOut(BaseModel):
    id: int
    author: str
    post_date: str
    scope: str
    content: str
    content_type: str
    metadata: dict[str, Any]
    source: Optional[str]
    pushed_at: str
    updated_at: Optional[str]
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_models.py -v
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/models.py tests/test_models.py
git commit -m "feat: Pydantic models for push payload + metadata v1.0"
```

---

### Task 3.2: POST /api/v1/push endpoint

**Files:**
- Create: `codaily/api/__init__.py` (empty)
- Create: `codaily/api/push.py`
- Modify: `codaily/app.py` (mount push router)
- Create: `tests/test_push.py`

- [ ] **Step 1: Write failing test**

`tests/test_push.py`:
```python
import json
from codaily.auth.jira import JiraProfile


async def _get_pdl_token(client, db, jira, username="alice"):
    jira.add(username, "pw", JiraProfile(username=username, display_name=username, email=None, avatar_url=None))
    await db.execute("INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)", (username, "root"))
    await client.post("/api/v1/auth/login", json={"username": username, "password": "pw"})
    r = await client.post("/api/v1/sessions/pdl-token", json={"label": "test"})
    return r.json()["token"]


async def test_push_creates_post_201(app_and_db):
    app, db, jira, client = app_and_db
    token = await _get_pdl_token(client, db, jira)
    client.cookies.clear()  # publisher uses bearer, not cookie
    r = await client.post(
        "/api/v1/push",
        json={
            "post_date": "2026-04-19",
            "scope": "day",
            "content": "hello",
            "metadata": {"schema_version": "1.0", "issue_keys": ["X-1"]},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "created"
    assert isinstance(body["id"], int)

    row = await db.fetch_one("SELECT author, post_date, scope, content, metadata FROM posts WHERE id=?", (body["id"],))
    assert row["author"] == "alice"
    assert row["post_date"] == "2026-04-19"
    assert row["scope"] == "day"
    assert row["content"] == "hello"
    assert json.loads(row["metadata"])["schema_version"] == "1.0"


async def test_push_upsert_returns_200_and_updates(app_and_db):
    app, db, jira, client = app_and_db
    token = await _get_pdl_token(client, db, jira)
    client.cookies.clear()

    r1 = await client.post("/api/v1/push",
        json={"post_date": "2026-04-19", "scope": "day", "content": "v1"},
        headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 201

    r2 = await client.post("/api/v1/push",
        json={"post_date": "2026-04-19", "scope": "day", "content": "v2"},
        headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "updated"
    assert r2.json()["id"] == r1.json()["id"]

    row = await db.fetch_one("SELECT content, updated_at FROM posts WHERE id=?", (r1.json()["id"],))
    assert row["content"] == "v2"
    assert row["updated_at"] is not None


async def test_push_rejects_metadata_without_schema_version(app_and_db):
    app, db, jira, client = app_and_db
    token = await _get_pdl_token(client, db, jira)
    client.cookies.clear()
    r = await client.post("/api/v1/push",
        json={"post_date": "2026-04-19", "scope": "day", "content": "x",
              "metadata": {"issue_keys": ["X-1"]}},  # no schema_version
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400
    assert "schema_version" in r.json()["detail"]


async def test_push_accepts_unknown_schema_major_stores_raw(app_and_db):
    app, db, jira, client = app_and_db
    token = await _get_pdl_token(client, db, jira)
    client.cookies.clear()
    r = await client.post("/api/v1/push",
        json={"post_date": "2026-04-19", "scope": "day", "content": "x",
              "metadata": {"schema_version": "99.0", "anything": [1, 2, 3]}},
        headers={"Authorization": f"Bearer {token}"})
    # Forward-compat: store as-is, no error
    assert r.status_code == 201
    row = await db.fetch_one("SELECT metadata FROM posts WHERE id=?", (r.json()["id"],))
    md = json.loads(row["metadata"])
    assert md["schema_version"] == "99.0"
    assert md["anything"] == [1, 2, 3]


async def test_push_rejects_browser_session_with_403(app_and_db):
    app, db, jira, client = app_and_db
    # Login (gets browser cookie)
    await _get_pdl_token(client, db, jira)  # also logs in
    # Use the browser cookie (still set), not a pdl token
    r = await client.post("/api/v1/push",
        json={"post_date": "2026-04-19", "scope": "day", "content": "x"})
    assert r.status_code == 403
    assert r.json()["detail"] == "此接口仅限 publisher token"


async def test_push_rejects_invalid_date(app_and_db):
    app, db, jira, client = app_and_db
    token = await _get_pdl_token(client, db, jira)
    client.cookies.clear()
    r = await client.post("/api/v1/push",
        json={"post_date": "2026/04/19", "scope": "day", "content": "x"},
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422  # Pydantic validation
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_push.py -v
```

- [ ] **Step 3: Implement codaily/api/push.py**

```python
from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException, Request

from ..audit import log_action
from ..auth.deps import current_user
from ..models import PushBody, MetadataV1


router = APIRouter(prefix="/api/v1", tags=["push"])


@router.post("/push")
async def push(body: PushBody, request: Request, user: dict = Depends(current_user)):
    # Only pdl-publisher tokens may push; browser session blocked.
    if user.get("_session_kind") != "pdl-publisher":
        raise HTTPException(status_code=403, detail="此接口仅限 publisher token")

    # Metadata presence → must carry schema_version; unknown versions OK (stored raw).
    if body.metadata is not None:
        if "schema_version" not in body.metadata:
            raise HTTPException(status_code=400, detail="metadata present but schema_version missing")
        # For v1.x, do strict validation; for others, store as-is.
        ver = str(body.metadata["schema_version"])
        if ver.startswith("1."):
            try:
                MetadataV1.model_validate(body.metadata)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"metadata v1 validation: {e}")

    db = request.app.state.db
    # Upsert on (author, post_date, scope)
    existing = await db.fetch_one(
        "SELECT id FROM posts WHERE author = ? AND post_date = ? AND scope = ? "
        "AND deleted_at IS NULL",
        (user["jira_username"], body.post_date, body.scope),
    )
    md_json = json.dumps(body.metadata) if body.metadata else "{}"

    if existing:
        await db.execute(
            "UPDATE posts SET content=?, content_type=?, metadata=?, source=?, "
            "updated_at=datetime('now') WHERE id=?",
            (body.content, body.content_type, md_json, body.source, existing["id"]),
        )
        post_id = existing["id"]
        status_str = "updated"
        http_status = 200
    else:
        post_id = await db.execute(
            "INSERT INTO posts (author, post_date, scope, content, content_type, metadata, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user["jira_username"], body.post_date, body.scope,
             body.content, body.content_type, md_json, body.source),
        )
        status_str = "created"
        http_status = 201

    await log_action(
        db,
        actor=user["jira_username"],
        action="push",
        target=str(post_id),
        detail={"date": body.post_date, "scope": body.scope, "status": status_str},
    )
    from fastapi.responses import JSONResponse
    return JSONResponse({"id": post_id, "status": status_str}, status_code=http_status)
```

- [ ] **Step 4: Mount router in app.py**

```python
from .api.push import router as push_router
# inside create_app:
app.include_router(push_router)
```

- [ ] **Step 5: Run — expected PASS**

```bash
pytest tests/test_push.py -v
```

Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add codaily/api/__init__.py codaily/api/push.py codaily/app.py tests/test_push.py
git commit -m "feat: POST /api/v1/push with upsert + metadata v1 validation"
```

---

### Task 3.3: Posts list / get / delete / restore

**Files:**
- Create: `codaily/api/posts.py`
- Modify: `codaily/app.py` (mount posts router)
- Create: `tests/test_posts.py`

- [ ] **Step 1: Write failing test**

`tests/test_posts.py`:
```python
import json
from codaily.auth.jira import JiraProfile


async def _login(client, db, jira, u="alice"):
    jira.add(u, "pw", JiraProfile(username=u, display_name=u, email=None, avatar_url=None))
    await db.execute("INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)", (u, "root"))
    await client.post("/api/v1/auth/login", json={"username": u, "password": "pw"})


async def _insert_post(db, author="alice", date_="2026-04-19", scope="day"):
    return await db.execute(
        "INSERT INTO posts (author, post_date, scope, content, metadata) VALUES (?, ?, ?, ?, ?)",
        (author, date_, scope, "hello", "{}"),
    )


async def test_get_post_returns_200(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    pid = await _insert_post(db)
    r = await client.get(f"/api/v1/posts/{pid}")
    assert r.status_code == 200
    assert r.json()["id"] == pid
    assert r.json()["author"] == "alice"


async def test_get_nonexistent_returns_404(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    r = await client.get("/api/v1/posts/99999")
    assert r.status_code == 404


async def test_list_posts_filter_by_author(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    await _insert_post(db, author="alice", date_="2026-04-19")
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    await _insert_post(db, author="bob", date_="2026-04-19")
    r = await client.get("/api/v1/posts", params={"author": "bob"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["author"] == "bob"


async def test_author_delete_soft_deletes(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    pid = await _insert_post(db)
    r = await client.delete(f"/api/v1/posts/{pid}")
    assert r.status_code == 204
    row = await db.fetch_one("SELECT deleted_at FROM posts WHERE id=?", (pid,))
    assert row["deleted_at"] is not None
    # default list excludes soft-deleted
    r2 = await client.get("/api/v1/posts")
    assert all(p["id"] != pid for p in r2.json())


async def test_non_author_cannot_delete(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    pid = await _insert_post(db, author="bob")
    r = await client.delete(f"/api/v1/posts/{pid}")
    assert r.status_code == 403


async def test_restore_clears_deleted_at(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    pid = await _insert_post(db)
    await client.delete(f"/api/v1/posts/{pid}")
    r = await client.post(f"/api/v1/posts/{pid}/restore")
    assert r.status_code == 204
    row = await db.fetch_one("SELECT deleted_at FROM posts WHERE id=?", (pid,))
    assert row["deleted_at"] is None


async def test_list_with_include_deleted_shows_recycle_bin(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    pid = await _insert_post(db)
    await client.delete(f"/api/v1/posts/{pid}")
    r = await client.get("/api/v1/posts", params={"author": "alice", "include_deleted": "true"})
    ids = [p["id"] for p in r.json()]
    assert pid in ids
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_posts.py -v
```

- [ ] **Step 3: Implement codaily/api/posts.py**

```python
from __future__ import annotations
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..audit import log_action
from ..auth.deps import current_user


router = APIRouter(prefix="/api/v1/posts", tags=["posts"])


def _hydrate(row: dict) -> dict:
    return {
        "id": row["id"],
        "author": row["author"],
        "post_date": row["post_date"],
        "scope": row["scope"],
        "content": row["content"],
        "content_type": row["content_type"],
        "metadata": json.loads(row.get("metadata") or "{}"),
        "source": row.get("source"),
        "pushed_at": row["pushed_at"],
        "updated_at": row.get("updated_at"),
        "deleted_at": row.get("deleted_at"),
    }


@router.get("")
async def list_posts(
    request: Request,
    user: dict = Depends(current_user),
    author: Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    issue_key: Optional[str] = None,
    include_deleted: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
):
    db = request.app.state.db
    sql = "SELECT * FROM posts WHERE 1=1"
    params: list = []
    if author:
        sql += " AND author = ?"; params.append(author)
    if from_date:
        sql += " AND post_date >= ?"; params.append(from_date)
    if to_date:
        sql += " AND post_date <= ?"; params.append(to_date)
    if issue_key:
        sql += " AND metadata LIKE ?"; params.append(f"%{issue_key}%")
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    elif include_deleted and author != user["jira_username"]:
        # Only author can see their own recycle bin
        sql += " AND deleted_at IS NULL"
    sql += " ORDER BY post_date DESC, id DESC LIMIT ?"; params.append(limit)
    rows = await db.fetch_all(sql, tuple(params))
    return [_hydrate(r) for r in rows]


@router.get("/{post_id}")
async def get_post(post_id: int, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    row = await db.fetch_one("SELECT * FROM posts WHERE id = ?", (post_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="post not found")
    if row["deleted_at"] is not None and row["author"] != user["jira_username"]:
        raise HTTPException(status_code=404, detail="post not found")
    return _hydrate(row)


@router.delete("/{post_id}", status_code=204)
async def delete_post(post_id: int, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    row = await db.fetch_one("SELECT author FROM posts WHERE id = ?", (post_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="post not found")
    if row["author"] != user["jira_username"]:
        raise HTTPException(status_code=403, detail="only author can delete")
    await db.execute("UPDATE posts SET deleted_at = datetime('now') WHERE id = ?", (post_id,))
    await log_action(db, actor=user["jira_username"], action="delete-post", target=str(post_id))


@router.post("/{post_id}/restore", status_code=204)
async def restore_post(post_id: int, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    row = await db.fetch_one("SELECT author FROM posts WHERE id = ?", (post_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="post not found")
    if row["author"] != user["jira_username"]:
        raise HTTPException(status_code=403, detail="only author can restore")
    await db.execute("UPDATE posts SET deleted_at = NULL WHERE id = ?", (post_id,))
    await log_action(db, actor=user["jira_username"], action="restore-post", target=str(post_id))
```

- [ ] **Step 4: Mount in app.py**

```python
from .api.posts import router as posts_router
app.include_router(posts_router)
```

- [ ] **Step 5: Run — expected PASS**

```bash
pytest tests/test_posts.py -v
```

Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git add codaily/api/posts.py codaily/app.py tests/test_posts.py
git commit -m "feat: posts list/get/delete/restore (author soft-delete)"
```

---

### Task 3.4: Post hide / unhide (follower view)

**Files:**
- Modify: `codaily/api/posts.py` (add hide/unhide routes)
- Create: `tests/test_post_hides.py`

- [ ] **Step 1: Write failing test**

`tests/test_post_hides.py`:
```python
from codaily.auth.jira import JiraProfile


async def _login(client, db, jira, u="alice"):
    jira.add(u, "pw", JiraProfile(username=u, display_name=u, email=None, avatar_url=None))
    await db.execute("INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)", (u, "root"))
    await client.post("/api/v1/auth/login", json={"username": u, "password": "pw"})


async def test_hide_inserts_post_hide_row(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    pid = await db.execute("INSERT INTO posts (author, post_date, scope, content, metadata) VALUES (?,?,?,?,?)",
                           ("bob", "2026-04-19", "day", "x", "{}"))
    await _login(client, db, jira, "alice")

    r = await client.post(f"/api/v1/posts/{pid}/hide")
    assert r.status_code == 204

    row = await db.fetch_one("SELECT follower, post_id FROM post_hides WHERE follower='alice' AND post_id=?", (pid,))
    assert row["follower"] == "alice"
    assert row["post_id"] == pid


async def test_hide_is_idempotent(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    pid = await db.execute("INSERT INTO posts (author, post_date, scope, content, metadata) VALUES (?,?,?,?,?)",
                           ("bob", "2026-04-19", "day", "x", "{}"))
    await _login(client, db, jira, "alice")
    await client.post(f"/api/v1/posts/{pid}/hide")
    r = await client.post(f"/api/v1/posts/{pid}/hide")
    assert r.status_code == 204  # second call also 204
    rows = await db.fetch_all("SELECT * FROM post_hides WHERE follower='alice'")
    assert len(rows) == 1


async def test_unhide_removes_row(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    pid = await db.execute("INSERT INTO posts (author, post_date, scope, content, metadata) VALUES (?,?,?,?,?)",
                           ("bob", "2026-04-19", "day", "x", "{}"))
    await _login(client, db, jira, "alice")
    await client.post(f"/api/v1/posts/{pid}/hide")
    r = await client.post(f"/api/v1/posts/{pid}/unhide")
    assert r.status_code == 204
    rows = await db.fetch_all("SELECT * FROM post_hides WHERE follower='alice'")
    assert len(rows) == 0


async def test_author_cannot_hide_own_post(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    pid = await db.execute("INSERT INTO posts (author, post_date, scope, content, metadata) VALUES (?,?,?,?,?)",
                           ("alice", "2026-04-19", "day", "x", "{}"))
    r = await client.post(f"/api/v1/posts/{pid}/hide")
    assert r.status_code == 400
    assert r.json()["detail"] == "不能隐藏自己的日报，请使用删除"
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_post_hides.py -v
```

- [ ] **Step 3: Append hide/unhide to codaily/api/posts.py**

```python
@router.post("/{post_id}/hide", status_code=204)
async def hide_post(post_id: int, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    row = await db.fetch_one("SELECT author FROM posts WHERE id = ?", (post_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="post not found")
    if row["author"] == user["jira_username"]:
        raise HTTPException(status_code=400, detail="不能隐藏自己的日报，请使用删除")
    await db.execute(
        "INSERT OR IGNORE INTO post_hides (follower, post_id) VALUES (?, ?)",
        (user["jira_username"], post_id),
    )
    await log_action(db, actor=user["jira_username"], action="hide-post", target=str(post_id))


@router.post("/{post_id}/unhide", status_code=204)
async def unhide_post(post_id: int, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    await db.execute(
        "DELETE FROM post_hides WHERE follower = ? AND post_id = ?",
        (user["jira_username"], post_id),
    )
    await log_action(db, actor=user["jira_username"], action="unhide-post", target=str(post_id))
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_post_hides.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/api/posts.py tests/test_post_hides.py
git commit -m "feat: follower post hide/unhide (self-scoped view filter)"
```

---

## Phase 4: Follows + Users + Feed

### Task 4.1: Follows endpoints

**Files:**
- Create: `codaily/api/follows.py`
- Modify: `codaily/app.py` (mount follows router)
- Create: `tests/test_follow.py`

- [ ] **Step 1: Write failing test**

`tests/test_follow.py`:
```python
from codaily.auth.jira import JiraProfile


async def _login(client, db, jira, u="alice"):
    jira.add(u, "pw", JiraProfile(username=u, display_name=u, email=None, avatar_url=None))
    await db.execute("INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)", (u, "root"))
    await client.post("/api/v1/auth/login", json={"username": u, "password": "pw"})


async def test_follow_creates_row(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "B"))
    await _login(client, db, jira, "alice")
    r = await client.post("/api/v1/follows", json={"followee": "bob"})
    assert r.status_code == 201
    row = await db.fetch_one("SELECT follower, followee FROM follows WHERE follower='alice' AND followee='bob'")
    assert row["follower"] == "alice"
    assert row["followee"] == "bob"


async def test_follow_is_idempotent(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "B"))
    await _login(client, db, jira, "alice")
    r1 = await client.post("/api/v1/follows", json={"followee": "bob"})
    r2 = await client.post("/api/v1/follows", json={"followee": "bob"})
    assert r1.status_code == 201
    assert r2.status_code == 200
    rows = await db.fetch_all("SELECT * FROM follows WHERE follower='alice' AND followee='bob'")
    assert len(rows) == 1


async def test_cannot_self_follow(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    r = await client.post("/api/v1/follows", json={"followee": "alice"})
    assert r.status_code == 400
    assert r.json()["detail"] == "不能关注自己"


async def test_follow_nonexistent_user_404(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    r = await client.post("/api/v1/follows", json={"followee": "ghost"})
    assert r.status_code == 404


async def test_unfollow_removes_row(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "B"))
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})
    r = await client.delete("/api/v1/follows/bob")
    assert r.status_code == 204
    rows = await db.fetch_all("SELECT * FROM follows WHERE follower='alice' AND followee='bob'")
    assert len(rows) == 0


async def test_following_list(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("carol", "Carol"))
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})
    await client.post("/api/v1/follows", json={"followee": "carol"})

    r = await client.get("/api/v1/follows/following")
    assert r.status_code == 200
    names = sorted([u["jira_username"] for u in r.json()])
    assert names == ["bob", "carol"]


async def test_followers_list_visible_to_followee(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    # alice follows bob
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})
    # bob logs in (replaces cookie)
    client.cookies.clear()
    await _login(client, db, jira, "bob")

    r = await client.get("/api/v1/follows/followers")
    names = [u["jira_username"] for u in r.json()]
    assert names == ["alice"]
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_follow.py -v
```

- [ ] **Step 3: Implement codaily/api/follows.py**

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..audit import log_action
from ..auth.deps import current_user


router = APIRouter(prefix="/api/v1/follows", tags=["follows"])


class FollowBody(BaseModel):
    followee: str


@router.post("", status_code=201)
async def follow(body: FollowBody, request: Request, user: dict = Depends(current_user)):
    if body.followee == user["jira_username"]:
        raise HTTPException(status_code=400, detail="不能关注自己")
    db = request.app.state.db
    fe = await db.fetch_one("SELECT jira_username FROM users WHERE jira_username = ?", (body.followee,))
    if fe is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    existing = await db.fetch_one(
        "SELECT 1 FROM follows WHERE follower = ? AND followee = ?",
        (user["jira_username"], body.followee),
    )
    if existing:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "already-following"}, status_code=200)
    await db.execute(
        "INSERT INTO follows (follower, followee) VALUES (?, ?)",
        (user["jira_username"], body.followee),
    )
    await log_action(db, actor=user["jira_username"], action="follow", target=body.followee)
    return {"status": "followed"}


@router.delete("/{followee}", status_code=204)
async def unfollow(followee: str, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    await db.execute(
        "DELETE FROM follows WHERE follower = ? AND followee = ?",
        (user["jira_username"], followee),
    )
    await log_action(db, actor=user["jira_username"], action="unfollow", target=followee)


@router.get("/following")
async def following(request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT u.jira_username, u.display_name, u.avatar_url, f.followed_at "
        "FROM follows f JOIN users u ON u.jira_username = f.followee "
        "WHERE f.follower = ? ORDER BY u.jira_username",
        (user["jira_username"],),
    )
    return rows


@router.get("/followers")
async def followers(request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT u.jira_username, u.display_name, u.avatar_url, f.followed_at "
        "FROM follows f JOIN users u ON u.jira_username = f.follower "
        "WHERE f.followee = ? ORDER BY u.jira_username",
        (user["jira_username"],),
    )
    return rows
```

- [ ] **Step 4: Mount in app.py**

```python
from .api.follows import router as follows_router
app.include_router(follows_router)
```

- [ ] **Step 5: Run — expected PASS**

```bash
pytest tests/test_follow.py -v
```

Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git add codaily/api/follows.py codaily/app.py tests/test_follow.py
git commit -m "feat: unilateral follow/unfollow + following/followers lists"
```

---

### Task 4.2: Users directory + profile

**Files:**
- Create: `codaily/api/users.py`
- Modify: `codaily/app.py`
- Create: `tests/test_users_routes.py`

- [ ] **Step 1: Write failing test**

`tests/test_users_routes.py`:
```python
from codaily.auth.jira import JiraProfile


async def _login(client, db, jira, u="alice"):
    jira.add(u, "pw", JiraProfile(username=u, display_name=u, email=None, avatar_url=None))
    await db.execute("INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)", (u, "root"))
    await client.post("/api/v1/auth/login", json={"username": u, "password": "pw"})


async def test_users_directory_lists_all_with_follow_state(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("carol", "Carol"))
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})

    r = await client.get("/api/v1/users")
    assert r.status_code == 200
    by_name = {u["jira_username"]: u for u in r.json()}
    assert by_name["bob"]["is_followed_by_me"] is True
    assert by_name["carol"]["is_followed_by_me"] is False
    # should not include self
    assert "alice" not in by_name


async def test_user_profile_returns_user_with_counts(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})

    r = await client.get("/api/v1/users/bob")
    assert r.status_code == 200
    body = r.json()
    assert body["jira_username"] == "bob"
    assert body["display_name"] == "Bob"
    assert body["follower_count"] == 1
    assert body["following_count"] == 0
    assert body["is_followed_by_me"] is True


async def test_user_profile_404_for_unknown(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    r = await client.get("/api/v1/users/ghost")
    assert r.status_code == 404
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_users_routes.py -v
```

- [ ] **Step 3: Implement codaily/api/users.py**

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.deps import current_user


router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("")
async def directory(request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    me = user["jira_username"]
    rows = await db.fetch_all(
        "SELECT u.jira_username, u.display_name, u.avatar_url, "
        "CASE WHEN f.followee IS NULL THEN 0 ELSE 1 END AS followed "
        "FROM users u "
        "LEFT JOIN follows f ON f.followee = u.jira_username AND f.follower = ? "
        "WHERE u.jira_username != ? "
        "ORDER BY u.jira_username",
        (me, me),
    )
    return [
        {
            "jira_username": r["jira_username"],
            "display_name": r["display_name"],
            "avatar_url": r["avatar_url"],
            "is_followed_by_me": bool(r["followed"]),
        }
        for r in rows
    ]


@router.get("/{username}")
async def profile(username: str, request: Request, user: dict = Depends(current_user)):
    db = request.app.state.db
    u = await db.fetch_one("SELECT * FROM users WHERE jira_username = ?", (username,))
    if u is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    followers_count = (await db.fetch_one("SELECT COUNT(*) AS n FROM follows WHERE followee = ?", (username,)))["n"]
    following_count = (await db.fetch_one("SELECT COUNT(*) AS n FROM follows WHERE follower = ?", (username,)))["n"]
    im_following = (await db.fetch_one(
        "SELECT 1 FROM follows WHERE follower = ? AND followee = ?",
        (user["jira_username"], username),
    )) is not None
    return {
        "jira_username": u["jira_username"],
        "display_name": u["display_name"],
        "email": u["email"],
        "avatar_url": u["avatar_url"],
        "is_admin": bool(u["is_admin"]),
        "follower_count": followers_count,
        "following_count": following_count,
        "is_followed_by_me": im_following,
    }
```

- [ ] **Step 4: Mount in app.py**

```python
from .api.users import router as users_router
app.include_router(users_router)
```

- [ ] **Step 5: Run — expected PASS**

```bash
pytest tests/test_users_routes.py -v
```

Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add codaily/api/users.py codaily/app.py tests/test_users_routes.py
git commit -m "feat: user directory + profile with follower/following counts"
```

---

### Task 4.3: Feed with pagination + hide filter

**Files:**
- Create: `codaily/api/feed.py`
- Modify: `codaily/app.py`
- Create: `tests/test_feed.py`

- [ ] **Step 1: Write failing test**

`tests/test_feed.py`:
```python
from codaily.auth.jira import JiraProfile


async def _login(client, db, jira, u="alice"):
    jira.add(u, "pw", JiraProfile(username=u, display_name=u, email=None, avatar_url=None))
    await db.execute("INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)", (u, "root"))
    await client.post("/api/v1/auth/login", json={"username": u, "password": "pw"})


async def _seed_users_and_posts(db):
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "Bob"))
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("carol", "Carol"))
    for i, d in enumerate(["2026-04-15", "2026-04-16", "2026-04-17"]):
        await db.execute(
            "INSERT INTO posts (author, post_date, scope, content, metadata) VALUES (?,?,?,?,?)",
            ("bob", d, "day", f"bob-{i}", "{}"),
        )
    await db.execute(
        "INSERT INTO posts (author, post_date, scope, content, metadata) VALUES (?,?,?,?,?)",
        ("carol", "2026-04-16", "day", "carol-1", "{}"),
    )


async def test_feed_only_returns_followings_posts(app_and_db):
    app, db, jira, client = app_and_db
    await _seed_users_and_posts(db)
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})
    # alice does NOT follow carol
    r = await client.get("/api/v1/feed")
    assert r.status_code == 200
    items = r.json()["items"]
    authors = {p["author"] for p in items}
    assert authors == {"bob"}
    assert len(items) == 3


async def test_feed_excludes_hidden_posts(app_and_db):
    app, db, jira, client = app_and_db
    await _seed_users_and_posts(db)
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})
    # hide the middle bob post
    bobs = await db.fetch_all("SELECT id, post_date FROM posts WHERE author='bob' ORDER BY post_date")
    mid = bobs[1]["id"]
    await client.post(f"/api/v1/posts/{mid}/hide")

    r = await client.get("/api/v1/feed")
    ids = {p["id"] for p in r.json()["items"]}
    assert mid not in ids
    assert len(ids) == 2


async def test_feed_excludes_soft_deleted(app_and_db):
    app, db, jira, client = app_and_db
    await _seed_users_and_posts(db)
    await db.execute("UPDATE posts SET deleted_at=datetime('now') WHERE author='bob' AND post_date='2026-04-15'")
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})
    r = await client.get("/api/v1/feed")
    dates = {p["post_date"] for p in r.json()["items"]}
    assert "2026-04-15" not in dates


async def test_feed_pagination_cursor(app_and_db):
    app, db, jira, client = app_and_db
    await db.execute("INSERT INTO users (jira_username, display_name) VALUES (?, ?)", ("bob", "B"))
    for i in range(5):
        await db.execute(
            "INSERT INTO posts (author, post_date, scope, content, metadata) VALUES (?,?,?,?,?)",
            ("bob", f"2026-04-{10+i:02d}", "day", f"p{i}", "{}"),
        )
    await _login(client, db, jira, "alice")
    await client.post("/api/v1/follows", json={"followee": "bob"})

    r1 = await client.get("/api/v1/feed", params={"limit": 2})
    b1 = r1.json()
    assert len(b1["items"]) == 2
    assert b1["items"][0]["post_date"] == "2026-04-14"
    assert b1["next_cursor"] is not None

    r2 = await client.get("/api/v1/feed", params={"limit": 2, "cursor": b1["next_cursor"]})
    b2 = r2.json()
    assert len(b2["items"]) == 2
    assert b2["items"][0]["post_date"] == "2026-04-12"
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_feed.py -v
```

- [ ] **Step 3: Implement codaily/api/feed.py**

```python
from __future__ import annotations
import base64
import json
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request

from ..auth.deps import current_user


router = APIRouter(prefix="/api/v1/feed", tags=["feed"])


def _encode_cursor(post_date: str, post_id: int) -> str:
    raw = f"{post_date}|{post_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(c: str) -> tuple[str, int]:
    padded = c + "=" * (-len(c) % 4)
    raw = base64.urlsafe_b64decode(padded.encode()).decode()
    date_part, id_part = raw.split("|")
    return date_part, int(id_part)


@router.get("")
async def feed(
    request: Request,
    user: dict = Depends(current_user),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: Optional[str] = None,
):
    db = request.app.state.db
    me = user["jira_username"]

    sql = (
        "SELECT p.* FROM posts p "
        "JOIN follows f ON f.followee = p.author AND f.follower = ? "
        "WHERE p.deleted_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM post_hides h WHERE h.follower = ? AND h.post_id = p.id) "
    )
    params: list = [me, me]

    if cursor:
        cdate, cid = _decode_cursor(cursor)
        sql += "AND (p.post_date, p.id) < (?, ?) "
        params.extend([cdate, cid])

    sql += "ORDER BY p.post_date DESC, p.id DESC LIMIT ?"
    params.append(limit + 1)  # fetch one extra to know if more pages exist

    rows = await db.fetch_all(sql, tuple(params))
    has_more = len(rows) > limit
    items_rows = rows[:limit]

    items = [
        {
            "id": r["id"],
            "author": r["author"],
            "post_date": r["post_date"],
            "scope": r["scope"],
            "content": r["content"],
            "content_type": r["content_type"],
            "metadata": json.loads(r.get("metadata") or "{}"),
            "pushed_at": r["pushed_at"],
            "updated_at": r.get("updated_at"),
        }
        for r in items_rows
    ]

    next_cursor = None
    if has_more and items_rows:
        last = items_rows[-1]
        next_cursor = _encode_cursor(last["post_date"], last["id"])

    return {"items": items, "next_cursor": next_cursor}
```

- [ ] **Step 4: Mount in app.py**

```python
from .api.feed import router as feed_router
app.include_router(feed_router)
```

- [ ] **Step 5: Run — expected PASS**

```bash
pytest tests/test_feed.py -v
```

Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
git add codaily/api/feed.py codaily/app.py tests/test_feed.py
git commit -m "feat: /feed with cursor pagination + hide + soft-delete filters"
```

---

## Phase 5: Admin

### Task 5.1: Invites CRUD

**Files:**
- Create: `codaily/api/admin.py`
- Modify: `codaily/app.py`
- Create: `tests/test_admin.py`

- [ ] **Step 1: Write failing test**

`tests/test_admin.py`:
```python
from codaily.auth.jira import JiraProfile


async def _login(client, db, jira, u, is_admin=False):
    jira.add(u, "pw", JiraProfile(username=u, display_name=u, email=None, avatar_url=None))
    await db.execute("INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)", (u, "root"))
    if is_admin:
        await db.execute("INSERT OR IGNORE INTO users (jira_username, display_name, is_admin) VALUES (?,?,1)", (u, u))
    await client.post("/api/v1/auth/login", json={"username": u, "password": "pw"})


async def test_admin_can_list_invites(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "root", is_admin=True)
    r = await client.get("/api/v1/admin/invites")
    assert r.status_code == 200
    names = [i["jira_username"] for i in r.json()]
    assert "root" in names  # bootstrap admin is self-invited


async def test_nonadmin_cannot_list_invites(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")  # not admin
    r = await client.get("/api/v1/admin/invites")
    assert r.status_code == 403


async def test_admin_can_add_invite(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "root", is_admin=True)
    r = await client.post("/api/v1/admin/invites", json={"jira_username": "bob", "note": "team"})
    assert r.status_code == 201
    row = await db.fetch_one("SELECT jira_username, invited_by, note FROM invites WHERE jira_username='bob'")
    assert row["jira_username"] == "bob"
    assert row["invited_by"] == "root"
    assert row["note"] == "team"


async def test_cannot_add_duplicate_invite(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "root", is_admin=True)
    await client.post("/api/v1/admin/invites", json={"jira_username": "bob"})
    r = await client.post("/api/v1/admin/invites", json={"jira_username": "bob"})
    assert r.status_code == 409


async def test_admin_can_delete_unconsumed_invite(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "root", is_admin=True)
    await client.post("/api/v1/admin/invites", json={"jira_username": "bob"})
    r = await client.delete("/api/v1/admin/invites/bob")
    assert r.status_code == 204
    row = await db.fetch_one("SELECT 1 FROM invites WHERE jira_username='bob'")
    assert row is None


async def test_cannot_delete_consumed_invite(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "root", is_admin=True)
    await client.post("/api/v1/admin/invites", json={"jira_username": "bob"})
    await db.execute("UPDATE invites SET consumed_at=datetime('now') WHERE jira_username='bob'")
    r = await client.delete("/api/v1/admin/invites/bob")
    assert r.status_code == 409
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_admin.py -v
```

- [ ] **Step 3: Implement codaily/api/admin.py**

```python
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..audit import log_action
from ..auth.deps import require_admin


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class InviteBody(BaseModel):
    jira_username: str
    note: Optional[str] = None


@router.get("/invites")
async def list_invites(request: Request, user: dict = Depends(require_admin)):
    db = request.app.state.db
    return await db.fetch_all(
        "SELECT jira_username, invited_by, invited_at, consumed_at, note FROM invites "
        "ORDER BY invited_at DESC"
    )


@router.post("/invites", status_code=201)
async def add_invite(body: InviteBody, request: Request, user: dict = Depends(require_admin)):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT 1 FROM invites WHERE jira_username=?", (body.jira_username,))
    if existing:
        raise HTTPException(status_code=409, detail="该 Jira 账号已在邀请列表中")
    await db.execute(
        "INSERT INTO invites (jira_username, invited_by, note) VALUES (?, ?, ?)",
        (body.jira_username, user["jira_username"], body.note),
    )
    await log_action(db, actor=user["jira_username"], action="invite", target=body.jira_username,
                     detail={"note": body.note})
    return {"jira_username": body.jira_username, "status": "invited"}


@router.delete("/invites/{username}", status_code=204)
async def delete_invite(username: str, request: Request, user: dict = Depends(require_admin)):
    db = request.app.state.db
    row = await db.fetch_one("SELECT consumed_at FROM invites WHERE jira_username=?", (username,))
    if row is None:
        raise HTTPException(status_code=404, detail="邀请不存在")
    if row["consumed_at"] is not None:
        raise HTTPException(status_code=409, detail="邀请已被使用，无法删除")
    await db.execute("DELETE FROM invites WHERE jira_username=?", (username,))
    await log_action(db, actor=user["jira_username"], action="revoke-invite", target=username)
```

- [ ] **Step 4: Mount in app.py**

```python
from .api.admin import router as admin_router
app.include_router(admin_router)
```

- [ ] **Step 5: Run — expected PASS**

```bash
pytest tests/test_admin.py -v
```

Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add codaily/api/admin.py codaily/app.py tests/test_admin.py
git commit -m "feat: admin invites CRUD with consumed-invite protection"
```

---

### Task 5.2: Admin audit query

**Files:**
- Modify: `codaily/api/admin.py` (add audit query)
- Create: `tests/test_audit_query.py`

- [ ] **Step 1: Write failing test**

`tests/test_audit_query.py`:
```python
from codaily.auth.jira import JiraProfile
from codaily.audit import log_action


async def _login(client, db, jira, u, is_admin=False):
    jira.add(u, "pw", JiraProfile(username=u, display_name=u, email=None, avatar_url=None))
    await db.execute("INSERT OR IGNORE INTO invites (jira_username, invited_by) VALUES (?, ?)", (u, "root"))
    if is_admin:
        await db.execute("INSERT OR IGNORE INTO users (jira_username, display_name, is_admin) VALUES (?,?,1)", (u, u))
    await client.post("/api/v1/auth/login", json={"username": u, "password": "pw"})


async def test_audit_query_filters_by_actor(app_and_db):
    app, db, jira, client = app_and_db
    await log_action(db, actor="alice", action="push", target="1")
    await log_action(db, actor="bob", action="push", target="2")
    await _login(client, db, jira, "root", is_admin=True)

    r = await client.get("/api/v1/admin/audit", params={"actor": "alice"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["actor"] == "alice"


async def test_audit_query_filters_by_action(app_and_db):
    app, db, jira, client = app_and_db
    await log_action(db, actor="alice", action="login")
    await log_action(db, actor="alice", action="push", target="1")
    await _login(client, db, jira, "root", is_admin=True)

    r = await client.get("/api/v1/admin/audit", params={"action": "push"})
    actions = [r["action"] for r in r.json()]
    assert actions == ["push"]


async def test_audit_query_limit_default_and_cap(app_and_db):
    app, db, jira, client = app_and_db
    for _ in range(5):
        await log_action(db, actor="alice", action="x")
    await _login(client, db, jira, "root", is_admin=True)

    r = await client.get("/api/v1/admin/audit", params={"limit": 2})
    assert len(r.json()) == 2


async def test_audit_query_non_admin_403(app_and_db):
    app, db, jira, client = app_and_db
    await _login(client, db, jira, "alice")
    r = await client.get("/api/v1/admin/audit")
    assert r.status_code == 403
```

- [ ] **Step 2: Run — expected FAIL**

```bash
pytest tests/test_audit_query.py -v
```

- [ ] **Step 3: Append audit endpoint to codaily/api/admin.py**

```python
@router.get("/audit")
async def audit(
    request: Request,
    user: dict = Depends(require_admin),
    actor: Optional[str] = None,
    action: Optional[str] = None,
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=500),
):
    db = request.app.state.db
    sql = "SELECT id, actor, action, target, detail, ip, user_agent, created_at FROM audit_log WHERE 1=1"
    params: list = []
    if actor:
        sql += " AND actor = ?"; params.append(actor)
    if action:
        sql += " AND action = ?"; params.append(action)
    if from_ts:
        sql += " AND created_at >= ?"; params.append(from_ts)
    if to_ts:
        sql += " AND created_at <= ?"; params.append(to_ts)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"; params.append(limit)
    return await db.fetch_all(sql, tuple(params))
```

- [ ] **Step 4: Run — expected PASS**

```bash
pytest tests/test_audit_query.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add codaily/api/admin.py tests/test_audit_query.py
git commit -m "feat: admin audit log query with filters"
```

---

## Phase 6: Frontend Core

**Testing note:** Frontend tasks verify via `npm run build` success + dev-server manual render. End-to-end user flow is covered by Task 8.3. This mirrors PDL's frontend pattern (no Vue component unit tests).

### Task 6.1: Router, API client, auth store

**Files:**
- Modify: `web/frontend/src/router.js`
- Create: `web/frontend/src/api.js`
- Create: `web/frontend/src/stores/auth.js`

- [ ] **Step 1: Write src/api.js**

```js
import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    // 401 → redirect to /login from any route guard; do not swallow
    return Promise.reject(err)
  }
)

export default {
  // auth
  login: (username, password) => api.post('/auth/login', { username, password }),
  logout: () => api.post('/auth/logout'),
  me: () => api.get('/auth/me'),

  // sessions
  listSessions: () => api.get('/sessions'),
  createPdlToken: (label) => api.post('/sessions/pdl-token', { label }),
  revokeSession: (id) => api.delete(`/sessions/${id}`),

  // posts
  listPosts: (params = {}) => api.get('/posts', { params }),
  getPost: (id) => api.get(`/posts/${id}`),
  deletePost: (id) => api.delete(`/posts/${id}`),
  restorePost: (id) => api.post(`/posts/${id}/restore`),
  hidePost: (id) => api.post(`/posts/${id}/hide`),
  unhidePost: (id) => api.post(`/posts/${id}/unhide`),

  // follow
  follow: (followee) => api.post('/follows', { followee }),
  unfollow: (followee) => api.delete(`/follows/${followee}`),
  listFollowing: () => api.get('/follows/following'),
  listFollowers: () => api.get('/follows/followers'),

  // users
  listUsers: () => api.get('/users'),
  getUser: (username) => api.get(`/users/${username}`),

  // feed
  getFeed: (params = {}) => api.get('/feed', { params }),

  // admin
  listInvites: () => api.get('/admin/invites'),
  addInvite: (jira_username, note) => api.post('/admin/invites', { jira_username, note }),
  deleteInvite: (username) => api.delete(`/admin/invites/${username}`),
  getAudit: (params = {}) => api.get('/admin/audit', { params }),
}
```

- [ ] **Step 2: Write src/stores/auth.js (Pinia)**

```js
import { defineStore } from 'pinia'
import api from '../api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,          // { jira_username, display_name, is_admin, ... } or null
    ready: false,        // true after initial /me check finishes
  }),
  getters: {
    isAuthed: (s) => s.user !== null,
    isAdmin: (s) => !!s.user?.is_admin,
  },
  actions: {
    async fetchMe() {
      try {
        const r = await api.me()
        this.user = r.data
      } catch {
        this.user = null
      } finally {
        this.ready = true
      }
    },
    async login(username, password) {
      const r = await api.login(username, password)
      this.user = r.data.user
    },
    async logout() {
      await api.logout().catch(() => {})
      this.user = null
    },
  },
})
```

- [ ] **Step 3: Replace src/router.js**

```js
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from './stores/auth'

import Login from './views/Login.vue'
import Feed from './views/Feed.vue'
import UserProfile from './views/UserProfile.vue'
import Settings from './views/Settings.vue'
import AdminPanel from './views/AdminPanel.vue'

const routes = [
  { path: '/login', name: 'login', component: Login, meta: { public: true } },
  { path: '/', name: 'feed', component: Feed },
  { path: '/u/:username', name: 'profile', component: UserProfile, props: true },
  { path: '/settings', name: 'settings', component: Settings },
  { path: '/settings/admin', name: 'admin', component: AdminPanel, meta: { adminOnly: true } },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(async (to) => {
  const store = useAuthStore()
  if (!store.ready) await store.fetchMe()
  if (to.meta.public) return true
  if (!store.isAuthed) return { name: 'login', query: { next: to.fullPath } }
  if (to.meta.adminOnly && !store.isAdmin) return { name: 'feed' }
  return true
})

export default router
```

- [ ] **Step 4: Create placeholder view files so build succeeds**

Create each of `web/frontend/src/views/Login.vue`, `Feed.vue`, `UserProfile.vue`, `Settings.vue`, `AdminPanel.vue` with identical stub (will be replaced in next tasks):

```vue
<template>
  <div>(stub — wired in later task)</div>
</template>
```

- [ ] **Step 5: Smoke build**

```bash
cd web/frontend
npm run build
```

Expected: `dist/` rebuilt without errors.

- [ ] **Step 6: Commit**

```bash
cd ../..
git add web/frontend/
git commit -m "feat: frontend router + axios client + Pinia auth store"
```

---

### Task 6.2: Login page

**Files:**
- Replace: `web/frontend/src/views/Login.vue`

- [ ] **Step 1: Write Login.vue**

```vue
<template>
  <el-card class="login-card" shadow="hover">
    <h2 style="text-align: center; margin-top: 0">CoDaily — 日报广场</h2>
    <p style="text-align: center; color: #888; margin-bottom: 24px">
      使用 Jira 账号登录（需管理员邀请）
    </p>
    <el-form :model="form" @submit.prevent="submit" label-width="80px">
      <el-form-item label="用户名">
        <el-input v-model="form.username" autofocus />
      </el-form-item>
      <el-form-item label="密码">
        <el-input v-model="form.password" type="password" show-password />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" style="width: 100%">
          登录
        </el-button>
      </el-form-item>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const form = ref({ username: '', password: '' })
const loading = ref(false)
const error = ref('')
const router = useRouter()
const route = useRoute()
const store = useAuthStore()

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await store.login(form.value.username, form.value.password)
    const next = route.query.next || '/'
    router.replace(next)
  } catch (e) {
    const detail = e.response?.data?.detail
    error.value = detail || '登录失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-card {
  max-width: 420px;
  margin: 80px auto;
}
</style>
```

- [ ] **Step 2: Smoke build + manual check**

```bash
cd web/frontend && npm run build
```

Start dev server separately:
```bash
# terminal 1: backend
CODAILY_DB=/tmp/x.db CODAILY_JIRA_BASE=https://jira.fanruan.com CODAILY_ADMIN=root uvicorn codaily.app:create_app --factory --port 8000
# terminal 2: frontend dev
cd web/frontend && npm run dev
```

Visit `http://localhost:5173/login` — confirm login card renders.

- [ ] **Step 3: Commit**

```bash
cd ../..
git add web/frontend/src/views/Login.vue
git commit -m "feat: Login page"
```

---

### Task 6.3: Feed page

**Files:**
- Replace: `web/frontend/src/views/Feed.vue`
- Create: `web/frontend/src/components/PostCard.vue`

- [ ] **Step 1: Write PostCard.vue**

```vue
<template>
  <el-card class="post-card" shadow="never">
    <template #header>
      <div class="post-header">
        <router-link :to="{ name: 'profile', params: { username: post.author } }" class="author">
          @{{ post.author }}
        </router-link>
        <span class="meta">
          <span class="post-date">{{ post.post_date }}</span>
          <span class="scope-chip">{{ post.scope }}</span>
          <span class="pushed-at">推送于 {{ post.pushed_at }}</span>
        </span>
      </div>
    </template>
    <div class="content" v-html="rendered" />
    <div v-if="issueKeys.length" class="issue-keys">
      <el-tag v-for="k in issueKeys" :key="k" size="small" style="margin-right: 6px">{{ k }}</el-tag>
    </div>
    <div class="actions">
      <el-button v-if="!isMine" link type="info" size="small" @click="$emit('hide', post.id)">
        从我的 feed 隐藏
      </el-button>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { useAuthStore } from '../stores/auth'

const props = defineProps({ post: { type: Object, required: true } })
defineEmits(['hide'])

const store = useAuthStore()
const isMine = computed(() => props.post.author === store.user?.jira_username)

// Minimal inline markdown-to-HTML: paragraphs + line breaks.
// For MVP we display content as-is; a real markdown renderer can come in v2.
const rendered = computed(() => {
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return '<pre class="post-body">' + esc(props.post.content) + '</pre>'
})

const issueKeys = computed(() => props.post.metadata?.issue_keys || [])
</script>

<style scoped>
.post-card { margin-bottom: 12px; }
.post-header { display: flex; justify-content: space-between; align-items: center; }
.author { font-weight: 600; color: #1f8ef1; text-decoration: none; }
.meta { display: flex; gap: 10px; color: #888; font-size: 12px; }
.scope-chip {
  background: #f0f0f0; padding: 2px 8px; border-radius: 10px;
}
.content :deep(pre) {
  white-space: pre-wrap; word-break: break-word;
  font-family: inherit; font-size: 14px; margin: 0;
}
.issue-keys { margin-top: 8px; }
.actions { margin-top: 8px; text-align: right; }
</style>
```

- [ ] **Step 2: Write Feed.vue**

```vue
<template>
  <el-container>
    <el-aside width="220px" class="sidebar">
      <h4>我关注的 ({{ following.length }})</h4>
      <el-empty v-if="!following.length" description="还没关注任何人" :image-size="60" />
      <ul class="follow-list">
        <li v-for="u in following" :key="u.jira_username">
          <router-link :to="{ name: 'profile', params: { username: u.jira_username } }">
            @{{ u.jira_username }}
          </router-link>
        </li>
      </ul>
      <el-divider />
      <el-button @click="$router.push('/u/_discover')" size="small">发现用户</el-button>
    </el-aside>

    <el-main>
      <h3>Feed</h3>
      <el-empty v-if="!loading && !items.length" description="还没有日报（先去关注几个人吧）" />
      <post-card v-for="p in items" :key="p.id" :post="p" @hide="onHide" />
      <el-button v-if="nextCursor" @click="loadMore" :loading="loading" plain style="width: 100%; margin-top: 8px">
        加载更早的
      </el-button>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import PostCard from '../components/PostCard.vue'

const items = ref([])
const nextCursor = ref(null)
const following = ref([])
const loading = ref(false)

async function load(cursor = null) {
  loading.value = true
  try {
    const r = await api.getFeed({ limit: 30, cursor: cursor || undefined })
    if (cursor) items.value.push(...r.data.items)
    else items.value = r.data.items
    nextCursor.value = r.data.next_cursor
  } finally {
    loading.value = false
  }
}

async function loadMore() { await load(nextCursor.value) }

async function onHide(id) {
  await api.hidePost(id)
  items.value = items.value.filter(p => p.id !== id)
  ElMessage.success('已从你的 feed 隐藏')
}

onMounted(async () => {
  await load()
  following.value = (await api.listFollowing()).data
})
</script>

<style scoped>
.sidebar { padding: 12px; border-right: 1px solid #eee; }
.follow-list { list-style: none; padding-left: 0; }
.follow-list li { padding: 4px 0; }
.follow-list a { color: #1f8ef1; text-decoration: none; }
</style>
```

- [ ] **Step 3: Smoke build**

```bash
cd web/frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
cd ../.. && git add web/frontend/src/views/Feed.vue web/frontend/src/components/PostCard.vue
git commit -m "feat: Feed page with PostCard + sidebar + pagination"
```

---

### Task 6.4: User profile page

**Files:**
- Replace: `web/frontend/src/views/UserProfile.vue`

- [ ] **Step 1: Write UserProfile.vue**

```vue
<template>
  <div v-if="username === '_discover'">
    <h3>发现用户</h3>
    <el-table :data="users" style="width: 100%">
      <el-table-column label="用户">
        <template #default="{ row }">
          <router-link :to="{ name: 'profile', params: { username: row.jira_username } }">
            @{{ row.jira_username }}
          </router-link>
        </template>
      </el-table-column>
      <el-table-column prop="display_name" label="显示名" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button v-if="row.is_followed_by_me" size="small" @click="toggleFollow(row, false)">取关</el-button>
          <el-button v-else size="small" type="primary" @click="toggleFollow(row, true)">关注</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>

  <div v-else-if="profile" class="profile">
    <div class="profile-header">
      <el-avatar :size="64" :src="profile.avatar_url">{{ profile.display_name[0] }}</el-avatar>
      <div class="info">
        <h2>{{ profile.display_name }}</h2>
        <div class="username">@{{ profile.jira_username }}</div>
        <div class="counts">
          关注者 {{ profile.follower_count }} · 正在关注 {{ profile.following_count }}
        </div>
      </div>
      <el-button v-if="!isSelf && profile.is_followed_by_me" @click="toggle(false)">取关</el-button>
      <el-button v-else-if="!isSelf" type="primary" @click="toggle(true)">关注</el-button>
    </div>
    <el-divider />
    <h4>最近日报</h4>
    <post-card v-for="p in posts" :key="p.id" :post="p" />
  </div>
</template>

<script setup>
import { ref, watch, onMounted, computed } from 'vue'
import api from '../api'
import PostCard from '../components/PostCard.vue'
import { useAuthStore } from '../stores/auth'

const props = defineProps({ username: { type: String, required: true } })

const profile = ref(null)
const posts = ref([])
const users = ref([])
const store = useAuthStore()

const isSelf = computed(() => profile.value?.jira_username === store.user?.jira_username)

async function loadUser() {
  if (props.username === '_discover') {
    users.value = (await api.listUsers()).data
    profile.value = null
    return
  }
  profile.value = (await api.getUser(props.username)).data
  posts.value = (await api.listPosts({ author: props.username, limit: 30 })).data
}

async function toggle(follow) {
  if (follow) await api.follow(profile.value.jira_username)
  else await api.unfollow(profile.value.jira_username)
  await loadUser()
}

async function toggleFollow(row, follow) {
  if (follow) await api.follow(row.jira_username)
  else await api.unfollow(row.jira_username)
  users.value = (await api.listUsers()).data
}

watch(() => props.username, loadUser)
onMounted(loadUser)
</script>

<style scoped>
.profile { max-width: 780px; margin: 0 auto; }
.profile-header { display: flex; align-items: center; gap: 20px; }
.info { flex: 1; }
.username { color: #888; font-size: 14px; }
.counts { color: #666; font-size: 13px; margin-top: 4px; }
</style>
```

- [ ] **Step 2: Smoke build**

```bash
cd web/frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
cd ../.. && git add web/frontend/src/views/UserProfile.vue
git commit -m "feat: User profile page + discover users (_discover route)"
```

---

## Phase 7: Frontend Settings + Admin + App Shell

### Task 7.1: Settings page (Sessions / Recycle / Followers tabs)

**Files:**
- Replace: `web/frontend/src/views/Settings.vue`

- [ ] **Step 1: Write Settings.vue**

```vue
<template>
  <el-tabs v-model="active">
    <el-tab-pane label="Sessions" name="sessions">
      <el-button type="primary" @click="showNewToken = true" style="margin-bottom: 12px">
        生成 PDL Token
      </el-button>
      <el-table :data="sessions">
        <el-table-column prop="client_kind" label="类型" width="140" />
        <el-table-column prop="label" label="标签" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column prop="last_used_at" label="最后使用" width="180" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-popconfirm title="确定吊销？" @confirm="revoke(row.id)">
              <template #reference><el-button size="small" class="danger-btn">吊销</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <el-dialog v-model="showNewToken" title="生成 PDL Token" width="460px">
        <el-form>
          <el-form-item label="标签">
            <el-input v-model="newLabel" placeholder="如：MacBook PDL" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="showNewToken = false">取消</el-button>
          <el-button type="primary" :disabled="!newLabel" @click="createToken">生成</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="showTokenValue" title="Token 仅显示一次，请立即复制" width="520px" :close-on-click-modal="false">
        <el-alert type="warning" :closable="false">
          关闭此对话框后再也查不到此 token，请粘贴到 PDL Settings 后再关闭。
        </el-alert>
        <el-input v-model="tokenValue" readonly style="margin-top: 12px" />
        <template #footer>
          <el-button type="primary" @click="copyToken">复制</el-button>
          <el-button @click="showTokenValue = false">我已复制</el-button>
        </template>
      </el-dialog>
    </el-tab-pane>

    <el-tab-pane label="回收站" name="recycle">
      <el-empty v-if="!recycle.length" description="回收站为空" />
      <el-table v-else :data="recycle">
        <el-table-column prop="post_date" label="日期" width="120" />
        <el-table-column prop="scope" label="Scope" width="100" />
        <el-table-column prop="content" label="内容预览" :show-overflow-tooltip="true" />
        <el-table-column prop="deleted_at" label="删除时间" width="180" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="restore(row.id)">恢复</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-tab-pane>

    <el-tab-pane label="Followers" name="followers">
      <p>关注你的人：</p>
      <el-empty v-if="!followers.length" description="还没人关注你" />
      <el-table v-else :data="followers">
        <el-table-column label="用户">
          <template #default="{ row }">@{{ row.jira_username }}</template>
        </el-table-column>
        <el-table-column prop="display_name" label="显示名" />
        <el-table-column prop="followed_at" label="关注时间" width="180" />
      </el-table>
    </el-tab-pane>
  </el-tabs>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useAuthStore } from '../stores/auth'

const active = ref('sessions')
const sessions = ref([])
const recycle = ref([])
const followers = ref([])
const showNewToken = ref(false)
const showTokenValue = ref(false)
const newLabel = ref('')
const tokenValue = ref('')
const store = useAuthStore()

async function loadSessions() { sessions.value = (await api.listSessions()).data }
async function loadRecycle() {
  const r = await api.listPosts({ author: store.user.jira_username, include_deleted: 'true' })
  recycle.value = r.data.filter(p => p.deleted_at)
}
async function loadFollowers() { followers.value = (await api.listFollowers()).data }

async function revoke(id) {
  await api.revokeSession(id)
  ElMessage.success('已吊销')
  await loadSessions()
}

async function createToken() {
  const r = await api.createPdlToken(newLabel.value)
  tokenValue.value = r.data.token
  showNewToken.value = false
  showTokenValue.value = true
  newLabel.value = ''
  await loadSessions()
}

async function copyToken() {
  await navigator.clipboard.writeText(tokenValue.value)
  ElMessage.success('已复制')
}

async function restore(id) {
  await api.restorePost(id)
  ElMessage.success('已恢复')
  await loadRecycle()
}

watch(active, (t) => {
  if (t === 'sessions') loadSessions()
  if (t === 'recycle') loadRecycle()
  if (t === 'followers') loadFollowers()
})
onMounted(loadSessions)
</script>

<style scoped>
.danger-btn { color: #f56c6c; }
</style>
```

- [ ] **Step 2: Smoke build**

```bash
cd web/frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
cd ../.. && git add web/frontend/src/views/Settings.vue
git commit -m "feat: Settings page (Sessions / Recycle / Followers tabs)"
```

---

### Task 7.2: Admin panel

**Files:**
- Replace: `web/frontend/src/views/AdminPanel.vue`

- [ ] **Step 1: Write AdminPanel.vue**

```vue
<template>
  <el-tabs v-model="active">
    <el-tab-pane label="邀请白名单" name="invites">
      <el-form inline @submit.prevent="addInvite" style="margin-bottom: 12px">
        <el-form-item label="Jira 用户名">
          <el-input v-model="newInvite.jira_username" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="newInvite.note" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit" :disabled="!newInvite.jira_username">
            添加邀请
          </el-button>
        </el-form-item>
      </el-form>

      <el-table :data="invites">
        <el-table-column prop="jira_username" label="用户" />
        <el-table-column prop="invited_by" label="邀请人" />
        <el-table-column prop="invited_at" label="邀请时间" width="180" />
        <el-table-column prop="consumed_at" label="首次登录" width="180" />
        <el-table-column prop="note" label="备注" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-popconfirm
              :title="row.consumed_at ? '此邀请已被使用，无法删除' : '确定删除？'"
              :disabled="!!row.consumed_at"
              @confirm="deleteInvite(row.jira_username)"
            >
              <template #reference>
                <el-button size="small" :disabled="!!row.consumed_at" class="danger-btn">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-tab-pane>

    <el-tab-pane label="审计日志" name="audit">
      <el-form inline @submit.prevent="loadAudit" style="margin-bottom: 12px">
        <el-form-item label="Actor"><el-input v-model="auditFilter.actor" clearable /></el-form-item>
        <el-form-item label="Action"><el-input v-model="auditFilter.action" clearable /></el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item>
      </el-form>
      <el-table :data="audit">
        <el-table-column prop="created_at" label="时间" width="200" />
        <el-table-column prop="actor" label="Actor" width="140" />
        <el-table-column prop="action" label="Action" width="140" />
        <el-table-column prop="target" label="Target" width="200" />
        <el-table-column prop="detail" label="Detail" :show-overflow-tooltip="true" />
        <el-table-column prop="ip" label="IP" width="140" />
      </el-table>
    </el-tab-pane>
  </el-tabs>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const active = ref('invites')
const invites = ref([])
const audit = ref([])
const newInvite = ref({ jira_username: '', note: '' })
const auditFilter = ref({ actor: '', action: '' })

async function loadInvites() { invites.value = (await api.listInvites()).data }

async function addInvite() {
  try {
    await api.addInvite(newInvite.value.jira_username, newInvite.value.note || null)
    ElMessage.success('已添加')
    newInvite.value = { jira_username: '', note: '' }
    await loadInvites()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '添加失败')
  }
}

async function deleteInvite(username) {
  try {
    await api.deleteInvite(username)
    ElMessage.success('已删除')
    await loadInvites()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

async function loadAudit() {
  const params = {}
  if (auditFilter.value.actor) params.actor = auditFilter.value.actor
  if (auditFilter.value.action) params.action = auditFilter.value.action
  audit.value = (await api.getAudit(params)).data
}

watch(active, (t) => { if (t === 'audit') loadAudit() })
onMounted(loadInvites)
</script>

<style scoped>
.danger-btn { color: #f56c6c; }
</style>
```

- [ ] **Step 2: Smoke build**

```bash
cd web/frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
cd ../.. && git add web/frontend/src/views/AdminPanel.vue
git commit -m "feat: Admin panel (invites + audit log query)"
```

---

### Task 7.3: App shell with header & navigation

**Files:**
- Replace: `web/frontend/src/App.vue`

- [ ] **Step 1: Write App.vue**

```vue
<template>
  <el-container style="height: 100vh">
    <el-header class="app-header">
      <div class="brand">
        <router-link to="/">CoDaily · 日报广场</router-link>
      </div>
      <div v-if="store.isAuthed" class="nav">
        <el-button text @click="$router.push('/')">Feed</el-button>
        <el-button text @click="$router.push('/u/_discover')">发现</el-button>
        <el-dropdown @command="onCmd">
          <span class="user-chip">
            @{{ store.user.jira_username }}
            <el-icon><arrow-down /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile">我的档案</el-dropdown-item>
              <el-dropdown-item command="settings">设置</el-dropdown-item>
              <el-dropdown-item v-if="store.isAdmin" command="admin">管理员</el-dropdown-item>
              <el-dropdown-item divided command="logout">退出</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { ArrowDown } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'

const store = useAuthStore()
const router = useRouter()

async function onCmd(cmd) {
  if (cmd === 'profile') router.push(`/u/${store.user.jira_username}`)
  else if (cmd === 'settings') router.push('/settings')
  else if (cmd === 'admin') router.push('/settings/admin')
  else if (cmd === 'logout') { await store.logout(); router.replace('/login') }
}
</script>

<style scoped>
.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #eee;
  background: #fff;
}
.brand a { color: #303133; text-decoration: none; font-weight: 600; font-size: 18px; }
.nav { display: flex; gap: 10px; align-items: center; }
.user-chip { cursor: pointer; display: inline-flex; align-items: center; gap: 4px; }
</style>
```

- [ ] **Step 2: Smoke build**

```bash
cd web/frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
cd ../.. && git add web/frontend/src/App.vue
git commit -m "feat: app shell with header nav + user dropdown"
```

---

## Phase 8: Deployment + E2E + Docs

### Task 8.1: Multi-stage Dockerfile

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Write Dockerfile**

```dockerfile
# ─── Stage 1: build frontend ───────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /app/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json* ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build

# ─── Stage 2: python runtime ───────────────────────────────
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install "fastapi>=0.110" "uvicorn[standard]>=0.29" \
    "aiosqlite>=0.19" "httpx>=0.27" "pydantic>=2.6" "python-multipart>=0.0.9" "slowapi>=0.1.9"

COPY codaily/ ./codaily/
COPY --from=frontend /app/web/frontend/dist ./web/frontend/dist

# Mount point for SQLite DB and backups
VOLUME ["/data"]
ENV CODAILY_DB=/data/codaily.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

CMD ["uvicorn", "codaily.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Update codaily/app.py to serve frontend dist**

Append to `codaily/app.py` (inside `create_app`, after router registration):

```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles

dist = Path(__file__).resolve().parent.parent / "web" / "frontend" / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
```

- [ ] **Step 3: Verify the new app.py still passes tests**

```bash
pytest -v
```

Expected: all green. The static mount only activates if `dist/` exists; in tests it doesn't, so no effect.

- [ ] **Step 4: Smoke build image locally**

```bash
docker build -t codaily:local .
docker images codaily
```

Expected: image built without errors.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile codaily/app.py
git commit -m "feat: multi-stage Dockerfile + serve frontend dist from FastAPI"
```

---

### Task 8.2: docker-compose + Caddyfile + backup script

**Files:**
- Create: `docker-compose.yml`
- Create: `Caddyfile`
- Create: `scripts/backup.sh`
- Create: `docs/deployment.md`

- [ ] **Step 1: Write docker-compose.yml**

```yaml
services:
  app:
    image: codaily:latest
    build: .
    environment:
      - CODAILY_DB=/data/codaily.db
      - CODAILY_JIRA_BASE=${CODAILY_JIRA_BASE:-https://jira.fanruan.com}
      - CODAILY_ADMIN=${CODAILY_ADMIN}
      - CODAILY_LOG_LEVEL=${CODAILY_LOG_LEVEL:-INFO}
    volumes:
      - codaily-data:/data
    restart: unless-stopped
    expose:
      - "8000"

  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    restart: unless-stopped
    depends_on:
      - app

  backup:
    image: alpine:3.19
    volumes:
      - codaily-data:/data:ro
      - ./backups:/backups
    entrypoint: ["/bin/sh", "-c"]
    command: |
      "apk add --no-cache sqlite && \
       while true; do \
         sqlite3 /data/codaily.db \".backup /backups/codaily-$$(date +%F).db\" && \
         find /backups -name 'codaily-*.db' -mtime +30 -delete; \
         sleep 86400; \
       done"
    restart: unless-stopped

volumes:
  codaily-data:
  caddy-data:
  caddy-config:
```

- [ ] **Step 2: Write Caddyfile**

```
codaily.fanruan.com {
    reverse_proxy app:8000
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
    }
}
```

- [ ] **Step 3: Write scripts/backup.sh**

```bash
mkdir -p scripts
cat > scripts/backup.sh <<'EOF'
#!/usr/bin/env bash
# Manual backup. The "backup" docker service runs this daily, but you can also
# invoke on-demand: ./scripts/backup.sh
set -euo pipefail
DEST=${1:-./backups}
mkdir -p "$DEST"
docker compose exec -T app sqlite3 /data/codaily.db ".backup /tmp/b.db"
docker compose cp app:/tmp/b.db "$DEST/codaily-$(date +%F_%H-%M-%S).db"
echo "Backup written to $DEST"
EOF
chmod +x scripts/backup.sh
```

- [ ] **Step 4: Write docs/deployment.md**

```markdown
# Deployment

## First-time setup

1. Provision a VPS (Hetzner CX11 ~$5/mo works). Open ports 80, 443.
2. Point DNS: `codaily.fanruan.com` A → VPS IP.
3. Install Docker + Compose plugin.
4. Clone this repo and create `.env`:
       CODAILY_ADMIN=connery
       CODAILY_JIRA_BASE=https://jira.fanruan.com
5. `docker compose up -d`
6. Caddy auto-fetches TLS on first HTTPS hit.
7. Log in as `connery` (CODAILY_ADMIN is auto-self-invited) and add more
   invites via the Admin panel.

## Backup

Daily backup runs automatically in the `backup` service. Files land in
`./backups/codaily-YYYY-MM-DD.db`. Retention: 30 days.

Restore: stop app, copy the desired `.db` over the live DB volume, start.

## Upgrade

    git pull
    docker compose build app
    docker compose up -d app
```

- [ ] **Step 5: Smoke validate compose file**

```bash
docker compose config > /dev/null
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml Caddyfile scripts/backup.sh docs/deployment.md
git commit -m "feat: docker-compose + Caddy + daily backup service + deployment docs"
```

---

### Task 8.3: E2E happy-path test + README + push-contract doc

**Files:**
- Create: `tests/test_e2e.py`
- Create: `docs/push-contract-v1.md`
- Replace: `README.md`

- [ ] **Step 1: Write E2E test**

`tests/test_e2e.py`:
```python
"""Full happy-path: admin invites → user logs in → push → another user follows → feed."""
from codaily.auth.jira import JiraProfile


async def test_e2e_happy_path(app_and_db):
    app, db, jira, client = app_and_db

    # Setup: root is pre-bootstrapped as admin via CODAILY_ADMIN env
    # ----- 1. root logs in -----
    jira.add("root", "pw", JiraProfile(username="root", display_name="Root", email=None, avatar_url=None))
    r = await client.post("/api/v1/auth/login", json={"username": "root", "password": "pw"})
    assert r.status_code == 200
    assert r.json()["user"]["is_admin"] == 1

    # ----- 2. root invites alice and bob -----
    for u in ("alice", "bob"):
        r = await client.post("/api/v1/admin/invites", json={"jira_username": u})
        assert r.status_code == 201

    # ----- 3. alice logs in (new client cookies scope) -----
    client.cookies.clear()
    jira.add("alice", "pw", JiraProfile(username="alice", display_name="Alice", email=None, avatar_url=None))
    r = await client.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})
    assert r.status_code == 200

    # ----- 4. alice generates PDL token -----
    r = await client.post("/api/v1/sessions/pdl-token", json={"label": "laptop"})
    assert r.status_code == 201
    alice_token = r.json()["token"]

    # ----- 5. alice pushes a post via the PDL token (no cookie) -----
    client.cookies.clear()
    r = await client.post(
        "/api/v1/push",
        json={
            "post_date": "2026-04-19",
            "scope": "day",
            "content": "Fixed SQL parser bug",
            "metadata": {
                "schema_version": "1.0",
                "issue_keys": ["POLARDB-123"],
                "time_spent_sec": 7200,
                "entries": [{"issue_key": "POLARDB-123", "hours": 2.0, "summary": "parser fix"}],
            },
        },
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 201
    post_id = r.json()["id"]

    # ----- 6. bob logs in -----
    jira.add("bob", "pw", JiraProfile(username="bob", display_name="Bob", email=None, avatar_url=None))
    r = await client.post("/api/v1/auth/login", json={"username": "bob", "password": "pw"})
    assert r.status_code == 200

    # ----- 7. bob follows alice -----
    r = await client.post("/api/v1/follows", json={"followee": "alice"})
    assert r.status_code == 201

    # ----- 8. bob's feed contains alice's post -----
    r = await client.get("/api/v1/feed")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == post_id
    assert body["items"][0]["author"] == "alice"
    assert body["items"][0]["content"] == "Fixed SQL parser bug"
    assert body["items"][0]["metadata"]["issue_keys"] == ["POLARDB-123"]

    # ----- 9. alice (from her view) sees bob as a follower -----
    client.cookies.clear()
    await client.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})
    r = await client.get("/api/v1/follows/followers")
    names = [u["jira_username"] for u in r.json()]
    assert names == ["bob"]

    # ----- 10. audit log has all the actions (admin view) -----
    client.cookies.clear()
    await client.post("/api/v1/auth/login", json={"username": "root", "password": "pw"})
    r = await client.get("/api/v1/admin/audit", params={"limit": 500})
    actions = {row["action"] for row in r.json()}
    assert "login" in actions
    assert "invite" in actions
    assert "push" in actions
    assert "follow" in actions
```

- [ ] **Step 2: Run — expected PASS**

```bash
pytest tests/test_e2e.py -v
```

Expected: `1 passed`.

- [ ] **Step 3: Write docs/push-contract-v1.md**

```markdown
# CoDaily Push Contract v1.0

This document is the **normative** specification for publishers pushing to CoDaily.
Authoritative source of truth — publishers should only read this doc, not server code.

## Endpoint

    POST /api/v1/push
    Authorization: Bearer <pdl-publisher-token>
    Content-Type: application/json

The token must be a `pdl-publisher` session (generated via the web Settings
page). Browser-session tokens are rejected with 403.

## Request body

| Field          | Type                    | Required | Notes                                        |
|----------------|-------------------------|----------|----------------------------------------------|
| `post_date`    | string (YYYY-MM-DD)     | yes      | Date the log describes. Can be back-dated.  |
| `scope`        | string (≤ 64 chars)     | yes      | Open — publisher's choice (e.g. `day`, `week`, `monthly-retro`). |
| `content`      | string                  | yes      | The main body.                               |
| `content_type` | `markdown`/`json`/`text`| no       | Default `markdown`.                          |
| `metadata`     | object                  | no       | If present, **must** include `schema_version`. |
| `source`       | string (≤ 64 chars)     | no       | Client self-identifies, e.g. `pdl`.          |

### Responses

| Code | Meaning                                         |
|------|-------------------------------------------------|
| 201  | Created — first time `(author, post_date, scope)` |
| 200  | Updated — existing row upserted                  |
| 400  | Invalid metadata (missing `schema_version`, etc.)|
| 401  | Invalid/revoked token                            |
| 403  | Wrong token kind (e.g. browser session)          |
| 422  | Pydantic validation error on body                |
| 429  | Rate limit (120/min per token on this endpoint)  |

## Metadata v1.0

All fields optional except `schema_version`. Publishers MAY include extra
unknown fields; CoDaily will store them verbatim and the dashboard will ignore
them until a future schema version formalizes them.

| Field            | Type               | Description                                     |
|------------------|--------------------|-------------------------------------------------|
| `schema_version` | string             | `"1.0"` for this version                        |
| `issue_keys`     | string[]           | Jira issue keys mentioned                       |
| `time_spent_sec` | integer            | Total time spent                                |
| `entries`        | object[]           | `{issue_key: str, hours: float, summary: str}`  |
| `tags`           | string[]           | Freeform tags                                   |

### Example

```json
{
  "post_date": "2026-04-19",
  "scope": "day",
  "content": "Fixed SQL parser bug and reviewed a PR.",
  "content_type": "markdown",
  "metadata": {
    "schema_version": "1.0",
    "issue_keys": ["POLARDB-123", "POLARDB-456"],
    "time_spent_sec": 12600,
    "entries": [
      {"issue_key": "POLARDB-123", "hours": 2.0, "summary": "parser fix"},
      {"issue_key": "POLARDB-456", "hours": 1.5, "summary": "PR review"}
    ],
    "tags": ["backend", "review"]
  },
  "source": "pdl"
}
```

## Versioning

| Bump           | Semantics                                      | Client impact                |
|----------------|------------------------------------------------|------------------------------|
| `1.0 → 1.x`    | Add optional fields; extend enum values        | Old CoDaily ignores new fields; compatible |
| `1.x → 2.0`    | Rename / remove / semantic-reverse fields      | Old CoDaily displays degraded view         |

The URL version (`/api/v1/`) is independent. Breaking API-shape changes will
move to `/api/v2/`; `/api/v1/` will remain supported for at least 6 months
after `v2` ships.

## Delete / retraction

- Author deletion: author sends `DELETE /api/v1/posts/{id}` (not a push-contract
  concern — uses a regular session; the publisher can learn the id from the
  push response and surface a "revert" button).
- Re-pushing the same `(author, post_date, scope)` triple upserts (response 200).
  No special retraction call is needed to overwrite.
```

- [ ] **Step 4: Replace README.md**

```markdown
# CoDaily / 日报广场

Daily-log exchange platform. Colleagues subscribe to each other's daily reports
pushed by publishers (e.g. PDL). Closed-invite group, Jira authentication.

- **Design spec:** `docs/specs/2026-04-19-codaily-daily-plaza-design.md`
- **Publisher push contract:** `docs/push-contract-v1.md`
- **Deployment:** `docs/deployment.md`
- **Implementation plan:** `docs/plans/2026-04-19-codaily-daily-plaza-plan.md`

## Dev quickstart

    python -m venv .venv && source .venv/bin/activate
    pip install -e ".[dev]"

    # backend
    export CODAILY_DB=/tmp/codaily-dev.db
    export CODAILY_JIRA_BASE=https://jira.fanruan.com
    export CODAILY_ADMIN=your-jira-username
    uvicorn codaily.app:create_app --factory --reload --port 8000

    # frontend (separate terminal)
    cd web/frontend && npm install && npm run dev

Then browse http://localhost:5173, log in with your Jira credentials.

## Run tests

    pytest -v

## Production

See `docs/deployment.md`.

## Project principles

See `AGENTS.md`.
```

- [ ] **Step 5: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass (sum of every previous task's tests + E2E).

- [ ] **Step 6: Commit**

```bash
git add tests/test_e2e.py docs/push-contract-v1.md README.md
git commit -m "docs: E2E happy-path test + push-contract-v1 + README"
```

---

## Final verification

After all tasks are complete:

- [ ] **Run full test suite**

```bash
pytest -v
```

Expected: every test in `tests/` passes (roughly 50+ tests).

- [ ] **Build the production image**

```bash
docker compose build
```

- [ ] **Start the stack locally and hit /health over Caddy**

```bash
CODAILY_ADMIN=test docker compose up -d
curl -skL https://localhost/health   # will 502 until Caddy gets cert for localhost
docker compose logs app | tail -20
docker compose down -v
```

- [ ] **Smoke login flow**

Start just the app service locally without Caddy:
```bash
CODAILY_ADMIN=your-jira-user docker compose up app -d
# visit http://localhost:8000 — login page should render
docker compose down -v
```

- [ ] **Tag v0.1.0 and push the repo**

```bash
git tag v0.1.0
git push --tags
# push to remote (after creating polars-daily-plaza on GitHub/internal git)
git remote add origin <repo-url>
git push -u origin master --tags
```

---

## Self-review checklist (for plan author)

After writing this plan, the plan author has verified:

- ✅ Spec coverage: every section in `docs/superpowers/specs/2026-04-19-codaily-daily-plaza-design.md` has at least one corresponding task:
  - §2 Architecture → Task 0.3, 0.4
  - §3 Data Model → Task 1.2
  - §4 Push Contract → Task 3.1, 3.2
  - §5 Auth → Task 2.1–2.6
  - §6 API endpoints → Task 3.*, 4.*, 5.*
  - §7 Frontend → Task 6.*, 7.*
  - §8 PDL publisher — lives in PDL repo, tracked separately (scope call-out at end)
  - §9 Deployment → Task 8.1, 8.2
  - §10 Ops → Task 8.2 (backup), Task 2.6 (rate limit)
  - §11 Testing strategy → each task's tests; Task 8.3 E2E
  - §12 Project structure → Task 0.1 + each file-creating task
- ✅ No placeholders — every step has concrete code
- ✅ Type/name consistency — `token_hash`, `current_user`, `require_admin`, `PushBody`, etc. match across tasks
- ✅ Each task produces one commit
- ✅ Tests precede implementation (TDD) where applicable; Vue files use build-smoke instead

## Out of plan scope

The following are **intentionally not in this plan** (tracked as future work):

1. **PDL side `CoDailyPublisher` implementation** — belongs in the PDL repo. After CoDaily is deployed and you have a production token, open a PR against PDL that:
   - adds `auto_daily_log/publishers/codaily.py`
   - registers it in `auto_daily_log/publishers/registry.py`
   - updates PDL `Settings.vue` publisher dropdown
   - adds a test under PDL `tests/test_publisher_and_types.py`
   (Spec §8 has the code sketch.)

2. **Avatar proxying** — Task 2.4 stores whatever avatar URL Jira returns. If Jira's avatar needs auth in production, a small `/api/v1/avatars/{username}` proxy endpoint will be needed later.

3. **CI (GitHub Actions)** — add once repo is on a git host.

4. **Production domain / TLS cert** — Caddy handles automatically on first HTTPS hit, but DNS setup is manual.

5. **Metrics / Grafana** — MVP relies on docker logs + `/health`; add Prometheus exporter when scale demands.

6. **`POST /api/v1/posts/{id}/unhide`** and other "undelete" refinements — already included in Task 3.4.





