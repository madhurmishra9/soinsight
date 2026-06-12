from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.db import create_db_tables
from app.db import engine as app_engine
from app.logging import get_logger, setup_logging
from app.settings import settings
from routers.analysis import router as analysis_router
from routers.insights import router as insights_router
from routers.questions import router as questions_router
from routers.scheduler import router as scheduler_router
from routers.scheduler import set_scheduler
from routers.settings import router as settings_router
from services.scheduler import SchedulerService

log = get_logger("soinsight.main")


async def _check_ollama() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_url}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging(settings.log_level)
    create_db_tables()

    ok = await _check_ollama()
    if ok:
        log.info("ollama_reachable", url=settings.ollama_url)
    else:
        log.warning("ollama_unreachable", url=settings.ollama_url)

    scheduler = SchedulerService(engine=app_engine, ollama_url=settings.ollama_url)
    set_scheduler(scheduler)
    await scheduler.start()

    yield

    await scheduler.stop()


app = FastAPI(
    title="SOInsight",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

# CORS — local UI origins only (dev server + single-process mode)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
app.include_router(settings_router)
app.include_router(questions_router)
app.include_router(analysis_router)
app.include_router(insights_router)
app.include_router(scheduler_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/deps")
async def health_deps() -> dict[str, Any]:
    ollama_ok = await _check_ollama()
    return {
        "ollama": {
            "status": "ok" if ollama_ok else "unreachable",
            "url": settings.ollama_url,
        },
    }


# ── Single-process mode: serve the built UI from frontend/dist ────────────────
# When the frontend has been built (npm run build), the backend serves it
# directly — start one process, open http://localhost:8000, done.
_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")

    log.info("ui_serving", path=str(_DIST))
