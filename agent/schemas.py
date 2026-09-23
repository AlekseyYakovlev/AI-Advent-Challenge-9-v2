"""Pydantic request/response schemas for the Agent REST API."""

import re
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from shared.models import ContextStrategy

TITLE_MAX_LENGTH = 200
ROLE_MAX_LENGTH = 50
CONTENT_MAX_LENGTH = 100_000
SYSTEM_PROMPT_MAX_LENGTH = 10_000
FACTS_JSON_MAX_LENGTH = 50_000
SUMMARY_TEXT_MAX_LENGTH = 50_000
USERNAME_MAX_LENGTH = 64
PASSWORD_MAX_LENGTH = 128
MEMORY_KEY_MAX_LENGTH = 200
MEMORY_VALUE_MAX_LENGTH = 50_000
PROFILE_FIELD_MAX_LENGTH = 2000
TASK_TITLE_MAX_LENGTH = 200
TASK_DESCRIPTION_MAX_LENGTH = 5_000
TASK_GOAL_MAX_LENGTH = 2_000
TASK_NOTE_MAX_LENGTH = 2_000
INVARIANT_TITLE_MAX_LENGTH = 200
INVARIANT_RULE_MAX_LENGTH = 2000
INVARIANT_CONFLICT_NOTE_MAX_LENGTH = 2000
MCP_NAME_MAX_LENGTH = 100
MCP_COMMAND_MAX_LENGTH = 1000
MCP_ARG_MAX_LENGTH = 1000
MCP_ARGS_MAX_ITEMS = 50
MCP_ENV_MAX_ITEMS = 50
MCP_ENV_VALUE_MAX_LENGTH = 4000
MCP_CWD_MAX_LENGTH = 1000
MCP_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class HealthResponse(BaseModel):
    """Health-check payload."""

    status: str = "ok"


class ChatCreate(BaseModel):
    """Request body for creating a new chat."""

    title: str = Field(default="New Chat", max_length=TITLE_MAX_LENGTH)


class ChatResponse(BaseModel):
    """Serialized chat session."""

    id: int
    title: str = Field(max_length=TITLE_MAX_LENGTH)
    current_leaf_message_id: Optional[int] = None
    created_at: datetime


class MessageResponse(BaseModel):
    """Serialized message node in a chat tree."""

    id: int
    chat_id: int
    parent_id: Optional[int] = None
    role: str = Field(max_length=ROLE_MAX_LENGTH)
    content: str = Field(max_length=CONTENT_MAX_LENGTH)
    token_count: int = 0
    created_at: datetime


class BranchRequest(BaseModel):
    """Request body for switching the active branch."""

    message_id: int = Field(gt=0)


class SettingsUpdate(BaseModel):
    """Partial settings update for global or per-chat scope."""

    chat_id: Optional[int] = None
    system_prompt: Optional[str] = Field(
        default=None,
        max_length=SYSTEM_PROMPT_MAX_LENGTH,
    )
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    context_length: Optional[int] = Field(default=None, ge=512, le=131072)
    max_tokens: Optional[int] = Field(default=None, ge=256, le=128000)
    strategy: Optional[ContextStrategy] = None
    facts_json: Optional[str] = Field(
        default=None,
        max_length=FACTS_JSON_MAX_LENGTH,
    )
    summary_text: Optional[str] = Field(
        default=None,
        max_length=SUMMARY_TEXT_MAX_LENGTH,
    )


class SettingsResponse(BaseModel):
    """Effective settings returned to the client."""

    id: int
    chat_id: Optional[int] = None
    system_prompt: str = Field(max_length=SYSTEM_PROMPT_MAX_LENGTH)
    temperature: float
    context_length: int
    max_tokens: int
    strategy: ContextStrategy
    facts_json: str = Field(max_length=FACTS_JSON_MAX_LENGTH)
    summary_text: str = Field(default="", max_length=SUMMARY_TEXT_MAX_LENGTH)


class ModelLoadStatus(str, Enum):
    """LM Studio model load lifecycle status."""

    LOADED = "LOADED"
    IDLE = "IDLE"
    UNREACHABLE = "UNREACHABLE"
    ERROR = "ERROR"


