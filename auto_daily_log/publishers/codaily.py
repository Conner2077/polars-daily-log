"""CoDailyPublisher — push a PDL summary to CoDaily (日报广场) as a post.

CoDaily push-contract v1.0: POST /api/v1/push with Bearer token.
See: https://codaily.fanruan.com (docs/push-contract-v1.md in the CoDaily repo).

publisher_config shape:
  {
    "url":   "https://codaily.fanruan.com",
    "token": "<pdl-publisher token from CoDaily Settings>",
    "scope": "day"                               // optional, default "day"
  }

Adapter mapping (WorklogPublisher.submit ↔ CoDaily push-contract):
  issue_key       → metadata.issue_keys[0]  (skipped for ALL/DAILY sentinels)
  time_spent_sec  → metadata.time_spent_sec + one entries[] row
  comment         → content (+ entries[0].summary truncated)
  started (ISO)   → post_date (first 10 chars)
  config.scope    → scope
"""
from __future__ import annotations

from typing import Optional

import httpx

from . import PublishResult


_SKIP_ISSUE_KEYS = {"", "ALL", "DAILY"}


class CoDailyPublisher:
    name = "codaily"
    display_name = "CoDaily（日报广场）"

    def __init__(self, config: dict) -> None:
        self._url: str = (config.get("url") or "").rstrip("/")
        self._token: str = config.get("token") or ""
        self._scope: str = config.get("scope") or "day"
        self._timeout: int = int(config.get("timeout") or 15)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _build_body(
        self, *, issue_key: str, time_spent_sec: int, comment: str, started: str
    ) -> dict:
        post_date = started[:10] if started else ""
        metadata: dict = {
            "schema_version": "1.0",
            "time_spent_sec": int(time_spent_sec or 0),
        }
        if issue_key and issue_key not in _SKIP_ISSUE_KEYS:
            hours = round((time_spent_sec or 0) / 3600, 2)
            metadata["issue_keys"] = [issue_key]
            metadata["entries"] = [
                {"issue_key": issue_key, "hours": hours, "summary": comment[:200]},
            ]
        return {
            "post_date": post_date,
            "scope": self._scope,
            "content": comment or "",
            "content_type": "markdown",
            "metadata": metadata,
            "source": "pdl",
        }

    async def submit(
        self,
        *,
        issue_key: str,
        time_spent_sec: int,
        comment: str,
        started: str,
    ) -> PublishResult:
        if not self._url:
            return PublishResult(success=False, platform=self.name, error="CoDaily URL 未配置")
        if not self._token:
            return PublishResult(success=False, platform=self.name, error="CoDaily token 未配置")

        body = self._build_body(
            issue_key=issue_key,
            time_spent_sec=time_spent_sec,
            comment=comment,
            started=started,
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as http:
                r = await http.post(
                    f"{self._url}/api/v1/push",
                    json=body,
                    headers=self._headers(),
                )
                if r.status_code >= 400:
                    detail = _extract_detail(r)
                    return PublishResult(
                        success=False,
                        platform=self.name,
                        error=f"HTTP {r.status_code}: {detail}",
                        raw={"status_code": r.status_code, "body": r.text[:500]},
                    )
                try:
                    resp = r.json()
                    post_id = resp.get("id")
                except ValueError:
                    post_id = None
                return PublishResult(
                    success=True,
                    worklog_id=str(post_id) if post_id is not None else "",
                    platform=self.name,
                    raw={"status_code": r.status_code, "body": r.text[:500]},
                )
        except httpx.TimeoutException:
            return PublishResult(
                success=False, platform=self.name,
                error=f"CoDaily 超时 ({self._timeout}s)",
            )
        except Exception as exc:
            return PublishResult(
                success=False, platform=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def delete(self, worklog_id: str, *, issue_key: str) -> bool:
        if not self._url or not self._token or not worklog_id:
            return False
        try:
            async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as http:
                r = await http.delete(
                    f"{self._url}/api/v1/posts/{worklog_id}",
                    headers=self._headers(),
                )
                return r.status_code < 400
        except Exception:
            return False

    async def check_connection(self) -> bool:
        if not self._url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
                r = await http.get(f"{self._url}/health")
                return r.status_code < 400
        except Exception:
            return False


def _extract_detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body)[:200]
    except ValueError:
        pass
    return r.text[:200]
