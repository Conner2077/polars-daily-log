from fastapi import FastAPI
from ..models.database import Database
from .api import settings, issues, activities, worklogs, dashboard, git_repos

def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Auto Daily Log", version="0.1.0")
    app.state.db = db
    app.include_router(settings.router, prefix="/api")
    app.include_router(issues.router, prefix="/api")
    app.include_router(activities.router, prefix="/api")
    app.include_router(worklogs.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(git_repos.router, prefix="/api")
    return app
