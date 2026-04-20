import pytest
import httpx
from auto_daily_log.jira_client.client import JiraClient
from auto_daily_log.config import JiraConfig

@pytest.fixture
def jira_client():
    config = JiraConfig(server_url="https://jira.example.com", username="testuser", pat="test-token", auth_mode="pat")
    return JiraClient(config)


@pytest.fixture
def jira_client_cookie():
    config = JiraConfig(server_url="https://jira.example.com", auth_mode="cookie", cookie="JSESSIONID=abc")
    return JiraClient(config)

def test_build_worklog_payload(jira_client):
    payload = jira_client._build_worklog_payload(
        time_spent_sec=3600, comment="Did some work", started="2026-04-12T09:00:00.000+0800",
    )
    assert payload["timeSpentSeconds"] == 3600
    assert payload["comment"] == "Did some work"
    assert payload["started"] == "2026-04-12T09:00:00.000+0800"

def test_build_auth_headers_pat(jira_client):
    import base64
    headers = jira_client._headers()
    expected = "Basic " + base64.b64encode(b"testuser:test-token").decode()
    assert headers["Authorization"] == expected
    assert headers["Content-Type"] == "application/json"


def test_build_auth_headers_cookie(jira_client_cookie):
    headers = jira_client_cookie._headers()
    assert headers["Cookie"] == "JSESSIONID=abc"
    assert headers["X-Atlassian-Token"] == "no-check"
    assert "Authorization" not in headers

@pytest.mark.asyncio
async def test_fetch_issue_info(jira_client, httpx_mock):
    httpx_mock.add_response(
        url="https://jira.example.com/rest/api/2/issue/PROJ-101?fields=summary,description",
        json={"key": "PROJ-101", "fields": {"summary": "Fix SQL parser", "description": "Fix JOIN handling"}},
    )
    info = await jira_client.fetch_issue("PROJ-101")
    assert info["key"] == "PROJ-101"
    assert info["summary"] == "Fix SQL parser"

@pytest.mark.asyncio
async def test_submit_worklog(jira_client, httpx_mock):
    httpx_mock.add_response(
        url="https://jira.example.com/rest/api/2/issue/PROJ-101/worklog",
        json={"id": "12345"}, status_code=201,
    )
    result = await jira_client.submit_worklog(
        issue_key="PROJ-101", time_spent_sec=3600, comment="Fixed bug", started="2026-04-12T09:00:00.000+0800",
    )
    assert result["id"] == "12345"
