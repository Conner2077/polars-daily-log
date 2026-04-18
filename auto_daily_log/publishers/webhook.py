"""WebhookPublisher — POST worklog data to any HTTP endpoint.

Covers generic webhooks, 企业微信群机器人, 飞书机器人, Slack incoming
webhooks, etc. The ``publisher_config`` JSON controls the target URL,
optional extra headers, timeout, and body format.

publisher_config shape:
  {
    "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
    "format": "generic" | "wecom" | "feishu" | "slack",
    "headers": {"X-Custom": "value"},   // optional
    "timeout": 10                        // optional, default 10s
  }

Body formats:
  generic  → raw JSON: {issue_key, time_spent_sec, comment, started}
  wecom    → {"msgtype":"text","text":{"content":"..."}}
  feishu   → {"msg_type":"text","content":{"text":"..."}}
  slack    → {"text":"..."}
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from . import PublishResult


class WebhookPublisher:
    name = "webhook"
    display_name = "Webhook"

    def __init__(self, config: dict) -> None:
        self._url: str = config.get("url", "")
        self._format: str = config.get("format", "generic")
        self._headers: dict = config.get("headers") or {}
        self._timeout: int = config.get("timeout", 10)

    def _build_body(
        self, *, issue_key: str, time_spent_sec: int, comment: str, started: str
    ) -> dict:
        hours = round(time_spent_sec / 3600, 1)
        if issue_key:
            text = f"[{issue_key}] {hours}h — {comment}"
        else:
            text = comment

        if self._format == "wecom":
            return {"msgtype": "markdown", "markdown": {"content": text}}
        if self._format == "feishu":
            return {"msg_type": "text", "content": {"text": text}}
        if self._format == "slack":
            return {"text": text}
        # generic
        return {
            "issue_key": issue_key,
            "time_spent_sec": time_spent_sec,
            "time_spent_hours": hours,
            "comment": comment,
            "started": started,
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
            return PublishResult(
                success=False, platform=self.name,
                error="webhook URL 未配置",
            )
        body = self._build_body(
            issue_key=issue_key, time_spent_sec=time_spent_sec,
            comment=comment, started=started,
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                headers = {"Content-Type": "application/json", **self._headers}
                r = await client.post(self._url, json=body, headers=headers)
                if r.status_code >= 400:
                    return PublishResult(
                        success=False, platform=self.name,
                        error=f"HTTP {r.status_code}: {r.text[:200]}",
                        raw={"status_code": r.status_code, "body": r.text[:500]},
                    )
                # WeChat Work / Feishu return HTTP 200 with errcode in body
                try:
                    resp = r.json()
                    errcode = resp.get("errcode") or resp.get("code")
                    if errcode and errcode != 0:
                        errmsg = resp.get("errmsg") or resp.get("msg") or r.text[:200]
                        return PublishResult(
                            success=False, platform=self.name,
                            error=f"errcode {errcode}: {errmsg}",
                            raw={"status_code": r.status_code, "body": r.text[:500]},
                        )
                except (ValueError, AttributeError):
                    pass  # non-JSON response, treat HTTP 2xx as success
                return PublishResult(
                    success=True, worklog_id=f"webhook-{r.status_code}",
                    platform=self.name,
                    raw={"status_code": r.status_code, "body": r.text[:500]},
                )
        except httpx.TimeoutException:
            return PublishResult(
                success=False, platform=self.name,
                error=f"webhook 超时 ({self._timeout}s)",
            )
        except Exception as exc:
            return PublishResult(
                success=False, platform=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def delete(self, worklog_id: str, *, issue_key: str) -> bool:
        # Webhooks are fire-and-forget; no delete concept.
        return False

    async def check_connection(self) -> bool:
        if not self._url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.head(self._url)
                return r.status_code < 500
        except Exception:
            return False
