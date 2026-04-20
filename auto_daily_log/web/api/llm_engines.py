"""CRUD API for LLM engine configurations."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...summarizer.url_helper import normalize_base_url

router = APIRouter(tags=["llm-engines"])

VALID_PROTOCOLS = {"openai_compat", "anthropic", "ollama"}


class EngineCreate(BaseModel):
    name: str
    display_name: str
    protocol: str = "openai_compat"
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    is_default: bool = False


class EngineUpdate(BaseModel):
    display_name: Optional[str] = None
    protocol: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    is_default: Optional[bool] = None
    enabled: Optional[bool] = None


@router.get("/llm-engines")
async def list_engines(request: Request):
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT name, display_name, protocol, api_key, model, base_url, is_default, enabled, created_at "
        "FROM llm_engines ORDER BY is_default DESC, name"
    )
    result = []
    for r in rows:
        d = dict(r)
        key = d.pop("api_key", "") or ""
        d["_has_key"] = bool(key)
        d["_key_hint"] = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else ("****" if key else "")
        result.append(d)
    return result


@router.post("/llm-engines", status_code=201)
async def create_engine(body: EngineCreate, request: Request):
    db = request.app.state.db
    if body.protocol not in VALID_PROTOCOLS:
        raise HTTPException(400, f"protocol 必须是 {VALID_PROTOCOLS} 之一")
    existing = await db.fetch_one("SELECT name FROM llm_engines WHERE name = ?", (body.name,))
    if existing:
        raise HTTPException(409, f"引擎 '{body.name}' 已存在")
    if body.is_default:
        await db.execute("UPDATE llm_engines SET is_default = 0")
    base_url = normalize_base_url(body.base_url, engine=body.protocol)
    await db.execute(
        "INSERT INTO llm_engines (name, display_name, protocol, api_key, model, base_url, is_default) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (body.name, body.display_name, body.protocol, body.api_key,
         body.model, base_url, 1 if body.is_default else 0),
    )
    return {"name": body.name, "status": "created"}


@router.put("/llm-engines/{name}")
async def update_engine(name: str, body: EngineUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM llm_engines WHERE name = ?", (name,))
    if not existing:
        raise HTTPException(404, f"引擎 '{name}' 不存在")
    updates: list[str] = []
    params: list = []
    if body.display_name is not None:
        updates.append("display_name = ?"); params.append(body.display_name)
    if body.protocol is not None:
        if body.protocol not in VALID_PROTOCOLS:
            raise HTTPException(400, f"protocol 必须是 {VALID_PROTOCOLS} 之一")
        updates.append("protocol = ?"); params.append(body.protocol)
    if body.api_key is not None:
        updates.append("api_key = ?"); params.append(body.api_key)
    if body.model is not None:
        updates.append("model = ?"); params.append(body.model)
    if body.base_url is not None:
        protocol_hint = body.protocol or existing["protocol"]
        updates.append("base_url = ?")
        params.append(normalize_base_url(body.base_url, engine=protocol_hint))
    if body.is_default is not None:
        if body.is_default:
            await db.execute("UPDATE llm_engines SET is_default = 0")
        updates.append("is_default = ?"); params.append(1 if body.is_default else 0)
    if body.enabled is not None:
        updates.append("enabled = ?"); params.append(1 if body.enabled else 0)
    if not updates:
        raise HTTPException(400, "没有要更新的字段")
    params.append(name)
    await db.execute(f"UPDATE llm_engines SET {', '.join(updates)} WHERE name = ?", tuple(params))
    return {"name": name, "status": "updated"}


@router.delete("/llm-engines/{name}")
async def delete_engine(name: str, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM llm_engines WHERE name = ?", (name,))
    if not existing:
        raise HTTPException(404, f"引擎 '{name}' 不存在")
    if existing["is_default"]:
        raise HTTPException(403, "不能删除默认引擎，请先设置其他引擎为默认")
    await db.execute("DELETE FROM llm_engines WHERE name = ?", (name,))
    return {"name": name, "status": "deleted"}


@router.get("/llm-engines/export")
async def export_engines(request: Request):
    """Export all engines as JSON (includes full api_key for backup/migration)."""
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT name, display_name, protocol, api_key, model, base_url, is_default, enabled "
        "FROM llm_engines ORDER BY is_default DESC, name"
    )
    return [dict(r) for r in rows]


@router.post("/llm-engines/import")
async def import_engines(request: Request):
    """Import engines from JSON array. Existing engines (by name) are updated."""
    db = request.app.state.db
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(400, "请求体必须是 JSON 数组")
    imported = 0
    for item in body:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = item["name"]
        protocol = item.get("protocol", "openai_compat")
        if protocol not in VALID_PROTOCOLS:
            protocol = "openai_compat"
        display_name = item.get("display_name", name)
        api_key = item.get("api_key", "")
        model = item.get("model", "")
        base_url = normalize_base_url(item.get("base_url", ""), engine=protocol)
        is_default = 1 if item.get("is_default") else 0
        enabled = 1 if item.get("enabled", True) else 0

        existing = await db.fetch_one("SELECT name FROM llm_engines WHERE name = ?", (name,))
        if existing:
            updates = ["display_name = ?", "protocol = ?", "model = ?", "base_url = ?", "enabled = ?"]
            params = [display_name, protocol, model, base_url, enabled]
            if api_key:
                updates.append("api_key = ?")
                params.append(api_key)
            if is_default:
                await db.execute("UPDATE llm_engines SET is_default = 0")
                updates.append("is_default = 1")
            params.append(name)
            await db.execute(
                f"UPDATE llm_engines SET {', '.join(updates)} WHERE name = ?",
                tuple(params),
            )
        else:
            if is_default:
                await db.execute("UPDATE llm_engines SET is_default = 0")
            await db.execute(
                "INSERT INTO llm_engines (name, display_name, protocol, api_key, model, base_url, is_default, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, display_name, protocol, api_key, model, base_url, is_default, enabled),
            )
        imported += 1
    return {"imported": imported}


@router.post("/llm-engines/{name}/check")
async def check_engine(name: str, request: Request):
    """Quick health check — try to call the engine with a trivial prompt."""
    db = request.app.state.db
    from ...summarizer.engine_registry import get_engine_by_name
    engine = await get_engine_by_name(db, name)
    if not engine:
        raise HTTPException(400, f"引擎 '{name}' 不可用（未找到或无 API key）")
    try:
        result = await engine.generate("回复 OK 两个字母即可。")
        return {"status": "ok", "response": result.strip()[:100]}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}
