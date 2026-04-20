"""Tests for /api/llm-engines CRUD — focus on base_url normalization parity with /api/settings."""
import pytest


async def _get(client, name):
    resp = await client.get("/api/llm-engines")
    assert resp.status_code == 200
    engines = [e for e in resp.json() if e["name"] == name]
    assert len(engines) == 1
    return engines[0]


@pytest.mark.asyncio
async def test_create_openai_compat_bare_url_preserved(app_client):
    """Bare hosts stay as-is — UI warns instead of mutating user input."""
    r = await app_client.post("/api/llm-engines", json={
        "name": "local-gw",
        "display_name": "Local Gateway",
        "protocol": "openai_compat",
        "api_key": "sk-test",
        "model": "gpt-4",
        "base_url": "http://localhost:3001",
    })
    assert r.status_code == 201
    engine = await _get(app_client, "local-gw")
    assert engine["base_url"] == "http://localhost:3001"


@pytest.mark.asyncio
async def test_create_openai_compat_full_endpoint_stripped(app_client):
    r = await app_client.post("/api/llm-engines", json={
        "name": "moonshot",
        "display_name": "Moonshot",
        "protocol": "openai_compat",
        "api_key": "sk-test",
        "model": "moonshot-v1-8k",
        "base_url": "https://api.moonshot.cn/v1/chat/completions",
    })
    assert r.status_code == 201
    engine = await _get(app_client, "moonshot")
    assert engine["base_url"] == "https://api.moonshot.cn/v1"


@pytest.mark.asyncio
async def test_create_openai_compat_custom_proxy_path_preserved(app_client):
    r = await app_client.post("/api/llm-engines", json={
        "name": "proxy",
        "display_name": "Proxy",
        "protocol": "openai_compat",
        "api_key": "sk-test",
        "model": "gpt-4",
        "base_url": "https://proxy.example.com/openai-proxy",
    })
    assert r.status_code == 201
    engine = await _get(app_client, "proxy")
    assert engine["base_url"] == "https://proxy.example.com/openai-proxy"


@pytest.mark.asyncio
async def test_create_anthropic_strips_v1(app_client):
    r = await app_client.post("/api/llm-engines", json={
        "name": "claude",
        "display_name": "Claude",
        "protocol": "anthropic",
        "api_key": "sk-test",
        "model": "claude-sonnet-4-20250514",
        "base_url": "https://api.anthropic.com/v1",
    })
    assert r.status_code == 201
    engine = await _get(app_client, "claude")
    assert engine["base_url"] == "https://api.anthropic.com"


@pytest.mark.asyncio
async def test_create_ollama_bare_host_stays_bare(app_client):
    r = await app_client.post("/api/llm-engines", json={
        "name": "ollama",
        "display_name": "Ollama",
        "protocol": "ollama",
        "api_key": "none",
        "model": "llama3",
        "base_url": "http://localhost:11434",
    })
    assert r.status_code == 201
    engine = await _get(app_client, "ollama")
    assert engine["base_url"] == "http://localhost:11434"


@pytest.mark.asyncio
async def test_update_normalizes_base_url(app_client):
    """Update strips trailing endpoints; bare hosts stay bare."""
    create = await app_client.post("/api/llm-engines", json={
        "name": "upd-test",
        "display_name": "Update Test",
        "protocol": "openai_compat",
        "api_key": "sk-test",
        "model": "gpt-4",
        "base_url": "https://api.moonshot.cn/v1",
    })
    assert create.status_code == 201

    upd = await app_client.put("/api/llm-engines/upd-test", json={
        "base_url": "https://api.moonshot.cn/v1/chat/completions",
    })
    assert upd.status_code == 200

    engine = await _get(app_client, "upd-test")
    assert engine["base_url"] == "https://api.moonshot.cn/v1"


@pytest.mark.asyncio
async def test_update_with_protocol_change_uses_new_protocol_for_normalization(app_client):
    create = await app_client.post("/api/llm-engines", json={
        "name": "switch",
        "display_name": "Switch",
        "protocol": "openai_compat",
        "api_key": "sk-test",
        "model": "gpt-4",
        "base_url": "https://api.openai.com/v1",
    })
    assert create.status_code == 201

    upd = await app_client.put("/api/llm-engines/switch", json={
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
    })
    assert upd.status_code == 200

    engine = await _get(app_client, "switch")
    assert engine["base_url"] == "https://api.anthropic.com"
    assert engine["protocol"] == "anthropic"


@pytest.mark.asyncio
async def test_export_includes_full_api_key(app_client):
    await app_client.post("/api/llm-engines", json={
        "name": "exp1", "display_name": "Export Test",
        "protocol": "openai_compat", "api_key": "sk-full-secret-key",
        "model": "gpt-4", "base_url": "https://api.example.com",
    })
    r = await app_client.get("/api/llm-engines/export")
    assert r.status_code == 200
    engines = r.json()
    match = [e for e in engines if e["name"] == "exp1"]
    assert len(match) == 1
    assert match[0]["api_key"] == "sk-full-secret-key"


@pytest.mark.asyncio
async def test_import_creates_and_updates(app_client):
    # Create one engine first
    await app_client.post("/api/llm-engines", json={
        "name": "imp-existing", "display_name": "Old Name",
        "protocol": "openai_compat", "api_key": "sk-old",
        "model": "old-model", "base_url": "https://old.example.com",
    })
    # Import: update existing + create new
    payload = [
        {"name": "imp-existing", "display_name": "Updated Name", "protocol": "anthropic",
         "api_key": "sk-new", "model": "new-model", "base_url": "https://new.example.com"},
        {"name": "imp-new", "display_name": "Brand New", "protocol": "ollama",
         "api_key": "none", "model": "llama3", "base_url": "http://localhost:11434"},
    ]
    r = await app_client.post("/api/llm-engines/import", json=payload)
    assert r.status_code == 200
    assert r.json()["imported"] == 2

    updated = await _get(app_client, "imp-existing")
    assert updated["display_name"] == "Updated Name"
    assert updated["protocol"] == "anthropic"

    created = await _get(app_client, "imp-new")
    assert created["display_name"] == "Brand New"
    assert created["protocol"] == "ollama"


@pytest.mark.asyncio
async def test_export_import_roundtrip(app_client):
    """Export then import should be idempotent."""
    await app_client.post("/api/llm-engines", json={
        "name": "rt1", "display_name": "Roundtrip", "is_default": True,
        "protocol": "openai_compat", "api_key": "sk-rt", "model": "m", "base_url": "https://rt.example.com",
    })
    export_r = await app_client.get("/api/llm-engines/export")
    exported = export_r.json()

    import_r = await app_client.post("/api/llm-engines/import", json=exported)
    assert import_r.status_code == 200

    re_export_r = await app_client.get("/api/llm-engines/export")
    rt1_before = [e for e in exported if e["name"] == "rt1"][0]
    rt1_after = [e for e in re_export_r.json() if e["name"] == "rt1"][0]
    assert rt1_before["api_key"] == rt1_after["api_key"]
    assert rt1_before["model"] == rt1_after["model"]
