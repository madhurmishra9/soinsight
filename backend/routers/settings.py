import httpx
import structlog
from fastapi import APIRouter
from pydantic import BaseModel, SecretStr

from app.settings import settings as env_settings
from services.so_client import SOAuth, SOClient

log = structlog.get_logger("soinsight.routers.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])

_current_config: dict[str, str] = {
    "base_url": env_settings.so_base_url,
    "api_key": env_settings.so_api_key,
    "team": env_settings.so_team,
    "ollama_url": env_settings.ollama_url,
}


class OllamaModelsResponse(BaseModel):
    models: list[str]


class SOConfig(BaseModel):
    base_url: str
    # Optional: a blank/omitted api_key means "keep whatever is already stored" —
    # the UI never re-populates this field with a saved key, so requiring it on
    # every save would force a re-paste any time another field is edited.
    api_key: SecretStr | None = None
    team: str = ""
    ollama_url: str = ""
    ollama_model: str = ""


class SOConfigResponse(BaseModel):
    base_url: str
    team: str
    ollama_url: str = ""
    ollama_model: str = ""
    default_tags: str = ""
    has_api_key: bool = False
    # the api_key value itself is intentionally omitted — never echo secrets


class TestConnectionResponse(BaseModel):
    reachable: bool
    version: str | None = None
    scopes: list[str] = []
    error: str | None = None


@router.get("", response_model=SOConfigResponse)
async def get_config() -> SOConfigResponse:
    return SOConfigResponse(
        base_url=_current_config.get("base_url", ""),
        team=_current_config.get("team", ""),
        ollama_url=_current_config.get("ollama_url", env_settings.ollama_url),
        ollama_model=env_settings.ollama_model,
        default_tags=env_settings.default_tags,
        has_api_key=bool(_current_config.get("api_key")),
    )


@router.post("", response_model=SOConfigResponse)
async def store_config(body: SOConfig) -> SOConfigResponse:
    """
    Persist SO connection settings for this session.
    The api_key is stored in memory only and never logged or returned. A blank
    or omitted api_key keeps whatever key is already stored, so re-saving other
    fields (team, Ollama URL, ...) never forces the key to be re-entered.
    """
    _current_config["base_url"] = body.base_url
    if body.api_key is not None and body.api_key.get_secret_value():
        _current_config["api_key"] = body.api_key.get_secret_value()
    _current_config["team"] = body.team
    if body.ollama_url:
        _current_config["ollama_url"] = body.ollama_url
    if body.ollama_model:
        env_settings.ollama_model = body.ollama_model

    log.info("so_config_updated", base_url=body.base_url, team=body.team,
             ollama_model=env_settings.ollama_model)
    return SOConfigResponse(
        base_url=body.base_url, team=body.team,
        ollama_url=_current_config.get("ollama_url", ""),
        ollama_model=env_settings.ollama_model,
        default_tags=env_settings.default_tags,
        has_api_key=bool(_current_config.get("api_key")),
    )


@router.get("/ollama-models", response_model=OllamaModelsResponse)
async def list_ollama_models() -> OllamaModelsResponse:
    """List models installed in the local Ollama instance."""
    url = _current_config.get("ollama_url") or env_settings.ollama_url
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{url.rstrip('/')}/api/tags")
            r.raise_for_status()
            data = r.json()
            names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            return OllamaModelsResponse(models=sorted(names))
    except Exception as exc:
        log.warning("ollama_models_failed", error=str(exc))
        return OllamaModelsResponse(models=[])


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
        mode="bearer",
        api_key=_current_config.get("api_key") or None,
    )
    try:
        async with SOClient(base_url=_current_config["base_url"], auth=auth) as client:
            conn = await client.test_connection()
            scopes = await client.list_scopes() if conn.get("ok") else []
    except BaseException as exc:
        # Anything unexpected (a bug, a shape SOClient's own handling didn't
        # anticipate, a connection-level failure outside test_connection's own
        # try/except) must still report "unreachable", not a 500 -- this is a
        # health-check endpoint, not something that should ever crash on a
        # flaky/misconfigured instance.
        log.error("so_test_unexpected_error", error=str(exc), exc_info=True)
        return TestConnectionResponse(
            reachable=False,
            version=None,
            scopes=[],
            error=str(exc),
        )

    log.info(
        "so_test_result",
        ok=conn.get("ok"),
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
        reachable=bool(conn.get("ok")),
        version=version,
        scopes=scope_names,
        error=conn.get("error"),
    )
