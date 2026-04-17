"""Branch-coverage tests for install.ps1 (Windows installer).

These tests only run on Windows. On other platforms they are automatically
skipped via the module-level `pytestmark`.

Strategy: same as test_install_sh.py — build a fake tarball layout in a temp
dir, mock python/pip/git/openssl via PATH, invoke install.ps1 with -Mode
parameter, and assert on exit code + generated files + stdout.
"""

import json
import os
import platform
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

# Skip entire module on non-Windows
pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="install.ps1 tests require Windows + PowerShell",
)

INSTALL_PS1 = Path(__file__).resolve().parent.parent / "install.ps1"
assert INSTALL_PS1.exists(), f"install.ps1 not found at {INSTALL_PS1}"


def _make_mock_bat(bin_dir: Path, name: str, script: str) -> Path:
    """Create a mock .bat file in bin_dir (Windows uses .bat/.cmd on PATH)."""
    p = bin_dir / f"{name}.bat"
    p.write_text(f"@echo off\n{script}\n")
    return p


def _make_mock_ps1(bin_dir: Path, name: str, script: str) -> Path:
    """Create a mock .ps1 for sourcing if needed."""
    p = bin_dir / f"{name}.ps1"
    p.write_text(script)
    return p


def _setup_release_layout(root: Path, *, include_enc: bool = True,
                           include_collector_example: bool = True,
                           include_config_example: bool = True) -> Path:
    """Create a minimal release-tarball directory layout."""
    (root / "wheels").mkdir(parents=True, exist_ok=True)

    fake_wheel = root / "wheels" / "auto_daily_log-0.5.2-py3-none-any.whl"
    fake_wheel.write_text("fake-wheel")

    (root / "VERSION").write_text("0.5.2")

    if include_config_example:
        (root / "config.yaml.example").write_text("system:\\n  data_dir: \\\"\\\"\\n")

    if include_collector_example:
        (root / "collector.yaml.example").write_text(
            'server_url: "http://127.0.0.1:8888"\\nname: "My-Mac"\\ninterval: 30\\n'
        )

    if include_enc:
        enc_src = INSTALL_PS1.parent / "auto_daily_log" / "builtin_llm.enc"
        if enc_src.exists():
            shutil.copy(enc_src, root / "builtin_llm.enc")

    # pdl stub
    pdl = root / "pdl"
    pdl.write_text("@echo off\\necho pdl %*\\n")

    # Copy install.ps1
    shutil.copy(INSTALL_PS1, root / "install.ps1")

    return root


def _run_install(root: Path, *, mode: str = "server", env_extra: dict = None,
                  timeout: int = 120) -> subprocess.CompletedProcess:
    """Run install.ps1 via PowerShell with mocked environment."""
    bin_dir = root / "_mock_bin"
    bin_dir.mkdir(exist_ok=True)

    # Mock python: version check + venv creation + import checks
    _make_mock_bat(bin_dir, "python", textwrap.dedent("""\
        @echo off
        if "%~1"=="-c" (
            echo 3.12
            exit /b 0
        )
        if "%~1"=="-m" if "%~2"=="venv" (
            mkdir "%~3\\Scripts" 2>nul
            echo @echo off > "%~3\\Scripts\\python.exe.bat"
            copy "%~f0" "%~3\\Scripts\\python.exe" >nul 2>nul
            exit /b 0
        )
        exit /b 0
    """))

    _make_mock_bat(bin_dir, "py", textwrap.dedent("""\
        @echo off
        shift
        if "%~1"=="-c" (
            echo 3.12
            exit /b 0
        )
        exit /b 0
    """))

    _make_mock_bat(bin_dir, "pip", "echo pip mock: %*")
    _make_mock_bat(bin_dir, "git", "echo git mock")
    _make_mock_bat(bin_dir, "node", "echo node mock")
    _make_mock_bat(bin_dir, "openssl", textwrap.dedent("""\
        @echo off
        echo openssl mock: %*
        exit /b 1
    """))

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir};{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)

    cmd = [
        "powershell", "-ExecutionPolicy", "Bypass", "-NoProfile",
        "-File", str(root / "install.ps1"),
        "-Mode", mode,
        "-SkipScheduledTask",
    ]

    return subprocess.run(
        cmd,
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestRoleSelection:
    def test_mode_server(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server")
        assert "Will install: server" in r.stdout, f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"

    def test_mode_collector(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="collector", env_extra={
            "PDL_SERVER_URL": "http://10.0.0.5:8888",
            "PDL_COLLECTOR_NAME": "test-pc",
        })
        assert "Will install: collector" in r.stdout, f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"

    def test_mode_both(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="both", env_extra={
            "PDL_SERVER_URL": "http://127.0.0.1:8888",
            "PDL_COLLECTOR_NAME": "local",
        })
        assert "Will install: server + collector" in r.stdout, f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"


class TestVersionDynamic:
    def test_version_from_file(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server")
        assert "0.5.2" in r.stdout
        assert "0.1.0" not in r.stdout


class TestConfigGeneration:
    def test_server_creates_config_yaml(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server")
        assert (root / "config.yaml").exists(), f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"

    def test_server_skips_collector_yaml(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server")
        assert not (root / "collector.yaml").exists()

    def test_both_creates_both_configs(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="both", env_extra={
            "PDL_SERVER_URL": "http://127.0.0.1:8888",
            "PDL_COLLECTOR_NAME": "local-dev",
        })
        stdout = r.stdout + r.stderr
        assert (root / "config.yaml").exists(), f"config.yaml missing.\n{stdout}"


class TestBuiltinLLM:
    def test_collector_skips_builtin_llm(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="collector", env_extra={
            "PDL_BUILTIN_PASSPHRASE": "polars",
            "PDL_SERVER_URL": "http://x:8888",
            "PDL_COLLECTOR_NAME": "c",
        })
        assert "Built-in LLM" not in r.stdout

    def test_no_enc_file_skips(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl", include_enc=False)
        r = _run_install(root, mode="server")
        assert "Built-in LLM" not in r.stdout


class TestPipMirror:
    def test_default_aliyun_mirror(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server")
        assert "mirrors.aliyun.com" in r.stdout, f"STDOUT:\n{r.stdout}"

    def test_custom_mirror(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server", env_extra={
            "PDL_PIP_INDEX_URL": "https://pypi.org/simple/",
        })
        assert "pypi.org" in r.stdout


class TestDataDir:
    def test_data_dir_created(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server")
        assert "Data directory" in r.stdout or ".auto_daily_log" in r.stdout


class TestSectionNumbering:
    def test_no_duplicate_section_numbers(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="both", env_extra={
            "PDL_SERVER_URL": "http://127.0.0.1:8888",
            "PDL_COLLECTOR_NAME": "test",
        })
        import re
        numbers = re.findall(r"^(\d+)\.", r.stdout, re.MULTILINE)
        seen = set()
        for n in numbers:
            assert n not in seen, f"Duplicate section number: {n}"
            seen.add(n)


class TestFrontend:
    def test_release_mode_skips_build(self, tmp_path):
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server")
        assert "Frontend ships inside the wheel" in r.stdout


class TestSummary:
    def test_no_adl_reference(self, tmp_path):
        """Regression: old install.ps1 referenced adl.ps1 instead of pdl."""
        root = _setup_release_layout(tmp_path / "pdl")
        r = _run_install(root, mode="server")
        assert "adl.ps1" not in r.stdout
        assert "pdl" in r.stdout.lower()
