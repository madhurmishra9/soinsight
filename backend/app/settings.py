from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    so_base_url: str = "https://your-instance.stackenterprise.co/api/v3"
    so_api_key: str = ""
    so_team: str = ""
    ollama_url: str = "http://localhost:11434"
    db_path: str = "./data/soinsight.db"
    chroma_path: str = "./data/chroma"
    log_level: str = "INFO"


settings = Settings()
