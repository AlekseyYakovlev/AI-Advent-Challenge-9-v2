"""Tests for the non-streaming RAG stage calls."""

import asyncio
from typing import Any

import httpx
import pytest

from agent import rag_llm
from agent.llm_client import ChatCompletionResult
from agent.rag_llm import StageOutcome, llm_rerank, rewrite_query
from shared.config import settings

QUESTION = "штраф за превышение на 40"
REWRITTEN = "штраф превышение скорости 40 км/ч ст. 12.9"


def _result(content: str | None) -> ChatCompletionResult:
    return ChatCompletionResult(
        content=content, finish_reason="stop", has_reasoning=False, completion_tokens=5
    )


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


class FakeClient:
    """Records complete_chat_detailed calls and replays scripted outcomes."""

    def __init__(self, *script: Any, delay: float = 0.0) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.delay = delay

    async def complete_chat_detailed(self, **kwargs: Any) -> ChatCompletionResult:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _chunks(count: int) -> list[dict[str, Any]]:
    return [{"section": f"Раздел {i}", "text": f"текст {i}"} for i in range(count)]


async def test_rewrite_success_and_call_parameters() -> None:
    client = FakeClient(_result(REWRITTEN))
    outcome = await rewrite_query(client, "model-x", QUESTION)
    assert outcome == StageOutcome(REWRITTEN, None)
    call = client.calls[0]
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 96
    assert call["extra_body"] == {"reasoning_effort": "none"}
    assert call["model"] == "model-x"


@pytest.mark.parametrize("code", [400, 422])
async def test_rewrite_retries_without_reasoning_field(code: int) -> None:
    client = FakeClient(_status_error(code), _result(REWRITTEN))
    outcome = await rewrite_query(client, "m", QUESTION)
    assert outcome.value == REWRITTEN
    assert len(client.calls) == 2
    assert client.calls[1]["extra_body"] is None


@pytest.mark.parametrize(
    "error",
    [_status_error(500), httpx.ConnectError("down"), RuntimeError("boom")],
)
async def test_rewrite_failures_become_http_error(error: Exception) -> None:
    outcome = await rewrite_query(FakeClient(error), "m", QUESTION)
    assert outcome == StageOutcome(None, "http_error")


async def test_rewrite_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_LLM_STAGE_TIMEOUT", 0.01)
    outcome = await rewrite_query(FakeClient(_result(REWRITTEN), delay=1.0), "m", QUESTION)
    assert outcome == StageOutcome(None, "timeout")


async def test_rewrite_bad_and_unchanged_output() -> None:
    chatty = await rewrite_query(FakeClient(_result("Конечно! Вот запрос: штраф")), "m", QUESTION)
    empty = await rewrite_query(FakeClient(_result("")), "m", QUESTION)
    same = await rewrite_query(FakeClient(_result(QUESTION)), "m", QUESTION)
    assert chatty == StageOutcome(None, "bad_output")
    assert empty == StageOutcome(None, "bad_output")
    assert same == StageOutcome(None, "unchanged")


async def test_cancellation_propagates() -> None:
    with pytest.raises(asyncio.CancelledError):
        await rewrite_query(FakeClient(asyncio.CancelledError()), "m", QUESTION)
    with pytest.raises(asyncio.CancelledError):
        await llm_rerank(FakeClient(asyncio.CancelledError()), "m", QUESTION, _chunks(2))


async def test_rerank_single_call_for_fifteen_chunks() -> None:
    reply = "\n".join(f"{i}: {i % 10}" for i in range(1, 11))
    client = FakeClient(_result(reply))
    outcome = await llm_rerank(client, "m", QUESTION, _chunks(15))
    assert len(client.calls) == 1
    prompt = client.calls[0]["messages"][1]["content"]
    assert "[10]" in prompt and "[11]" not in prompt
    assert outcome.reason is None
    assert len(outcome.value) == 10
    assert all(isinstance(value, float) for value in outcome.value)
    assert client.calls[0]["max_tokens"] == 200


async def test_rerank_retry_makes_two_calls() -> None:
    client = FakeClient(_status_error(422), _result("[1] 7\n[2] 3"))
    outcome = await llm_rerank(client, "m", QUESTION, _chunks(2))
    assert len(client.calls) == 2
    assert outcome == StageOutcome([7.0, 3.0], None)


async def test_rerank_missing_index_is_bad_output() -> None:
    outcome = await llm_rerank(FakeClient(_result("1: 7")), "m", QUESTION, _chunks(2))
    assert outcome == StageOutcome(None, "bad_output")


async def test_rerank_empty_chunks_skips_client() -> None:
    client = FakeClient()
    assert await llm_rerank(client, "m", QUESTION, []) == StageOutcome([], None)
    assert client.calls == []


async def test_rerank_http_error_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    err = await llm_rerank(FakeClient(httpx.ConnectError("x")), "m", QUESTION, _chunks(2))
    assert err == StageOutcome(None, "http_error")
    monkeypatch.setattr(settings, "RAG_LLM_STAGE_TIMEOUT", 0.01)
    slow = await llm_rerank(
        FakeClient(_result("[1] 1\n[2] 2"), delay=1.0), "m", QUESTION, _chunks(2)
    )
    assert slow == StageOutcome(None, "timeout")
    assert rag_llm.STAGE_TEMPERATURE == 0.0
