"""CRUD API for summary type configuration.

Built-in types (daily/weekly/monthly) can be edited but not deleted.
User-created types support full CRUD.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["summary-types"])

VALID_SCOPE_TYPES = {"day", "week", "month", "custom_days", "issue_based"}
VALID_REVIEW_MODES = {"auto", "manual"}


class SummaryTypeCreate(BaseModel):
    name: str
    display_name: str
    scope_rule: str = '{"type":"day"}'
    schedule_rule: Optional[str] = None
    prompt_key: str = "summarize"
    prompt_template: Optional[str] = None    # per-type custom prompt; NULL = use global
    review_mode: str = "manual"
    publisher_name: Optional[str] = None
    publisher_config: str = "{}"


class SummaryTypeUpdate(BaseModel):
    display_name: Optional[str] = None
    scope_rule: Optional[str] = None
    schedule_rule: Optional[str] = None
    prompt_key: Optional[str] = None
    prompt_template: Optional[str] = None
    review_mode: Optional[str] = None
    publisher_name: Optional[str] = None
    publisher_config: Optional[str] = None
    enabled: Optional[bool] = None


def _validate_scope_rule(raw: str) -> None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(400, f"scope_rule 不是合法 JSON: {raw}")
    if not isinstance(obj, dict) or obj.get("type") not in VALID_SCOPE_TYPES:
        raise HTTPException(400, f"scope_rule.type 必须是 {VALID_SCOPE_TYPES} 之一")


def _validate_review_mode(mode: str) -> None:
    if mode not in VALID_REVIEW_MODES:
        raise HTTPException(400, f"review_mode 必须是 {VALID_REVIEW_MODES} 之一")


@router.get("/summary-types")
async def list_summary_types(request: Request):
    """Return all summary types, built-in first."""
    db = request.app.state.db
    rows = await db.fetch_all(
        "SELECT * FROM summary_types ORDER BY is_builtin DESC, name"
    )
    import json as _json
    result = []
    for r in rows:
        d = dict(r)
        # Add derived scope_type for frontend convenience (avoids JSON.parse in UI)
        try:
            d["scope_type"] = _json.loads(d.get("scope_rule") or "{}").get("type", "day")
        except (_json.JSONDecodeError, TypeError):
            d["scope_type"] = "day"
        result.append(d)
    return result


@router.post("/summary-types", status_code=201)
async def create_summary_type(body: SummaryTypeCreate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one(
        "SELECT name FROM summary_types WHERE name = ?", (body.name,)
    )
    if existing:
        raise HTTPException(409, f"总结类型 '{body.name}' 已存在")
    _validate_scope_rule(body.scope_rule)
    _validate_review_mode(body.review_mode)
    await db.execute(
        "INSERT INTO summary_types "
        "(name, display_name, scope_rule, schedule_rule, prompt_key, prompt_template, "
        "review_mode, publisher_name, publisher_config, is_builtin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (body.name, body.display_name, body.scope_rule, body.schedule_rule,
         body.prompt_key, body.prompt_template, body.review_mode,
         body.publisher_name, body.publisher_config),
    )
    return {"name": body.name, "status": "created"}


@router.put("/summary-types/{name}")
async def update_summary_type(name: str, body: SummaryTypeUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM summary_types WHERE name = ?", (name,))
    if not existing:
        raise HTTPException(404, f"总结类型 '{name}' 不存在")
    updates: list[str] = []
    params: list = []
    if body.display_name is not None:
        updates.append("display_name = ?")
        params.append(body.display_name)
    if body.scope_rule is not None:
        _validate_scope_rule(body.scope_rule)
        updates.append("scope_rule = ?")
        params.append(body.scope_rule)
    if body.schedule_rule is not None:
        updates.append("schedule_rule = ?")
        params.append(body.schedule_rule)
    if body.prompt_key is not None:
        updates.append("prompt_key = ?")
        params.append(body.prompt_key)
    if body.prompt_template is not None:
        updates.append("prompt_template = ?")
        # Empty string = clear custom prompt (fall back to global)
        params.append(body.prompt_template or None)
    if body.review_mode is not None:
        _validate_review_mode(body.review_mode)
        updates.append("review_mode = ?")
        params.append(body.review_mode)
    if body.publisher_name is not None:
        updates.append("publisher_name = ?")
        params.append(body.publisher_name or None)
    if body.publisher_config is not None:
        updates.append("publisher_config = ?")
        params.append(body.publisher_config)
    if body.enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if body.enabled else 0)
    if not updates:
        raise HTTPException(400, "没有要更新的字段")
    params.append(name)
    await db.execute(
        f"UPDATE summary_types SET {', '.join(updates)} WHERE name = ?",
        tuple(params),
    )
    return {"name": name, "status": "updated"}


@router.delete("/summary-types/{name}")
async def delete_summary_type(name: str, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM summary_types WHERE name = ?", (name,))
    if not existing:
        raise HTTPException(404, f"总结类型 '{name}' 不存在")
    if existing["is_builtin"]:
        raise HTTPException(403, f"内置类型 '{name}' 不能删除，只能修改配置")
    await db.execute("DELETE FROM summary_types WHERE name = ?", (name,))
    return {"name": name, "status": "deleted"}
