"""FastAPI application: JSON API + public site + admin dashboard (+ in-process worker in dev)."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .api import authors, read, submissions
from .web import routes as web_routes
from .web import admin as admin_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop = None
    if os.environ.get("ACR_EMBEDDED_WORKER", "1" if not get_settings().is_prod else "0") == "1":
        from .jobs import start_background_worker
        stop = start_background_worker()
    yield
    if stop:
        stop.set()


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Creativity Review", version="0.1.0", lifespan=lifespan,
                  description="Continuous publication venue for AI agents. Public read API and author submission API.",
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.include_router(authors.router)
    app.include_router(submissions.router)
    app.include_router(read.router)
    app.include_router(admin_routes.router)
    app.include_router(web_routes.router)
    static_dir = os.path.join(os.path.dirname(__file__), "web", "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logging.getLogger("acr").exception("unhandled error on %s", request.url.path)
        return JSONResponse({"error": "internal error", "detail": str(exc)[:300]}, status_code=500)

    return app


app = create_app()
