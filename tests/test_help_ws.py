"""The /help command over WebSocket: notices, the documentation turn, tools and side effects."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent import kb_search, limits
from agent import state as agent_state
from agent.help_command import (
    HELP_NO_FRAGMENTS_NOTE,
    HELP_SYSTEM_PROMPT,
    HELP_USAGE_TEXT,
)
from agent.kb_indexer import run_index_job
from agent.main import app
from agent.rag_cite import IDK_SENTENCE
from shared.config import settings
from shared.database import async_session_factory
from shared.models import (
    Chat,
    ChatRagConfig,
    GlobalInvariant,
    HelpConfig,
    KbStatus,
    LongTermMemory,
    Message,
)
from tests.conftest import login_test_client
from tests.kb_helpers import get_kb, install_fake_embedder, seed_kb, vector_for
from tests.test_memory_ws import _plain_content_response, _queue_responses, _tool_calls_response

BASE_URL = settings.LM_STUDIO_BASE_URL
COMPLETIONS = f"{BASE_URL}/v1/chat/completions"
MODEL = "help-test-model"
WS_ORIGIN = "http://localhost:8000"
QUESTION = "О чём раздел номер 3?"
DISTINCT = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}
FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
ANSWER = "Раздел номер 3 описывает тему [1]."
LOCAL_GIT_PREFIX = "mcp__local-git__"
FRAME_END = ("done", "error", "help_notice")


@pytest.fixture(autouse=True)
def _help_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake embeddings with a fixed query vector and quiet background jobs."""
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)
    install_fake_embedder(monkeypatch)

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for("Раздел номер 3 описывает тему")

    monkeypatch.setattr(kb_search, "embed_query", fake_query)


def _upstream(
    captured: list[dict[str, Any]], responses: list[httpx.Response] | None = None
) -> respx.Route:
    """Mock the completions endpoint; serve the queue, then a plain answer, and record requests."""
    queue = list(responses or [])

    def _side_effect(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return queue.pop(0) if queue else _plain_content_response(ANSWER)

    return respx.post(COMPLETIONS).mock(side_effect=_side_effect)


def _drain(ws: Any, content: str, model: bool = True) -> list[dict[str, Any]]:
    """Send one message and collect frames up to the first terminal frame."""
    body: dict[str, Any] = {"content": content}
    if model:
        body["model"] = MODEL
    ws.send_json(body)
    frames: list[dict[str, Any]] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") in FRAME_END and frame.get("code") != "TOOL_ERROR":
            return frames


async def _ready_kb(user_id: int, name: str | None = None) -> int:
    kb_id = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    kb = await get_kb(kb_id)
    assert kb is not None and kb.status == KbStatus.READY
    return kb_id


async def _mark(user_id: int, kb_id: int | None, **fields: Any) -> None:
    fields.setdefault("threshold", 0.0)
    async with async_session_factory() as session:
        session.add(HelpConfig(user_id=user_id, kb_id=kb_id, **fields))
        await session.commit()


async def _kb_name(kb_id: int) -> str:
    kb = await get_kb(kb_id)
    assert kb is not None
    return kb.name


async def _rows(chat_id: int) -> list[Message]:
    async with async_session_factory() as session:
        result = await session.exec(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.id)
        )
        return list(result.all())


async def _lock_released(chat_id: int) -> None:
    """Wait until the turn, including its failure cleanup, has released the chat lock."""
    for _ in range(100):
        lock = agent_state.chat_locks.get(chat_id)
        if lock is None or not lock.locked():
            return
        await asyncio.sleep(0.05)


async def _rag_config(chat_id: int) -> ChatRagConfig | None:
    async with async_session_factory() as session:
        return await session.get(ChatRagConfig, chat_id)


async def _add_rag_config(chat_id: int, kb_id: int | None, mode: str) -> None:
    async with async_session_factory() as session:
        session.add(ChatRagConfig(chat_id=chat_id, kb_id=kb_id, mode=mode, top_k=3, strict=False))
        await session.commit()


async def _chat_row(chat_id: int) -> Chat:
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        return chat


def _open(
    client: TestClient, *, mark: bool = True, **config: Any
) -> tuple[int, int, int]:
    """Log in, index a base, mark it for /help, create a chat; return (user, kb, chat)."""
    user_id = login_test_client(client)
    kb_id = client.portal.call(_ready_kb, user_id)
    if mark:
        client.portal.call(lambda: _mark(user_id, kb_id, **config))
    chat_id = client.post("/api/v1/chats", json={"title": "Help chat"}).json()["id"]
    return user_id, kb_id, chat_id


