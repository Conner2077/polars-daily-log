import pytest

@pytest.mark.asyncio
async def test_get_settings(app_client):
    response = await app_client.get("/api/settings")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

@pytest.mark.asyncio
async def test_put_setting(app_client):
    response = await app_client.put("/api/settings/monitor.interval_sec", json={"value": "60"})
    assert response.status_code == 200
    response = await app_client.get("/api/settings")
    found = [s for s in response.json() if s["key"] == "monitor.interval_sec"]
    assert len(found) == 1
    assert found[0]["value"] == "60"
