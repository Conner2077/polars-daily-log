from fastapi import FastAPI
from ..models.database import Database
from .api import settings, issues, activities, worklogs, dashboard, git_repos, search

def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Auto Daily Log", version="0.1.0")
    app.state.db = db
    app.include_router(settings.router, prefix="/api")
    app.include_router(issues.router, prefix="/api")
    app.include_router(activities.router, prefix="/api")
    app.include_router(worklogs.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(git_repos.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    from fastapi.staticfiles import StaticFiles
    from pathlib import Path
    frontend_dist = Path(__file__).parent.parent.parent / "web" / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    return app
