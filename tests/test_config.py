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
