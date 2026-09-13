"""SQLModel database models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel


class ContextStrategy(str, Enum):
    """Context window management strategy for a chat."""

    SLIDING = "sliding"
    STICKY = "sticky"
    BRANCHING = "branching"


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
    max_tokens: int = Field(default=4096)
    strategy: ContextStrategy = Field(default=ContextStrategy.SLIDING)
    facts_json: str = Field(default="{}")
    summary_text: str = Field(default="")


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
