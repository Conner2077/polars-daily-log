from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["issues"])

class IssueCreate(BaseModel):
    issue_key: str
    summary: str = ""
    description: str = ""

class IssueUpdate(BaseModel):
    is_active: Optional[bool] = None
    summary: Optional[str] = None
    description: Optional[str] = None

@router.get("/issues")
async def list_issues(request: Request):
    db = request.app.state.db
    rows = await db.fetch_all("SELECT * FROM jira_issues ORDER BY created_at DESC")
    return [{**r, "is_active": bool(r["is_active"])} for r in rows]

@router.get("/issues/fetch/{issue_key}")
async def fetch_jira_issue(issue_key: str, request: Request):
    """Fetch issue summary and description from Jira API (cookie or PAT)."""
    from ...jira_client.client import MissingJiraConfig, build_jira_client_from_db
    db = request.app.state.db
    try:
        client = await build_jira_client_from_db(db)
    except MissingJiraConfig as e:
        raise HTTPException(400, str(e))
    try:
        return await client.fetch_issue(issue_key)
    except Exception as e:
        detail = str(e)
        if "404" in detail:
            raise HTTPException(404, f"Issue {issue_key} not found in Jira")
        raise HTTPException(502, f"Jira API error: {detail[:200]}")


@router.post("/issues", status_code=201)
async def add_issue(body: IssueCreate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT id FROM jira_issues WHERE issue_key = ?", (body.issue_key,))
    if existing:
        raise HTTPException(400, f"Issue {body.issue_key} already exists")
    await db.execute("INSERT INTO jira_issues (issue_key, summary, description) VALUES (?, ?, ?)", (body.issue_key, body.summary, body.description))
    return {"issue_key": body.issue_key, "summary": body.summary, "is_active": True}

@router.patch("/issues/{issue_key}")
async def update_issue(issue_key: str, body: IssueUpdate, request: Request):
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM jira_issues WHERE issue_key = ?", (issue_key,))
    if not existing:
        raise HTTPException(404, f"Issue {issue_key} not found")
    updates, params = [], []
    if body.is_active is not None:
        updates.append("is_active = ?"); params.append(int(body.is_active))
    if body.summary is not None:
        updates.append("summary = ?"); params.append(body.summary)
    if body.description is not None:
        updates.append("description = ?"); params.append(body.description)
    if updates:
        params.append(issue_key)
        await db.execute(f"UPDATE jira_issues SET {', '.join(updates)} WHERE issue_key = ?", tuple(params))
    return {"status": "updated"}

@router.delete("/issues/{issue_key}")
async def delete_issue(issue_key: str, request: Request):
    db = request.app.state.db
    await db.execute("DELETE FROM jira_issues WHERE issue_key = ?", (issue_key,))
    return {"status": "deleted"}
