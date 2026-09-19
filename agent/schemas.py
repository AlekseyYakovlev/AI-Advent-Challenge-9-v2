"""Pydantic request/response schemas for the Agent REST API."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from shared.models import ContextStrategy

TITLE_MAX_LENGTH = 200
ROLE_MAX_LENGTH = 50
CONTENT_MAX_LENGTH = 100_000
SYSTEM_PROMPT_MAX_LENGTH = 10_000
FACTS_JSON_MAX_LENGTH = 50_000
SUMMARY_TEXT_MAX_LENGTH = 50_000
USERNAME_MAX_LENGTH = 64
PASSWORD_MAX_LENGTH = 128


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
