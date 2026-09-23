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


class Profile(SQLModel, table=True):
    """User-scoped style/format/constraint preferences, injected into every request (D-01)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
    )
    style: str = Field(default="", max_length=2000)
    format: str = Field(default="", max_length=2000)
    constraints: str = Field(default="", max_length=2000)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class GlobalInvariant(SQLModel, table=True):
    """App-wide ground rule shared by every account (D-02 — deliberately NOT user_id-scoped)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=200)
    rule_text: str = Field(max_length=2000)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class ChatInvariant(SQLModel, table=True):
    """Per-chat ground rule layered on top of (and optionally overriding) a global invariant (D-05)."""

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
    title: str = Field(max_length=200)
    rule_text: str = Field(max_length=2000)
    overrides_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("globalinvariant.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class InvariantConflict(SQLModel, table=True):
    """Persisted record of a detected invariant conflict (D-13)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    message_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("message.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    invariant_scope: str = Field(max_length=10)
    invariant_id: int
    invariant_title: str = Field(max_length=200)
    note: str = Field(default="", max_length=2000)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class TaskState(str, Enum):
    """Lifecycle state of a task (TASK-01)."""

    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    DONE = "done"
    CANCELLED = "cancelled"


class Task(SQLModel, table=True):
    """A discrete, chat-scoped unit of work created by the LLM (TASK-01/02/03)."""

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
    title: str = Field(max_length=200)
    description: str = Field(default="", max_length=5_000)
    goal: str = Field(default="", max_length=2_000)
    state: TaskState = Field(
        default=TaskState.PLANNING,
        sa_column=Column(
            SAEnum(
                TaskState,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
    is_paused: bool = Field(default=False)
    delegate_to: Optional[str] = Field(default=None, max_length=200)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class TaskTransition(SQLModel, table=True):
    """Append-only state-change history for a Task (D-11)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("task.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    from_state: Optional[TaskState] = Field(
        default=None,
        sa_column=Column(
            SAEnum(
                TaskState,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=True,
        ),
    )
    to_state: TaskState = Field(
        sa_column=Column(
            SAEnum(
                TaskState,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
    note: str = Field(default="", max_length=2_000)
    rejected: bool = Field(default=False)
    rejection_reason: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


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


class McpServerConfig(SQLModel, table=True):
    """User-scoped MCP stdio server launch config."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    name: str = Field(max_length=100)
    command: str = Field(max_length=1000)
    args_json: str = Field(default="[]")
    env_json: str = Field(default="{}")
    cwd: Optional[str] = Field(default=None, max_length=1000)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
