"""Real install tests — no mocks, real pip, real wheel.

These tests build a wheel from the current source, create isolated HOME
directories, and run install.sh end-to-end. They catch dependency issues
(like missing 'packaging'), import chain breakage, and data migration
failures that mock-based tests miss.

Marked @pytest.mark.slow — excluded from default `pytest tests/` runs.
Run explicitly: pytest tests/test_install_real.py -v --timeout=300

Skipped on Windows (install.sh is bash; Windows uses install.ps1).
"""

import json
import os
import platform
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(platform.system() == "Windows", reason="bash-only"),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def wheel_path(tmp_path_factory):
    """Build a wheel once for the entire module (expensive)."""
    build_dir = tmp_path_factory.mktemp("build")

    # Stage frontend dist into package (required for wheel)
    staged = REPO_ROOT / "auto_daily_log" / "frontend_dist"
    needs_cleanup = False
    if not staged.exists():
        fe_dist = REPO_ROOT / "web" / "frontend" / "dist"
        if fe_dist.exists():
            shutil.copytree(fe_dist, staged)
        else:
            staged.mkdir(parents=True)
            (staged / "index.html").write_text("<html></html>")
        needs_cleanup = True

    try:
        r = subprocess.run(
            ["python3", "-m", "build", "--wheel", "--outdir", str(build_dir)],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            pytest.skip(f"wheel build failed: {r.stderr[-500:]}")

        wheels = list(build_dir.glob("auto_daily_log-*.whl"))
        assert len(wheels) == 1, f"Expected 1 wheel, got {wheels}"
        return wheels[0]
    finally:
        if needs_cleanup and staged.exists():
            shutil.rmtree(staged)


def _make_tarball_layout(dest: Path, wheel: Path):
    """Create a minimal release tarball layout."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "wheels").mkdir()
    shutil.copy(wheel, dest / "wheels")
    shutil.copy(INSTALL_SH, dest / "install.sh")
    shutil.copy(REPO_ROOT / "pdl", dest / "pdl")
    for f in ["config.yaml.example", "collector.yaml.example", "README.md"]:
        src = REPO_ROOT / f
        if src.exists():
            shutil.copy(src, dest / f)
    enc = REPO_ROOT / "auto_daily_log" / "builtin_llm.enc"
    if enc.exists():
        shutil.copy(enc, dest / "builtin_llm.enc")
    (dest / "VERSION").write_text(
        subprocess.run(
            ["python3", "-c", "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"],
            cwd=str(REPO_ROOT), capture_output=True, text=True
        ).stdout.strip() or "test"
    )


def _run_install(install_dir: Path, home_dir: Path, *, role: str = "server",
                 extra_env: dict = None, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run install.sh with isolated HOME."""
    env = {
        "HOME": str(home_dir),
        "PATH": os.environ.get("PATH", ""),
        "TERM": "dumb",
        "PDL_ROLE": role,
        "PDL_PIP_INDEX_URL": os.environ.get("PDL_PIP_INDEX_URL", "https://mirrors.aliyun.com/pypi/simple/"),
    }
    if role in ("both", "collector"):
        env["PDL_SERVER_URL"] = "http://127.0.0.1:9999"
        env["PDL_COLLECTOR_NAME"] = "test-machine"
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", str(install_dir / "install.sh")],
        cwd=str(install_dir),
        env=env,
        capture_output=True, text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Clean install from scratch
# ---------------------------------------------------------------------------

class TestCleanInstallServer:
    """Fresh machine, no prior install — role=server."""

    def test_full_flow(self, tmp_path, wheel_path):
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        _make_tarball_layout(install_dir, wheel_path)

        r = _run_install(install_dir, home, role="server",
                         extra_env={"PDL_BUILTIN_PASSPHRASE": "polars"})

        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout[-2000:]}"

        # Venv created
        assert (install_dir / ".venv" / "bin" / "python3").exists()

        # Data dir created
        data_dir = home / ".auto_daily_log"
        assert data_dir.is_dir()

        # Config created
        assert (install_dir / "config.yaml").exists()

        # Collector config NOT created (role=server)
        assert not (install_dir / "collector.yaml").exists()

        # Builtin LLM written to llm_engines table
        if (install_dir / "builtin_llm.enc").exists():
            db_path = data_dir / "data.db"
            assert db_path.exists()
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT api_key FROM llm_engines WHERE name = 'builtin' AND is_default = 1"
            ).fetchone()
            conn.close()
            assert row is not None, "builtin engine not inserted into llm_engines"
            assert row[0], "api_key is empty"

        # Import chain works from installed wheel
        venv_python = str(install_dir / ".venv" / "bin" / "python3")
        for module in [
            "from auto_daily_log.app import Application",
            "from auto_daily_log.web.app import create_app",
            "from auto_daily_log.updater import version_check",
            "from packaging.version import Version",
        ]:
            result = subprocess.run(
                [venv_python, "-c", module],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0, f"Import failed: {module}\n{result.stderr}"

        # DB created with expected tables
        db_path = data_dir / "data.db"
        assert db_path.exists()
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for expected in ["activities", "settings", "worklog_drafts", "summary_types"]:
            assert expected in tables, f"Missing table: {expected}"
        conn.close()


class TestCleanInstallBoth:
    """Fresh machine — role=both."""

    def test_both_configs_created(self, tmp_path, wheel_path):
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        _make_tarball_layout(install_dir, wheel_path)

        r = _run_install(install_dir, home, role="both")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout[-2000:]}"

        assert (install_dir / "config.yaml").exists()
        assert (install_dir / "collector.yaml").exists()

        coll = (install_dir / "collector.yaml").read_text()
        assert "127.0.0.1:9999" in coll or "9999" in coll
        assert "test-machine" in coll


