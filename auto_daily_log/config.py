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
