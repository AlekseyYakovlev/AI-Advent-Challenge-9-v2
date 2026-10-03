"""Fail-soft RAG pre-step for a chat turn."""

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from agent.context_engine import _message_tokens
from agent.llm_client import count_tokens
from agent.rag import (
    MODE_OFF,
    MODE_RAG,
    MSG_CONTEXT_FULL,
    MSG_RETRIEVAL_FAILED,
    RagFailure,
    build_rag_block,
    build_rag_payload,
    merge_rag_block,
    rag_budget,
    retrieve,
    serialize_rag_payload,
    sources_from_chunks,
)
from shared.logger import get_logger
from shared.models import Chat, ChatRagConfig, KnowledgeBase

logger = get_logger(__name__)


@dataclass(frozen=True)
class RagTurn:
    """Outcome of the RAG pre-step: one payload for both storage and the done frame."""

    mode: str
    payload: dict[str, Any]

    @property
    def sources_json(self) -> str:
        """Serialized payload stored in Message.rag_sources."""
        return serialize_rag_payload(self.payload)

    @property
    def done_payload(self) -> dict[str, Any]:
        """Payload sent as done.rag."""
        return self.payload


def _payload(
    mode: str,
    kb_id: int | None,
    kb_name: str | None,
    top_k: int | None,
    *,
    sources: list[dict[str, Any]] | None = None,
    dropped: int = 0,
    context_tokens: int = 0,
    warning: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a RAG payload with metadata-only sources."""
    return build_rag_payload(
        mode=mode,
        kb_id=kb_id,
        kb_name=kb_name,
        top_k=top_k,
        sources=sources or [],
        dropped=dropped,
        context_tokens=context_tokens,
        warning=warning,
    )


async def _load_kb(
    session: AsyncSession, chat: Chat, config: ChatRagConfig
) -> KnowledgeBase | None:
    """Load the configured KB; a KB owned by someone else counts as missing."""
    if config.kb_id is None:
        return None
    kb = await session.get(KnowledgeBase, config.kb_id)
    if kb is not None and kb.user_id != chat.user_id:
        logger.warning("rag_kb_foreign", chat_id=chat.id, kb_id=config.kb_id)
        return None
    return kb


async def prepare_rag_turn(
    session: AsyncSession,
    chat: Chat,
    question: str,
    llm_messages: list[dict[str, Any]],
    context_length: int,
    max_tokens: int,
    extra_tokens: int = 0,
) -> RagTurn:
    """Retrieve and merge fragments into the outbound list; never raises except on cancel."""
    chat_id = chat.id
    kb_id: int | None = None
    kb_name: str | None = None
    top_k: int | None = None
    try:
        config = await session.get(ChatRagConfig, chat_id)
        if config is None:
            turn = RagTurn(MODE_OFF, _payload(MODE_OFF, None, None, None))
            _log(chat_id, turn)
            return turn
        kb_id, top_k = config.kb_id, config.top_k
        kb = await _load_kb(session, chat, config)
        kb_name = kb.name if kb is not None else None
        if kb is None and config.kb_id is not None:
            kb_id = None
        if config.mode != MODE_RAG:
            turn = RagTurn(MODE_OFF, _payload(MODE_OFF, kb_id, kb_name, None))
            _log(chat_id, turn)
            return turn
        chunks = await retrieve(session, kb, question, config.top_k)
        used = _message_tokens(llm_messages) + extra_tokens
        budget = rag_budget(context_length, used, max_tokens)
        block, kept, dropped = build_rag_block(chunks, budget)
        if chunks and block is None:
            raise RagFailure("context_full", MSG_CONTEXT_FULL)
        context_tokens = 0
        if block is not None:
            merge_rag_block(llm_messages, block)
            context_tokens = count_tokens(block)
        turn = RagTurn(
            MODE_RAG,
            _payload(
                MODE_RAG,
                kb_id,
                kb_name,
                top_k,
                sources=sources_from_chunks(kept),
                dropped=dropped,
                context_tokens=context_tokens,
            ),
        )
    except asyncio.CancelledError:
        raise
    except RagFailure as exc:
        turn = RagTurn(
            MODE_RAG,
            _payload(MODE_RAG, kb_id, kb_name, top_k, warning={"code": exc.code, "text": exc.text}),
        )
    except Exception as exc:
        logger.error("rag_turn_failed", chat_id=chat_id, error=type(exc).__name__)
        turn = RagTurn(
            MODE_RAG,
            _payload(
                MODE_RAG,
                kb_id,
                kb_name,
                top_k,
                warning={"code": "retrieval_failed", "text": MSG_RETRIEVAL_FAILED},
            ),
        )
    _log(chat_id, turn)
    return turn


def _log(chat_id: int, turn: RagTurn) -> None:
    """Log the turn outcome with ids and counts only."""
    payload = turn.payload
    warning = payload["warning"]
    logger.info(
        "rag_turn_prepared",
        chat_id=chat_id,
        mode=turn.mode,
        kb_id=payload["kb_id"],
        kept=len(payload["sources"]),
        dropped=payload["dropped"],
        context_tokens=payload["context_tokens"],
        warning=warning["code"] if warning else None,
    )
