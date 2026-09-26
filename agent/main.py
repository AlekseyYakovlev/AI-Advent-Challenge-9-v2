"""Agent FastAPI application: REST API and in-memory state for WebSockets."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.dependencies import (
    get_current_user,
    require_allowed_origin,
    require_json_content_type,
)
from agent.schemas import (
    BranchRequest,
    ChatCreate,
    ChatInvariantCreate,
    ChatInvariantResponse,
    ChatInvariantUpdate,
    ChatMemoryResponse,
    ChatResponse,
    CreateUserRequest,
    GlobalInvariantCreate,
    GlobalInvariantResponse,
    GlobalInvariantUpdate,
    HealthResponse,
    InvariantConflictResponse,
    LoginRequest,
    McpConnectionStatus,
    McpConnectResult,
    McpErrorCode,
    McpServerCreate,
    McpServerResponse,
    McpServerUpdate,
    MemoryEntryResponse,
    MessageResponse,
    ModelLoadRequest,
    ModelLoadResult,
    ProfileResponse,
    ProfileUpdate,
    SettingsResponse,
    SettingsUpdate,
    TaskResponse,
    TaskTransitionResponse,
    UserResponse,
)
from agent.llm_client import LMStudioClient
from agent.state import CORS_ORIGINS, chat_locks, cleanup_chat_caches
from agent.context_engine import compute_chat_stats
from agent import invariants, mcp_client, mcp_config, memory, profile, tasks
from agent.events import ws_events
from agent.scheduler import scheduler
from agent.scheduler_api import router as scheduler_router
from agent.ws import ws_chat
from shared.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from shared.config import settings as app_config
from shared.database import engine, get_session, init_db
from shared.logger import get_logger
from shared.models import (
    Chat,
    ChatInvariant,
    ContextStrategy,
    GlobalInvariant,
    InvariantConflict,
    McpServerConfig,
    Message,
    Profile,
    Session as SessionRow,
    Settings,
    Task,
    TaskTransition,
    User,
)
from shared.runtime import check_python_version

# The Agent is spawned by the supervisor, so it guards its own interpreter too.
check_python_version()

logger = get_logger(__name__)

lm_studio_client = LMStudioClient()


async def _ensure_global_settings(session: AsyncSession, user_id: int | None) -> Settings:
    """Create default global settings row for the given owner when missing."""
    conditions = [Settings.chat_id.is_(None)]
    if user_id is None:
        conditions.append(Settings.user_id.is_(None))
    else:
        conditions.append(Settings.user_id == user_id)
    result = await session.exec(select(Settings).where(*conditions))
    row = result.first()
    if row is not None:
        return row
    row = Settings(chat_id=None, user_id=user_id)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _get_chat_or_404(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
) -> Chat:
    """Load a chat owned by user_id or raise HTTP 404 (never 403, to avoid an IDOR oracle)."""
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        logger.warning("chat_access_denied", chat_id=chat_id, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat {chat_id} not found",
        )
    return chat


async def _get_task_or_404(
    session: AsyncSession,
    task_id: int,
    user_id: int,
) -> Task:
    """Load a task owned by user_id or raise HTTP 404 (never 403, to avoid an IDOR oracle)."""
    task = await session.get(Task, task_id)
    if task is None or task.user_id != user_id:
        logger.warning("task_access_denied", task_id=task_id, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return task


async def _get_chat_invariant_or_404(
    session: AsyncSession,
    chat_id: int,
    invariant_id: int,
) -> ChatInvariant:
    """Load a per-chat invariant belonging to chat_id or raise HTTP 404 (never 403)."""
    invariant = await invariants.get_chat_invariant(session, invariant_id)
    if invariant is None or invariant.chat_id != chat_id:
        logger.warning(
            "invariant_access_denied", chat_id=chat_id, invariant_id=invariant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invariant {invariant_id} not found",
        )
    return invariant


def _env_mask_error(
    user_id: int,
    server_id: int | None,
    exc: mcp_config.EnvMaskError,
) -> HTTPException:
    """Build the HTTP 422 for a masked env value whose key has nothing stored (key name only)."""
    logger.warning("mcp_env_mask_rejected", user_id=user_id, server_id=server_id, key=exc.key)
    return HTTPException(
        status_code=422,  # the status constant was renamed across Starlette versions
        detail=(
            f"Для переменной {exc.key} значение скрыто (•••), но такой переменной нет "
            "в сохранённых. Введите значение заново."
        ),
    )


async def _get_mcp_server_or_404(
    session: AsyncSession,
    user_id: int,
    server_id: int,
) -> McpServerConfig:
    """Load an MCP server config owned by user_id or raise HTTP 404 (never 403)."""
    row = await mcp_config.get_server(session, user_id, server_id)
    if row is None:
        logger.warning("mcp_server_access_denied", user_id=user_id, server_id=server_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id} not found",
        )
    return row


def _mcp_server_to_response(
    row: McpServerConfig,
    connection: McpConnectResult,
) -> McpServerResponse:
    """Serialize a config with its connection state; env values are reduced to key names."""
    return McpServerResponse(
        id=row.id,
        name=row.name,
        command=row.command,
        args=mcp_config.load_args(row),
        env_keys=sorted(mcp_config.load_env(row).keys()),
        cwd=row.cwd,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
        connection=connection,
    )


async def _resolve_settings(
    session: AsyncSession,
    chat_id: int | None,
    user_id: int,
) -> Settings:
    """Return per-chat settings or fall back to the caller's global defaults."""
    if chat_id is not None:
        await _get_chat_or_404(session, chat_id, user_id)
        result = await session.exec(
            select(Settings).where(Settings.chat_id == chat_id),
        )
        row = result.first()
        if row is not None:
            return row
    return await _ensure_global_settings(session, user_id)


