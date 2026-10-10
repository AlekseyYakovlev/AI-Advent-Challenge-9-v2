"""WebSocket turn tests for RAG: the outbound request is captured with respx."""

import functools
import json
from typing import Any

import httpx
import pytest
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent import kb_search
from agent.embeddings import EmbeddingError
from agent.kb_indexer import run_index_job
from agent.main import app
from agent.rag import BLOCK_CLOSE, NO_FRAGMENTS_INSTRUCTION
from kb_helpers import DIM, get_kb, install_fake_embedder, seed_kb, vector_for
from shared.config import settings
from shared.database import async_session_factory
from shared.models import ChatRagConfig, KbStatus, Message
from tests.conftest import login_test_client

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"
WS_ORIGIN = "http://localhost:8000"
BLOCK_MARK = "=== Фрагменты из базы знаний"
QUESTION = "О чём раздел номер 3?"
DISTINCT = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}


def _sse_body(lines: list[str]) -> bytes:
    """Join raw SSE `data: ...` lines into a byte body."""
    return ("\n".join(lines) + "\n").encode()


def _plain_content_response(text: str) -> httpx.Response:
    """Build a plain-content-only SSE response, one word per chunk."""
    words = text.split(" ")
    chunks = [word + (" " if i < len(words) - 1 else "") for i, word in enumerate(words)]
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": chunk}, "finish_reason": None}]})}'
        for chunk in chunks
    ]
    lines.append('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}')
    lines.append("data: [DONE]")
    return httpx.Response(200, content=_sse_body(lines))


def _recording_route(captured: list[dict[str, Any]]) -> respx.Route:
    """Mock the completions endpoint and record each request body."""

    def _side_effect(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _plain_content_response("Ответ готов.")

    return respx.post(f"{BASE_URL}/v1/chat/completions").mock(side_effect=_side_effect)


def _send_and_drain(ws: Any, content: str) -> list[dict[str, Any]]:
    """Send one chat message and collect every frame up to and including `done`."""
    ws.send_json({"content": content, "model": MODEL})
    frames: list[dict[str, Any]] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") in ("done", "error"):
            break
    return frames


async def _ready_kb(user_id: int) -> int:
    kb_id = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    assert (await get_kb(kb_id)).status == KbStatus.READY
    return kb_id


async def _set_config(
    chat_id: int,
    kb_id: int | None,
    mode: str = "rag",
    top_k: int = 3,
    **flags: Any,
) -> None:
    flags.setdefault("strict", False)
    async with async_session_factory() as session:
        session.add(
            ChatRagConfig(chat_id=chat_id, kb_id=kb_id, mode=mode, top_k=top_k, **flags)
        )
        await session.commit()


async def _messages(chat_id: int) -> list[Message]:
    async with async_session_factory() as session:
        result = await session.exec(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.id)
        )
        return list(result.all())


def _patch_query(monkeypatch: pytest.MonkeyPatch, vector: list[float] | None = None) -> None:
    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector or vector_for("Раздел номер 3 описывает тему")

    monkeypatch.setattr(kb_search, "embed_query", fake_query)


def _last_user_content(request_body: dict[str, Any]) -> str:
    users = [m for m in request_body["messages"] if m["role"] == "user"]
    return users[-1]["content"]


def _open_rag_chat(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "rag",
    top_k: int = 3,
    **flags: Any,
) -> int:
    install_fake_embedder(monkeypatch)
    _patch_query(monkeypatch)
    user_id = login_test_client(client)
    kb_id = client.portal.call(_ready_kb, user_id)
    chat_id = client.post("/api/v1/chats", json={"title": "RAG"}).json()["id"]
    client.portal.call(functools.partial(_set_config, chat_id, kb_id, mode, top_k, **flags))
    return chat_id


@respx.mock
def test_block_in_outbound_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        assert frames[-1]["type"] == "done"
        outbound = _last_user_content(captured[0])
        assert BLOCK_MARK in outbound
        assert outbound.endswith(f"Вопрос: {QUESTION}")
        rows = client.portal.call(_messages, chat_id)
        user_rows = [m for m in rows if m.role == "user"]
        assert [m.content for m in user_rows] == [QUESTION]
        assert all(BLOCK_MARK not in m.content for m in rows)


