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
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    default_tags: str = "cloudsql,cloudspanner,cloudstorage"
    db_path: str = "./data/soinsight.db"
    chroma_path: str = "./data/chroma"
    log_level: str = "INFO"
    enable_schedule: bool = False
    schedule_interval_hours: int = 24
    schedule_window_days: int = 90


settings = Settings()
