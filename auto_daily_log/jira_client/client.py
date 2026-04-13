from typing import Optional
import httpx
from ..config import JiraConfig


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
        return {
            "Authorization": f"Bearer {self._config.pat}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = self._config.server_url.rstrip("/")
        return f"{base}{path}"

    def _build_worklog_payload(self, time_spent_sec: int, comment: str, started: str) -> dict:
        return {"timeSpentSeconds": time_spent_sec, "started": started, "comment": comment}

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
            response.raise_for_status()
            return response.json()

    async def test_connection(self) -> bool:
        try:
            url = self._url("/rest/api/2/myself")
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(url, headers=self._headers())
                return response.status_code == 200
        except Exception:
            return False