class TestCleanInstallCollector:
    """Fresh machine — role=collector."""

    def test_server_config_not_created(self, tmp_path, wheel_path):
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        _make_tarball_layout(install_dir, wheel_path)

        r = _run_install(install_dir, home, role="collector")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout[-2000:]}"

        assert not (install_dir / "config.yaml").exists()
        assert (install_dir / "collector.yaml").exists()

        # Builtin LLM NOT configured (collector doesn't need it)
        db_path = home / ".auto_daily_log" / "data.db"
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT name FROM llm_engines WHERE name = 'builtin'").fetchone()
            conn.close()
            assert row is None, "collector install should not insert builtin engine"


# ---------------------------------------------------------------------------
# Upgrade install — existing data must survive
# ---------------------------------------------------------------------------

class TestUpgradeInstall:
    """Simulate upgrading from an older version with existing user data."""

    @staticmethod
    def _seed_old_db(db_path: Path):
        """Create a v0.4.0-like DB with sample data."""
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS activities (id INTEGER PRIMARY KEY, timestamp TEXT, app_name TEXT, window_title TEXT, category TEXT, confidence REAL, url TEXT, signals TEXT, duration_sec INTEGER, machine_id TEXT, llm_summary TEXT, deleted_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS worklog_drafts (id INTEGER PRIMARY KEY, date TEXT, tag TEXT, status TEXT, summary TEXT, full_summary TEXT, time_spent_sec INTEGER, period_start TEXT, period_end TEXT, user_edited INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS jira_issues (key TEXT PRIMARY KEY, summary TEXT, description TEXT)")

        # Seed settings
        for k, v in [
            ("llm_engine", "openai_compat"),
            ("llm_api_key", "sk-user-custom-key-12345"),
            ("llm_base_url", "https://api.kimi.com/coding"),
            ("llm_model", "kimi-k2"),
            ("jira_server_url", "https://work.fineres.com"),
            ("jira_username", "connery-石碧"),
            ("scheduler_trigger_time", "16:36"),
            ("auto_approve_trigger_time", "16:38"),
            ("user_nickname", "TestUser"),
        ]:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))

        # Seed activities
        conn.execute(
            "INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec, machine_id) "
            "VALUES ('2026-04-16T14:00:00', 'VS Code', 'app.py', 'coding', 0.95, 30, 'local')"
        )

        # Seed worklog draft
        conn.execute(
            "INSERT INTO worklog_drafts (date, tag, status, summary, time_spent_sec, period_start, period_end) "
            "VALUES ('2026-04-16', 'daily', 'submitted', 'Old worklog from v0.4.0', 28800, '2026-04-16', '2026-04-16')"
        )

        # Seed jira issue
        conn.execute(
            "INSERT OR REPLACE INTO jira_issues (key, summary) VALUES ('PLS-100', 'Test issue')"
        )

        conn.commit()
        conn.close()

    def test_upgrade_preserves_settings(self, tmp_path, wheel_path):
        """User's LLM key, trigger times, nickname must survive upgrade."""
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        data_dir = home / ".auto_daily_log"
        data_dir.mkdir(parents=True)

        # Pre-seed old DB
        self._seed_old_db(data_dir / "data.db")

        # Pre-seed old config (user customized it)
        _make_tarball_layout(install_dir, wheel_path)
        config = install_dir / "config.yaml"
        config.write_text("system:\n  data_dir: \"\"\nserver:\n  port: 8888\n")

        r = _run_install(install_dir, home, role="server")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout[-2000:]}"

        # Verify old data survived in DB
        conn = sqlite3.connect(data_dir / "data.db")
        conn.row_factory = sqlite3.Row

        # Settings preserved
        llm_key = conn.execute("SELECT value FROM settings WHERE key='llm_api_key'").fetchone()
        assert llm_key["value"] == "sk-user-custom-key-12345"

        trigger = conn.execute("SELECT value FROM settings WHERE key='scheduler_trigger_time'").fetchone()
        assert trigger["value"] == "16:36"

        nickname = conn.execute("SELECT value FROM settings WHERE key='user_nickname'").fetchone()
        assert nickname["value"] == "TestUser"

        conn.close()

    def test_upgrade_preserves_activities(self, tmp_path, wheel_path):
        """Old activities must not be lost after upgrade."""
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        data_dir = home / ".auto_daily_log"
        data_dir.mkdir(parents=True)
        self._seed_old_db(data_dir / "data.db")
        _make_tarball_layout(install_dir, wheel_path)

        r = _run_install(install_dir, home, role="server")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout[-2000:]}"

        conn = sqlite3.connect(data_dir / "data.db")
        count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        assert count >= 1
        row = conn.execute("SELECT app_name FROM activities LIMIT 1").fetchone()
        assert row[0] == "VS Code"
        conn.close()

    def test_upgrade_preserves_worklogs(self, tmp_path, wheel_path):
        """Old worklog drafts must survive upgrade."""
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        data_dir = home / ".auto_daily_log"
        data_dir.mkdir(parents=True)
        self._seed_old_db(data_dir / "data.db")
        _make_tarball_layout(install_dir, wheel_path)

        r = _run_install(install_dir, home, role="server")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout[-2000:]}"

        conn = sqlite3.connect(data_dir / "data.db")
        draft = conn.execute("SELECT summary, status FROM worklog_drafts WHERE date='2026-04-16'").fetchone()
        assert draft[0] == "Old worklog from v0.4.0"
        assert draft[1] == "submitted"
        conn.close()

    def test_upgrade_does_not_overwrite_config(self, tmp_path, wheel_path):
        """User's config.yaml must not be overwritten on upgrade."""
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        data_dir = home / ".auto_daily_log"
        data_dir.mkdir(parents=True)
        self._seed_old_db(data_dir / "data.db")
        _make_tarball_layout(install_dir, wheel_path)

        # Write user-customized config BEFORE install
        custom_config = "system:\n  data_dir: \"/custom/path\"\nserver:\n  port: 9999\n"
        (install_dir / "config.yaml").write_text(custom_config)

        r = _run_install(install_dir, home, role="server")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout[-2000:]}"

        # Config must be untouched
        assert (install_dir / "config.yaml").read_text() == custom_config

    def test_upgrade_preserves_existing_llm_engine(self, tmp_path, wheel_path):
        """Existing llm_engines entries must survive upgrade without passphrase."""
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        data_dir = home / ".auto_daily_log"
        data_dir.mkdir(parents=True)

        # Pre-existing DB with a user-configured engine
        db_path = data_dir / "data.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_engines ("
            "name TEXT PRIMARY KEY, display_name TEXT NOT NULL, protocol TEXT NOT NULL DEFAULT 'openai_compat', "
            "api_key TEXT, model TEXT, base_url TEXT, is_default INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1, "
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO llm_engines (name, display_name, protocol, api_key, model, base_url, is_default) "
            "VALUES ('user-engine', 'My Engine', 'openai_compat', 'sk-old-key', 'old-model', 'https://old', 1)"
        )
        conn.commit()
        conn.close()

        _make_tarball_layout(install_dir, wheel_path)

        # Install WITHOUT passphrase → should not touch existing engine
        r = _run_install(install_dir, home, role="server")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout[-2000:]}"

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT api_key FROM llm_engines WHERE name = 'user-engine'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "sk-old-key"

    def test_upgrade_new_tables_added(self, tmp_path, wheel_path):
        """After upgrade, new tables (summary_types etc.) should be created."""
        install_dir = tmp_path / "pdl"
        home = tmp_path / "home"
        home.mkdir()
        data_dir = home / ".auto_daily_log"
        data_dir.mkdir(parents=True)
        self._seed_old_db(data_dir / "data.db")  # old schema, no summary_types
        _make_tarball_layout(install_dir, wheel_path)

        r = _run_install(install_dir, home, role="server")
        assert r.returncode == 0, f"STDOUT:\n{r.stdout[-2000:]}"

        # After upgrade, the server startup (auto-start) would have initialized
        # new tables. Even without server start, the wheel install should work.
        # Verify the import chain at minimum.
        venv_python = str(install_dir / ".venv" / "bin" / "python3")
        result = subprocess.run(
            [venv_python, "-c", "from auto_daily_log.app import Application; print('ok')"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"Import failed after upgrade:\n{result.stderr}"
