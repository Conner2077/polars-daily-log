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
    enabled: bool = True  # disable for pure-server mode (no built-in collector)
    interval_sec: int = 30
    ocr_enabled: bool = True
    ocr_engine: str = "auto"
    screenshot_retention_days: int = 7
    privacy: PrivacyConfig = PrivacyConfig()
    phash_enabled: bool = True
    phash_threshold: int = 20
    idle_threshold_sec: int = 180
    # Apps that self-exit when probed via AppleScript / window title APIs.
    # These apps get their window_title read skipped; only app name is captured.
    hostile_apps_applescript: list[str] = [
        "wechat", "wecom", "企业微信", "微信", "wechatwork", "wechatappex",
    ]
    # Apps that self-exit when screen is captured. Default empty — empirically
    # macOS screencapture is global and does NOT trigger WeCom/WeChat self-exit
    # (only AppleScript `tell process` does). Populate this list only if you
    # encounter an app that truly reacts to screen capture.
    hostile_apps_screenshot: list[str] = []


class GitRepoConfig(BaseModel):
    path: str
    author_email: str = ""


class GitConfig(BaseModel):
    repos: list[GitRepoConfig] = []


class JiraConfig(BaseModel):
    server_url: str = ""
    pat: str = ""
    auth_mode: str = "cookie"  # "bearer" or "cookie"
    cookie: str = ""


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
    trigger_time: str = "21:30"


class SystemConfig(BaseModel):
    language: str = "zh"
    data_retention_days: int = 90
    data_dir: str = ""  # default: ~/.auto_daily_log
    activity_retention_days: int = 7    # active activities kept this many days
    recycle_retention_days: int = 30    # soft-deleted activities kept this many days

    @property
    def resolved_data_dir(self) -> Path:
        if self.data_dir:
            p = Path(self.data_dir).expanduser()
        else:
            p = Path.home() / ".auto_daily_log"
        p.mkdir(parents=True, exist_ok=True)
        return p


class EmbeddingConfig(BaseModel):
    enabled: bool = True
    model: str = ""
    dimensions: int = 1024


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    monitor: MonitorConfig = MonitorConfig()
    git: GitConfig = GitConfig()
    jira: JiraConfig = JiraConfig()
    llm: LLMConfig = LLMConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    auto_approve: AutoApproveConfig = AutoApproveConfig()
    system: SystemConfig = SystemConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()


def load_config(config_path: Optional[str]) -> AppConfig:
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return AppConfig(**data)
    return AppConfig()