class ModelLoadResult(BaseModel):
    """Result of a model load or unload operation."""

    status: ModelLoadStatus
    message: str
    model_id: Optional[str] = None


class McpConnectionStatus(str, Enum):
    """Lifecycle status of an MCP server connection as reported by the API."""

    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    ERROR = "error"


class McpErrorCode(str, Enum):
    """Fixed classification of MCP connection failures."""

    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    PROCESS_EXITED = "PROCESS_EXITED"
    HANDSHAKE_TIMEOUT = "HANDSHAKE_TIMEOUT"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"


class McpServerInfo(BaseModel):
    """Identity reported by an MCP server during the initialize handshake."""

    name: str
    version: str
    protocol_version: str


class McpToolInfo(BaseModel):
    """A single tool advertised by an MCP server."""

    name: str
    description: Optional[str] = None
    input_schema: dict[str, Any]


class McpConnectResult(BaseModel):
    """Result of an MCP connect, status check, or disconnect; never raised to callers."""

    status: McpConnectionStatus
    server_info: Optional[McpServerInfo] = None
    tools: list[McpToolInfo] = Field(default_factory=list)
    error_code: Optional[McpErrorCode] = None
    error_message: Optional[str] = None
    detail: Optional[str] = None
    stderr_tail: Optional[str] = None


