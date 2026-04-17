"""CRUD API for summary_types and scope_outputs (pipeline refactor Phase 2).

Replaces the old summary_types API. Built-in scopes (daily/weekly/monthly)
can be edited but not deleted.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["scopes"])

VALID_SCOPE_TYPES = {"day", "week", "month", "quarter", "custom"}
VALID_OUTPUT_MODES = {"single", "per_issue"}


async def _reload_scheduler(request: Request):
    """Hot-reload scheduler jobs after scope config changes."""
    app_instance = getattr(request.app.state, "application", None)
    if app_instance:
        try:
            await app_instance.reload_scheduler_jobs()
        except Exception as e:
            print(f"[Scopes] scheduler reload failed (non-fatal): {e}")


# ── Request models ───────────────────────────────────────────────────


class ScopeCreate(BaseModel):
    name: str
    display_name: str
    scope_type: str = "day"
    schedule_rule: Optional[str] = None
    enabled: bool = True


class ScopeUpdate(BaseModel):
    display_name: Optional[str] = None
    scope_type: Optional[str] = None
    schedule_rule: Optional[str] = None
    enabled: Optional[bool] = None


class OutputCreate(BaseModel):
    display_name: str
    output_mode: str = "single"
    issue_source: Optional[str] = None
    prompt_template: Optional[str] = None
    publisher_name: Optional[str] = None
    publisher_config: str = "{}"
    auto_publish: bool = False
    enabled: bool = True


class OutputUpdate(BaseModel):
    display_name: Optional[str] = None
    output_mode: Optional[str] = None
    issue_source: Optional[str] = None
    prompt_template: Optional[str] = None
    publisher_name: Optional[str] = None
    publisher_config: Optional[str] = None
    auto_publish: Optional[bool] = None
    enabled: Optional[bool] = None


# ── summary_types CRUD ─────────────────────────────────────────────────


@router.get("/scopes")
async def list_scopes(request: Request):
    db = request.app.state.db
    import json as _json
    scopes = await db.fetch_all(
        "SELECT * FROM summary_types ORDER BY is_builtin DESC, name"
    )
    # Attach outputs + derived scope_type to each scope
    result = []
    for s in scopes:
        outputs = await db.fetch_all(
            "SELECT * FROM scope_outputs WHERE scope_name = ? ORDER BY id",
            (s["name"],),
        )
        row = dict(s)
        row["outputs"] = [dict(o) for o in outputs]
        # Derive scope_type from scope_rule JSON for frontend convenience
        try:
            row["scope_type"] = _json.loads(row.get("scope_rule") or "{}").get("type", "day")
        except (_json.JSONDecodeError, TypeError):
            row["scope_type"] = "day"
        result.append(row)
    return result


@router.post("/scopes", status_code=201)
async def create_scope(body: ScopeCreate, request: Request):
    db = request.app.state.db
    if body.scope_type not in VALID_SCOPE_TYPES:
        raise HTTPException(400, f"scope_type 必须是 {VALID_SCOPE_TYPES} 之一")
    existing = await db.fetch_one(
        "SELECT name FROM summary_types WHERE name = ?", (body.name,)
    )
    if existing:
        raise HTTPException(409, f"总结周期 '{body.name}' 已存在")
    scope_rule = json.dumps({"type": body.scope_type})
    await db.execute(
        "INSERT INTO summary_types (name, display_name, scope_rule, schedule_rule, enabled) "
        "VALUES (?, ?, ?, ?, ?)",
        (body.name, body.display_name, scope_rule,
         body.schedule_rule, 1 if body.enabled else 0),
    )
    await _reload_scheduler(request)
    return {"name": body.name, "status": "created"}


@router.put("/scopes/{name}")
async def update_scope(name: str, body: ScopeUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM summary_types WHERE name = ?", (name,))
    if not existing:
        raise HTTPException(404, f"总结周期 '{name}' 不存在")
    updates: list[str] = []
    params: list = []
    if body.display_name is not None:
        updates.append("display_name = ?")
        params.append(body.display_name)
    if body.scope_type is not None:
        if body.scope_type not in VALID_SCOPE_TYPES:
            raise HTTPException(400, f"scope_type 必须是 {VALID_SCOPE_TYPES} 之一")
        updates.append("scope_rule = ?")
        params.append(json.dumps({"type": body.scope_type}))
    if body.schedule_rule is not None:
        updates.append("schedule_rule = ?")
        params.append(body.schedule_rule or None)
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
    await _reload_scheduler(request)
    return {"name": name, "status": "updated"}


@router.delete("/scopes/{name}")
async def delete_scope(name: str, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM summary_types WHERE name = ?", (name,))
    if not existing:
        raise HTTPException(404, f"总结周期 '{name}' 不存在")
    if existing["is_builtin"]:
        raise HTTPException(403, f"内置周期 '{name}' 不能删除")
    await db.execute("DELETE FROM scope_outputs WHERE scope_name = ?", (name,))
    await db.execute("DELETE FROM summary_types WHERE name = ?", (name,))
    await _reload_scheduler(request)
    return {"name": name, "status": "deleted"}


# ── scope_outputs CRUD ───────────────────────────────────────────────


@router.get("/scopes/{scope_name}/outputs")
async def list_outputs(scope_name: str, request: Request):
    db = request.app.state.db
    scope = await db.fetch_one("SELECT name FROM summary_types WHERE name = ?", (scope_name,))
    if not scope:
        raise HTTPException(404, f"总结周期 '{scope_name}' 不存在")
    return await db.fetch_all(
        "SELECT * FROM scope_outputs WHERE scope_name = ? ORDER BY id",
        (scope_name,),
    )


@router.post("/scopes/{scope_name}/outputs", status_code=201)
async def create_output(scope_name: str, body: OutputCreate, request: Request):
    db = request.app.state.db
    scope = await db.fetch_one("SELECT name FROM summary_types WHERE name = ?", (scope_name,))
    if not scope:
        raise HTTPException(404, f"总结周期 '{scope_name}' 不存在")
    if body.output_mode not in VALID_OUTPUT_MODES:
        raise HTTPException(400, f"output_mode 必须是 {VALID_OUTPUT_MODES} 之一")
    output_id = await db.execute(
        "INSERT INTO scope_outputs "
        "(scope_name, display_name, output_mode, issue_source, prompt_template, "
        "publisher_name, publisher_config, auto_publish, enabled) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (scope_name, body.display_name, body.output_mode, body.issue_source,
         body.prompt_template, body.publisher_name, body.publisher_config,
         1 if body.auto_publish else 0, 1 if body.enabled else 0),
    )
    return {"id": output_id, "status": "created"}


@router.put("/scopes/outputs/{output_id}")
async def update_output(output_id: int, body: OutputUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM scope_outputs WHERE id = ?", (output_id,))
    if not existing:
        raise HTTPException(404, f"输出配置 #{output_id} 不存在")
    updates: list[str] = []
    params: list = []
    if body.display_name is not None:
        updates.append("display_name = ?")
        params.append(body.display_name)
    if body.output_mode is not None:
        if body.output_mode not in VALID_OUTPUT_MODES:
            raise HTTPException(400, f"output_mode 必须是 {VALID_OUTPUT_MODES} 之一")
        updates.append("output_mode = ?")
        params.append(body.output_mode)
    if body.issue_source is not None:
        updates.append("issue_source = ?")
        params.append(body.issue_source or None)
    if body.prompt_template is not None:
        updates.append("prompt_template = ?")
        params.append(body.prompt_template or None)
    if body.publisher_name is not None:
        updates.append("publisher_name = ?")
        params.append(body.publisher_name or None)
    if body.publisher_config is not None:
        updates.append("publisher_config = ?")
        params.append(body.publisher_config)
    if body.auto_publish is not None:
        updates.append("auto_publish = ?")
        params.append(1 if body.auto_publish else 0)
    if body.enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if body.enabled else 0)
    if not updates:
        raise HTTPException(400, "没有要更新的字段")
    params.append(output_id)
    await db.execute(
        f"UPDATE scope_outputs SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    return {"id": output_id, "status": "updated"}


@router.delete("/scopes/outputs/{output_id}")
async def delete_output(output_id: int, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM scope_outputs WHERE id = ?", (output_id,))
    if not existing:
        raise HTTPException(404, f"输出配置 #{output_id} 不存在")
    await db.execute("DELETE FROM scope_outputs WHERE id = ?", (output_id,))
    return {"id": output_id, "status": "deleted"}