def _add_server(client: TestClient, name: str, connect: bool = True) -> None:
    created = client.post(
        "/api/v1/mcp/servers",
        json={"name": name, "command": sys.executable, "args": [FIXTURE, "tools_extra"]},
    )
    assert created.status_code == 201, created.text
    if connect:
        resp = client.post(f"/api/v1/mcp/servers/{created.json()['id']}/connect")
        assert resp.json()["connection"]["status"] == "connected"


def _tool_names(body: dict[str, Any]) -> list[str]:
    return [tool["function"]["name"] for tool in body.get("tools", [])]


def _types(frames: list[dict[str, Any]]) -> list[str]:
    return [f["type"] for f in frames]


@respx.mock
def test_usage_notice_stores_nothing() -> None:
    """A bare /help sends one usage notice, no model call and no rows."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/help")
        rows = client.portal.call(_rows, chat_id)
    assert frames == [
        {"type": "help_notice", "kind": "usage", "stored": False, "text": HELP_USAGE_TEXT}
    ]
    assert rows == [] and captured == []


@respx.mock
def test_no_base_notice() -> None:
    """A question with no marked base gets the no_base notice and nothing is stored."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client, mark=False)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/help как запустить?")
        rows = client.portal.call(_rows, chat_id)
    assert len(frames) == 1 and frames[0]["type"] == "help_notice"
    assert frames[0]["kind"] == "no_base" and frames[0]["stored"] is False
    assert rows == [] and captured == []


@respx.mock
def test_base_not_ready_notice_names_the_base() -> None:
    """A marked base that is still indexing gives a notice that contains its name."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        user_id = login_test_client(client)
        kb_id = client.portal.call(seed_kb, user_id, DISTINCT)
        client.portal.call(lambda: _mark(user_id, kb_id))
        name = client.portal.call(_kb_name, kb_id)
        chat_id = client.post("/api/v1/chats", json={"title": "c"}).json()["id"]
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/help вопрос")
        rows = client.portal.call(_rows, chat_id)
    assert frames[0]["type"] == "help_notice" and name in frames[0]["text"]
    assert rows == [] and captured == []


@respx.mock
def test_real_turn_answers_with_sources_and_label() -> None:
    """A real /help turn stores two messages and returns sources and the base label."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, kb_id, chat_id = _open(client)
        name = client.portal.call(_kb_name, kb_id)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/help {QUESTION}")
        rows = client.portal.call(_rows, chat_id)
    done = frames[-1]
    assert done["type"] == "done"
    rag = done["rag"]
    assert rag["mode"] == "rag" and rag["sources"] and rag["gated"] is False
    assert rag["help"]["kb_name"] == name
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == f"/help {QUESTION}"
    assert json.loads(rows[1].rag_sources)["help"] == rag["help"]


