"""Application configuration loaded from environment / .env file."""

import os
import re
import sys

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BIND_LOOPBACK: str = "127.0.0.1"
BIND_ALL: str = "0.0.0.0"

# Smallest accepted value of each limit setting; used by the fail-fast error message.
LIMIT_SETTING_MINIMUMS: dict[str, int] = {
    "RATE_LIMIT_MESSAGES_PER_MINUTE": 0,
    "LOGIN_MAX_FAILED_ATTEMPTS": 0,
    "LOGIN_LOCKOUT_SECONDS": 1,
    "MAX_CONCURRENT_GENERATIONS": 0,
}


def limit_setting_error_message(name: str, raw: object) -> str:
    """Return the ASCII-only explanation printed when a limit setting holds a bad value."""
    minimum = LIMIT_SETTING_MINIMUMS[name]
    expected = f"Expected a whole number >= {minimum}"
    if minimum == 0:
        expected += " (0 switches this limit off)"
    return (
        f"ERROR: invalid {name} value {ascii(raw)}.\n"
        f"{expected}.\n"
        f"Fix {name} in .env and start again."
    )


def bind_host_error_message(value: str) -> str:
    """Return the explanation printed when BIND_HOST holds an unsupported value."""
    return (
        f"ERROR: invalid BIND_HOST value {value!r}.\n"
        "Accepted values:\n"
        "  127.0.0.1  (or localhost, or unset/empty)  - local access only (default)\n"
        "  0.0.0.0                                    - reachable from the whole local network\n"
        "Binding to a specific LAN IP or '::' is not supported: use 0.0.0.0.\n"
        "Fix BIND_HOST in .env and start again."
    )


class Settings(BaseSettings):
    """Runtime settings for UI, Agent, database, and LLM integration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    UI_PORT: int = 8000
    AGENT_PORT: int = 8001
    # Single bind address for UI and Agent: 127.0.0.1 (default) or 0.0.0.0 (whole LAN).
    BIND_HOST: str = BIND_LOOPBACK
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
    # Messages per minute per chat, shared by WebSocket and HTTP chat; 0 = off.
    RATE_LIMIT_MESSAGES_PER_MINUTE: int = Field(default=30, ge=0)
    # Wrong passwords per username and client IP before login is blocked; counted until a
    # successful login, with no expiry by time; 0 = off.
    LOGIN_MAX_FAILED_ATTEMPTS: int = Field(default=5, ge=0)
    # How long that login block lasts, in seconds.
    LOGIN_LOCKOUT_SECONDS: int = Field(default=300, ge=1)
    # Chat turns allowed to generate at once, all providers together; 0 = no cap.
    MAX_CONCURRENT_GENERATIONS: int = Field(default=2, ge=0)

    @field_validator("BIND_HOST", mode="before")
    @classmethod
    def _check_bind_host(cls, value: object) -> str:
        """Accept only loopback (and its synonyms) or 0.0.0.0; normalise the result."""
        stripped = "" if value is None else str(value).strip()
        lowered = stripped.lower()
        if lowered in ("", "localhost", BIND_LOOPBACK):
            return BIND_LOOPBACK
        if lowered == BIND_ALL:
            return BIND_ALL
        raise ValueError(bind_host_error_message(stripped))

    @property
    def is_lan_mode(self) -> bool:
        """True when the servers listen on all interfaces."""
        return self.BIND_HOST == BIND_ALL


def _load_settings() -> Settings:
    """Build settings; an invalid BIND_HOST or limit value prints a short message and exits 2."""
    try:
        return Settings()
    except ValidationError as exc:
        for error in exc.errors():
            if error["loc"] == ("BIND_HOST",):
                # Logging is not configured at import time, so stderr is used directly.
                print(str(error["ctx"]["error"]), file=sys.stderr)
                raise SystemExit(2) from None
            if error["loc"] and error["loc"][0] in LIMIT_SETTING_MINIMUMS:
                name = str(error["loc"][0])
                print(limit_setting_error_message(name, error.get("input")), file=sys.stderr)
                raise SystemExit(2) from None
        raise


settings = _load_settings()

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
