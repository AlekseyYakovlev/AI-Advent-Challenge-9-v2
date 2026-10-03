"""REST routes for per-chat RAG settings and source snippets."""

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from agent.kb_api import MSG_KB_NOT_FOUND, _get_owned_kb, _unprocessable
from agent.rag import MODE_OFF
from shared.database import get_session
from shared.logger import get_logger
from shared.models import Chat, ChatRagConfig, KbChunk, KbStatus, KnowledgeBase, User

logger = get_logger(__name__)

router = APIRouter(tags=["rag"])

MSG_KB_NOT_READY = "База знаний ещё не готова"
MSG_CHUNK_NOT_FOUND = "Фрагмент не найден"
DEFAULT_TOP_K = 5


class RagConfigIn(BaseModel):
    """Body of a per-chat RAG settings update."""

    mode: Literal["off", "rag"]
    kb_id: int | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=20)


class RagConfigOut(BaseModel):
    """Per-chat RAG settings together with the attached knowledge base summary."""

    chat_id: int
    mode: str
    kb_id: int | None
    kb_name: str | None
    kb_status: str | None
    top_k: int


async def _get_owned_chat(session: AsyncSession, chat_id: int, user_id: int) -> Chat:
    """Load a chat owned by user_id or raise 404 (never 403, to avoid an IDOR oracle)."""
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        logger.warning("chat_access_denied", chat_id=chat_id, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat {chat_id} not found",
        )
    return chat


async def _config_out(
    session: AsyncSession, chat: Chat, row: ChatRagConfig | None
) -> RagConfigOut:
    """Build the response, resolving the KB name/status only for a KB the chat owner owns."""
    if row is None:
        return RagConfigOut(
            chat_id=chat.id, mode=MODE_OFF, kb_id=None, kb_name=None,
            kb_status=None, top_k=DEFAULT_TOP_K,
        )
    kb: KnowledgeBase | None = None
    if row.kb_id is not None:
        kb = await session.get(KnowledgeBase, row.kb_id)
        if kb is not None and kb.user_id != chat.user_id:
            kb = None
    return RagConfigOut(
        chat_id=chat.id,
        mode=row.mode,
        kb_id=kb.id if kb is not None else None,
        kb_name=kb.name if kb is not None else None,
        kb_status=kb.status.value if kb is not None else None,
        top_k=row.top_k,
    )


@router.get("/api/v1/chats/{chat_id}/rag")
async def get_rag_config(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> RagConfigOut:
    """Return the chat's RAG settings; defaults (off, no KB, top_k 5) when never configured."""
    chat = await _get_owned_chat(session, chat_id, current_user.id)
    row = await session.get(ChatRagConfig, chat_id)
    return await _config_out(session, chat, row)


@router.put(
    "/api/v1/chats/{chat_id}/rag",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def put_rag_config(
    chat_id: int,
    body: RagConfigIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> RagConfigOut:
    """Store the chat's RAG mode, knowledge base and top_k."""
    chat = await _get_owned_chat(session, chat_id, current_user.id)
    mode = body.mode
    if body.kb_id is not None:
        kb = await _get_owned_kb(session, body.kb_id, current_user.id)
        if kb.status != KbStatus.READY:
            raise _unprocessable(MSG_KB_NOT_READY)
    else:
        mode = MODE_OFF
    try:
        row = await session.get(ChatRagConfig, chat_id)
        if row is None:
            row = ChatRagConfig(chat_id=chat_id)
            session.add(row)
        row.mode = mode
        row.kb_id = body.kb_id
        row.top_k = body.top_k
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
    except SQLAlchemyError:
        await session.rollback()
        raise
    logger.info(
        "rag_config_updated", chat_id=chat_id, mode=mode, kb_id=body.kb_id, top_k=body.top_k
    )
    return await _config_out(session, chat, row)


@router.get("/api/v1/kb/{kb_id}/chunks/{chunk_id}")
async def get_chunk_snippet(
    kb_id: int,
    chunk_id: str,
    file: str | None = Query(default=None, max_length=500),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return one chunk's text for the owner of its knowledge base."""
    await _get_owned_kb(session, kb_id, current_user.id)
    result = await session.exec(
        select(KbChunk).where(KbChunk.kb_id == kb_id, KbChunk.chunk_id == chunk_id)
    )
    chunk = result.first()
    # The file check guards against a stale citation resolving to a reused kb_id.
    if chunk is None or (file is not None and chunk.source != file):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=MSG_CHUNK_NOT_FOUND)
    logger.info("rag_snippet_served", kb_id=kb_id)
    return {
        "chunk_id": chunk.chunk_id,
        "source": chunk.source,
        "section": chunk.section,
        "title": chunk.title,
        "text": chunk.text,
    }