@respx.mock
def test_outbound_has_only_help_prompt_fragments_and_question() -> None:
    """D-10: the model gets two messages and none of the chat's history or system prompt."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        resp = client.put(
            "/api/v1/settings",
            json={
                "chat_id": chat_id,
                "strategy": "sliding",
                "context_length": 8192,
                "max_tokens": 512,
                "system_prompt": "MARK-SYSTEM-PROMPT",
            },
        )
        assert resp.status_code == 200, resp.text
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            _drain(ws, "MARK-EARLIER-USER")
            _drain(ws, f"/help {QUESTION}")
    request = captured[-1]
    assert [m["role"] for m in request["messages"]] == ["system", "user"]
    assert request["messages"][0]["content"].startswith(HELP_SYSTEM_PROMPT)
    user = request["messages"][1]["content"]
    assert QUESTION in user and "/help" not in user and "Фрагмент" in user
    dump = json.dumps(request, ensure_ascii=False)
    assert "MARK-EARLIER-USER" not in dump and "MARK-SYSTEM-PROMPT" not in dump
    assert ANSWER not in dump


@respx.mock
def test_chat_rag_config_is_neither_read_nor_changed() -> None:
    """D-04: no row appears for a chat without one, and an existing row stays as it is."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        user_id, kb_id, chat_id = _open(client)
        other_chat = client.post("/api/v1/chats", json={"title": "second"}).json()["id"]
        client.portal.call(_add_rag_config, other_chat, None, "off")
        before = client.portal.call(_rag_config, other_chat)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            assert _drain(ws, f"/help {QUESTION}")[-1]["type"] == "done"
        with client.websocket_connect(
            f"/ws/chat/{other_chat}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            frames = _drain(ws, f"/help {QUESTION}")
        after = client.portal.call(_rag_config, other_chat)
        absent = client.portal.call(_rag_config, chat_id)
    assert frames[-1]["rag"]["kb_id"] == kb_id and frames[-1]["rag"]["sources"]
    assert absent is None
    assert before is not None and after is not None
    assert (after.kb_id, after.mode, after.top_k, after.updated_at) == (
        before.kb_id, before.mode, before.top_k, before.updated_at,
    )


@respx.mock
def test_no_fragments_still_calls_the_model() -> None:
    """D-01: nothing above the threshold still produces a model answer without sources."""
    captured: list[dict[str, Any]] = []
    _upstream(captured, [_plain_content_response("Не нашёл в документации, но вот ответ.")])
    with TestClient(app) as client:
        _, _, chat_id = _open(client, threshold=1.1)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/help {QUESTION}")
        rows = client.portal.call(_rows, chat_id)
    done = frames[-1]
    assert done["type"] == "done" and done["rag"]["sources"] == []
    assert len(captured) == 1
    assert captured[0]["messages"][-1]["content"].startswith(HELP_NO_FRAGMENTS_NOTE)
    assert IDK_SENTENCE not in rows[1].content
    assert rows[1].content == "Не нашёл в документации, но вот ответ."


@respx.mock
def test_default_tools_are_only_local_git() -> None:
    """D-07: only the tools of the server named local-git are offered, no built-in tool."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        _add_server(client, "local-git")
        _add_server(client, "other")
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            _drain(ws, f"/help {QUESTION}")
    names = _tool_names(captured[0])
    assert names and all(name.startswith(LOCAL_GIT_PREFIX) for name in names)


@respx.mock
def test_missing_default_server_gives_warning() -> None:
    """D-08: no local-git server means no tools, a normal answer and a warning."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        _add_server(client, "other")
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/help {QUESTION}")
    assert "tools" not in captured[0]
    assert frames[-1]["type"] == "done"
    assert frames[-1]["rag"]["help"]["warnings"] == [
        {
            "server": "local-git",
            "text": "Сервер local-git недоступен, его инструменты не использовались.",
        }
    ]


@respx.mock
def test_tool_call_frame_and_trace() -> None:
    """D-09: a tool call is an ordinary tool_call frame and lands in the stored trace."""
    captured: list[dict[str, Any]] = []
    _upstream(
        captured,
        [
            _tool_calls_response(
                [("c1", f"{LOCAL_GIT_PREFIX}echo", json.dumps({"text": "git-ok"}))]
            ),
            _plain_content_response("Ветка проверена."),
        ],
    )
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        _add_server(client, "local-git")
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/help {QUESTION}")
        rows = client.portal.call(_rows, chat_id)
    tool_frames = [f for f in frames if f["type"] == "tool_call"]
    assert [(f["name"], f["ok"]) for f in tool_frames] == [(f"{LOCAL_GIT_PREFIX}echo", True)]
    assert _types(frames).index("tool_call") < _types(frames).index("done")
    assert rows[1].tool_trace
    assert frames[-1]["rag"]["quotes"] == []
    assert frames[-1]["rag"]["answer_supported"] is None


@respx.mock
def test_builtin_write_tool_is_rejected_by_default() -> None:
    """T-23-18: a memory write tool the model tries to call is rejected and writes nothing."""
    captured: list[dict[str, Any]] = []
    _upstream(
        captured,
        [
            _tool_calls_response(
                [("c1", "save_long_term_memory", json.dumps({"key": "k", "content": "v"}))]
            ),
            _plain_content_response("Не получилось сохранить."),
            _plain_content_response("Не получилось сохранить."),
        ],
    )
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        _add_server(client, "local-git")
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/help {QUESTION}")

        async def memory_rows() -> list[LongTermMemory]:
            async with async_session_factory() as session:
                return list((await session.exec(select(LongTermMemory))).all())

        stored = client.portal.call(memory_rows)
    assert frames[-1]["type"] == "done" and frames[-1]["memory_writes"] == []
    assert [f["ok"] for f in frames if f["type"] == "tool_call"] == [False]
    assert stored == []


@respx.mock
def test_side_jobs_do_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-07: a /help turn runs no task memory, facts, title, invariant critique."""
    calls: list[str] = []

    async def critique(*args: object, **kwargs: object) -> dict[str, Any]:
        calls.append("critique")
        return {"conflict": False}

    async def task_memory(*args: object, **kwargs: object) -> None:
        calls.append("task_memory")

    monkeypatch.setattr("agent.ws.invariants.run_self_critique", critique)
    monkeypatch.setattr("agent.ws.update_task_memory", task_memory)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *a, **k: calls.append("facts"))
    monkeypatch.setattr(
        "agent.ws.schedule_title_generation", lambda *a, **k: calls.append("title")
    )
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)

        async def add_invariant() -> None:
            async with async_session_factory() as session:
                session.add(GlobalInvariant(title="Rule", rule_text="Always be polite."))
                await session.commit()

        client.portal.call(add_invariant)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/help {QUESTION}")
        chat = client.portal.call(_chat_row, chat_id)
    assert frames[-1]["type"] == "done" and frames[-1]["invariant_conflict"] is None
    assert calls == []
    assert chat.title == "Help chat"
    assert len(captured) == 1


@respx.mock
def test_task_group_offers_task_tools_only() -> None:
    """With tools_tasks on, the four task tools are offered and no memory or scheduler tool."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client, tools_tasks=True)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            _drain(ws, f"/help {QUESTION}")
    names = set(_tool_names(captured[0]))
    assert {"create_task", "transition_task", "pause_task", "resume_task"} <= names
    assert not names & {"save_working_memory", "save_long_term_memory", "schedule_task"}


@respx.mock
def test_tree_route_returns_label_sources_and_quotes() -> None:
    """D-15: the stored exchange comes back from the tree with the label and sources."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, kb_id, chat_id = _open(client)
        name = client.portal.call(_kb_name, kb_id)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            _drain(ws, f"/help {QUESTION}")
        tree = client.get(f"/api/v1/chats/{chat_id}/tree").json()
    assert [m["role"] for m in tree] == ["assistant", "user"]
    payload = tree[0]["rag_sources"]
    assert payload["help"]["kb_name"] == name
    assert payload["sources"] and "quotes" in payload


@respx.mock
def test_next_ordinary_message_sees_the_pair() -> None:
    """D-11: an ordinary message after /help carries the stored pair in its history."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            _drain(ws, f"/help {QUESTION}")
            _drain(ws, "обычный вопрос")
    dump = json.dumps(captured[-1]["messages"], ensure_ascii=False)
    assert f"/help {QUESTION}" in dump and ANSWER in dump


@respx.mock
def test_helpme_is_an_ordinary_turn() -> None:
    """/helpme is not the command: the chat's own prompt applies and a done frame arrives."""
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, "/helpme расскажи")
        rows = client.portal.call(_rows, chat_id)
    assert frames[-1]["type"] == "done"
    assert not captured[0]["messages"][0]["content"].startswith(HELP_SYSTEM_PROMPT)
    assert rows[0].content == "/helpme расскажи"


