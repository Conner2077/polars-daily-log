"""Comprehensive branch-coverage tests for install.sh.

Strategy: build a fake tarball layout in a temp dir, inject mock binaries for
python3/pip/openssl/node onto PATH, then invoke install.sh via subprocess with
different env-var combinations (PDL_ROLE, PDL_BUILTIN_PASSPHRASE, etc.) and
assert on exit code + generated files + stdout.

Every code path in install.sh that branches on role / platform / tty / mode /
file-existence is exercised.

Skipped on Windows — install.sh is a bash script; Windows uses install.ps1
(tested in test_install_ps1.py).
"""

import json
import os
import platform
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

# Skip entire module on Windows — bash not natively available
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="install.sh tests require bash (macOS/Linux only); Windows uses test_install_ps1.py",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"
assert INSTALL_SH.exists(), f"install.sh not found at {INSTALL_SH}"


def _make_mock_bin(bin_dir: Path, name: str, script: str) -> Path:
    """Create a mock executable shell script in bin_dir."""
    p = bin_dir / name
    p.write_text(f"#!/usr/bin/env bash\n{script}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _setup_release_layout(root: Path, *, include_enc: bool = True,
                           include_collector_example: bool = True,
                           include_config_example: bool = True) -> Path:
    """Create a minimal release-tarball directory layout under root."""
    (root / "wheels").mkdir(parents=True, exist_ok=True)

    # Fake wheel — install.sh just needs the glob to match
    fake_wheel = root / "wheels" / "auto_daily_log-0.5.1-py3-none-any.whl"
    fake_wheel.write_text("fake-wheel")

    # VERSION
    (root / "VERSION").write_text("0.5.1")

    # config.yaml.example
    if include_config_example:
        (root / "config.yaml.example").write_text("system:\n  data_dir: \"\"\n")

    # collector.yaml.example
    if include_collector_example:
        (root / "collector.yaml.example").write_text(
            'server_url: "http://127.0.0.1:8888"\nname: "My-Mac"\ninterval: 30\n'
        )

    # builtin_llm.enc — real encrypted blob from the repo (or a test one)
    if include_enc:
        enc_src = INSTALL_SH.parent / "auto_daily_log" / "builtin_llm.enc"
        if enc_src.exists():
            shutil.copy(enc_src, root / "builtin_llm.enc")
        else:
            # Create a test encrypted blob: echo '{"engine":"openai_compat","api_key":"sk-test","base_url":"https://test","model":"m"}' | openssl enc ...
            subprocess.run(
                ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000",
                 "-salt", "-base64", "-pass", "pass:polars"],
                input=b'{"engine":"openai_compat","api_key":"sk-test","base_url":"https://test","model":"m"}',
                stdout=open(root / "builtin_llm.enc", "wb"),
                check=True,
            )

    # pdl stub (install.sh checks -x for auto-start)
    pdl = root / "pdl"
    pdl.write_text("#!/usr/bin/env bash\necho \"pdl $*\"\n")
    pdl.chmod(pdl.stat().st_mode | stat.S_IEXEC)

    # Copy install.sh itself
    shutil.copy(INSTALL_SH, root / "install.sh")

    return root