@respx.mock
def test_done_frame_has_rag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        done = frames[-1]
        rag = done["rag"]
        assert rag["mode"] == "rag"
        assert len(rag["sources"]) >= 1
        for source in rag["sources"]:
            assert {"rank", "chunk_id", "file", "score"} <= source.keys()
            assert "text" not in source
        stored = client.portal.call(_messages, chat_id)[-1]
        parsed = json.loads(stored.rag_sources)
        assert parsed["mode"] == "rag"
        assert [s["chunk_id"] for s in parsed["sources"]] == [
            s["chunk_id"] for s in rag["sources"]
        ]
        assert "text" not in parsed["sources"][0]


@respx.mock
def test_rag_off_turn_stores_mode_off() -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Plain"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, "привет")
        assert frames[-1]["rag"]["mode"] == "off"
        stored = client.portal.call(_messages, chat_id)[-1]
        assert json.loads(stored.rag_sources)["mode"] == "off"
        assert BLOCK_MARK not in _last_user_content(captured[0])


@pytest.mark.parametrize("failure", ["kb_deleted", "embedder_unavailable", "dim_mismatch"])
@respx.mock
def test_failure_degrades_with_warning(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        if failure == "kb_deleted":
            client.portal.call(_detach_kb, chat_id)
        elif failure == "embedder_unavailable":

            async def failing(*args: object) -> list[float]:
                raise EmbeddingError("нет модели")

            monkeypatch.setattr(kb_search, "embed_query", failing)
        else:
            _patch_query(monkeypatch, [0.5] * (DIM + 3))
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        assert not [f for f in frames if f.get("type") == "error"]
        assert frames[-1]["type"] == "done"
        assert frames[-1]["rag"]["warning"]["code"] == failure
        assert BLOCK_MARK not in _last_user_content(captured[0])
        rows = client.portal.call(_messages, chat_id)
        assert [m.role for m in rows] == ["user", "assistant"]
        assert rows[0].content == QUESTION


async def _detach_kb(chat_id: int) -> None:
    async with async_session_factory() as session:
        config = await session.get(ChatRagConfig, chat_id)
        config.kb_id = None
        session.add(config)
        await session.commit()


@respx.mock
def test_no_compression_never_deletes_user_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        install_fake_embedder(monkeypatch)
        _patch_query(monkeypatch)
        user_id = login_test_client(client)
        kb_id = client.portal.call(_ready_kb, user_id)
        chat_id = client.post("/api/v1/chats", json={"title": "Tight"}).json()["id"]
        resp = client.put(
            "/api/v1/settings",
            json={
                "chat_id": chat_id,
                "strategy": "no_compression",
                "context_length": 4096,
                "max_tokens": 512,
            },
        )
        assert resp.status_code == 200
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            first = _send_and_drain(ws, "word " * 2800)
            assert first[-1]["type"] == "done"
            client.portal.call(_set_config, chat_id, kb_id, "rag", 20)
            frames = _send_and_drain(ws, QUESTION)
        done = frames[-1]
        assert done["type"] == "done"
        rag = done["rag"]
        fitted = rag["context_tokens"] <= int(4096 * 0.30) and rag["dropped"] > 0
        assert (rag["warning"] or {}).get("code") == "context_full" or fitted
        rows = client.portal.call(_messages, chat_id)
        assert [m.content for m in rows if m.role == "user"][-1] == QUESTION


def _stage_route(captured: list[dict[str, Any]], *, stage_ok: bool) -> respx.Route:
    """Serve stage calls (stream false) as JSON or 500, and answer streams as SSE."""

    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        if body.get("stream") is False:
            if not stage_ok:
                return httpx.Response(500, json={"error": "boom"})
            content = "значение раздела номер 3"
            return httpx.Response(
                200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
            )
        return _plain_content_response("Ответ готов.")

    return respx.post(f"{BASE_URL}/v1/chat/completions").mock(side_effect=_side_effect)


@respx.mock
def test_done_frame_carries_search_trace_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        rag = frames[-1]["rag"]
        assert rag["v"] == 4
        assert rag["verdict"] == "ok"
        assert rag["search"]["candidates"]
        assert rag["search"]["query"] == QUESTION
        stored = json.loads(client.portal.call(_messages, chat_id)[-1].rag_sources)
        assert stored["verdict"] == rag["verdict"]
        assert len(stored["search"]["candidates"]) == len(rag["search"]["candidates"])


@respx.mock
def test_below_threshold_turn_still_answers_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, threshold=0.99)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        done = frames[-1]
        assert done["type"] == "done"
        assert done["rag"]["verdict"] == "below_threshold"
        assert done["rag"]["warning"] is None
        outbound = _last_user_content(captured[0])
        assert NO_FRAGMENTS_INSTRUCTION in outbound
        assert BLOCK_MARK not in outbound
        rows = client.portal.call(_messages, chat_id)
        assert [m.content for m in rows if m.role == "user"] == [QUESTION]


