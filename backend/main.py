"""CiteVerify — Legal Citation Verification API."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.jobs import purge_old_jobs, shutdown_executor
from backend.routers import upload, jobs, reports

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("CiteVerify starting up")
    purge_old_jobs()
    yield
    shutdown_executor()
    logger.info("CiteVerify shutting down")


# Disable OpenAPI docs in production
_disable_docs = os.getenv("DISABLE_DOCS", "true").lower() == "true"

app = FastAPI(
    title="CiteVerify",
    lifespan=lifespan,
    docs_url=None if _disable_docs else "/docs",
    redoc_url=None if _disable_docs else "/redoc",
    openapi_url=None if _disable_docs else "/openapi.json",
)

# CORS — configurable via env var for production
_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(upload.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(reports.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error in request")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# SPA catch-all — serve frontend for non-API routes
if FRONTEND_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa_catchall(path: str):
        # Don't serve SPA for unmatched /api/ paths — return 404 instead
        if path.startswith("api/") or path == "api":
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        file_path = FRONTEND_DIR / path
        if file_path.is_file() and file_path.resolve().is_relative_to(FRONTEND_DIR.resolve()):
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
