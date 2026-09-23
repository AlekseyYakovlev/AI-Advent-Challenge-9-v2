"""Application configuration loaded from environment / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for UI, Agent, database, and LLM integration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    UI_PORT: int = 8000
    AGENT_PORT: int = 8001
    DB_PATH: str = "app.db"
    DEEPSEEK_API_KEY: str = ""
    LM_STUDIO_BASE_URL: str = "http://localhost:1234"
    LLM_TIMEOUT: float = 60.0
    MCP_CONNECT_TIMEOUT: float = 10.0


settings = Settings()
