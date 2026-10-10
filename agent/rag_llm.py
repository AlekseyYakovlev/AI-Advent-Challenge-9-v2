"""Non-streaming LLM stages of RAG retrieval: query rewrite, history condensing and batched rerank."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from agent.llm_client import ChatCompletionResult
from agent.rag_rank import (
    RERANK_TOP_N,
    build_condense_messages,
    build_rerank_messages,
    build_rewrite_messages,
    parse_rerank_scores,
    validate_condensed,
    validate_rewrite,
)
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)

STAGE_TEMPERATURE = 0.0
STAGE_REASONING_EFFORT = "none"
REJECTED_STATUS_CODES = frozenset({400, 422})
REWRITE_MAX_TOKENS = 96
CONDENSE_MAX_TOKENS = 160
RERANK_MAX_TOKENS = 200

REASON_BAD_OUTPUT = "bad_output"
REASON_TIMEOUT = "timeout"
REASON_HTTP_ERROR = "http_error"
REASON_UNCHANGED = "unchanged"


@dataclass(frozen=True)
class StageOutcome:
    """Result of an optional LLM stage: a validated value or a reason it was skipped."""

    value: Any | None
    reason: str | None


async def _complete(
    client: Any, messages: list[dict[str, str]], model: str, max_tokens: int
) -> ChatCompletionResult:
    """Call the model with reasoning disabled; repeat once without it on HTTP 400/422."""
    try:
        return await client.complete_chat_detailed(
            messages=messages,
            model=model,
            temperature=STAGE_TEMPERATURE,
            max_tokens=max_tokens,
            extra_body={"reasoning_effort": STAGE_REASONING_EFFORT},
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code not in REJECTED_STATUS_CODES:
            raise
        logger.info(
            "rag_stage_reasoning_control_rejected", model=model, status_code=status_code
        )
    return await client.complete_chat_detailed(
        messages=messages,
        model=model,
        temperature=STAGE_TEMPERATURE,
        max_tokens=max_tokens,
        extra_body=None,
    )


async def complete_stage(
    stage: str,
    client: Any,
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int,
    timeout: float,
) -> tuple[ChatCompletionResult | None, str | None]:
    """Run one stage call under the given timeout; failures become a reason code."""
    try:
        result = await asyncio.wait_for(
            _complete(client, messages, model, max_tokens),
            timeout=timeout,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError as exc:
        logger.warning(
            "rag_stage_llm_failed", stage=stage, model=model, error=type(exc).__name__
        )
        return None, REASON_TIMEOUT
    except Exception as exc:
        logger.warning(
            "rag_stage_llm_failed", stage=stage, model=model, error=type(exc).__name__
        )
        return None, REASON_HTTP_ERROR
    return result, None


async def _call(
    stage: str, client: Any, messages: list[dict[str, str]], model: str, max_tokens: int
) -> tuple[ChatCompletionResult | None, str | None]:
    """Run one stage call under the RAG stage timeout."""
    return await complete_stage(
        stage, client, messages, model, max_tokens, settings.RAG_LLM_STAGE_TIMEOUT
    )


def _log_unusable(event: str, model: str, result: ChatCompletionResult) -> None:
    """Record why a reply was rejected without logging its text."""
    logger.warning(
        event,
        model=model,
        finish_reason=result.finish_reason,
        content_empty=not (result.content or "").strip(),
        has_reasoning=result.has_reasoning,
        completion_tokens=result.completion_tokens,
    )


async def rewrite_query(client: Any, model: str, question: str) -> StageOutcome:
    """Rewrite the question as a keyword-rich search query on the chat's own model."""
    result, reason = await _call(
        "rewrite", client, build_rewrite_messages(question), model, REWRITE_MAX_TOKENS
    )
    if result is None:
        return StageOutcome(None, reason)
    text, reason = validate_rewrite(question, result.content)
    if reason == REASON_BAD_OUTPUT:
        _log_unusable("rag_rewrite_unusable", model, result)
    return StageOutcome(text, reason)


async def condense_query(
    client: Any,
    model: str,
    question: str,
    pairs: Sequence[tuple[str, str]],
    memory_text: str | None,
) -> StageOutcome:
    """Condense a follow-up with recent history and task memory into a standalone query."""
    result, reason = await _call(
        "history",
        client,
        build_condense_messages(question, pairs, memory_text),
        model,
        CONDENSE_MAX_TOKENS,
    )
    if result is None:
        return StageOutcome(None, reason)
    text, reason = validate_condensed(question, result.content)
    if reason == REASON_BAD_OUTPUT:
        _log_unusable("rag_condense_unusable", model, result)
    return StageOutcome(text, reason)


async def llm_rerank(
    client: Any, model: str, question: str, chunks: list[dict[str, Any]]
) -> StageOutcome:
    """Score up to RERANK_TOP_N chunks in one prompt; returns one score per chunk."""
    batch = chunks[:RERANK_TOP_N]
    if not batch:
        return StageOutcome([], None)
    result, reason = await _call(
        "rerank", client, build_rerank_messages(question, batch), model, RERANK_MAX_TOKENS
    )
    if result is None:
        return StageOutcome(None, reason)
    scores = parse_rerank_scores(result.content, len(batch))
    if scores is None:
        _log_unusable("rag_rerank_unusable", model, result)
        return StageOutcome(None, REASON_BAD_OUTPUT)
    return StageOutcome(scores, None)
