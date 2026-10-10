"""WebSocket tests of the in-turn task-memory update in RAG chats."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent.main import app
from agent.rag import parse_rag_payload
from agent.rag_rank import CONDENSE_SYSTEM_PROMPT
from agent.task_memory import EXTRACT_SYSTEM_PROMPT, TaskMemoryDoc
from shared.config import settings
from shared.database import async_session_factory
from shared.models import ChatTaskMemory, Message
from tests.conftest import login_test_client
from tests.test_rag_ws import (
    BASE_URL,
    BLOCK_MARK,
    QUESTION,
    WS_ORIGIN,
    _messages,
    _open_rag_chat,
    _plain_content_response,
    _send_and_drain,
)

FIRST_TEXT = "О чём раздел номер 3? Хочу разобраться с разделом. Только тема 7919."
SECOND_TEXT = "Ещё уточняю: интересует раздел номер 5."
ANSWER = "Ответ готов."


def _extract_reply(goal: str | None, clarified: list[str], constraints: list[str]) -> str:
    return json.dumps(
        {
            "goal": goal,
            "goal_changed": False,
            "clarified": clarified,
            "constraints": constraints,
        },
        ensure_ascii=False,
    )


def _json_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": text}}]},
    )


def _kind(body: dict[str, Any]) -> str:
    system = body["messages"][0]["content"]
    if system == EXTRACT_SYSTEM_PROMPT:
        return "extract"
    if system == CONDENSE_SYSTEM_PROMPT:
        return "condense"
    return "answer"


def _route(
    captured: list[dict[str, Any]],
    extract: Callable[[], httpx.Response | Exception],
) -> respx.Route:
    """One mock for the three request kinds; `extract` builds each extraction reply."""

    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        kind = _kind(body)
        if kind == "extract":
            result = extract()
            if isinstance(result, Exception):
                raise result
            return result
        if kind == "condense":
            return _json_response("раздел номер 5")
        return _plain_content_response(ANSWER)

    return respx.post(f"{BASE_URL}/v1/chat/completions").mock(side_effect=_side_effect)


def _kinds(captured: list[dict[str, Any]]) -> list[str]:
    return [_kind(body) for body in captured]


async def _row(chat_id: int) -> ChatTaskMemory | None:
    async with async_session_factory() as session:
        return await session.get(ChatTaskMemory, chat_id)


async def _doc(chat_id: int) -> TaskMemoryDoc:
    row = await _row(chat_id)
    return TaskMemoryDoc() if row is None else TaskMemoryDoc.model_validate_json(row.doc_json)


async def _assistant(chat_id: int) -> Message:
    return [m for m in await _messages(chat_id) if m.role == "assistant"][-1]


def _first_reply() -> httpx.Response:
    return _json_response(
        _extract_reply(
            "Разобраться с разделом номер 3",
            [],
            ["Только тема 7919"],
        )
    )


@respx.mock
def test_first_turn_updates_memory_and_done_carries_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    _route(captured, _first_reply)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, FIRST_TEXT)
        rag = frames[-1]["rag"]
        memory = rag["task_memory"]
        assert rag["v"] == 4
        assert memory["goal"] == "Разобраться с разделом номер 3"
        assert [i["text"] for i in memory["constraints"]] == ["Только тема 7919"]
        assert memory["new"]["goal"] is True
        assert memory["new"]["ids"] == [memory["constraints"][0]["id"]]
        assert memory["failed"] is False
        stored = parse_rag_payload(client.portal.call(_assistant, chat_id).rag_sources)
        assert stored["task_memory"] == memory
        doc = client.portal.call(_doc, chat_id)
        assert doc.goal == memory["goal"]
        assert [i.text for i in doc.constraints] == ["Только тема 7919"]
    assert _kinds(captured) == ["answer", "extract"]


@respx.mock
def test_second_turn_condenses_then_extracts_and_marks_only_new_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    replies = [
        _first_reply,
        lambda: _json_response(_extract_reply(None, ["Интересует раздел номер 5"], [])),
    ]
    _route(captured, lambda: replies.pop(0)())
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            first = _send_and_drain(ws, FIRST_TEXT)
            captured.clear()
            second = _send_and_drain(ws, SECOND_TEXT)
        old_id = first[-1]["rag"]["task_memory"]["constraints"][0]["id"]
        memory = second[-1]["rag"]["task_memory"]
        assert [i["text"] for i in memory["constraints"]] == ["Только тема 7919"]
        assert [i["text"] for i in memory["clarified"]] == ["Интересует раздел номер 5"]
        assert old_id not in memory["new"]["ids"]
        assert memory["new"]["ids"] == [memory["clarified"][0]["id"]]
        assert memory["new"]["goal"] is False
    kinds = _kinds(captured)
    assert kinds.count("condense") == 1
    assert kinds.index("condense") < kinds.index("answer") < kinds.index("extract")
    assert kinds.count("extract") == 1


@pytest.mark.parametrize(
    "failure",
    [
        lambda: _json_response("это не JSON"),
        lambda: httpx.Response(500, json={"error": "boom"}),
        lambda: httpx.ReadTimeout("timeout"),
    ],
    ids=["garbage", "http_500", "timeout"],
)
@respx.mock
def test_extraction_failure_keeps_turn_and_memory(
    monkeypatch: pytest.MonkeyPatch, failure: Callable[[], httpx.Response | Exception]
) -> None:
    captured: list[dict[str, Any]] = []
    replies = [_first_reply, failure]
    _route(captured, lambda: replies.pop(0)())
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            first = _send_and_drain(ws, FIRST_TEXT)
            second = _send_and_drain(ws, SECOND_TEXT)
        assert second[-1]["type"] == "done"
        memory = second[-1]["rag"]["task_memory"]
        before = first[-1]["rag"]["task_memory"]
        assert memory["failed"] is True
        assert memory["goal"] == before["goal"]
        assert memory["constraints"] == before["constraints"]
        assert memory["new"] == {"goal": False, "ids": []}
        assert client.portal.call(_assistant, chat_id).content == ANSWER
        doc = client.portal.call(_doc, chat_id)
        assert doc.goal == before["goal"]
        assert [i.text for i in doc.constraints] == ["Только тема 7919"]


@respx.mock
def test_gated_turn_updates_memory_without_answer_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    _route(captured, _first_reply)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, threshold=0.99, strict=True)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, FIRST_TEXT)
        assert frames[0]["content"].startswith("Не знаю")
        memory = frames[-1]["rag"]["task_memory"]
        assert memory["goal"] == "Разобраться с разделом номер 3"
        assert [i["text"] for i in memory["constraints"]] == ["Только тема 7919"]
        stored = parse_rag_payload(client.portal.call(_assistant, chat_id).rag_sources)
        assert stored["task_memory"] == memory
    assert _kinds(captured) == ["extract"]
    assert captured[0]["stream"] is False
    assert "<assistant_answer>" not in captured[0]["messages"][1]["content"]


@respx.mock
def test_gated_turn_persist_failure_leaves_no_memory_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import ws as ws_module
    from agent.state import active_streams

    captured: list[dict[str, Any]] = []
    _route(captured, _first_reply)
    original = ws_module._persist_assistant_message

    async def failing_persist(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk full")

    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, threshold=0.99, strict=True)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            monkeypatch.setattr(ws_module, "_persist_assistant_message", failing_persist)
            frames = _send_and_drain(ws, FIRST_TEXT)
            assert frames[0]["code"] == "RAG_GATED_FAILED"
            assert client.portal.call(_row, chat_id) is None
            assert client.portal.call(_messages, chat_id) == []
            assert chat_id not in active_streams
            monkeypatch.setattr(ws_module, "_persist_assistant_message", original)
            second = _send_and_drain(ws, FIRST_TEXT)
        assert second[-1]["type"] == "done"


@respx.mock
def test_rag_off_makes_no_extraction_call() -> None:
    captured: list[dict[str, Any]] = []
    _route(captured, _first_reply)
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Plain"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, FIRST_TEXT)
        assert frames[-1]["type"] == "done"
        assert "task_memory" not in (frames[-1].get("rag") or {})
        assert client.portal.call(_row, chat_id) is None
    assert "extract" not in _kinds(captured)


@respx.mock
def test_flag_off_makes_no_extraction_or_condensing_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    _route(captured, _first_reply)
    monkeypatch.setattr(settings, "TASK_MEMORY_ENABLED", False)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            _send_and_drain(ws, FIRST_TEXT)
            frames = _send_and_drain(ws, SECOND_TEXT)
        assert frames[-1]["type"] == "done"
        assert "task_memory" not in frames[-1]["rag"]
        assert client.portal.call(_row, chat_id) is None
    assert "extract" not in _kinds(captured)
    assert "condense" not in _kinds(captured)


@respx.mock
def test_llm_error_turn_makes_no_extraction_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(500, json={"error": "boom"})

    respx.post(f"{BASE_URL}/v1/chat/completions").mock(side_effect=_side_effect)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, FIRST_TEXT)
        assert frames[-1]["type"] == "error"
        assert client.portal.call(_row, chat_id) is None
    assert "extract" not in _kinds(captured)


@respx.mock
def test_extraction_request_has_user_text_and_clean_answer_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    _route(captured, _first_reply)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            _send_and_drain(ws, QUESTION)
    extract = [body for body in captured if _kind(body) == "extract"]
    assert len(extract) == 1
    content = extract[0]["messages"][1]["content"]
    assert QUESTION in content
    assert f"<assistant_answer>{ANSWER}</assistant_answer>" in content
    assert "Цитаты:" not in content
    assert BLOCK_MARK not in json.dumps(extract[0], ensure_ascii=False)


@respx.mock
def test_session_rows_are_per_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _route(captured, _first_reply)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            _send_and_drain(ws, FIRST_TEXT)

        async def _count() -> int:
            async with async_session_factory() as session:
                return len((await session.exec(select(ChatTaskMemory))).all())

        assert client.portal.call(_count) == 1
