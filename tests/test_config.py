import pytest
from pathlib import Path
from auto_daily_log.config import load_config, AppConfig, resolve_db_path


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
  engine: anthropic
""")
    config = load_config(str(config_file))
    assert config.server.port == 9090
    assert config.monitor.interval_sec == 60
    assert config.monitor.ocr_enabled is False
    assert config.llm.engine == "anthropic"


def test_load_config_with_defaults():
    config = load_config(None)
    assert config.server.port == 8888
    assert config.monitor.interval_sec == 30
    assert config.monitor.ocr_enabled is True
    assert config.llm.engine == "openai_compat"
    assert config.scheduler.trigger_time == "18:00"
    assert config.auto_approve.enabled is True
    assert config.auto_approve.trigger_time == "21:30"


def test_resolve_db_path_explicit_override(tmp_path):
    custom = tmp_path / "elsewhere" / "custom.db"
    assert resolve_db_path(str(custom)) == custom


def test_resolve_db_path_reads_data_dir_from_config(tmp_path, monkeypatch):
    data_dir = tmp_path / "my_data"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"system:\n  data_dir: {data_dir}\n")
    monkeypatch.setenv("PDL_SERVER_CONFIG", str(config_file))

    assert resolve_db_path() == data_dir / "data.db"


def test_resolve_db_path_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("PDL_SERVER_CONFIG", raising=False)
    assert resolve_db_path() == Path.home() / ".auto_daily_log" / "data.db"
