from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

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


app = FastAPI(title="SOInsight", version="0.1.0", lifespan=lifespan)
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
