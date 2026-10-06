"""Routing of chat, critique, facts and title calls through the selected LLM provider."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent import providers
from agent.context_engine import extract_and_update_facts
from agent.main import app
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, Settings
from tests.conftest import _create_user, login_test_client
from tests.test_memory_ws import (
    WS_ORIGIN,
    _plain_content_response,
    _queue_responses,
    _tool_calls_response,
)

LM_URL = f"{settings.LM_STUDIO_BASE_URL}/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "routing-model"


@pytest.fixture(autouse=True)
def _quiet_background_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the title job and facts extraction out of tests that only inspect the stream."""
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)


def _create_deepseek(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, name: str = "DeepSeek"
) -> int:
    """Create a DeepSeek provider for the logged-in user and return its id."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    resp = client.post(
        "/api/v1/llm-providers",
        json={
            "name": name,
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _new_chat(client: TestClient) -> int:
    return client.post("/api/v1/chats", json={"title": "New Chat"}).json()["id"]


def _turn(
    client: TestClient,
    chat_id: int,
    provider_id: int | None = None,
    after: Callable[[], None] | None = None,
) -> list[dict]:
    """Send one message and collect frames up to the first done or error frame.

    `after` runs while the socket is still open, so cleanup the handler does after
    the error frame is not cut short by the disconnect.
    """
    body: dict[str, Any] = {"content": "привет", "model": MODEL}
    if provider_id is not None:
        body["provider_id"] = provider_id
    with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
        ws.send_json(body)
        frames: list[dict] = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame.get("type") in ("done", "error"):
                if after is not None:
                    after()
                return frames


def _tree_ids(client: TestClient, chat_id: int, settle: bool = False) -> list[int]:
    """Return the stored message ids; with settle, wait for the asynchronous message removal."""
    for _ in range(40 if settle else 1):
        ids = _tree_ids_once(client, chat_id)
        if not (settle and ids):
            break
        time.sleep(0.05)
    return ids


def _tree_ids_once(client: TestClient, chat_id: int) -> list[int]:
    resp = client.get(f"/api/v1/chats/{chat_id}/tree")
    assert resp.status_code == 200
    data = resp.json()
    messages = data["messages"] if isinstance(data, dict) else data
    return [m["id"] for m in messages]


@respx.mock
def test_legacy_payload_streams_from_lm_studio_without_auth() -> None:
    lm = respx.post(LM_URL).mock(side_effect=_queue_responses([_plain_content_response("ок")]))
    ds = respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(500))

    with TestClient(app) as client:
        login_test_client(client)
        frames = _turn(client, _new_chat(client))

    assert frames[-1]["type"] == "done"
    assert lm.call_count == 1
    assert "Authorization" not in lm.calls[0].request.headers
    assert not ds.called


@respx.mock
def test_provider_id_streams_from_deepseek_with_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lm = respx.post(LM_URL).mock(return_value=httpx.Response(500))
    ds = respx.post(DEEPSEEK_URL).mock(
        side_effect=_queue_responses([_plain_content_response("привет мир")]),
    )

    with TestClient(app) as client:
        login_test_client(client)
        provider_id = _create_deepseek(client, monkeypatch)
        frames = _turn(client, _new_chat(client), provider_id)

    assert frames[-1]["type"] == "done"
    assert ds.call_count == 1
    assert ds.calls[0].request.headers["Authorization"] == "Bearer sk-test"
    assert not lm.called


@respx.mock
def test_deleted_provider_gives_error_frame_and_no_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lm = respx.post(LM_URL).mock(return_value=httpx.Response(500))
    ds = respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(500))

    with TestClient(app) as client:
        login_test_client(client)
        # Seed the built-in providers first so the deleted id cannot be reused by a later seed.
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        assert client.get("/api/v1/llm-providers").status_code == 200
        provider_id = _create_deepseek(client, monkeypatch, name="Custom")
        assert client.delete(f"/api/v1/llm-providers/{provider_id}").status_code == 204
        chat_id = _new_chat(client)
        frames = _turn(client, chat_id, provider_id)
        stored = _tree_ids(client, chat_id)

    assert len(frames) == 1
    assert frames[0]["type"] == "error"
    assert frames[0]["code"] == "PROVIDER_UNAVAILABLE"
    assert frames[0]["detail"].startswith("Провайдер")
    assert "недоступен" in frames[0]["detail"]
    assert stored == []
    assert not lm.called and not ds.called


@respx.mock
def test_disabled_provider_names_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(500))

    with TestClient(app) as client:
        login_test_client(client)
        provider_id = _create_deepseek(client, monkeypatch)
        resp = client.put(f"/api/v1/llm-providers/{provider_id}", json={"enabled": False})
        assert resp.status_code == 200
        chat_id = _new_chat(client)
        frames = _turn(client, chat_id, provider_id)
        stored = _tree_ids(client, chat_id)

    assert frames[0]["code"] == "PROVIDER_UNAVAILABLE"
    assert "«DeepSeek»" in frames[0]["detail"]
    assert stored == []


@respx.mock
def test_foreign_provider_does_not_leak_its_name(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(500))

    with TestClient(app) as client:
        login_test_client(client, "provider_owner", "pw-owner")
        foreign_id = _create_deepseek(client, monkeypatch)
        client.post("/api/v1/auth/logout")
        login_test_client(client, "provider_thief", "pw-thief")
        chat_id = _new_chat(client)
        frames = _turn(client, chat_id, foreign_id)
        stored = _tree_ids(client, chat_id)

    assert frames[0]["code"] == "PROVIDER_UNAVAILABLE"
    assert "DeepSeek" not in frames[0]["detail"]
    assert stored == []
    assert not ds.called


@respx.mock
def test_provider_401_during_stream_names_provider_not_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(401, json={"error": "bad"}))

    with TestClient(app) as client:
        login_test_client(client)
        provider_id = _create_deepseek(client, monkeypatch)
        chat_id = _new_chat(client)
        stored: list[int] = [-1]

        def _settle() -> None:
            stored[:] = _tree_ids(client, chat_id, settle=True)

        frames = _turn(client, chat_id, provider_id, after=_settle)

    error = frames[-1]
    assert error["code"] == "LLM_ERROR"
    assert "DeepSeek" in error["detail"]
    assert "DEEPSEEK_API_KEY" in error["detail"]
    assert "sk-test" not in error["detail"]
    assert stored == []


@respx.mock
def test_provider_401_on_tool_follow_up_names_provider_not_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _side_effect(request: httpx.Request) -> httpx.Response:
        if not queue:
            return httpx.Response(401, json={"error": "bad"})
        return queue.pop(0)

    queue = [
        _tool_calls_response(
            [
                (
                    "call_1",
                    "save_working_memory",
                    json.dumps({"key": "k", "content": "v"}),
                )
            ]
        )
    ]
    respx.post(DEEPSEEK_URL).mock(side_effect=_side_effect)

    with TestClient(app) as client:
        login_test_client(client)
        provider_id = _create_deepseek(client, monkeypatch)
        frames = _turn(client, _new_chat(client), provider_id)

    # The tool already ran, so the turn is kept and the failure is a note in the reply.
    assert frames[-1]["type"] == "done"
    note = "".join(f.get("content", "") for f in frames if f.get("type") == "token")
    assert "DeepSeek" in note
    assert "DEEPSEEK_API_KEY" in note
    assert "sk-test" not in note


@respx.mock
def test_turn_passes_provider_to_title_and_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title_calls: list[tuple[Any, ...]] = []
    facts_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        "agent.ws.schedule_title_generation", lambda *args: title_calls.append(args)
    )
    monkeypatch.setattr(
        "agent.ws.extract_and_update_facts",
        lambda *args, **kwargs: facts_calls.append((args, kwargs)),
    )
    respx.post(DEEPSEEK_URL).mock(
        side_effect=_queue_responses([_plain_content_response("ответ")]),
    )

    with TestClient(app) as client:
        user_id = login_test_client(client)
        provider_id = _create_deepseek(client, monkeypatch)
        frames = _turn(client, _new_chat(client), provider_id)

    assert frames[-1]["type"] == "done"
    assert len(title_calls) == 1
    assert title_calls[0][1] == user_id
    assert title_calls[0][5] == provider_id
    assert len(facts_calls) == 1
    assert facts_calls[0][1] == {"user_id": user_id, "provider_id": provider_id}


@respx.mock
def test_self_critique_receives_the_turn_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    async def _critique(
        active: list[dict[str, Any]],
        text: str,
        calls: list[dict[str, Any]],
        model: str,
        client: Any,
    ) -> dict[str, Any]:
        seen.append(client)
        return {"conflict": False}

    monkeypatch.setattr("agent.ws.invariants.run_self_critique", _critique)
    respx.post(DEEPSEEK_URL).mock(
        side_effect=_queue_responses([_plain_content_response("ответ")]),
    )

    with TestClient(app) as client:
        login_test_client(client)
        provider_id = _create_deepseek(client, monkeypatch)
        resp = client.post(
            "/api/v1/invariants", json={"title": "Без Docker", "rule_text": "Не предлагай Docker"}
        )
        assert resp.status_code == 201
        frames = _turn(client, _new_chat(client), provider_id)

    assert frames[-1]["type"] == "done"
    assert len(seen) == 1
    assert seen[0]._base_url == "https://api.deepseek.com"


async def _setup_facts(monkeypatch: pytest.MonkeyPatch, name: str) -> tuple[int, int, int]:
    """Create a user with a DeepSeek provider and a chat with an empty facts row."""
    monkeypatch.setattr("agent.context_engine.FACTS_DEBOUNCE_SECONDS", 0.05)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    user_id = await _create_user(name, "pw")
    async with async_session_factory() as session:
        row = await providers.create_provider(
            session,
            user_id,
            {
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "enabled": True,
            },
        )
        chat = Chat(title="Facts", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(Settings(chat_id=chat.id, facts_json="{}"))
        await session.commit()
        return user_id, row.id, chat.id


async def _facts_json(chat_id: int) -> str:
    async with async_session_factory() as session:
        result = await session.exec(select(Settings).where(Settings.chat_id == chat_id))
        return result.first().facts_json


@respx.mock
async def test_facts_extraction_uses_selected_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, provider_id, chat_id = await _setup_facts(monkeypatch, "facts_ds")
    route = respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"language": "Go"}'}}]}
        )
    )

    async with async_session_factory() as session:
        extract_and_update_facts(
            session, chat_id, "I love Go", "m", user_id=user_id, provider_id=provider_id
        )
        await asyncio.sleep(0.5)

    assert route.call_count == 1
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-test"
    assert json.loads(await _facts_json(chat_id)) == {"language": "Go"}


@respx.mock
async def test_facts_extraction_skipped_for_deleted_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _, chat_id = await _setup_facts(monkeypatch, "facts_gone")
    route = respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(500))

    async with async_session_factory() as session:
        extract_and_update_facts(
            session, chat_id, "I love Go", "m", user_id=user_id, provider_id=987654
        )
        await asyncio.sleep(0.5)

    assert not route.called
    assert await _facts_json(chat_id) == "{}"