def _normalize_strategy(strategy: str | ContextStrategy) -> ContextStrategy:
    """Map stored strategy strings to a valid enum, including legacy values."""
    if isinstance(strategy, ContextStrategy):
        return strategy
    valid = {item.value for item in ContextStrategy}
    if strategy in valid:
        return ContextStrategy(strategy)
    return ContextStrategy.SLIDING_WINDOW


def _settings_to_response(row: Settings) -> SettingsResponse:
    """Map a Settings ORM row to the API response schema."""
    return SettingsResponse(
        id=row.id,
        chat_id=row.chat_id,
        system_prompt=row.system_prompt,
        temperature=row.temperature,
        context_length=row.context_length,
        max_tokens=row.max_tokens,
        strategy=_normalize_strategy(row.strategy),
        facts_json=row.facts_json,
        summary_text=row.summary_text,
    )


def _profile_to_response(row: Profile) -> ProfileResponse:
    """Map a Profile ORM row to the API response schema."""
    return ProfileResponse(
        id=row.id,
        style=row.style,
        format=row.format,
        constraints=row.constraints,
        updated_at=row.updated_at,
    )


def _global_invariant_to_response(row: GlobalInvariant) -> GlobalInvariantResponse:
    """Map a GlobalInvariant ORM row to the API response schema."""
    return GlobalInvariantResponse(
        id=row.id,
        title=row.title,
        rule_text=row.rule_text,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _chat_invariant_to_response(
    session: AsyncSession,
    row: ChatInvariant,
) -> ChatInvariantResponse:
    """Map a ChatInvariant ORM row to the API response schema, resolving overrides_title."""
    overrides_title: str | None = None
    if row.overrides_id is not None:
        overridden = await invariants.get_global(session, row.overrides_id)
        overrides_title = overridden.title if overridden is not None else None
    return ChatInvariantResponse(
        id=row.id,
        chat_id=row.chat_id,
        title=row.title,
        rule_text=row.rule_text,
        overrides_id=row.overrides_id,
        overrides_title=overrides_title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _invariant_conflict_to_response(row: InvariantConflict) -> InvariantConflictResponse:
    """Map an InvariantConflict ORM row to the API response schema."""
    return InvariantConflictResponse(
        id=row.id,
        chat_id=row.chat_id,
        message_id=row.message_id,
        invariant_scope=row.invariant_scope,
        invariant_id=row.invariant_id,
        invariant_title=row.invariant_title,
        note=row.note,
        created_at=row.created_at,
    )


def _task_to_response(row: Task, history: list[TaskTransition]) -> TaskResponse:
    """Map a Task ORM row and its transition history to the API response schema."""
    return TaskResponse(
        id=row.id,
        title=row.title,
        description=row.description,
        goal=row.goal,
        state=row.state.value,
        is_paused=row.is_paused,
        delegate_to=row.delegate_to,
        created_at=row.created_at,
        updated_at=row.updated_at,
        history=[
            TaskTransitionResponse(
                from_state=(h.from_state.value if h.from_state is not None else None),
                to_state=h.to_state.value,
                note=h.note,
                rejected=h.rejected,
                rejection_reason=h.rejection_reason,
                created_at=h.created_at,
            )
            for h in history
        ],
    )


async def _task_response_with_history(session: AsyncSession, row: Task) -> TaskResponse:
    """Map a Task ORM row to its response shape, embedding freshly loaded history."""
    history = await tasks.list_transitions(session, row.id)
    return _task_to_response(row, history)


def _chat_to_response(chat: Chat) -> ChatResponse:
    """Map a Chat ORM row to the API response schema."""
    return ChatResponse(
        id=chat.id,
        title=chat.title,
        current_leaf_message_id=chat.current_leaf_message_id,
        created_at=chat.created_at,
    )


def _message_to_response(message: Message) -> MessageResponse:
    """Map a Message ORM row to the API response schema."""
    return MessageResponse(
        id=message.id,
        chat_id=message.chat_id,
        parent_id=message.parent_id,
        role=message.role,
        content=message.content,
        token_count=message.token_count,
        created_at=message.created_at,
    )


async def _build_tree_path(
    session: AsyncSession,
    chat: Chat,
) -> list[Message]:
    """Walk parent links from the current leaf up to the root."""
    if chat.current_leaf_message_id is None:
        return []
    path: list[Message] = []
    current_id: int | None = chat.current_leaf_message_id
    while current_id is not None:
        message = await session.get(Message, current_id)
        if message is None or message.chat_id != chat.id:
            break
        path.append(message)
        current_id = message.parent_id
    return path


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Init the database and scheduler on startup; stop the scheduler and dispose the engine on exit."""
    logger.info("agent_starting")
    await init_db()
    # The supervisor hard-kills the Agent, so shutdown hooks may never have run: fail any
    # run left RUNNING before the loop can claim or block on it.
    await scheduler.recover_orphaned_runs()
    if app_config.SCHEDULER_ENABLED:
        await scheduler.start()
    yield
    logger.info("agent_shutting_down")
    await scheduler.stop()
    await mcp_client.cleanup_all_sessions()
    await engine.dispose()


app = FastAPI(title="AI Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scheduler_router)


@app.get("/debug/routes")
async def debug_routes() -> dict[str, list[dict[str, Any]]]:
    """List all registered routes for debugging."""
    routes: list[dict[str, Any]] = []
    for route in app.routes:
        route_info: dict[str, Any] = {
            "path": getattr(route, "path", str(route)),
            "methods": list(getattr(route, "methods", []) or []),
            "name": getattr(route, "name", None),
        }
        if hasattr(route, "path_format"):
            route_info["path_format"] = route.path_format
        routes.append(route_info)
    return {"routes": routes}


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service health status."""
    return HealthResponse()


@app.post("/api/v1/auth/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    """Verify credentials and issue a DB-backed HTTP-only session cookie."""
    result = await session.exec(select(User).where(User.username == body.username))
    user = result.first()
    if user is None or not verify_password(body.password, user.password_hash):
        logger.info("login_failed", username=body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль",
        )

    token = generate_session_token()
    session_row = SessionRow(
        token_hash=hash_session_token(token),
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS),
    )
    session.add(session_row)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
    )
    logger.info("login_success", username=user.username)
    return UserResponse(id=user.id, username=user.username)


async def _has_live_web_session(session: AsyncSession, user_id: int) -> bool:
    """Return True if the user still has a non-expired web session row."""
    result = await session.exec(select(SessionRow).where(SessionRow.user_id == user_id))
    now: datetime = datetime.now(timezone.utc)
    for row in result.all():
        expires_at: datetime = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at >= now:
            return True
    return False


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Revoke the presented session server-side and clear the cookie."""
    if session_id is not None:
        result = await session.exec(
            select(SessionRow).where(SessionRow.token_hash == hash_session_token(session_id)),
        )
        row = result.first()
        if row is not None:
            try:
                await session.delete(row)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    # A user with another live web session (other tab/device) keeps their MCP sessions.
    # An expired web session that never calls logout is still not covered by this cleanup.
    try:
        if not await _has_live_web_session(session, current_user.id):
            await mcp_client.cleanup_user_sessions(current_user.id)
    except Exception as exc:
        # MCP cleanup must never make logout fail.
        logger.warning(
            "logout_mcp_cleanup_failed",
            user_id=current_user.id,
            error_type=type(exc).__name__,
        )
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    logger.info("logout", user_id=current_user.id)


@app.get("/api/v1/auth/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the currently authenticated user."""
    return UserResponse(id=current_user.id, username=current_user.username)


@app.post(
    "/api/v1/auth/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    body: CreateUserRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Create an additional equal-privilege account (AUTH-02); no public signup route exists (D-06)."""
    result = await session.exec(select(User).where(User.username == body.username))
    if result.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким именем уже существует",
        )

    row = User(username=body.username, password_hash=hash_password(body.password))
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким именем уже существует",
        ) from None
    except Exception:
        await session.rollback()
        raise

    logger.info("user_created", username=body.username, created_by=current_user.id)
    return UserResponse(id=row.id, username=row.username)


@app.get("/api/v1/chats", response_model=list[ChatResponse])
async def list_chats(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ChatResponse]:
    """List the caller's chats ordered by creation time descending."""
    result = await session.exec(
        select(Chat)
        .where(Chat.user_id == current_user.id)
        .order_by(Chat.created_at.desc()),
    )
    return [_chat_to_response(chat) for chat in result.all()]


@app.post(
    "/api/v1/chats",
    response_model=ChatResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat(
    body: ChatCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatResponse:
    """Create a new chat session owned by the caller."""
    chat = Chat(title=body.title, user_id=current_user.id)
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return _chat_to_response(chat)


@app.delete(
    "/api/v1/chats/{chat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a chat and clear related in-memory caches."""
    chat = await _get_chat_or_404(session, chat_id, current_user.id)
    try:
        await session.delete(chat)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    cleanup_chat_caches(chat_id)


@app.get(
    "/api/v1/chats/{chat_id}/tree",
    response_model=list[MessageResponse],
)
async def get_chat_tree(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[MessageResponse]:
    """Return the message path from the current leaf to the root."""
    chat = await _get_chat_or_404(session, chat_id, current_user.id)
    path = await _build_tree_path(session, chat)
    return [_message_to_response(msg) for msg in path]


@app.get("/api/v1/chats/{chat_id}/stats")
async def get_chat_stats(
    chat_id: int,
    model: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Get real-time statistics for a chat."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    return await compute_chat_stats(session, chat_id, model=model)


@app.get("/api/v1/chats/{chat_id}/memory", response_model=ChatMemoryResponse)
async def get_chat_memory(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatMemoryResponse:
    """Return this chat's working memory and the caller's full long-term memory (D-02)."""
    chat = await _get_chat_or_404(session, chat_id, current_user.id)
    working = await memory.list_working_memory(session, chat_id)
    # user-scoped by design (D-02): long-term memory is cross-chat
    long_term = await memory.list_long_term_memory(session, current_user.id)
    path = await _build_tree_path(session, chat)
    return ChatMemoryResponse(
        chat_id=chat_id,
        short_term_message_count=len(path),
        working=[
            MemoryEntryResponse(id=row.id, key=row.key, value=row.value, updated_at=row.updated_at)
            for row in working
        ],
        long_term=[
            MemoryEntryResponse(id=row.id, key=row.key, value=row.value, updated_at=row.updated_at)
            for row in long_term
        ],
    )


@app.get("/api/v1/chats/{chat_id}/tasks", response_model=list[TaskResponse])
async def get_chat_tasks(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[TaskResponse]:
    """Return this chat's tasks with embedded, oldest-first transition history."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    rows = await tasks.list_tasks_for_chat(session, chat_id)
    return [
        _task_to_response(row, await tasks.list_transitions(session, row.id)) for row in rows
    ]


@app.get(
    "/api/v1/chats/{chat_id}/invariants",
    response_model=list[ChatInvariantResponse],
)
async def get_chat_invariants(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ChatInvariantResponse]:
    """Return this chat's per-chat invariants (ownership-checked, D-05)."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    rows = await invariants.list_chat_invariants(session, chat_id)
    return [await _chat_invariant_to_response(session, row) for row in rows]


@app.post(
    "/api/v1/chats/{chat_id}/invariants",
    response_model=ChatInvariantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_invariant_endpoint(
    chat_id: int,
    body: ChatInvariantCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatInvariantResponse:
    """Create a per-chat invariant, optionally overriding an existing global one (D-05)."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    if body.overrides_id is not None:
        global_row = await invariants.get_global(session, body.overrides_id)
        if global_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invariant {body.overrides_id} not found",
            )
    row = await invariants.create_chat_invariant(
        session, current_user.id, chat_id, body.title, body.rule_text, body.overrides_id,
    )
    return await _chat_invariant_to_response(session, row)


@app.put(
    "/api/v1/chats/{chat_id}/invariants/{invariant_id}",
    response_model=ChatInvariantResponse,
)
async def update_chat_invariant_endpoint(
    chat_id: int,
    invariant_id: int,
    body: ChatInvariantUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatInvariantResponse:
    """Update a per-chat invariant (ownership-checked, D-05)."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    await _get_chat_invariant_or_404(session, chat_id, invariant_id)
    updates = body.model_dump(exclude_unset=True)
    if updates.get("overrides_id") is not None:
        global_row = await invariants.get_global(session, updates["overrides_id"])
        if global_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invariant {updates['overrides_id']} not found",
            )
    row = await invariants.update_chat_invariant(session, invariant_id, **updates)
    return await _chat_invariant_to_response(session, row)


@app.delete(
    "/api/v1/chats/{chat_id}/invariants/{invariant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_chat_invariant_endpoint(
    chat_id: int,
    invariant_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a per-chat invariant (ownership-checked, D-05)."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    await _get_chat_invariant_or_404(session, chat_id, invariant_id)
    await invariants.delete_chat_invariant(session, invariant_id)


@app.get(
    "/api/v1/chats/{chat_id}/invariant-conflicts",
    response_model=list[InvariantConflictResponse],
)
async def get_invariant_conflicts(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[InvariantConflictResponse]:
    """Return this chat's persisted invariant-conflict log (ownership-checked, D-13)."""
    await _get_chat_or_404(session, chat_id, current_user.id)
    rows = await invariants.list_conflicts(session, chat_id)
    return [_invariant_conflict_to_response(row) for row in rows]


@app.post("/api/v1/tasks/{task_id}/pause", response_model=TaskResponse)
async def pause_task_endpoint(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Pause a task directly, bypassing the tool dispatcher (D-12, manual UI control)."""
    task = await _get_task_or_404(session, task_id, current_user.id)
    if task.chat_id not in chat_locks:
        chat_locks[task.chat_id] = asyncio.Lock()
    async with chat_locks[task.chat_id]:
        try:
            row = await tasks.set_paused(session, current_user.id, task.chat_id, task_id, True)
            return await _task_response_with_history(session, row)
        except tasks.IllegalTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc),
            ) from exc


@app.post("/api/v1/tasks/{task_id}/resume", response_model=TaskResponse)
async def resume_task_endpoint(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Resume a task directly, bypassing the tool dispatcher (D-12, manual UI control)."""
    task = await _get_task_or_404(session, task_id, current_user.id)
    if task.chat_id not in chat_locks:
        chat_locks[task.chat_id] = asyncio.Lock()
    async with chat_locks[task.chat_id]:
        try:
            row = await tasks.set_paused(session, current_user.id, task.chat_id, task_id, False)
            return await _task_response_with_history(session, row)
        except tasks.IllegalTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc),
            ) from exc


@app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task_endpoint(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Cancel a task directly, bypassing the tool dispatcher (D-07/D-12: manual-only)."""
    task = await _get_task_or_404(session, task_id, current_user.id)
    if task.chat_id not in chat_locks:
        chat_locks[task.chat_id] = asyncio.Lock()
    async with chat_locks[task.chat_id]:
        try:
            row = await tasks.cancel_task(session, current_user.id, task.chat_id, task_id)
            return await _task_response_with_history(session, row)
        except tasks.IllegalTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc),
            ) from exc


@app.post(
    "/api/v1/chats/{chat_id}/branch",
    response_model=ChatResponse,
)
async def branch_chat(
    chat_id: int,
    body: BranchRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatResponse:
    """Switch the active branch to the given message."""
    chat = await _get_chat_or_404(session, chat_id, current_user.id)
    message = await session.get(Message, body.message_id)
    if message is None or message.chat_id != chat_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {body.message_id} not found in chat {chat_id}",
        )
    chat.current_leaf_message_id = body.message_id
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return _chat_to_response(chat)


@app.get("/api/v1/settings", response_model=SettingsResponse)
async def get_settings(
    chat_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SettingsResponse:
    """Return effective settings with global fallback, scoped to the caller."""
    row = await _resolve_settings(session, chat_id, current_user.id)
    return _settings_to_response(row)


@app.put("/api/v1/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SettingsResponse:
    """Create or update global or per-chat settings owned by the caller."""
    if body.chat_id is None:
        row = await _ensure_global_settings(session, current_user.id)
    else:
        await _get_chat_or_404(session, body.chat_id, current_user.id)
        result = await session.exec(
            select(Settings).where(Settings.chat_id == body.chat_id),
        )
        row = result.first()
        if row is None:
            row = Settings(chat_id=body.chat_id, user_id=current_user.id)
            session.add(row)
    updates = body.model_dump(exclude_unset=True, exclude={"chat_id"})
    for field, value in updates.items():
        setattr(row, field, value)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    return _settings_to_response(row)


@app.get("/api/v1/profile", response_model=ProfileResponse)
async def get_profile(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """Return the caller's profile, lazily creating an empty row on first access."""
    row = await profile.get_or_create_profile(session, current_user.id)
    return _profile_to_response(row)


@app.put("/api/v1/profile", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """Update the caller's profile fields (UI-only write path, D-02)."""
    updates = body.model_dump(exclude_unset=True)
    row = await profile.update_profile(session, current_user.id, **updates)
    return _profile_to_response(row)


@app.get("/api/v1/invariants", response_model=list[GlobalInvariantResponse])
async def list_global_invariants(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[GlobalInvariantResponse]:
    """Return every global invariant. Auth required, but NOT ownership-filtered (D-02) —
    global invariants are a genuinely app-wide shared set, not a per-user resource.
    """
    rows = await invariants.list_global(session)
    return [_global_invariant_to_response(row) for row in rows]


@app.post(
    "/api/v1/invariants",
    response_model=GlobalInvariantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_global_invariant(
    body: GlobalInvariantCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GlobalInvariantResponse:
    """Create a global invariant. Any logged-in user may do this (D-02) — every account
    has equal "admin" capability, so no ownership check exists by design.
    """
    row = await invariants.create_global(session, body.title, body.rule_text)
    return _global_invariant_to_response(row)


@app.put("/api/v1/invariants/{invariant_id}", response_model=GlobalInvariantResponse)
async def update_global_invariant(
    invariant_id: int,
    body: GlobalInvariantUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GlobalInvariantResponse:
    """Update a global invariant. No ownership filter (D-02) — shared by every account."""
    updates = body.model_dump(exclude_unset=True)
    row = await invariants.update_global(session, invariant_id, **updates)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invariant {invariant_id} not found",
        )
    return _global_invariant_to_response(row)


@app.delete("/api/v1/invariants/{invariant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_global_invariant(
    invariant_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a global invariant. No ownership filter (D-02) — shared by every account."""
    deleted = await invariants.delete_global(session, invariant_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invariant {invariant_id} not found",
        )


@app.get("/api/v1/mcp/servers", response_model=list[McpServerResponse])
async def list_mcp_servers(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[McpServerResponse]:
    """List the user's MCP servers, checking liveness of connected ones."""
    rows = await mcp_config.list_servers(session, current_user.id)
    statuses = await asyncio.gather(
        *(mcp_client.get_status(current_user.id, row.id) for row in rows),
        return_exceptions=True,
    )
    responses: list[McpServerResponse] = []
    for row, outcome in zip(rows, statuses):
        if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, Exception):
            # One broken server must not fail the whole list.
            logger.error(
                "mcp_status_check_failed",
                user_id=current_user.id,
                server_id=row.id,
                error=type(outcome).__name__,
            )
            connection = mcp_client.build_error_result(
                McpErrorCode.PROTOCOL_ERROR,
                f"status check failed: {type(outcome).__name__}",
                "",
                app_config.MCP_CONNECT_TIMEOUT,
            )
        else:
            connection = outcome
        responses.append(_mcp_server_to_response(row, connection))
    return responses


@app.post(
    "/api/v1/mcp/servers",
    response_model=McpServerResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def create_mcp_server(
    body: McpServerCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
    """Create an MCP server config for the current user."""
    try:
        row = await mcp_config.create_server(
            session,
            current_user.id,
            body.name,
            body.command,
            body.args,
            body.env,
            body.cwd,
            body.enabled,
        )
    except mcp_config.EnvMaskError as exc:
        raise _env_mask_error(current_user.id, None, exc) from exc
    connection = McpConnectResult(status=McpConnectionStatus.NOT_CONNECTED)
    return _mcp_server_to_response(row, connection)


@app.put(
    "/api/v1/mcp/servers/{server_id}",
    response_model=McpServerResponse,
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def update_mcp_server(
    server_id: int,
    body: McpServerUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
    """Update an MCP server config; a connected server is disconnected once the edit is saved."""
    row = await _get_mcp_server_or_404(session, current_user.id, server_id)
    updates = body.model_dump(exclude_unset=True)
    if updates.get("env") is not None:
        # Reject before changing anything so a refused edit leaves the connection untouched.
        try:
            mcp_config.merge_env(mcp_config.load_env(row), updates["env"])
        except mcp_config.EnvMaskError as exc:
            raise _env_mask_error(current_user.id, row.id, exc) from exc
    try:
        row = await mcp_config.update_server(
            session,
            row,
            name=updates.get("name"),
            command=updates.get("command"),
            args=updates.get("args"),
            env=updates.get("env"),
            cwd=updates.get("cwd"),
            cwd_set="cwd" in body.model_fields_set,
            enabled=updates.get("enabled"),
        )
    except mcp_config.EnvMaskError as exc:
        raise _env_mask_error(current_user.id, row.id, exc) from exc
    # Unconditional, after a successful commit: also drops a remembered connect error
    # that the edit makes stale.
    await mcp_client.disconnect_server(current_user.id, row.id)
    connection = await mcp_client.get_status(current_user.id, row.id)
    return _mcp_server_to_response(row, connection)


@app.delete(
    "/api/v1/mcp/servers/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_allowed_origin)],
)
async def delete_mcp_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete an MCP server config after closing its live session."""
    row = await _get_mcp_server_or_404(session, current_user.id, server_id)
    await mcp_client.cleanup_server(current_user.id, row.id)
    await mcp_config.delete_server(session, row)


@app.post(
    "/api/v1/mcp/servers/{server_id}/connect",
    response_model=McpServerResponse,
    dependencies=[Depends(require_allowed_origin)],
)
async def connect_mcp_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
    """Connect to an MCP server; connect failures are reported in the body, not as HTTP errors."""
    row = await _get_mcp_server_or_404(session, current_user.id, server_id)
    if not row.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MCP server is disabled",
        )
    logger.info("mcp_connect_requested", user_id=current_user.id, server_id=row.id)
    try:
        result = await mcp_client.connect_server(
            current_user.id,
            row.id,
            row.command,
            mcp_config.load_args(row),
            mcp_config.load_env(row) or None,
            row.cwd,
        )
    except Exception as exc:
        # Last-resort guard: a connect problem must never take the Agent down.
        logger.error(
            "mcp_connect_unexpected_error",
            user_id=current_user.id,
            server_id=row.id,
            error=str(exc),
        )
        result = mcp_client.build_error_result(
            McpErrorCode.PROTOCOL_ERROR,
            str(exc),
            "",
            app_config.MCP_CONNECT_TIMEOUT,
        )
    return _mcp_server_to_response(row, result)


@app.post(
    "/api/v1/mcp/servers/{server_id}/disconnect",
    response_model=McpServerResponse,
    dependencies=[Depends(require_allowed_origin)],
)
async def disconnect_mcp_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
    """Close the live session of an MCP server."""
    row = await _get_mcp_server_or_404(session, current_user.id, server_id)
    logger.info("mcp_disconnect_requested", user_id=current_user.id, server_id=row.id)
    result = await mcp_client.disconnect_server(current_user.id, row.id)
    return _mcp_server_to_response(row, result)


@app.get("/api/v1/mcp/servers/{server_id}/status", response_model=McpServerResponse)
async def get_mcp_server_status(
    server_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
    """Return an MCP server's connection state after a liveness check."""
    row = await _get_mcp_server_or_404(session, current_user.id, server_id)
    result = await mcp_client.get_status(current_user.id, row.id)
    return _mcp_server_to_response(row, result)


@app.get("/api/v1/lm-studio/models")
async def list_lm_studio_models(
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List models available in LM Studio."""
    try:
        return await lm_studio_client.list_models()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LM Studio is not running",
        ) from None
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@app.post("/api/v1/lm-studio/load-model", response_model=ModelLoadResult)
async def load_lm_studio_model(
    body: ModelLoadRequest,
    current_user: User = Depends(get_current_user),
) -> ModelLoadResult:
    """Load a model in LM Studio."""
    return await lm_studio_client.load_model(
        body.model_id,
        body.gpu_offload,
        body.context_length,
    )


@app.post(
    "/api/v1/lm-studio/unload-model/{model_id}",
    response_model=ModelLoadResult,
)
async def unload_lm_studio_model(
    model_id: str,
    current_user: User = Depends(get_current_user),
) -> ModelLoadResult:
    """Unload a model from LM Studio."""
    return await lm_studio_client.unload_model(model_id)


@app.websocket("/ws/chat/{chat_id}")
async def websocket_chat_endpoint(websocket: WebSocket, chat_id: int) -> None:
    """WebSocket chat endpoint."""
    logger.info("ws_route_called", chat_id=chat_id)
    await ws_chat(websocket, chat_id)


@app.websocket("/ws/events")
async def websocket_events_endpoint(websocket: WebSocket) -> None:
    """User-level WebSocket for scheduler live events."""
    await ws_events(websocket)


logger.info("websocket_routes_registered")
