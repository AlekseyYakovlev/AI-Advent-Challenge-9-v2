"""Application configuration loaded from environment / .env file."""

import os
import re

from dotenv import dotenv_values
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
    # Short timeout for provider connection checks and model lists (not LLM_TIMEOUT).
    LLM_PROVIDER_CHECK_TIMEOUT: float = 10.0
    # File whose declared variable names provider key references may resolve.
    LLM_PROVIDER_ENV_FILE: str = ".env"
    # Knowledge-base storage root; empty means "<DB_PATH stem>_kb" next to the DB file.
    KB_STORAGE_DIR: str = ""
    # Per-request timeout for embeddings and embedding-model loads (separate from LLM_TIMEOUT).
    KB_EMBED_TIMEOUT: float = 120.0
    # Bound on the query embedding during a chat turn so RAG cannot stall a reply.
    RAG_EMBED_TIMEOUT: float = 30.0
    # Timeout in seconds for one query-rewrite or LLM-rerank call.
    RAG_LLM_STAGE_TIMEOUT: float = 45.0
    # Timeout in seconds for the post-turn task-memory extraction call.
    TASK_MEMORY_TIMEOUT: float = 30.0
    # Eval-only switch: false turns off task memory and history-aware query condensing
    # for the "no memory" baseline run; it has no UI and no API surface.
    TASK_MEMORY_ENABLED: bool = True


settings = Settings()

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")
BUILTIN_SECRET_ENV_NAMES: frozenset[str] = frozenset({"DEEPSEEK_API_KEY"})


def is_valid_env_name(name: str) -> bool:
    """Return True when name is a well-formed environment variable name."""
    return _ENV_NAME_RE.fullmatch(name) is not None


def resolve_env_secret(name: str | None) -> str | None:
    """Resolve a secret by variable name, only for names declared in the .env file or builtin."""
    if not name or not is_valid_env_name(name):
        return None
    file_values = dotenv_values(settings.LLM_PROVIDER_ENV_FILE)
    if name not in file_values and name not in BUILTIN_SECRET_ENV_NAMES:
        return None
    if name in os.environ:
        value: str | None = os.environ[name]
    else:
        value = file_values.get(name)
        if value is None and name in BUILTIN_SECRET_ENV_NAMES:
            value = getattr(settings, name, "")
    return value or None
