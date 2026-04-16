"""Check GitHub Releases for the latest published version.

The result is cached for 24h so the Web UI can read it cheaply on every
page load. Network is only touched when the cache is missing or stale.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Optional

import httpx
from packaging.version import InvalidVersion, Version

from .. import __version__
from . import GITHUB_REPO, WHEEL_NAME_TEMPLATE
from .paths import update_check_path

CACHE_TTL_SEC = 24 * 60 * 60
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


@dataclass
class UpdateCheck:
    current: str
    latest: str
    available: bool
    wheel_url: str
    release_url: str
    notes: str
    checked_at: float

    def to_dict(self) -> dict:
        return asdict(self)


def _parse(version: str) -> Optional[Version]:
    try:
        return Version(version.lstrip("v"))
    except InvalidVersion:
        return None


def _is_newer(latest: str, current: str) -> bool:
    lv, cv = _parse(latest), _parse(current)
    if lv is None or cv is None:
        return False
    return lv > cv


def _read_cache() -> Optional[UpdateCheck]:
    path = update_check_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("checked_at", 0) > CACHE_TTL_SEC:
            return None
        return UpdateCheck(**data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _write_cache(check: UpdateCheck) -> None:
    update_check_path().write_text(
        json.dumps(check.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _wheel_asset_url(release: dict, version: str) -> str:
    """Find the wheel asset URL in a GitHub Releases payload.

    Falls back to the conventional download URL if the asset list is
    missing (some old releases didn't attach the wheel)."""
    expected = WHEEL_NAME_TEMPLATE.format(version=version)
    for asset in release.get("assets", []) or []:
        if asset.get("name") == expected:
            return asset.get("browser_download_url", "")
    return (
        f"https://github.com/{GITHUB_REPO}/releases/download/"
        f"v{version}/{expected}"
    )


def check(*, force: bool = False, current: Optional[str] = None) -> UpdateCheck:
    """Return the latest update info, hitting cache unless ``force=True``."""
    if not force:
        cached = _read_cache()
        if cached is not None:
            return cached

    cur = current or __version__
    try:
        resp = httpx.get(GITHUB_API, timeout=5.0, follow_redirects=True)
        resp.raise_for_status()
        release = resp.json()
        tag = (release.get("tag_name") or "").lstrip("v")
        result = UpdateCheck(
            current=cur,
            latest=tag,
            available=_is_newer(tag, cur),
            wheel_url=_wheel_asset_url(release, tag) if tag else "",
            release_url=release.get("html_url", ""),
            notes=release.get("body", "") or "",
            checked_at=time.time(),
        )
    except (httpx.HTTPError, ValueError) as exc:
        # Offline / rate-limited / parse failure — return a "no update"
        # placeholder so the UI doesn't crash. Don't cache failures.
        return UpdateCheck(
            current=cur,
            latest=cur,
            available=False,
            wheel_url="",
            release_url="",
            notes=f"(check failed: {exc})",
            checked_at=time.time(),
        )

    _write_cache(result)
    return result
