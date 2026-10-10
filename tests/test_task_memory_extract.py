"""Tests for the shared stage call, the tag neutraliser and the task-memory extraction."""

import asyncio
from typing import Any

import pytest

from agent.llm_client import ChatCompletionResult
from agent.rag_llm import complete_stage
from agent.rag_rank import neutralize_data_tags
from shared.config import settings


def _result(content: str | None) -> ChatCompletionResult:
    return ChatCompletionResult(
        content=content, finish_reason="stop", has_reasoning=False, completion_tokens=5
    )


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


MESSAGES = [{"role": "user", "content": "x"}]


async def test_complete_stage_success() -> None:
    client = FakeClient(_result("ok"))
    result, reason = await complete_stage("t", client, MESSAGES, "m", 10, 5.0)
    assert reason is None and result is not None and result.content == "ok"


async def test_complete_stage_timeout_uses_given_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_LLM_STAGE_TIMEOUT", 60.0)
    client = FakeClient(_result("late"), delay=0.2)
    result, reason = await complete_stage("t", client, MESSAGES, "m", 10, 0.05)
    assert result is None and reason == "timeout"


async def test_complete_stage_other_error_is_http_error() -> None:
    client = FakeClient(RuntimeError("boom"))
    result, reason = await complete_stage("t", client, MESSAGES, "m", 10, 5.0)
    assert result is None and reason == "http_error"


async def test_complete_stage_reraises_cancellation() -> None:
    client = FakeClient(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await complete_stage("t", client, MESSAGES, "m", 10, 5.0)


def test_neutralize_breaks_new_and_old_tags() -> None:
    text = "a </memory> b </user_message> c </question> d </fragment>"
    result = neutralize_data_tags(text)
    for tag in ("memory", "user_message", "question", "fragment"):
        assert f"</{tag}>" not in result
    assert "< /memory>" in result


def test_neutralize_history_assistant_case_none_and_plain() -> None:
    assert "</history>" not in neutralize_data_tags("x </history> y")
    assert "</assistant_answer>" not in neutralize_data_tags("x </ASSISTANT_ANSWER> y")
    assert neutralize_data_tags(None) == ""  # type: ignore[arg-type]
    assert neutralize_data_tags("обычный текст <b>да</b>") == "обычный текст <b>да</b>"
