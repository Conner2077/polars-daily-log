import pytest

@pytest.mark.asyncio
async def test_list_issues_empty(app_client):
    response = await app_client.get("/api/issues")
    assert response.status_code == 200
    assert response.json() == []

@pytest.mark.asyncio
async def test_add_issue(app_client):
    response = await app_client.post("/api/issues", json={"issue_key": "PROJ-101", "summary": "Fix bug", "description": "Fix it"})
    assert response.status_code == 201
    assert response.json()["issue_key"] == "PROJ-101"
    response = await app_client.get("/api/issues")
    assert len(response.json()) == 1

@pytest.mark.asyncio
async def test_toggle_issue_active(app_client):
    await app_client.post("/api/issues", json={"issue_key": "PROJ-102", "summary": "Task", "description": ""})
    response = await app_client.patch("/api/issues/PROJ-102", json={"is_active": False})
    assert response.status_code == 200
    response = await app_client.get("/api/issues")
    issue = [i for i in response.json() if i["issue_key"] == "PROJ-102"][0]
    assert issue["is_active"] is False

@pytest.mark.asyncio
async def test_delete_issue(app_client):
    await app_client.post("/api/issues", json={"issue_key": "PROJ-103", "summary": "Delete me", "description": ""})
    response = await app_client.delete("/api/issues/PROJ-103")
    assert response.status_code == 200
    response = await app_client.get("/api/issues")
    assert len(response.json()) == 0
