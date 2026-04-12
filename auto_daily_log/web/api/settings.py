from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["settings"])

class SettingUpdate(BaseModel):
    value: str

@router.get("/settings")
async def list_settings(request: Request):
    db = request.app.state.db
    return await db.fetch_all("SELECT key, value, updated_at FROM settings")

@router.get("/settings/{key}")
async def get_setting(key: str, request: Request):
    db = request.app.state.db
    row = await db.fetch_one("SELECT * FROM settings WHERE key = ?", (key,))
    return row or {"key": key, "value": None}

@router.put("/settings/{key}")
async def put_setting(key: str, body: SettingUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT key FROM settings WHERE key = ?", (key,))
    if existing:
        await db.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?", (body.value, key))
    else:
        await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, body.value))
    return {"key": key, "value": body.value}