def _validate_mcp_text(value: str | None) -> str | None:
    """Strip a name/command and reject it when nothing is left."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty")
    return stripped


def _validate_mcp_args(value: list[str] | None) -> list[str] | None:
    """Reject launch args longer than the per-arg limit."""
    if value is None:
        return None
    for arg in value:
        if len(arg) > MCP_ARG_MAX_LENGTH:
            raise ValueError(f"each arg must be at most {MCP_ARG_MAX_LENGTH} characters")
    return value


def _validate_mcp_env(value: dict[str, str] | None) -> dict[str, str] | None:
    """Require POSIX-style env var names and bounded values."""
    if value is None:
        return None
    for key, item in value.items():
        if not MCP_ENV_KEY_PATTERN.match(key):
            raise ValueError(f"invalid environment variable name: {key!r}")
        if len(item) > MCP_ENV_VALUE_MAX_LENGTH:
            raise ValueError(
                f"environment value must be at most {MCP_ENV_VALUE_MAX_LENGTH} characters",
            )
    return value


class McpServerCreate(BaseModel):
    """Request body for creating an MCP server config; any non-empty command is accepted."""

    name: str = Field(min_length=1, max_length=MCP_NAME_MAX_LENGTH)
    command: str = Field(min_length=1, max_length=MCP_COMMAND_MAX_LENGTH)
    args: list[str] = Field(default_factory=list, max_length=MCP_ARGS_MAX_ITEMS)
    env: dict[str, str] = Field(default_factory=dict, max_length=MCP_ENV_MAX_ITEMS)
    cwd: Optional[str] = Field(default=None, max_length=MCP_CWD_MAX_LENGTH)
    enabled: bool = True

    @field_validator("name", "command")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        """Strip and reject blank name/command."""
        return _validate_mcp_text(value) or ""

    @field_validator("args")
    @classmethod
    def _check_args(cls, value: list[str]) -> list[str]:
        """Bound each arg's length."""
        return _validate_mcp_args(value) or []

    @field_validator("env")
    @classmethod
    def _check_env(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate env names and value lengths."""
        return _validate_mcp_env(value) or {}


class McpServerUpdate(BaseModel):
    """Partial update for an MCP server config."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=MCP_NAME_MAX_LENGTH)
    command: Optional[str] = Field(default=None, min_length=1, max_length=MCP_COMMAND_MAX_LENGTH)
    args: Optional[list[str]] = Field(default=None, max_length=MCP_ARGS_MAX_ITEMS)
    env: Optional[dict[str, str]] = Field(default=None, max_length=MCP_ENV_MAX_ITEMS)
    cwd: Optional[str] = Field(default=None, max_length=MCP_CWD_MAX_LENGTH)
    enabled: Optional[bool] = None

    @field_validator("name", "command")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        """Strip and reject blank name/command when supplied."""
        return _validate_mcp_text(value)

    @field_validator("args")
    @classmethod
    def _check_args(cls, value: list[str] | None) -> list[str] | None:
        """Bound each arg's length when supplied."""
        return _validate_mcp_args(value)

    @field_validator("env")
    @classmethod
    def _check_env(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        """Validate env names and value lengths when supplied."""
        return _validate_mcp_env(value)


class McpServerResponse(BaseModel):
    """Serialized MCP server config; env values are never exposed, only their names."""

    id: int
    name: str
    command: str
    args: list[str]
    env_keys: list[str]
    cwd: Optional[str] = None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    connection: McpConnectResult


class MessagePayload(BaseModel):
    """WebSocket inbound chat message."""

    content: str = Field(min_length=1, max_length=CONTENT_MAX_LENGTH)
    model: str = Field(min_length=1, max_length=200)


class ModelLoadRequest(BaseModel):
    """Request body for loading a model in LM Studio."""

    model_id: str
    gpu_offload: int = Field(default=0, ge=-1, le=100)
    context_length: Optional[int] = Field(default=None, gt=0)


class LoginRequest(BaseModel):
    """Request body for username/password login."""

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserResponse(BaseModel):
    """Serialized user (never includes password_hash)."""

    id: int
    username: str = Field(max_length=USERNAME_MAX_LENGTH)


class CreateUserRequest(BaseModel):
    """Request body for creating an additional user account (D-06)."""

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class MemoryEntryResponse(BaseModel):
    """Serialized memory row for the inspection panel."""

    id: int
    key: str = Field(max_length=MEMORY_KEY_MAX_LENGTH)
    value: str = Field(max_length=MEMORY_VALUE_MAX_LENGTH)
    updated_at: datetime


class ChatMemoryResponse(BaseModel):
    """GET /api/v1/chats/{chat_id}/memory response: all three memory layers."""

    chat_id: int
    short_term_message_count: int
    working: list[MemoryEntryResponse]
    long_term: list[MemoryEntryResponse]


class SaveWorkingMemoryArgs(BaseModel):
    """Tool-call arguments for save_working_memory."""

    key: str = Field(
        min_length=1,
        max_length=MEMORY_KEY_MAX_LENGTH,
        description="Short stable identifier for this scratchpad entry, e.g. current_task_step",
    )
    content: str = Field(
        min_length=1,
        max_length=MEMORY_VALUE_MAX_LENGTH,
        description="The value to store",
    )


class SaveLongTermMemoryArgs(BaseModel):
    """Tool-call arguments for save_long_term_memory."""

    key: str = Field(
        min_length=1,
        max_length=MEMORY_KEY_MAX_LENGTH,
        description="Short stable identifier for this durable fact, e.g. user_name",
    )
    content: str = Field(
        min_length=1,
        max_length=MEMORY_VALUE_MAX_LENGTH,
        description="The durable fact, decision, or preference to store",
    )


class ProfileUpdate(BaseModel):
    """Partial profile update (style/format/constraints), always scoped to current_user."""

    style: Optional[str] = Field(default=None, max_length=PROFILE_FIELD_MAX_LENGTH)
    format: Optional[str] = Field(default=None, max_length=PROFILE_FIELD_MAX_LENGTH)
    constraints: Optional[str] = Field(default=None, max_length=PROFILE_FIELD_MAX_LENGTH)


class ProfileResponse(BaseModel):
    """Serialized profile returned to the client."""

    id: int
    style: str = Field(default="", max_length=PROFILE_FIELD_MAX_LENGTH)
    format: str = Field(default="", max_length=PROFILE_FIELD_MAX_LENGTH)
    constraints: str = Field(default="", max_length=PROFILE_FIELD_MAX_LENGTH)
    updated_at: datetime


class CreateTaskArgs(BaseModel):
    """Tool-call arguments for create_task."""

    title: str = Field(
        min_length=1,
        max_length=TASK_TITLE_MAX_LENGTH,
        description="Short, specific title for this unit of work",
    )
    description: str = Field(
        min_length=1,
        max_length=TASK_DESCRIPTION_MAX_LENGTH,
        description="What this task involves",
    )
    goal: str = Field(
        min_length=1,
        max_length=TASK_GOAL_MAX_LENGTH,
        description="Concrete, checkable definition of done for this task",
    )


class LlmTaskState(str, Enum):
    """States the LLM may transition a task into via transition_task.

    Deliberately omits CANCELLED (D-07): cancellation is a manual-UI-only
    action, never an LLM tool.
    """

    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    DONE = "done"


class TransitionTaskArgs(BaseModel):
    """Tool-call arguments for transition_task."""

    task_id: int = Field(
        gt=0,
        description="The numeric id of an existing task in this chat",
    )
    new_state: LlmTaskState = Field(
        description="The lifecycle state to move the task into",
    )
    note: str = Field(
        default="",
        max_length=TASK_NOTE_MAX_LENGTH,
        description="Optional justification or context for this transition",
    )


class PauseTaskArgs(BaseModel):
    """Tool-call arguments for pause_task."""

    task_id: int = Field(
        gt=0,
        description="The numeric id of an existing task in this chat to pause",
    )


class ResumeTaskArgs(BaseModel):
    """Tool-call arguments for resume_task."""

    task_id: int = Field(
        gt=0,
        description="The numeric id of an existing task in this chat to resume",
    )


class TaskTransitionResponse(BaseModel):
    """Serialized task state-change history entry."""

    from_state: Optional[str] = None
    to_state: str
    note: str = ""
    rejected: bool = False
    rejection_reason: Optional[str] = Field(default=None, max_length=TASK_NOTE_MAX_LENGTH)
    created_at: datetime


class TaskResponse(BaseModel):
    """Serialized task, including its full transition history (D-11)."""

    id: int
    title: str
    description: str
    goal: str
    state: str
    is_paused: bool
    delegate_to: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    history: list[TaskTransitionResponse]


class GlobalInvariantCreate(BaseModel):
    """Request body for creating a global invariant (D-01, D-02)."""

    title: str = Field(min_length=1, max_length=INVARIANT_TITLE_MAX_LENGTH)
    rule_text: str = Field(min_length=1, max_length=INVARIANT_RULE_MAX_LENGTH)


class GlobalInvariantUpdate(BaseModel):
    """Partial update for a global invariant (D-04, full CRUD)."""

    title: Optional[str] = Field(
        default=None, min_length=1, max_length=INVARIANT_TITLE_MAX_LENGTH,
    )
    rule_text: Optional[str] = Field(
        default=None, min_length=1, max_length=INVARIANT_RULE_MAX_LENGTH,
    )


class GlobalInvariantResponse(BaseModel):
    """Serialized global invariant returned to the client."""

    id: int
    title: str = Field(max_length=INVARIANT_TITLE_MAX_LENGTH)
    rule_text: str = Field(max_length=INVARIANT_RULE_MAX_LENGTH)
    created_at: datetime
    updated_at: datetime


class ChatInvariantCreate(BaseModel):
    """Request body for creating a per-chat invariant (D-01, D-05)."""

    title: str = Field(min_length=1, max_length=INVARIANT_TITLE_MAX_LENGTH)
    rule_text: str = Field(min_length=1, max_length=INVARIANT_RULE_MAX_LENGTH)
    overrides_id: Optional[int] = None


class ChatInvariantUpdate(BaseModel):
    """Partial update for a per-chat invariant (D-04, full CRUD)."""

    title: Optional[str] = Field(
        default=None, min_length=1, max_length=INVARIANT_TITLE_MAX_LENGTH,
    )
    rule_text: Optional[str] = Field(
        default=None, min_length=1, max_length=INVARIANT_RULE_MAX_LENGTH,
    )
    overrides_id: Optional[int] = None


class ChatInvariantResponse(BaseModel):
    """Serialized per-chat invariant, including the resolved title of its overridden global."""

    id: int
    chat_id: int
    title: str = Field(max_length=INVARIANT_TITLE_MAX_LENGTH)
    rule_text: str = Field(max_length=INVARIANT_RULE_MAX_LENGTH)
    overrides_id: Optional[int] = None
    overrides_title: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class InvariantConflictResponse(BaseModel):
    """Serialized invariant conflict record returned to the client (D-13)."""

    id: int
    chat_id: int
    message_id: int
    invariant_scope: str
    invariant_id: int
    invariant_title: str = Field(max_length=INVARIANT_TITLE_MAX_LENGTH)
    note: str = Field(default="", max_length=INVARIANT_CONFLICT_NOTE_MAX_LENGTH)
    created_at: datetime
