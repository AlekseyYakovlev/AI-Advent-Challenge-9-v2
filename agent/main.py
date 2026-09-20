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

from agent.dependencies import get_current_user
from agent.schemas import (
    BranchRequest,
    ChatCreate,
    ChatMemoryResponse,
    ChatResponse,
    CreateUserRequest,
    GlobalInvariantCreate,
    GlobalInvariantResponse,
    GlobalInvariantUpdate,
    HealthResponse,
    LoginRequest,
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
from agent import invariants, memory, profile, tasks
from agent.ws import ws_chat
from shared.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from shared.database import engine, get_session, init_db
from shared.logger import get_logger
from shared.models import (
    Chat,
    ContextStrategy,
    GlobalInvariant,
    Message,
    Profile,
    Session as SessionRow,
    Settings,
    Task,
    TaskTransition,
    User,
)

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
    """Initialize the database on startup and dispose the engine on shutdown."""
    logger.info("agent_starting")
    await init_db()
    yield
    logger.info("agent_shutting_down")
    await engine.dispose()


app = FastAPI(title="AI Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        row = await tasks.set_paused(session, current_user.id, task.chat_id, task_id, True)
        return await _task_response_with_history(session, row)


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
        row = await tasks.set_paused(session, current_user.id, task.chat_id, task_id, False)
        return await _task_response_with_history(session, row)


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
        row = await tasks.cancel_task(session, current_user.id, task.chat_id, task_id)
        return await _task_response_with_history(session, row)


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


logger.info("websocket_routes_registered")
