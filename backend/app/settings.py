from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        case_sensitive=False,
    )

    so_base_url: str = "https://your-instance.stackenterprise.co/api/v3"
    so_api_key: str = ""
    so_team: str = ""
    # Path to a PEM file of extra CA certificate(s) to trust, for on-prem SO
    # Enterprise instances behind an internal/corporate CA. Certificates are
    # still validated -- this only adds a trusted issuer, it never disables
    # verification. Blank (default) uses httpx's normal system trust store.
    so_ca_bundle: str = ""
    # Opt-in escape hatch for instances with a self-signed/otherwise-untrusted
    # cert that so_ca_bundle can't cover. Defaults to False (verification on);
    # only takes effect when explicitly set true by whoever controls the
    # deployment. Takes priority over so_ca_bundle when both are set.
    so_insecure_skip_verify: bool = False
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    default_tags: str = "cloudsql,cloudspanner,cloudstorage"
    # When true, ingestion also pulls each new question's answers (one extra
    # API call per newly-inserted question that reports answer_count > 0).
    fetch_answers: bool = True
    # How many of those answer-fetch calls run concurrently. Answer fetches are
    # the dominant cost of a large first-time ingest (one HTTP round trip per
    # answered question); raising this cuts wall-clock time roughly linearly up
    # to what your SO Enterprise instance can comfortably absorb concurrently.
    # Keep conservative — this adds load to a shared, possibly on-prem instance.
    answer_fetch_concurrency: int = 10
    db_path: str = "./data/soinsight.db"
    chroma_path: str = "./data/chroma"
    log_level: str = "INFO"
    enable_schedule: bool = False
    schedule_interval_hours: int = 24
    schedule_window_days: int = 90


settings = Settings()