@respx.mock
def test_rewrite_runs_through_answer_client_before_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _stage_route(captured, stage_ok=True)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, rewrite=True)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        assert frames[-1]["type"] == "done"
    assert captured[0]["stream"] is False
    assert captured[0]["model"] == MODEL
    # The task-memory extraction call follows the stream, so the answer is the last streamed request.
    streamed = [body for body in captured if body.get("stream") is not False]
    assert streamed and captured.index(streamed[-1]) > 0


@respx.mock
def test_llm_rerank_failure_is_trace_only_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _stage_route(captured, stage_ok=False)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, llm_rerank=True)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        rag = frames[-1]["rag"]
        assert frames[-1]["type"] == "done"
        assert rag["warning"] is None
        assert any(item["stage"] == "llm" for item in rag["search"]["skipped"])


@respx.mock
def test_rag_off_turn_has_off_verdict_and_no_search() -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Plain"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, "привет")
        assert frames[-1]["rag"]["verdict"] == "off"
        assert frames[-1]["rag"]["search"] is None


def _stream_requests(captured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only the streaming completion requests; background calls are non-streaming."""
    return [body for body in captured if body.get("stream")]


@respx.mock
def test_strict_gate_sends_template_without_llm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _stage_route(captured, stage_ok=True)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, threshold=0.99, strict=True)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        assert [f["type"] for f in frames] == ["token", "done"]
        assert frames[0]["content"].startswith("Не знаю")
        assert [r for r in captured if r.get("stream")] == []
        rag = frames[-1]["rag"]
        assert rag["verdict"] == "below_threshold"
        assert rag["gated"] is True
        assert rag["sources"] == []
        assert rag["search"]["candidates"]
        assert rag["quotes"] == []


@respx.mock
def test_strict_gate_persists_reply_and_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _stage_route(captured, stage_ok=True)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, threshold=0.99, strict=True)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        rows = client.portal.call(_messages, chat_id)
        assert [m.role for m in rows] == ["user", "assistant"]
        assert rows[0].content == QUESTION
        assert rows[1].content == frames[0]["content"]
        stored = json.loads(rows[1].rag_sources)
        assert stored["gated"] is True
        assert stored == frames[-1]["rag"]


@respx.mock
def test_strict_gate_then_next_message_works(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _stage_route(captured, stage_ok=True)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, threshold=0.99, strict=True)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            first = _send_and_drain(ws, QUESTION)
            second = _send_and_drain(ws, QUESTION)
        assert first[-1]["type"] == "done"
        assert second[-1]["type"] == "done"
        assert len(client.portal.call(_messages, chat_id)) == 4


def _fragment_one(body: dict[str, Any]) -> str:
    """Extract the text of fragment 1 from the merged user message of a recorded request."""
    lines = _last_user_content(body).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("[1] "))
    collected: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("[2] ") or line == BLOCK_CLOSE:
            break
        collected.append(line)
    return " ".join(collected).strip()


def _reply_route(
    captured: list[dict[str, Any]], make_reply: Any
) -> respx.Route:
    """Answer streaming requests with make_reply(body); serve other calls as short JSON."""

    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        if body.get("stream") is False:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
            )
        return _plain_content_response(make_reply(body))

    return respx.post(f"{BASE_URL}/v1/chat/completions").mock(side_effect=_side_effect)


def _strict_turn(
    monkeypatch: pytest.MonkeyPatch, make_reply: Any, *, strict: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any], Message]:
    captured: list[dict[str, Any]] = []
    _reply_route(captured, make_reply)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, strict=strict)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _send_and_drain(ws, QUESTION)
        assistant = client.portal.call(_messages, chat_id)[-1]
    return frames, frames[-1], assistant


@respx.mock
def test_strict_answer_is_cut_and_quotes_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    def reply(body: dict[str, Any]) -> str:
        quote = _fragment_one(body)[:60]
        return f"Ответ [1].\n\nЦитаты:\n[1] «{quote}»"

    frames, done, assistant = _strict_turn(monkeypatch, reply)
    assert done["type"] == "done"
    assert assistant.content == "Ответ [1]."
    assert "Цитаты" not in assistant.content
    streamed = "".join(f["content"] for f in frames if f["type"] == "token")
    assert "Цитаты" in streamed
    rag = done["rag"]
    assert len(rag["quotes"]) == 1
    quote = rag["quotes"][0]
    assert quote["state"] == "exact"
    assert quote["rank"] == 1
    assert quote["auto"] is False
    assert quote["file"] == rag["sources"][0]["file"]
    assert rag["cited_ranks"] == [1]
    assert rag["answer_supported"] is True
    assert json.loads(assistant.rag_sources) == rag


@respx.mock
def test_strict_unverified_quote_kept_and_turn_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    def reply(body: dict[str, Any]) -> str:
        return "Ответ [1].\n\nЦитаты:\n[1] «Этой фразы нет ни в одном фрагменте вообще»"

    frames, done, assistant = _strict_turn(monkeypatch, reply)
    assert frames[-1]["type"] == "done"
    quotes = done["rag"]["quotes"]
    assert quotes[0]["state"] == "unverified"
    assert quotes[0]["auto"] is False
    assert any(q["auto"] for q in quotes[1:])
    assert assistant.content == "Ответ [1]."


@respx.mock
def test_strict_answer_without_refs_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    frames, done, assistant = _strict_turn(monkeypatch, lambda body: "Ответ готов.")
    assert assistant.content == "Ответ готов."
    assert done["rag"]["answer_supported"] is False
    assert any(q["auto"] for q in done["rag"]["quotes"])


@respx.mock
def test_strict_model_idk_has_no_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Не знаю, во фрагментах этого нет. Что именно вас интересует?"
    frames, done, assistant = _strict_turn(monkeypatch, lambda body: text)
    assert done["rag"]["verdict"] == "model_idk"
    assert done["rag"]["quotes"] == []
    assert done["rag"]["sources"]
    assert assistant.content == text


@respx.mock
def test_strict_off_keeps_tail_and_has_no_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Ответ [1].\n\nЦитаты:\n[1] «что-то»"
    frames, done, assistant = _strict_turn(monkeypatch, lambda body: text, strict=False)
    assert assistant.content == text
    assert done["rag"].get("quotes", []) == []


@respx.mock
def test_gated_turn_persist_failure_rolls_back_and_chat_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import ws as ws_module
    from agent.state import active_streams

    captured: list[dict[str, Any]] = []
    _stage_route(captured, stage_ok=True)
    original = ws_module._persist_assistant_message

    async def failing_persist(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk full")

    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, threshold=0.99, strict=True)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            monkeypatch.setattr(ws_module, "_persist_assistant_message", failing_persist)
            frames = _send_and_drain(ws, QUESTION)
            assert [f["type"] for f in frames] == ["error"]
            assert frames[0]["code"] == "RAG_GATED_FAILED"
            assert client.portal.call(_messages, chat_id) == []
            assert chat_id not in active_streams
            monkeypatch.setattr(ws_module, "_persist_assistant_message", original)
            second = _send_and_drain(ws, QUESTION)
        assert [f["type"] for f in second] == ["token", "done"]
        assert len(client.portal.call(_messages, chat_id)) == 2
        assert chat_id not in active_streams