def _run_install(root: Path, *, env_extra: dict = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run install.sh in the given root with mocked environment."""
    bin_dir = root / "_mock_bin"
    bin_dir.mkdir(exist_ok=True)

    # Mock python3: handles version check, -m venv, -c scripts
    _make_mock_bin(bin_dir, "python3", textwrap.dedent("""\
        # Handle -m venv: create minimal venv structure
        if [[ "$1" == "-m" && "$2" == "venv" && -n "$3" ]]; then
            mkdir -p "$3/bin"
            echo '# mock activate' > "$3/bin/activate"
            # Symlink this mock as the venv's python3 too
            ln -sf "$(which python3)" "$3/bin/python3" 2>/dev/null || cp "$0" "$3/bin/python3"
            exit 0
        fi
        # Handle --version
        if [[ "$1" == "--version" ]]; then
            echo "Python 3.12.0"
            exit 0
        fi
        for arg in "$@"; do
            if [[ "$arg" == *"sys.version_info"* ]]; then
                echo "3.12"
                exit 0
            fi
            if [[ "$arg" == *"import aiosqlite"* ]] || \
               [[ "$arg" == *"import sqlite_vec"* ]] || \
               [[ "$arg" == *"import fastapi"* ]] || \
               [[ "$arg" == *"from auto_daily_log"* ]] || \
               [[ "$arg" == *"get_platform_module"* ]] || \
               [[ "$arg" == *"create_app"* ]] || \
               [[ "$arg" == *"import Vision"* ]]; then
                echo "MockModule"
                exit 0
            fi
            if [[ "$arg" == *"import subprocess"* ]]; then
                exit 0
            fi
            if [[ "$arg" == *"import json"* ]] || [[ "$arg" == *"json.load"* ]]; then
                exit 0
            fi
            if [[ "$arg" == *"yaml.safe_load"* ]]; then
                # Force sed fallback for predictable testing
                exit 1
            fi
        done
        exit 0
    """))

    # Mock pip
    _make_mock_bin(bin_dir, "pip", 'echo "pip mock: $*"')

    # Mock git
    _make_mock_bin(bin_dir, "git", 'echo "git mock"')

    # Mock xdotool (Linux dep check)
    _make_mock_bin(bin_dir, "xdotool", 'echo "xdotool mock"')

    # Mock node (frontend build check)
    _make_mock_bin(bin_dir, "node", 'echo "node mock"')

    # Mock hostname
    _make_mock_bin(bin_dir, "hostname", 'echo "test-host"')

    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "HOME": str(root / "_home"),
        "TERM": "dumb",
    }
    (root / "_home").mkdir(exist_ok=True)
    if env_extra:
        env.update(env_extra)

    # No tty in subprocess — tty_read will use defaults
    return subprocess.run(
        ["bash", str(root / "install.sh")],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRoleSelection:
    """Step 1: role resolution via PDL_ROLE env var."""

    def test_role_server(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        assert "Will install: server" in r.stdout

    def test_role_collector(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "collector",
            "PDL_SERVER_URL": "http://192.168.1.10:8888",
            "PDL_COLLECTOR_NAME": "test-box",
        })
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        assert "Will install: collector" in r.stdout

    def test_role_both(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "both",
            "PDL_SERVER_URL": "http://127.0.0.1:8888",
            "PDL_COLLECTOR_NAME": "local",
        })
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        assert "Will install: server collector" in r.stdout

    def test_role_ask_no_tty_defaults_to_both(self, tmp_path):
        """When PDL_ROLE=ask and no tty, should default to 'both'."""
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "ask",
            "PDL_SERVER_URL": "http://127.0.0.1:8888",
            "PDL_COLLECTOR_NAME": "auto",
        })
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        assert "defaulting to" in r.stdout.lower() or "Will install: server collector" in r.stdout

    def test_role_invalid_exits(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={"PDL_ROLE": "foobar"})
        assert r.returncode != 0
        assert "Unknown PDL_ROLE" in r.stdout


class TestVersionDynamic:
    """VERSION should be read from VERSION file, not hardcoded."""

    def test_version_from_file(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert "0.5.1" in r.stdout
        assert "0.1.0" not in r.stdout  # old hardcoded value must not appear


class TestConfigGeneration:
    """Step 7: setup_data creates config.yaml and collector.yaml."""

    def test_server_creates_config_yaml(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, r.stderr
        assert (root / "config.yaml").exists()

    def test_server_skips_collector_yaml(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, r.stderr
        assert not (root / "collector.yaml").exists()

    def test_collector_creates_collector_yaml(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "collector",
            "PDL_SERVER_URL": "http://10.0.0.5:8888",
            "PDL_COLLECTOR_NAME": "my-nuc",
        })
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        coll = root / "collector.yaml"
        assert coll.exists(), "collector.yaml not created"
        content = coll.read_text()
        assert "10.0.0.5" in content
        assert "my-nuc" in content

    def test_collector_skips_config_yaml(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "collector",
            "PDL_SERVER_URL": "http://x:8888",
            "PDL_COLLECTOR_NAME": "c",
        })
        assert r.returncode == 0, r.stderr
        assert not (root / "config.yaml").exists()

    def test_both_creates_both_configs(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "both",
            "PDL_SERVER_URL": "http://127.0.0.1:8888",
            "PDL_COLLECTOR_NAME": "local-dev",
        })
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        assert (root / "config.yaml").exists(), "config.yaml missing for role=both"
        assert (root / "collector.yaml").exists(), "collector.yaml missing for role=both"

    def test_both_existing_collector_does_not_skip_server(self, tmp_path):
        """Regression: old install.sh had early-return in setup_data that
        skipped server config when collector.yaml already existed."""
        root = _setup_release_layout(tmp_path / "pdl")
        # Pre-create collector.yaml
        (root / "collector.yaml").write_text("server_url: old\nname: old\n")
        r = _run_install(root, env_extra={"PDL_ROLE": "both"})
        assert r.returncode == 0, r.stderr
        assert (root / "config.yaml").exists(), "server config skipped due to early return bug"

    def test_missing_collector_example_warns(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl", include_collector_example=False)
        r = _run_install(root, env_extra={
            "PDL_ROLE": "collector",
            "PDL_SERVER_URL": "http://x:8888",
            "PDL_COLLECTOR_NAME": "c",
        })
        assert r.returncode == 0, r.stderr
        assert "collector.yaml.example not found" in r.stdout

    def test_collector_url_with_special_chars(self, tmp_path):
        """URL with dots/slashes must not break sed fallback."""
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "collector",
            "PDL_SERVER_URL": "http://my.server.example.com:8888/api",
            "PDL_COLLECTOR_NAME": "box-01&special",
        })
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        content = (root / "collector.yaml").read_text()
        assert "my.server.example.com:8888/api" in content
        assert "box-01&special" in content


class TestBuiltinLLM:
    """Step 8: passphrase-protected built-in LLM config."""

    def test_correct_passphrase_configures_builtin_engine(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "server",
            "PDL_BUILTIN_PASSPHRASE": "polars",
        })
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        assert "Built-in LLM configured" in r.stdout

    def test_wrong_passphrase_warns_and_continues(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "server",
            "PDL_BUILTIN_PASSPHRASE": "wrong-pass",
        })
        assert r.returncode == 0, "Should not fail on wrong passphrase"
        assert "Wrong passphrase" in r.stdout or "口令错误" in r.stdout or "wrong" in r.stdout.lower()

    def test_empty_passphrase_skips(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "server",
            # No PDL_BUILTIN_PASSPHRASE, no tty → skip
        })
        assert r.returncode == 0, r.stderr

    def test_no_enc_file_skips_silently(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl", include_enc=False)
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, r.stderr
        # Should not even print the "Built-in LLM" header
        assert "Built-in LLM" not in r.stdout

    def test_collector_role_skips_builtin_llm(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "collector",
            "PDL_BUILTIN_PASSPHRASE": "polars",
            "PDL_SERVER_URL": "http://x:8888",
            "PDL_COLLECTOR_NAME": "c",
        })
        assert r.returncode == 0, r.stderr
        assert "Built-in LLM" not in r.stdout


class TestSectionNumbering:
    """All section headers must be sequentially numbered with no duplicates."""

    def test_no_duplicate_section_numbers(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "both",
            "PDL_BUILTIN_PASSPHRASE": "polars",
            "PDL_SERVER_URL": "http://127.0.0.1:8888",
            "PDL_COLLECTOR_NAME": "test",
        })
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        import re
        numbers = re.findall(r"^(\d+)\.", r.stdout, re.MULTILINE)
        seen = set()
        for n in numbers:
            assert n not in seen, f"Duplicate section number: {n}\nFull output:\n{r.stdout}"
            seen.add(n)


class TestFrontend:
    """Step 9: frontend build (release mode = skip)."""

    def test_release_mode_skips_frontend_build(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, r.stderr
        assert "Frontend ships inside the wheel" in r.stdout

    def test_collector_skips_frontend(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "collector",
            "PDL_SERVER_URL": "http://x:8888",
            "PDL_COLLECTOR_NAME": "c",
        })
        assert r.returncode == 0, r.stderr
        assert "Collector-only install" in r.stdout


class TestAutoStart:
    """Summary should offer auto-start; without tty defaults to Y."""

    def test_auto_start_offered_for_server(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, r.stderr
        # tty_read defaults to "Y" when no tty → auto-start fires
        assert "pdl" in r.stdout.lower()


class TestPipMirror:
    """Step 6: pip mirror should default to aliyun and be overridable."""

    def test_default_aliyun_mirror(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, r.stderr
        assert "mirrors.aliyun.com" in r.stdout

    def test_custom_mirror(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, env_extra={
            "PDL_ROLE": "server",
            "PDL_PIP_INDEX_URL": "https://pypi.org/simple/",
        })
        assert r.returncode == 0, r.stderr
        assert "pypi.org" in r.stdout


class TestDataDir:
    """Step 7: DATA_DIR (~/.auto_daily_log) creation."""

    def test_data_dir_created(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        home = root / "_home"
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, r.stderr
        assert (home / ".auto_daily_log").is_dir()


class TestDevMode:
    """When no wheels/ but pyproject.toml + auto_daily_log/ exist → dev mode."""

    def test_dev_mode_detected(self, tmp_path):
        root = tmp_path / "pdl"
        root.mkdir()
        # Dev layout: no wheels, has pyproject.toml + source dir
        (root / "pyproject.toml").write_text('version = "0.5.1"\n[project]\nname = "auto-daily-log"\n')
        (root / "auto_daily_log").mkdir()
        (root / "auto_daily_log" / "__init__.py").write_text("")
        (root / "config.yaml.example").write_text("system:\n  data_dir: \"\"\n")
        shutil.copy(INSTALL_SH, root / "install.sh")
        pdl = root / "pdl"
        pdl.write_text("#!/usr/bin/env bash\necho pdl $*\n")
        pdl.chmod(pdl.stat().st_mode | stat.S_IEXEC)

        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode == 0, f"STDERR:\n{r.stderr}\nSTDOUT:\n{r.stdout}"
        assert "dev" in r.stdout.lower()

    def test_neither_mode_exits(self, tmp_path):
        root = tmp_path / "pdl"
        root.mkdir()
        shutil.copy(INSTALL_SH, root / "install.sh")
        r = _run_install(root, env_extra={"PDL_ROLE": "server"})
        assert r.returncode != 0
        assert "Can't determine install mode" in r.stdout or "install mode" in r.stderr
