"""SQLModel database models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Enum as SAEnum, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


class ContextStrategy(str, Enum):
    """Context window management strategy for a chat."""

    SLIDING_WINDOW = "sliding"
    STICKY_FACTS = "sticky"
    TRUNCATE_MIDDLE = "truncate_middle"
    NO_COMPRESSION = "no_compression"


class Chat(SQLModel, table=True):
    """A conversation session."""

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(default="New Chat")
    current_leaf_message_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("message.id", ondelete="SET NULL", use_alter=True),
            nullable=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )


class Message(SQLModel, table=True):
    """A single message in a chat tree."""

    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    parent_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("message.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    role: str
    content: str
    token_count: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class Settings(SQLModel, table=True):
    """Per-chat or global LLM / context settings."""

    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="CASCADE"),
            unique=True,
            nullable=True,
        ),
    )
    system_prompt: str = Field(default="You are a helpful assistant.")
    temperature: float = Field(default=0.7)
    context_length: int = Field(default=4096, ge=512, le=131072)
    max_tokens: int = Field(default=4096)
    strategy: ContextStrategy = Field(
        default=ContextStrategy.SLIDING_WINDOW,
        sa_column=Column(
            SAEnum(
                ContextStrategy,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
        ),
    )
    facts_json: str = Field(default="{}")
    summary_text: str = Field(default="")
    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )


class TokenUsage(SQLModel, table=True):
    """Daily token usage aggregated per chat."""

    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    date: datetime
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)


class User(SQLModel, table=True):
    """A registered account (every account has equal "admin" capability)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class WorkingMemory(SQLModel, table=True):
    """Key-value scratchpad for a chat's current task data (D-03)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    chat_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("chat_id", "key", name="uq_working_memory_chat_key"),)


class LongTermMemory(SQLModel, table=True):
    """User-scoped, cross-chat memory for profile/decisions/knowledge (D-02)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    key: str = Field(max_length=200)
    value: str = Field(max_length=50_000)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_long_term_memory_user_key"),)


class Session(SQLModel, table=True):
    """A server-side, revocable login session for a user."""

    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    expires_at: datetime
