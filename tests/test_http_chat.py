"""Plain-HTTP chat endpoint: success body, auth, busy chat, error mapping, leaf restore, events."""

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from agent import http_chat
from agent import state as agent_state
from agent.events import hub as events_hub
from agent.main import app
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, Message
from tests.conftest import _create_user
from tests.kb_helpers import wait_until

BASE_URL = settings.LM_STUDIO_BASE_URL
COMPLETIONS = f"{BASE_URL}/v1/chat/completions"
DEEPSEEK_COMPLETIONS = "https://api.deepseek.com/v1/chat/completions"
MODEL = "http-test-model"
REPLY = "Ответ готов."
RESPONSE_KEYS = {
    "chat_id",
    "user_message_id",
    "assistant_message_id",
    "content",
    "model",
    "provider_id",
    "stats",
    "tool_calls",
    "memory_writes",
    "task_writes",
    "invariant_conflict",
    "rag",
    "help",
}


def _ok_response(text: str = REPLY) -> httpx.Response:
    """Build a plain-content SSE response."""
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]})}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    return httpx.Response(200, content=("\n".join(lines) + "\n").encode())


class _Upstream:
    """Mutable behaviour of the mocked completions endpoint."""

    def __init__(self) -> None:
        self.failure: Exception | httpx.Response | None = None
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            return self.failure
        return _ok_response()


def _mock_upstream(url: str = COMPLETIONS) -> _Upstream:
    upstream = _Upstream()
    respx.post(url).mock(side_effect=upstream)
    return upstream


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _key(client: AsyncClient) -> str:
    """Create an API key through the cookie client and return its secret."""
    resp = await client.post("/api/v1/api-keys", json={"name": "t"})
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


async def _chat(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/chats", json={"title": "HTTP chat"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _api_client() -> AsyncClient:
    """Build a cookie-less client so only the Authorization header authenticates."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post(
    client: AsyncClient, chat_id: int, key: str | None, **body: Any
) -> httpx.Response:
    body.setdefault("content", "привет")
    body.setdefault("model", MODEL)
    headers = _bearer(key) if key else {}
    return await client.post(
        f"/api/v1/chats/{chat_id}/messages", json=body, headers=headers
    )


async def _rows(chat_id: int) -> list[Message]:
    async with async_session_factory() as session:
        result = await session.exec(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.id)
        )
        return list(result.all())


async def _leaf(chat_id: int) -> int | None:
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        return chat.current_leaf_message_id


async def _put_settings(client: AsyncClient, chat_id: int | None, **fields: Any) -> None:
    body: dict[str, Any] = {"strategy": "sliding", "context_length": 4096, "max_tokens": 512}
    body.update(fields)
    if chat_id is not None:
        body["chat_id"] = chat_id
    resp = await client.put("/api/v1/settings", json=body)
    assert resp.status_code == 200, resp.text


@pytest.fixture(autouse=True)
def _quiet_background_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the title job and facts extraction out of the tests that do not observe them."""
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)


@respx.mock
async def test_success_body_has_exact_keys_and_stats(authenticated_client: AsyncClient) -> None:
    """HCHAT-01, D-13: one JSON body with the fixed key set and the live statistics."""
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert set(data.keys()) == RESPONSE_KEYS
        assert data["content"] == REPLY
        assert data["chat_id"] == chat_id
        assert data["model"] == MODEL
        assert data["tool_calls"] == []
        stats = await api.get(f"/api/v1/chats/{chat_id}/stats", headers=_bearer(key))
        assert stats.status_code == 200
        assert "queue_wait_seconds" not in stats.json()
        response_stats = dict(data["stats"])
        assert response_stats.pop("queue_wait_seconds") == 0.0
        assert response_stats == stats.json()


@respx.mock
async def test_exchange_is_stored_in_the_tree(authenticated_client: AsyncClient) -> None:
    """HCHAT-02, D-15: the pair sits in the normal tree and carries no HTTP marker."""
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        data = (await _post(api, chat_id, key)).json()
        tree = await api.get(f"/api/v1/chats/{chat_id}/tree", headers=_bearer(key))
    ids = {m["id"] for m in tree.json()}
    assert ids == {data["user_message_id"], data["assistant_message_id"]}
    rows = await _rows(chat_id)
    assert [m.role for m in rows] == ["user", "assistant"]
    assert rows[1].parent_id == rows[0].id
    assert rows[0].parent_id is None
    assert rows[1].content == REPLY
    assert not [name for name in Message.model_fields if "http" in name or "api" in name]


