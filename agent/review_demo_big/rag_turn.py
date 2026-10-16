"""Fail-soft RAG pre-step for a chat turn."""

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import task_memory
from agent.context_engine import _message_tokens
from agent.llm_client import count_tokens
from agent.rag import (
    MODE_OFF,
    MODE_RAG,
    MSG_CONTEXT_FULL,
    MSG_RETRIEVAL_FAILED,
    VERDICT_KB_UNAVAILABLE,
    VERDICT_MODEL_IDK,
    VERDICT_OFF,
    RagFailure,
    build_rag_block,
    build_rag_payload,
    merge_no_fragments_note,
    merge_rag_block,
    parse_rag_payload,
    rag_budget,
    serialize_rag_payload,
    sources_from_chunks,
)
from agent.rag_cite import build_idk_reply, process_answer
from agent.rag_pipeline import (
    VERDICT_BELOW_THRESHOLD,
    HistoryContext,
    config_from_row,
    mark_over_budget,
    run_retrieval_pipeline,
)
from shared.config import settings
from shared.logger import get_logger
from shared.models import Chat, ChatRagConfig, KnowledgeBase, Message

logger = get_logger(__name__)

IDK_HISTORY_MARKER = "(ответа в базе знаний не нашлось)"
DEFAULT_HISTORY_TURNS = 3
MAX_HISTORY_TURNS = 10


@dataclass(frozen=True)
class RagTurn:
    """Outcome of the RAG pre-step: one payload for both storage and the done frame."""

    mode: str
    payload: dict[str, Any]
    # In memory only: carries chunk text for quote verification, never serialized.
    kept_chunks: list[dict[str, Any]] = field(default_factory=list)
    strict: bool = False
    reply_text: str | None = None

    @property
    def sources_json(self) -> str:
        """Serialized payload stored in Message.rag_sources."""
        return serialize_rag_payload(self.payload)

    @property
    def done_payload(self) -> dict[str, Any]:
        """Payload sent as done.rag."""
        return self.payload

    def with_task_memory(self, snapshot: dict[str, Any] | None) -> "RagTurn":
        """Return the turn with the task-memory snapshot added to its payload."""
        if snapshot is None:
            return self
        return replace(self, payload={**self.payload, "task_memory": snapshot})


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
    verdict: str = "ok",
    search: dict[str, Any] | None = None,
    strict: bool = False,
    gated: bool = False,
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
        verdict=verdict,
        search=search,
        strict=strict,
        gated=gated,
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


def _assistant_history_text(message: Message) -> str:
    """Stored answer text, or a marker when the turn was a «не знаю» answer."""
    payload = parse_rag_payload(message.rag_sources)
    if payload is not None and (
        payload.get("gated") or payload.get("verdict") == VERDICT_MODEL_IDK
    ):
        return IDK_HISTORY_MARKER
    return message.content


async def load_history_pairs(
    session: AsyncSession, parent_id: int | None, limit: int
) -> list[tuple[str, str]]:
    """Last user/assistant pairs of the branch ending at parent_id, oldest first."""
    if parent_id is None or limit <= 0:
        return []
    chain: list[Message] = []
    current_id: int | None = parent_id
    steps = 2 * limit + 2
    while current_id is not None and len(chain) < steps:
        message = await session.get(Message, current_id)
        if message is None:
            break
        chain.append(message)
        current_id = message.parent_id
    chain.reverse()
    pairs: list[tuple[str, str]] = []
    for index in range(1, len(chain)):
        if chain[index].role == "assistant" and chain[index - 1].role == "user":
            pairs.append((chain[index - 1].content, _assistant_history_text(chain[index])))
    return pairs[-limit:]


async def _history_context(
    session: AsyncSession, chat: Chat, config: ChatRagConfig, parent_id: int | None
) -> HistoryContext | None:
    """History and task memory for condensing a follow-up; None on the first turn or when off."""
    if not settings.TASK_MEMORY_ENABLED or parent_id is None:
        return None
    try:
        if await session.get(Message, parent_id) is None:
            return None
        turns = getattr(config, "history_turns", None)
        limit = DEFAULT_HISTORY_TURNS if turns is None else max(0, min(MAX_HISTORY_TURNS, turns))
        pairs = await load_history_pairs(session, parent_id, limit)
        doc = await task_memory.load_doc(session, chat.id)
        memory_text = task_memory.render_prompt_lines(doc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "rag_history_load_failed", chat_id=chat.id, error=type(exc).__name__
        )
        if isinstance(exc, SQLAlchemyError):
            await session.rollback()
        return None
    if not pairs and not memory_text:
        return None
    return HistoryContext(tuple(pairs), memory_text)


