from fastapi import FastAPI, Request, Response
from starlette.responses import FileResponse
from ..models.database import Database
from .api import settings, issues, activities, worklogs, dashboard, git_repos, search, ingest, feedback, chat, machines, updates

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
    app.include_router(updates.router, prefix="/api")
    from fastapi.staticfiles import StaticFiles
    from pathlib import Path
    # Resolve the frontend dist directory.
    # Prefer the dev-repo tree when present: `pdl build` writes there on
    # every rebuild, while the wheel-installed copy under `frontend_dist/`
    # only changes on reinstall and quickly goes stale in dev. Fall back
    # to the wheel copy for users who installed via pip.
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "web" / "frontend" / "dist",  # dev repo
        Path(__file__).resolve().parent.parent / "frontend_dist",           # installed wheel
    ]
    for p in candidates:
        if p.exists() and (p / "index.html").exists():
            _dist_dir = p

            # index.html must NEVER be cached — it's the entry point that
            # references hashed chunk filenames. If the browser caches a
            # stale index.html, it loads old JS even after a new build.
            # Hashed assets (*.js, *.css) are fine to cache forever because
            # their filenames change on every build.
            @app.middleware("http")
            async def _no_cache_index(request: Request, call_next):
                response = await call_next(request)
                path = request.url.path
                if path == "/" or path.endswith(".html"):
                    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                    response.headers["Pragma"] = "no-cache"
                return response

            app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="frontend")
            break
    return app