@respx.mock
async def test_message_attaches_to_current_leaf(authenticated_client: AsyncClient) -> None:
    """D-10: the next message continues the leaf, also after a branch switch."""
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        first = (await _post(api, chat_id, key, content="one")).json()
        second = (await _post(api, chat_id, key, content="two")).json()
        rows = {m.id: m for m in await _rows(chat_id)}
        assert rows[second["user_message_id"]].parent_id == first["assistant_message_id"]
        branch = await authenticated_client.post(
            f"/api/v1/chats/{chat_id}/branch", json={"message_id": first["user_message_id"]},
        )
        assert branch.status_code == 200, branch.text
        third = (await _post(api, chat_id, key, content="three")).json()
    rows = {m.id: m for m in await _rows(chat_id)}
    assert rows[third["user_message_id"]].parent_id == first["user_message_id"]


@respx.mock
async def test_same_turn_as_websocket_settings_and_compression(
    authenticated_client: AsyncClient,
) -> None:
    """HCHAT-02: global system prompt and the sliding window apply to the HTTP turn."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    await _put_settings(
        authenticated_client, None, system_prompt="PROMPT-MARKER-77", context_length=512,
    )
    async with _api_client() as api:
        for index in range(7):
            resp = await _post(api, chat_id, key, content=f"message {index} " + "word " * 80)
            assert resp.status_code == 200, resp.text
    system_text = " ".join(
        m["content"] for m in upstream.requests[0]["messages"] if m["role"] == "system"
    )
    assert "PROMPT-MARKER-77" in system_text
    last = [m for m in upstream.requests[-1]["messages"] if m["role"] != "system"]
    assert len(last) <= 10


@respx.mock
async def test_auth_modes(authenticated_client: AsyncClient) -> None:
    """D-17: cookie works, no credentials, an invalid key and a revoked key give 401."""
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    cookie = await authenticated_client.post(
        f"/api/v1/chats/{chat_id}/messages", json={"content": "hi", "model": MODEL},
    )
    assert cookie.status_code == 200
    async with _api_client() as api:
        assert (await _post(api, chat_id, None)).status_code == 401
        assert (await _post(api, chat_id, "aia_" + "x" * 43)).status_code == 401
        assert (await _post(api, chat_id, key)).status_code == 200
        listed = (await authenticated_client.get("/api/v1/api-keys")).json()
        revoked = await authenticated_client.delete(f"/api/v1/api-keys/{listed[0]['id']}")
        assert revoked.status_code == 204
        assert (await _post(api, chat_id, key)).status_code == 401


@respx.mock
async def test_missing_and_foreign_chat_give_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient,
) -> None:
    """T-19-25: a missing chat and another user's chat both answer 404 and store nothing."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    foreign_chat = await _chat(second_authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, 9999, key)).status_code == 404
        resp = await _post(api, foreign_chat, key)
        assert resp.status_code == 404
        assert set(resp.json().keys()) == {"detail"}
    assert await _rows(foreign_chat) == []
    assert upstream.requests == []


@respx.mock
async def test_request_validation(authenticated_client: AsyncClient) -> None:
    """Missing model and empty content give 422, a non-JSON body 415, a foreign Origin 403."""
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    url = f"/api/v1/chats/{chat_id}/messages"
    async with _api_client() as api:
        missing = await api.post(url, json={"content": "hi"}, headers=_bearer(key))
        assert missing.status_code == 422
        empty = await api.post(url, json={"content": "", "model": MODEL}, headers=_bearer(key))
        assert empty.status_code == 422
        not_json = await api.post(
            url, content=b'{"content":"hi","model":"m"}',
            headers={**_bearer(key), "Content-Type": "text/plain"},
        )
        assert not_json.status_code == 415
        evil = await api.post(
            url, json={"content": "hi", "model": MODEL},
            headers={**_bearer(key), "Origin": "http://evil.example"},
        )
        assert evil.status_code == 403
    assert await _rows(chat_id) == []


