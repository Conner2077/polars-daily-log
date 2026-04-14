"""Shared Pydantic schemas for server/collector communication.

These schemas are the wire contract — server and collector must both
validate against them so changes stay backward compatible.
"""
from typing import Optional

from pydantic import BaseModel, Field


# ─── Platform Capabilities ───────────────────────────────────────────
# Enumerated set of things a collector can do. Server uses these to
# filter UI (e.g. hide "view screenshot" button for collectors without
# the "screenshot" capability).
CAPABILITY_SCREENSHOT = "screenshot"
CAPABILITY_OCR = "ocr"
CAPABILITY_IDLE = "idle"
CAPABILITY_BROWSER_TAB = "browser_tab"
CAPABILITY_WINDOW_TITLE = "window_title"
CAPABILITY_GIT = "git"
ALL_CAPABILITIES = {
    CAPABILITY_SCREENSHOT,
    CAPABILITY_OCR,
    CAPABILITY_IDLE,
    CAPABILITY_BROWSER_TAB,
    CAPABILITY_WINDOW_TITLE,
    CAPABILITY_GIT,
}


# ─── Platform IDs ────────────────────────────────────────────────────
PLATFORM_MACOS = "macos"
PLATFORM_WINDOWS = "windows"
PLATFORM_LINUX_X11 = "linux-x11"
PLATFORM_LINUX_WAYLAND = "linux-wayland"
PLATFORM_LINUX_HEADLESS = "linux-headless"


# ─── Collector registration ──────────────────────────────────────────
class CollectorRegisterRequest(BaseModel):
    """Sent by collector on first startup to register with server."""
    name: str = Field(..., min_length=1, max_length=64, description="User-friendly machine name")
    hostname: str = Field(..., min_length=1, max_length=255)
    platform: str = Field(..., description="One of PLATFORM_* constants")
    platform_detail: Optional[str] = Field(None, description="e.g. 'Ubuntu 22.04', 'macOS 14.2'")
    capabilities: list[str] = Field(default_factory=list, description="Subset of ALL_CAPABILITIES")


class CollectorRegisterResponse(BaseModel):
    """Server returns machine_id + token; collector stores both."""
    machine_id: str = Field(..., description="Server-assigned UUID")
    token: str = Field(..., min_length=32, description="Bearer token for future requests")


class CollectorInfo(BaseModel):
    """Read model used by server UI."""
    id: int
    machine_id: str
    name: str
    hostname: Optional[str]
    platform: Optional[str]
    platform_detail: Optional[str]
    capabilities: list[str]
    created_at: Optional[str]
    last_seen: Optional[str]
    is_active: bool
    is_paused: bool = False
    config_override: Optional[dict] = None


# ─── Activity ingestion ──────────────────────────────────────────────
class ActivityPayload(BaseModel):
    """Single activity record pushed by collector."""
    timestamp: str = Field(..., description="ISO 8601 with local TZ; stored as-is")
    app_name: Optional[str] = None
    window_title: Optional[str] = None
    category: Optional[str] = None
    confidence: Optional[float] = None
    url: Optional[str] = None
    signals: Optional[str] = Field(None, description="JSON string of extra signals (OCR text, screenshot path, etc.)")
    duration_sec: int = 0


class ActivityIngestRequest(BaseModel):
    activities: list[ActivityPayload] = Field(..., min_length=1, max_length=500)


class ActivityIngestResponse(BaseModel):
    accepted: int
    rejected: int = 0
    first_id: Optional[int] = None
    last_id: Optional[int] = None


# ─── Git commit ingestion ────────────────────────────────────────────
class CommitPayload(BaseModel):
    hash: str = Field(..., min_length=7, max_length=64)
    message: Optional[str] = None
    author: Optional[str] = None
    committed_at: Optional[str] = None
    files_changed: Optional[str] = Field(None, description="JSON string of file list")
    insertions: int = 0
    deletions: int = 0
    date: Optional[str] = Field(None, description="YYYY-MM-DD derived from committed_at")
    repo_path: Optional[str] = None


class CommitIngestRequest(BaseModel):
    commits: list[CommitPayload] = Field(..., min_length=1, max_length=500)


class CommitIngestResponse(BaseModel):
    accepted: int
    duplicates: int = 0


# ─── Heartbeat ───────────────────────────────────────────────────────
class HeartbeatRequest(BaseModel):
    collector_version: Optional[str] = None
    queue_size: int = 0  # local offline queue depth, informational


class HeartbeatResponse(BaseModel):
    server_time: str
    config_override: Optional[dict] = None
    is_paused: bool = False


class ConfigOverridePayload(BaseModel):
    """Server pushes partial config updates to a collector.

    Unknown keys are accepted (forward compat). Known keys that
    collectors honor in Phase 3:
      - interval_sec: int
      - ocr_enabled: bool
      - blocked_apps: list[str]
      - blocked_urls: list[str]
    """
    interval_sec: Optional[int] = None
    ocr_enabled: Optional[bool] = None
    blocked_apps: Optional[list[str]] = None
    blocked_urls: Optional[list[str]] = None
