from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        case_sensitive=False,
        # A .env holding a key this class doesn't declare (an old setting, a
        # typo, a var meant for another tool) used to abort startup with
        # pydantic's "extra inputs are not permitted". Ignore unknown keys.
        extra="ignore",
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
    # Model used for the remediation guide. Blank = use ollama_model. Remediation
    # is a handful of long-form reasoning calls per run, while classification is
    # thousands of short structured ones — so a small, fast classifier can be
    # paired with a larger model here without slowing the pipeline down.
    remediation_model: str = ""
    # Context window requested on every Ollama call. Ollama's own default is
    # smaller than SOInsight's prompts on many versions, and an overflowing
    # prompt is cut from the FRONT — dropping the taxonomy. Keep this fixed:
    # changing it between calls makes Ollama reload the model.
    ollama_num_ctx: int = 8192
    # How long Ollama keeps the model loaded after a call. Reloading a model on
    # CPU costs seconds to tens of seconds; analysis runs make many calls.
    ollama_keep_alive: str = "30m"
    # Per-request timeout (seconds) for Ollama generation calls. CPU inference of
    # a full batch can legitimately take minutes.
    ollama_timeout: int = 300
    # Questions per classification prompt. The static instructions are sent as
    # a cached system prefix and the reply is grammar-constrained to exactly one
    # entry per question, so batch size mostly trades latency per call against
    # the number of calls. 8-12 suits 3-4B models, 15-20 suits 7-8B ones.
    classify_batch_size: int = 10
    # Batches classified in parallel. Only helps when Ollama itself serves
    # requests in parallel (OLLAMA_NUM_PARALLEL > 1, typically on a GPU). On a
    # CPU-only machine leave at 1.
    classify_concurrency: int = 1
    # Characters of cleaned question body (HTML stripped) sent per question.
    classify_body_chars: int = 400
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