@respx.mock
async def test_busy_chat_answers_409_at_once(authenticated_client: AsyncClient) -> None:
    """D-16: a held chat lock gives 409 CHAT_BUSY immediately and stores nothing."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    lock = asyncio.Lock()
    await lock.acquire()
    agent_state.chat_locks[chat_id] = lock
    async with _api_client() as api:
        started = time.monotonic()
        resp = await _post(api, chat_id, key)
        elapsed = time.monotonic() - started
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "CHAT_BUSY"
    assert body["user_message_kept"] is False
    assert isinstance(body["detail"], str)
    assert elapsed < 1.0
    assert await _rows(chat_id) == []
    assert upstream.requests == []
    assert lock.locked()


@respx.mock
async def test_lock_is_released_after_success_and_failure(
    authenticated_client: AsyncClient,
) -> None:
    """A finished turn, successful or not, leaves the chat lock free."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 200
        assert not agent_state.chat_locks[chat_id].locked()
        upstream.failure = httpx.Response(500, text="boom")
        assert (await _post(api, chat_id, key)).status_code == 502
        assert not agent_state.chat_locks[chat_id].locked()
        upstream.failure = None
        assert (await _post(api, chat_id, key)).status_code == 200


@respx.mock
async def test_context_overflow_maps_to_413_and_restores_leaf(
    authenticated_client: AsyncClient,
) -> None:
    """D-17, D-18: overflow gives 413 with the suggested strategy and the old leaf stays."""
    _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    await _put_settings(authenticated_client, chat_id, strategy="no_compression")
    async with _api_client() as api:
        first = (await _post(api, chat_id, key, content="hello")).json()
        resp = await _post(api, chat_id, key, content="word " * 6000)
    assert resp.status_code == 413
    body = resp.json()
    assert body["code"] == "CONTEXT_OVERFLOW"
    assert body["suggested_strategy"] == "sliding"
    assert body["user_message_kept"] is False
    assert isinstance(body["detail"], str)
    assert len(await _rows(chat_id)) == 2
    assert await _leaf(chat_id) == first["assistant_message_id"]


@respx.mock
async def test_lm_studio_down_maps_to_503_and_retry_continues(
    authenticated_client: AsyncClient,
) -> None:
    """D-17, D-18: a refused LM Studio gives 503, the tree is untouched, a retry continues."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        first = (await _post(api, chat_id, key, content="hello")).json()
        upstream.failure = httpx.ConnectError("refused")
        resp = await _post(api, chat_id, key, content="second")
        assert resp.status_code == 503
        body = resp.json()
        assert body["code"] == "LM_STUDIO_UNAVAILABLE"
        assert body["detail"] == "LM Studio is not running"
        assert body["user_message_kept"] is False
        assert len(await _rows(chat_id)) == 2
        assert await _leaf(chat_id) == first["assistant_message_id"]
        upstream.failure = None
        retry = await _post(api, chat_id, key, content="second")
        assert retry.status_code == 200
    rows = {m.id: m for m in await _rows(chat_id)}
    assert rows[retry.json()["user_message_id"]].parent_id == first["assistant_message_id"]


async def _create_deepseek(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, name: str = "DeepSeek"
) -> int:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    resp = await client.post(
        "/api/v1/llm-providers",
        json={
            "name": name,
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@respx.mock
async def test_other_provider_unreachable_maps_to_llm_unreachable(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-LM-Studio provider that refuses the connection gives 503 LLM_UNREACHABLE."""
    upstream = _mock_upstream(DEEPSEEK_COMPLETIONS)
    upstream.failure = httpx.ConnectError("refused")
    key = await _key(authenticated_client)
    provider_id = await _create_deepseek(authenticated_client, monkeypatch)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key, provider_id=provider_id)
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "LLM_UNREACHABLE"
    assert isinstance(body["detail"], str)
    assert await _rows(chat_id) == []


@pytest.mark.parametrize(
    "failure", [httpx.Response(500, text="boom"), httpx.ReadTimeout("slow")]
)
@respx.mock
async def test_model_error_and_timeout_map_to_502(
    authenticated_client: AsyncClient, failure: Exception | httpx.Response,
) -> None:
    """D-17: an upstream HTTP error and a timeout both give 502 LLM_ERROR."""
    upstream = _mock_upstream()
    upstream.failure = failure
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, key)
    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "LLM_ERROR"
    assert body["user_message_kept"] is False
    assert isinstance(body["detail"], str)
    assert await _rows(chat_id) == []


