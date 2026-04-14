"""Collector configuration.

Loaded from collector.yaml at startup. Credentials (machine_id + token)
are persisted to a separate credentials file so config.yaml can be
checked in without leaking secrets.
"""
import os
import socket
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from shared.schemas import (
    PLATFORM_LINUX_HEADLESS,
    PLATFORM_LINUX_WAYLAND,
    PLATFORM_LINUX_X11,
    PLATFORM_MACOS,
    PLATFORM_WINDOWS,
)


class CollectorConfig(BaseModel):
    # Server connection
    server_url: str = Field(..., description="Central server base URL, e.g. http://10.0.0.5:8080")
    name: str = Field(default_factory=lambda: socket.gethostname(), description="Display name in UI")

    # Sampling
    interval_sec: int = 30
    ocr_enabled: bool = False
    ocr_engine: str = "auto"
    screenshot_retention_days: int = 7
    idle_threshold_sec: int = 180
    phash_enabled: bool = True
    phash_threshold: int = 20

    # Privacy
    blocked_apps: list[str] = Field(default_factory=list)
    blocked_urls: list[str] = Field(default_factory=list)

    # Data dir (for offline queue + local screenshots)
    data_dir: str = ""  # default: ~/.auto_daily_log_collector

    # Git collection (optional; each collector has its own repo list)
    git_repos: list[dict] = Field(default_factory=list)

    @property
    def resolved_data_dir(self) -> Path:
        if self.data_dir:
            p = Path(self.data_dir).expanduser()
        else:
            p = Path.home() / ".auto_daily_log_collector"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def credentials_file(self) -> Path:
        return self.resolved_data_dir / "credentials.json"


def detect_platform_id() -> str:
    """Return one of PLATFORM_* constants based on current OS + session."""
    import platform
    system = platform.system().lower()
    if system == "darwin":
        return PLATFORM_MACOS
    if system == "windows":
        return PLATFORM_WINDOWS
    # Linux — distinguish by session type
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland":
        return PLATFORM_LINUX_WAYLAND
    if os.environ.get("DISPLAY"):
        return PLATFORM_LINUX_X11
    return PLATFORM_LINUX_HEADLESS


def load_config(path: Optional[str]) -> CollectorConfig:
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return CollectorConfig(**data)
    raise FileNotFoundError(f"Collector config not found: {path}")
