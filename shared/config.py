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
    MCP_TOOL_CALL_TIMEOUT: float = 30.0
    MCP_TOOL_RESULT_MAX_CHARS: int = 20000
    MCP_AUTO_CONNECT: bool = True
    # Master switch for the background scheduler loop.
    SCHEDULER_ENABLED: bool = True
    # Seconds between scheduler ticks that look for due jobs.
    SCHEDULER_POLL_INTERVAL: float = 1.0
    # Overall wall-clock limit for a single scheduled run (overridable).
    SCHEDULER_RUN_TIMEOUT: float = 120.0
    # Smallest accepted repeat interval for interval jobs.
    SCHEDULER_MIN_INTERVAL_SECONDS: int = 10
    # A run started later than this after its slot is flagged as late.
    SCHEDULER_LATE_THRESHOLD_SECONDS: float = 60.0
    # Maximum number of scheduled runs executing at the same time.
    SCHEDULER_MAX_CONCURRENT_RUNS: int = 2
    # Cap on active or paused jobs a single user may own.
    SCHEDULER_MAX_ACTIVE_TASKS_PER_USER: int = 50


settings = Settings()
