"""SQLModel database models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Column,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
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
    tool_trace: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    rag_sources: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
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
    context_length: int = Field(default=65536, ge=512, le=131072)
    max_tokens: int = Field(default=65536)
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


class ScheduleType(str, Enum):
    """How a scheduled job repeats."""

    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


class ScheduledTaskStatus(str, Enum):
    """Lifecycle status of a scheduled job."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RunStatus(str, Enum):
    """Outcome of a single scheduled job run."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunTrigger(str, Enum):
    """What started a run."""

    SCHEDULE = "schedule"
    MANUAL = "manual"


class ScheduledTask(SQLModel, table=True):
    """A user-scoped job that runs a prompt on a schedule (once, interval or cron)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    origin_chat_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("chat.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    title: str = Field(max_length=200)
    prompt: str = Field(sa_column=Column(Text, nullable=False))
    model: str = Field(max_length=200)
    # Plain integer, no FK: a deleted provider must stay 'unavailable', never NULL (= legacy LM Studio).
    provider_id: Optional[int] = Field(default=None)
    schedule_type: ScheduleType = Field(
        sa_column=Column(
            SAEnum(
                ScheduleType,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
    run_at: Optional[datetime] = Field(default=None)
    interval_seconds: Optional[int] = Field(default=None)
    cron_expr: Optional[str] = Field(default=None, max_length=100)
    max_runs: Optional[int] = Field(default=None)
    run_count: int = Field(default=0)
    next_run_at: Optional[datetime] = Field(default=None)
    status: ScheduledTaskStatus = Field(
        default=ScheduledTaskStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                ScheduledTaskStatus,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (Index("ix_scheduledtask_status_next", "status", "next_run_at"),)


class TaskRun(SQLModel, table=True):
    """One execution attempt of a scheduled job, including skipped overlaps."""

    id: Optional[int] = Field(default=None, primary_key=True)
    scheduled_task_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("scheduledtask.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    status: RunStatus = Field(
        sa_column=Column(
            SAEnum(
                RunStatus,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
    trigger: RunTrigger = Field(
        sa_column=Column(
            SAEnum(
                RunTrigger,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
        ),
    )
    scheduled_for: Optional[datetime] = Field(default=None)
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    finished_at: Optional[datetime] = Field(default=None)
    is_late: bool = Field(default=False)
    model: str = Field(max_length=200)
    result_text: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    tool_trace: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # At most one RUNNING row per job (overlap invariant); other statuses are unrestricted.
    __table_args__ = (
        Index(
            "uq_taskrun_one_running",
            "scheduled_task_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
    )


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
    # False means the user pressed disconnect; lazy auto-connect must skip the server.
    auto_connect: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class LlmProvider(SQLModel, table=True):
    """User-scoped OpenAI-compatible LLM provider; the API key is stored only as a .env variable name."""

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
    base_url: str = Field(max_length=500)
    kind: str = Field(default="openai", max_length=20)
    api_key_env: Optional[str] = Field(default=None, max_length=100)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_llmprovider_user_name"),)


class LlmProviderSeed(SQLModel, table=True):
    """Marker that a provider was seeded once for a user, so deleting it sticks."""

    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    seed_key: str = Field(primary_key=True, max_length=50)


class KbStatus(str, Enum):
    """Indexing lifecycle status of a knowledge base."""

    QUEUED = "queued"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class KbStrategy(str, Enum):
    """Chunking strategy used when indexing a knowledge base."""

    FIXED = "fixed"
    STRUCTURAL = "structural"


def _kb_enum_column(enum_cls: type[Enum]) -> Column:
    """Build a non-null enum column that stores member values."""
    return Column(
        SAEnum(enum_cls, values_callable=lambda cls: [member.value for member in cls]),
        nullable=False,
    )


class KnowledgeBase(SQLModel, table=True):
    """A user-owned document collection with its indexing configuration and progress."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    name: str = Field(max_length=200)
    status: KbStatus = Field(default=KbStatus.QUEUED, sa_column=_kb_enum_column(KbStatus))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    strategy: KbStrategy = Field(sa_column=_kb_enum_column(KbStrategy))
    chunk_size: int
    chunk_overlap: int
    embedding_model: str
    dim: int | None = None
    query_prefix: str = ""
    doc_prefix: str = ""
    file_count: int = 0
    chunk_count: int = 0
    done_chunks: int = 0
    total_chunks: int = 0
    phase: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class KbDocument(SQLModel, table=True):
    """A source file uploaded into a knowledge base."""

    __table_args__ = (UniqueConstraint("kb_id", "sha256", name="uq_kbdocument_kb_sha256"),)

    id: int | None = Field(default=None, primary_key=True)
    kb_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("knowledgebase.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    filename: str
    sha256: str = Field(max_length=64)
    size_bytes: int
    page_count: int | None = None
    stored_name: str


class KbChunk(SQLModel, table=True):
    """A text chunk of a document; its id doubles as the FAISS vector id."""

    id: int | None = Field(default=None, primary_key=True)
    kb_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("knowledgebase.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    document_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("kbdocument.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    chunk_index: int
    chunk_id: str
    text: str = Field(sa_column=Column(Text, nullable=False))
    section: str | None = None
    source: str
    title: str
    page_start: int | None = None
    char_start: int
    char_end: int


class ChatRagConfig(SQLModel, table=True):
    """Per-chat RAG settings; an absent row means RAG is off.

    The four stage flags (lexical, llm_rerank, hybrid, rewrite) are independent
    switches, not a mode ladder. A None threshold means the calibrated value of
    the attached KB's embedding model; a number is a user override. The strict
    flag turns on quotes plus the «не знаю» gate together.
    """

    chat_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("chat.id", ondelete="CASCADE"), primary_key=True
        )
    )
    kb_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("knowledgebase.id", ondelete="SET NULL"), nullable=True
        ),
    )
    mode: str = Field(default="off", max_length=20)
    top_k: int = Field(default=5)
    candidate_k: int = Field(default=20)
    threshold: float | None = Field(default=None)
    lexical: bool = Field(default=False)
    llm_rerank: bool = Field(default=False)
    hybrid: bool = Field(default=False)
    rewrite: bool = Field(default=False)
    strict: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
