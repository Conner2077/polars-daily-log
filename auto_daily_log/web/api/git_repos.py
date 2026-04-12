from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["git_repos"])

class GitRepoCreate(BaseModel):
    path: str
    author_email: str = ""

class GitRepoUpdate(BaseModel):
    is_active: Optional[bool] = None
    author_email: Optional[str] = None

@router.get("/git-repos")
async def list_repos(request: Request):
    db = request.app.state.db
    rows = await db.fetch_all("SELECT * FROM git_repos ORDER BY created_at DESC")
    return [{**r, "is_active": bool(r["is_active"])} for r in rows]

@router.post("/git-repos", status_code=201)
async def add_repo(body: GitRepoCreate, request: Request):
    db = request.app.state.db
    repo_id = await db.execute("INSERT INTO git_repos (path, author_email) VALUES (?, ?)", (body.path, body.author_email))
    return {"id": repo_id, "path": body.path, "author_email": body.author_email}

@router.patch("/git-repos/{repo_id}")
async def update_repo(repo_id: int, body: GitRepoUpdate, request: Request):
    db = request.app.state.db
    updates, params = [], []
    if body.is_active is not None: updates.append("is_active = ?"); params.append(int(body.is_active))
    if body.author_email is not None: updates.append("author_email = ?"); params.append(body.author_email)
    if updates:
        params.append(repo_id)
        await db.execute(f"UPDATE git_repos SET {', '.join(updates)} WHERE id = ?", tuple(params))
    return {"status": "updated"}

@router.delete("/git-repos/{repo_id}")
async def delete_repo(repo_id: int, request: Request):
    db = request.app.state.db
    await db.execute("DELETE FROM git_repos WHERE id = ?", (repo_id,))
    return {"status": "deleted"}