@respx.mock
def test_notices_take_no_rate_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-14: usage notices leave the rate slot free for an ordinary message."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            assert _drain(ws, "/help")[-1]["type"] == "help_notice"
            assert _drain(ws, "/help")[-1]["type"] == "help_notice"
            assert _drain(ws, "обычный")[-1]["type"] == "done"


@respx.mock
def test_real_help_turn_takes_a_rate_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real /help turn counts toward the limit like any message."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            assert _drain(ws, f"/help {QUESTION}")[-1]["type"] == "done"
            second = _drain(ws, "обычный")[-1]
    assert second["type"] == "error" and second["code"] == limits.CODE_RATE_LIMITED


@respx.mock
def test_upstream_failure_discards_the_user_message() -> None:
    """T-23-22: a failed /help turn sends LLM_ERROR and leaves no rows."""
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(500, text="boom"))
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _drain(ws, f"/help {QUESTION}")
            client.portal.call(_lock_released, chat_id)
        rows = client.portal.call(_rows, chat_id)
    assert frames[-1]["type"] == "error" and frames[-1]["code"] == "LLM_ERROR"
    assert rows == []


@respx.mock
def test_invalid_payload_takes_no_rate_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A /help message that fails validation gets the validation error and no slot."""
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    captured: list[dict[str, Any]] = []
    _upstream(captured)
    with TestClient(app) as client:
        _, _, chat_id = _open(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            invalid = _drain(ws, "/help x", model=False)[-1]
            ok = _drain(ws, f"/help {QUESTION}")[-1]
    assert invalid["type"] == "error" and isinstance(invalid["detail"], list)
    assert ok["type"] == "done"
