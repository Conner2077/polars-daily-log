from typing import Optional
import base64
import httpx
from ..config import JiraConfig


def _strip_4byte(s: str) -> str:
    """Remove characters outside BMP (emoji, supplementary CJK).

    Some Jira instances (notably fanruan.com's) use MySQL collations that
    are not utf8mb4; 4-byte UTF-8 characters cause the worklog POST to
    return HTTP 500 with `内部服务器错误`. Strip proactively on every submit.
    """
    if not s:
        return s
    return "".join(c for c in s if ord(c) <= 0xFFFF)


class MissingJiraConfig(Exception):
    """Raised when Jira settings are incomplete. Callers translate to HTTP."""


async def build_jira_client_from_db(db) -> "JiraClient":
    """Single source of truth for constructing a JiraClient from the settings table.

    Reused by worklogs API, scheduler auto-submit, and feedback.
    Raises MissingJiraConfig with a human message if anything required is blank.
    """
    jira_url = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_server_url'") or {}).get("value", "")
    username = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_username'") or {}).get("value", "")
    pat = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_pat'") or {}).get("value", "")
    cookie = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_cookie'") or {}).get("value", "")
    auth_mode = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_auth_mode'") or {}).get("value", "cookie")

    # Legacy compat: "bearer" → "pat"
    if auth_mode == "bearer":
        auth_mode = "pat"

    if not jira_url:
        raise MissingJiraConfig("Jira Server URL 未配置，先到 Settings → Jira 填")
    if auth_mode == "cookie" and not cookie:
        raise MissingJiraConfig("Jira 未登录，先到 Settings → Jira 登录")
    if auth_mode == "pat" and not pat:
        raise MissingJiraConfig("Jira PAT 未配置，先到 Settings → Jira 填")
    if auth_mode == "pat" and not username:
        raise MissingJiraConfig("Jira 用户名未配置，先到 Settings → Jira 填")

    return JiraClient(JiraConfig(server_url=jira_url, username=username, pat=pat, auth_mode=auth_mode, cookie=cookie))


class JiraClient:
    def __init__(self, config: JiraConfig):
        self._config = config

    def _headers(self) -> dict:
        if self._config.auth_mode == "cookie":
            return {
                "Cookie": self._config.cookie,
                "Content-Type": "application/json",
                "X-Atlassian-Token": "no-check",
            }
        # PAT mode: Jira Server/Data Center uses Basic Auth (username:PAT)
        credentials = base64.b64encode(
            f"{self._config.username}:{self._config.pat}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = self._config.server_url.rstrip("/")
        return f"{base}{path}"

    def _build_worklog_payload(self, time_spent_sec: int, comment: str, started: str) -> dict:
        # Defense in depth: strip 4-byte UTF-8 here so every submit path
        # (manual, auto-approve, feedback) is safe against the emoji → 500 bug.
        return {"timeSpentSeconds": time_spent_sec, "started": started, "comment": _strip_4byte(comment)}

    async def fetch_issue(self, issue_key: str) -> dict:
        url = self._url(f"/rest/api/2/issue/{issue_key}?fields=summary,description")
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            return {"key": data["key"], "summary": data["fields"].get("summary", ""), "description": data["fields"].get("description", "")}

    async def submit_worklog(self, issue_key: str, time_spent_sec: int, comment: str, started: str) -> dict:
        url = self._url(f"/rest/api/2/issue/{issue_key}/worklog")
        payload = self._build_worklog_payload(time_spent_sec, comment, started)
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            if response.status_code >= 400:
                # Surface Jira's actual message instead of generic httpx text.
                body = response.text[:400]
                raise Exception(f"Jira {response.status_code} on {issue_key}/worklog: {body}")
            return response.json()

    async def test_connection(self) -> bool:
        try:
            url = self._url("/rest/api/2/myself")
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(url, headers=self._headers())
                return response.status_code == 200
        except Exception:
            return False
