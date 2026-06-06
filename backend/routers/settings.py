from typing import Any

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, SecretStr

from services.so_client import SOAuth, SOClient

log = structlog.get_logger("soinsight.routers.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])

# In-memory store for the current SO connection config.
# Persisted to SQLite in a later phase; for S1 this is sufficient.
_current_config: dict[str, str] = {}


class SOConfig(BaseModel):
    so_base_url: str
    so_api_key: SecretStr
    so_team: str = ""


class SOConfigResponse(BaseModel):
    so_base_url: str
    so_team: str
    # api_key intentionally omitted — never echo secrets


class TestConnectionResponse(BaseModel):
    ok: bool
    version: str | None
    scopes: list[dict[str, Any]]
    error: str | None = None


@router.post("", response_model=SOConfigResponse)
async def store_config(body: SOConfig) -> SOConfigResponse:
    """
    Persist SO connection settings for this session.
    The api_key is stored in memory only and never logged or returned.
    """
    _current_config["so_base_url"] = body.so_base_url
    _current_config["so_api_key"] = body.so_api_key.get_secret_value()
    _current_config["so_team"] = body.so_team

    log.info("so_config_updated", base_url=body.so_base_url, team=body.so_team)
    return SOConfigResponse(so_base_url=body.so_base_url, so_team=body.so_team)


@router.get("/test", response_model=TestConnectionResponse)
async def test_connection() -> TestConnectionResponse:
    """
    Probe the configured SO instance.
    Returns the detected API version and list of reachable scopes (teams/communities).
    """
    if not _current_config.get("so_base_url"):
        return TestConnectionResponse(
            ok=False,
            version=None,
            scopes=[],
            error="No SO config stored. Call POST /api/settings first.",
        )

    auth = SOAuth(
        mode="api_key",
        api_key=_current_config.get("so_api_key") or None,
    )
    async with SOClient(base_url=_current_config["so_base_url"], auth=auth) as client:
        conn = await client.test_connection()
        scopes = await client.list_scopes() if conn["ok"] else []

    log.info(
        "so_test_result",
        ok=conn["ok"],
        version=conn.get("version"),
        scope_count=len(scopes),
    )
    return TestConnectionResponse(
        ok=conn["ok"],
        version=conn.get("version"),
        scopes=scopes,
        error=conn.get("error"),
    )