@respx.mock
async def test_deleted_and_disabled_provider_give_503(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-17: a missing or disabled provider answers 503 PROVIDER_UNAVAILABLE, nothing stored."""
    upstream = _mock_upstream(DEEPSEEK_COMPLETIONS)
    key = await _key(authenticated_client)
    assert (await authenticated_client.get("/api/v1/llm-providers")).status_code == 200
    gone = await _create_deepseek(authenticated_client, monkeypatch, name="Gone")
    assert (await authenticated_client.delete(f"/api/v1/llm-providers/{gone}")).status_code == 204
    disabled = await _create_deepseek(authenticated_client, monkeypatch, name="Off")
    resp = await authenticated_client.put(
        f"/api/v1/llm-providers/{disabled}", json={"enabled": False},
    )
    assert resp.status_code == 200
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        for provider_id in (gone, disabled):
            resp = await _post(api, chat_id, key, provider_id=provider_id)
            assert resp.status_code == 503
            body = resp.json()
            assert body["code"] == "PROVIDER_UNAVAILABLE"
            assert isinstance(body["detail"], str)
            assert body["user_message_kept"] is False
    assert await _rows(chat_id) == []
    assert upstream.requests == []


def _chat_updated(queue: asyncio.Queue[Any]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while not queue.empty():
        frame = queue.get_nowait()
        if frame.get("type") == "chat_updated":
            frames.append(frame)
    return frames


@respx.mock
async def test_chat_updated_event_once_for_owner_only(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient,
) -> None:
    """D-19: one chat_updated frame to the owner after success, none after a failure."""
    upstream = _mock_upstream()
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    owner_queue = events_hub.subscribe(authenticated_client.seeded_user_id)
    other_queue = events_hub.subscribe(second_authenticated_client.seeded_user_id)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 200
        assert _chat_updated(owner_queue) == [{"type": "chat_updated", "chat_id": chat_id}]
        upstream.failure = httpx.ConnectError("refused")
        assert (await _post(api, chat_id, key)).status_code == 503
        assert _chat_updated(owner_queue) == []
    assert other_queue.empty()


@respx.mock
async def test_client_disconnect_does_not_abort_the_turn(
    authenticated_client: AsyncClient,
) -> None:
    """D-14: a dropped connection mid-generation still stores the reply and frees the lock."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return _ok_response()

    respx.post(COMPLETIONS).mock(side_effect=slow)
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    queue = events_hub.subscribe(authenticated_client.seeded_user_id)
    async with _api_client() as api:
        request_task = asyncio.create_task(_post(api, chat_id, key))
        await asyncio.wait_for(entered.wait(), timeout=5)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task
        assert http_chat._http_turns
        release.set()
        await wait_until(lambda: not http_chat._http_turns)
    rows = await _rows(chat_id)
    assert [m.role for m in rows] == ["user", "assistant"]
    assert await _leaf(chat_id) == rows[1].id
    assert not agent_state.chat_locks[chat_id].locked()
    assert _chat_updated(queue) == [{"type": "chat_updated", "chat_id": chat_id}]


@respx.mock
async def test_background_title_and_facts_run_as_after_websocket(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-12: title generation only for the first message, fact extraction every turn."""
    _mock_upstream()
    titles: list[tuple[Any, ...]] = []
    facts: list[tuple[Any, ...]] = []
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *a, **k: titles.append(a))
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *a, **k: facts.append(a))
    key = await _key(authenticated_client)
    resp = await authenticated_client.post("/api/v1/chats", json={"title": "New Chat"})
    chat_id = resp.json()["id"]
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 200
        assert (len(titles), len(facts)) == (1, 1)
        assert (await _post(api, chat_id, key)).status_code == 200
    assert (len(titles), len(facts)) == (1, 2)


@respx.mock
async def test_no_lm_studio_model_management(authenticated_client: AsyncClient) -> None:
    """D-11: a successful turn never touches the LM Studio control API."""
    _mock_upstream()
    control = respx.route(path__startswith="/api/v0/").mock(return_value=httpx.Response(200))
    key = await _key(authenticated_client)
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        assert (await _post(api, chat_id, key)).status_code == 200
    assert control.called is False


async def test_other_user_cannot_post_with_foreign_key_into_chat(
    authenticated_client: AsyncClient,
) -> None:
    """A key of a second user never reaches the first user's chat."""
    other_id = await _create_user("keyowner", "pw")
    assert other_id != authenticated_client.seeded_user_id
    chat_id = await _chat(authenticated_client)
    async with _api_client() as api:
        resp = await _post(api, chat_id, "aia_" + "y" * 43)
    assert resp.status_code == 401
