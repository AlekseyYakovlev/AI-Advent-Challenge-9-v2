"""Agent FastAPI application: REST API and in-memory state for WebSockets."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.schemas import (
    BranchRequest,
    ChatCreate,
    ChatResponse,
    HealthResponse,
    MessageResponse,
    ModelLoadRequest,
    ModelLoadResult,
    SettingsResponse,
    SettingsUpdate,
)
from agent.llm_client import LMStudioClient
from agent.state import CORS_ORIGINS, cleanup_chat_caches
from agent.ws import register_websocket_routes
from shared.database import async_session_factory, engine, get_session, init_db
from shared.logger import get_logger
from shared.models import Chat, Message, Settings

logger = get_logger(__name__)

lm_studio_client = LMStudioClient()


async def _ensure_global_settings(session: AsyncSession) -> Settings:
    """Create default global settings row when missing."""
    result = await session.exec(
        select(Settings).where(Settings.chat_id.is_(None)),
    )
    row = result.first()
    if row is not None:
        return row
    row = Settings(chat_id=None)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _get_chat_or_404(
    session: AsyncSession,
    chat_id: int,
) -> Chat:
    """Load a chat by id or raise HTTP 404."""
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat {chat_id} not found",
        )
    return chat


async def _resolve_settings(
    session: AsyncSession,
    chat_id: int | None,
) -> Settings:
    """Return per-chat settings or fall back to global defaults."""
    if chat_id is not None:
        await _get_chat_or_404(session, chat_id)
        result = await session.exec(
            select(Settings).where(Settings.chat_id == chat_id),
        )
        row = result.first()
        if row is not None:
            return row
    return await _ensure_global_settings(session)


def _settings_to_response(row: Settings) -> SettingsResponse:
    """Map a Settings ORM row to the API response schema."""
    return SettingsResponse(
        id=row.id,
        chat_id=row.chat_id,
        system_prompt=row.system_prompt,
        temperature=row.temperature,
        max_tokens=row.max_tokens,
        strategy=row.strategy,
        facts_json=row.facts_json,
        summary_text=row.summary_text,
    )


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
    async with async_session_factory() as session:
        await _ensure_global_settings(session)
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

register_websocket_routes(app)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service health status."""
    return HealthResponse()


@app.get("/api/v1/chats", response_model=list[ChatResponse])
async def list_chats(
    session: AsyncSession = Depends(get_session),
) -> list[ChatResponse]:
    """List all chats ordered by creation time descending."""
    result = await session.exec(
        select(Chat).order_by(Chat.created_at.desc()),
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
) -> ChatResponse:
    """Create a new chat session."""
    chat = Chat(title=body.title)
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
) -> None:
    """Delete a chat and clear related in-memory caches."""
    chat = await _get_chat_or_404(session, chat_id)
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
) -> list[MessageResponse]:
    """Return the message path from the current leaf to the root."""
    chat = await _get_chat_or_404(session, chat_id)
    path = await _build_tree_path(session, chat)
    return [_message_to_response(msg) for msg in path]


@app.post(
    "/api/v1/chats/{chat_id}/branch",
    response_model=ChatResponse,
)
async def branch_chat(
    chat_id: int,
    body: BranchRequest,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    """Switch the active branch to the given message."""
    chat = await _get_chat_or_404(session, chat_id)
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
) -> SettingsResponse:
    """Return effective settings with global fallback."""
    row = await _resolve_settings(session, chat_id)
    return _settings_to_response(row)


@app.put("/api/v1/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> SettingsResponse:
    """Create or update global or per-chat settings."""
    if body.chat_id is None:
        row = await _ensure_global_settings(session)
    else:
        await _get_chat_or_404(session, body.chat_id)
        result = await session.exec(
            select(Settings).where(Settings.chat_id == body.chat_id),
        )
        row = result.first()
        if row is None:
            row = Settings(chat_id=body.chat_id)
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


@app.get("/api/v1/lm-studio/models")
async def list_lm_studio_models() -> list[dict[str, Any]]:
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
async def load_lm_studio_model(body: ModelLoadRequest) -> ModelLoadResult:
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
async def unload_lm_studio_model(model_id: str) -> ModelLoadResult:
    """Unload a model from LM Studio."""
    return await lm_studio_client.unload_model(model_id)