async def prepare_rag_turn(
    session: AsyncSession,
    chat: Chat,
    question: str,
    llm_messages: list[dict[str, Any]],
    context_length: int,
    max_tokens: int,
    extra_tokens: int = 0,
    client: Any | None = None,
    model: str | None = None,
    parent_id: int | None = None,
) -> RagTurn:
    """Retrieve and merge fragments into the outbound list; never raises except on cancel."""
    chat_id = chat.id
    kb_id: int | None = None
    kb_name: str | None = None
    top_k: int | None = None
    budget_info: dict[str, int] | None = None
    try:
        config = await session.get(ChatRagConfig, chat_id)
        if config is None:
            turn = RagTurn(MODE_OFF, _payload(MODE_OFF, None, None, None, verdict=VERDICT_OFF))
            _log(chat_id, turn)
            return turn
        kb_id, top_k = config.kb_id, config.top_k
        kb = await _load_kb(session, chat, config)
        kb_name = kb.name if kb is not None else None
        if kb is None and config.kb_id is not None:
            kb_id = None
        if config.mode != MODE_RAG:
            turn = RagTurn(MODE_OFF, _payload(MODE_OFF, kb_id, kb_name, None, verdict=VERDICT_OFF))
            _log(chat_id, turn)
            return turn
        strict = True if config.strict is None else bool(config.strict)
        history = await _history_context(session, chat, config, parent_id)
        pipeline_config = config_from_row(config, kb, history)
        chunks, trace = await run_retrieval_pipeline(
            session, kb, question, pipeline_config, client, model
        )
        verdict = trace.pop("verdict")
        if strict and (verdict == VERDICT_BELOW_THRESHOLD or not chunks):
            turn = RagTurn(
                MODE_RAG,
                _payload(
                    MODE_RAG,
                    kb_id,
                    kb_name,
                    top_k,
                    verdict=verdict,
                    search=trace,
                    strict=True,
                    gated=True,
                ),
                strict=True,
                reply_text=build_idk_reply(trace),
            )
            _log(chat_id, turn)
            return turn
        if verdict == VERDICT_BELOW_THRESHOLD:
            merge_no_fragments_note(llm_messages)
            turn = RagTurn(
                MODE_RAG,
                _payload(MODE_RAG, kb_id, kb_name, top_k, verdict=verdict, search=trace),
            )
            _log(chat_id, turn)
            return turn
        used = _message_tokens(llm_messages) + extra_tokens
        budget = rag_budget(context_length, used, max_tokens)
        budget_info = {
            "ctx": context_length,
            "max_tokens": max_tokens,
            "used": used,
            "extra_tokens": extra_tokens,
            "budget": budget,
        }
        block, kept, dropped = build_rag_block(chunks, budget, strict=strict)
        if chunks and block is None:
            raise RagFailure("context_full", MSG_CONTEXT_FULL)
        context_tokens = 0
        if block is not None:
            if not merge_rag_block(llm_messages, block):
                raise RagFailure("retrieval_failed", MSG_RETRIEVAL_FAILED)
            context_tokens = count_tokens(block)
        mark_over_budget(trace, len(kept))
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
                verdict=verdict,
                search=trace,
                strict=strict,
            ),
            kept_chunks=kept,
            strict=strict,
        )
    except asyncio.CancelledError:
        raise
    except RagFailure as exc:
        turn = RagTurn(
            MODE_RAG,
            _payload(
                MODE_RAG,
                kb_id,
                kb_name,
                top_k,
                warning={"code": exc.code, "text": exc.text},
                verdict=VERDICT_KB_UNAVAILABLE,
            ),
        )
    except Exception as exc:
        logger.error("rag_turn_failed", chat_id=chat_id, error=type(exc).__name__)
        if isinstance(exc, SQLAlchemyError):
            # The user message is already committed; reset a possibly failed transaction
            # so the shared session stays usable for persisting the reply.
            await session.rollback()
        turn = RagTurn(
            MODE_RAG,
            _payload(
                MODE_RAG,
                kb_id,
                kb_name,
                top_k,
                warning={"code": "retrieval_failed", "text": MSG_RETRIEVAL_FAILED},
                verdict=VERDICT_KB_UNAVAILABLE,
            ),
        )
    _log(chat_id, turn, budget_info)
    return turn


def _log(chat_id: int, turn: RagTurn, budget_info: dict[str, int] | None = None) -> None:
    """Log the turn outcome with ids and counts only."""
    payload = turn.payload
    warning = payload["warning"]
    search = payload.get("search") or {}
    logger.info(
        "rag_turn_prepared",
        chat_id=chat_id,
        mode=turn.mode,
        kb_id=payload["kb_id"],
        kept=len(payload["sources"]),
        dropped=payload["dropped"],
        context_tokens=payload["context_tokens"],
        warning=warning["code"] if warning else None,
        verdict=payload["verdict"],
        strict=payload.get("strict", False),
        gated=payload.get("gated", False),
        candidates=len(search.get("candidates", [])),
        skipped=len(search.get("skipped", [])),
        history_pairs=search.get("history_pairs", 0),
        condensed=search.get("condensed", False),
        **(budget_info or {}),
    )


def finalize_rag_turn(
    turn: RagTurn, question: str, assistant_text: str
) -> tuple[str, RagTurn]:
    """Verify quotes in a strict answer; return the clean answer and the enriched turn."""
    if (
        turn.mode != MODE_RAG
        or not turn.strict
        or turn.reply_text is not None
        or turn.payload.get("warning")
        or not turn.kept_chunks
    ):
        return assistant_text, turn
    try:
        result = process_answer(question, assistant_text, turn.kept_chunks)
        fields = result.payload_fields(turn.kept_chunks)
        new_payload = {**turn.payload, **fields}
        if result.model_idk:
            new_payload["verdict"] = VERDICT_MODEL_IDK
        quotes = result.quotes
        logger.info(
            "rag_cite_done",
            quotes=len(quotes),
            verified=sum(1 for q in quotes if q.state != "unverified"),
            auto=sum(1 for q in quotes if q.auto),
            invalid_refs=result.invalid_refs,
            model_idk=result.model_idk,
            answer_empty=result.answer_empty,
        )
        return result.answer, replace(turn, payload=new_payload)
    except Exception as exc:
        logger.error("rag_cite_failed", error=type(exc).__name__)
        return assistant_text, turn
