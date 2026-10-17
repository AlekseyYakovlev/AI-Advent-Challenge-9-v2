"""Tests for the release plan phase: chat-model calls and build_plan over a fake ToolCaller."""

from typing import Any

import pytest

from agent.llm_client import ChatCompletionResult
from agent.release_llm import ReleaseLlm, choose_version, draft_changelog


def _result(content: str | None) -> ChatCompletionResult:
    """Build a completion result with the given text."""
    return ChatCompletionResult(
        content=content, finish_reason="stop", has_reasoning=False, completion_tokens=10
    )


class FakeClient:
    """Chat client double that returns queued results and records the messages."""

    def __init__(self, answers: list[str | None]) -> None:
        self._answers = list(answers)
        self.calls: list[list[dict[str, str]]] = []

    async def complete_chat_detailed(self, **kwargs: Any) -> ChatCompletionResult:
        self.calls.append(kwargs["messages"])
        return _result(self._answers.pop(0))


def _llm(answers: list[str | None]) -> tuple[ReleaseLlm, FakeClient]:
    client = FakeClient(answers)
    return ReleaseLlm(client, "test-model", timeout=5.0), client


FULL_BODY = "### Добавлено\n- Фаза 25: поиск\n- Фаза 26: видео\n- Day34 влит"
PARTIAL_BODY = "### Добавлено\n- Фаза 25: поиск\n- Day34 влит"
GROUPS = {"25": ["feat(25-01): search"], "26": ["feat(26-01): video"]}


async def test_llm_complete_returns_text() -> None:
    llm, client = _llm(["hello"])
    assert await llm.complete("changelog", [{"role": "user", "content": "x"}], 50) == "hello"
    assert len(client.calls) == 1


async def test_llm_complete_blank_content_is_none() -> None:
    llm, _ = _llm(["   "])
    assert await llm.complete("changelog", [{"role": "user", "content": "x"}], 50) is None


async def test_llm_complete_none_content_is_none() -> None:
    llm, _ = _llm([None])
    assert await llm.complete("decision", [{"role": "user", "content": "x"}], 50) is None


async def test_llm_complete_client_error_is_none() -> None:
    class Broken:
        async def complete_chat_detailed(self, **kwargs: Any) -> ChatCompletionResult:
            raise RuntimeError("boom")

    llm = ReleaseLlm(Broken(), "m", timeout=5.0)
    assert await llm.complete("changelog", [{"role": "user", "content": "x"}], 50) is None


async def test_changelog_single_call_when_covered() -> None:
    llm, client = _llm([FULL_BODY])
    body, missing, shrunk = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == FULL_BODY
    assert missing == []
    assert shrunk == 0
    assert len(client.calls) == 1


async def test_changelog_retry_covers_missing_phase() -> None:
    llm, client = _llm([PARTIAL_BODY, FULL_BODY])
    body, missing, _ = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == FULL_BODY
    assert missing == []
    assert len(client.calls) == 2
    assert "26" in client.calls[1][-1]["content"]


async def test_changelog_retry_still_missing_is_reported() -> None:
    llm, client = _llm([PARTIAL_BODY, PARTIAL_BODY])
    body, missing, _ = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == PARTIAL_BODY
    assert missing == ["26"]
    assert len(client.calls) == 2


async def test_changelog_keeps_better_draft() -> None:
    worse = "### Добавлено\n- Фаза 25: поиск"
    llm, _ = _llm([PARTIAL_BODY, worse])
    body, missing, _ = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == PARTIAL_BODY
    assert missing == ["26"]


async def test_changelog_unparseable_both_times() -> None:
    llm, client = _llm(["nonsense", "more nonsense"])
    body, missing, _ = await draft_changelog(llm, GROUPS, [], [])
    assert body is None
    assert missing == []
    assert len(client.calls) == 2


async def test_changelog_unparsed_first_draft_retry_gets_raw_text() -> None:
    llm, client = _llm(["x" * 6000, FULL_BODY])
    body, _, _ = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == FULL_BODY
    assistant = client.calls[1][-2]["content"]
    assert assistant == "x" * 4000


async def test_decision_valid_candidate() -> None:
    text = '{"version": "v4.1", "title": "Поиск", "rationale": "Малые правки."}'
    llm, _ = _llm([text])
    decision = await choose_version(llm, ["v5.0", "v4.1"], {"feat": 1}, FULL_BODY)
    assert decision.version == "v4.1"
    assert decision.title == "Поиск"


async def test_decision_invalid_version_rejected() -> None:
    llm, _ = _llm(['{"version": "v9.9", "title": "T", "rationale": "r"}'])
    decision = await choose_version(llm, ["v5.0", "v4.1"], {}, FULL_BODY)
    assert decision.version is None


async def test_decision_llm_failure() -> None:
    llm, _ = _llm([None])
    decision = await choose_version(llm, ["v5.0"], {}, FULL_BODY)
    assert (decision.version, decision.title, decision.rationale) == (None, None, None)
