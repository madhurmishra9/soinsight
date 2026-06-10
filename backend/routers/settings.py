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
    base_url: str
    api_key: SecretStr
    team: str = ""
    ollama_url: str = ""


class SOConfigResponse(BaseModel):
    base_url: str
    team: str
    # api_key intentionally omitted — never echo secrets


class TestConnectionResponse(BaseModel):
    reachable: bool
    version: str | None = None
    scopes: list[str] = []
    error: str | None = None


@router.post("", response_model=SOConfigResponse)
async def store_config(body: SOConfig) -> SOConfigResponse:
    """
    Persist SO connection settings for this session.
    The api_key is stored in memory only and never logged or returned.
    """
    _current_config["base_url"] = body.base_url
    _current_config["api_key"] = body.api_key.get_secret_value()
    _current_config["team"] = body.team
    if body.ollama_url:
        _current_config["ollama_url"] = body.ollama_url

    log.info("so_config_updated", base_url=body.base_url, team=body.team)
    return SOConfigResponse(base_url=body.base_url, team=body.team)


@router.get("/test", response_model=TestConnectionResponse)
async def test_connection() -> TestConnectionResponse:
    """
    Probe the configured SO instance.
    Returns the detected API version and list of reachable scopes (teams/communities).
    """
    if not _current_config.get("base_url"):
        return TestConnectionResponse(
            reachable=False,
            version=None,
            scopes=[],
            error="No SO config stored. Call POST /api/settings first.",
        )

    auth = SOAuth(
        mode="api_key",
        api_key=_current_config.get("api_key") or None,
    )
    async with SOClient(base_url=_current_config["base_url"], auth=auth) as client:
        conn = await client.test_connection()
        scopes = await client.list_scopes() if conn["ok"] else []

    log.info(
        "so_test_result",
        ok=conn["ok"],
        version=conn.get("version"),
        scope_count=len(scopes),
    )

    version = conn.get("version")
    if not version or version == "unknown":
        version = None

    scope_names = [
        str(s.get("name") or s.get("slug") or s.get("id"))
        for s in scopes
        if isinstance(s, dict) and (s.get("name") or s.get("slug") or s.get("id"))
    ]

    return TestConnectionResponse(
        reachable=conn["ok"],
        version=version,
        scopes=scope_names,
        error=conn.get("error"),
    )
