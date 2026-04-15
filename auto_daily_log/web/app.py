from fastapi import FastAPI
from ..models.database import Database
from .api import settings, issues, activities, worklogs, dashboard, git_repos, search, ingest, feedback, chat, machines

def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Polars Daily Log", version="0.1.0")
    app.state.db = db
    app.include_router(settings.router, prefix="/api")
    app.include_router(issues.router, prefix="/api")
    app.include_router(activities.router, prefix="/api")
    app.include_router(worklogs.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(git_repos.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(ingest.router, prefix="/api")
    app.include_router(feedback.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(machines.router, prefix="/api")
    from fastapi.staticfiles import StaticFiles
    from pathlib import Path
    # Prefer packaged dist (release wheel); fall back to dev source tree.
    candidates = [
        Path(__file__).resolve().parent.parent / "frontend_dist",           # installed wheel
        Path(__file__).resolve().parent.parent.parent / "web" / "frontend" / "dist",  # dev repo
    ]
    for p in candidates:
        if p.exists() and (p / "index.html").exists():
            app.mount("/", StaticFiles(directory=str(p), html=True), name="frontend")
            break
    return app
