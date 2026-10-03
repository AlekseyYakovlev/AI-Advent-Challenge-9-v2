"""Tests for the memory REST routes: GET chat memory, PUT/DELETE long-term entries."""

import pytest
from httpx import AsyncClient

from agent import memory
from agent.state import CORS_ORIGINS
from shared.database import async_session_factory


@pytest.mark.asyncio
async def test_get_chat_memory_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated GET returns 401."""
    resp = await client.get("/api/v1/chats/1/memory")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_chat_memory_returns_expected_shape(authenticated_client: AsyncClient) -> None:
    """Authenticated GET on an owned chat returns 200 with all expected keys."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Memory chat"})
    chat_id = chat_resp.json()["id"]

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"chat_id", "short_term_message_count", "working", "long_term"}
    assert body["chat_id"] == chat_id
    assert body["working"] == []
    assert body["long_term"] == []


@pytest.mark.asyncio
async def test_working_memory_not_leaked_across_chats(authenticated_client: AsyncClient) -> None:
    """Working memory saved for chat A does NOT appear in chat B's response."""
    user_id = authenticated_client.seeded_user_id
    chat_a_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Chat A"})
    chat_b_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Chat B"})
    chat_a = chat_a_resp.json()["id"]
    chat_b = chat_b_resp.json()["id"]

    async with async_session_factory() as session:
        await memory.save_working_memory(session, user_id, chat_a, "task", "chat A task")

    resp_b = await authenticated_client.get(f"/api/v1/chats/{chat_b}/memory")
    assert resp_b.status_code == 200
    assert resp_b.json()["working"] == []

    resp_a = await authenticated_client.get(f"/api/v1/chats/{chat_a}/memory")
    assert resp_a.status_code == 200
    assert len(resp_a.json()["working"]) == 1


@pytest.mark.asyncio
async def test_long_term_memory_visible_from_any_chat(authenticated_client: AsyncClient) -> None:
    """Long-term memory saved while in chat A DOES appear in chat B's response (D-02)."""
    user_id = authenticated_client.seeded_user_id
    chat_a_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Chat A"})
    chat_b_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Chat B"})
    chat_a = chat_a_resp.json()["id"]
    chat_b = chat_b_resp.json()["id"]

    async with async_session_factory() as session:
        await memory.save_long_term_memory(session, user_id, "profile_name", "Alex")

    resp_a = await authenticated_client.get(f"/api/v1/chats/{chat_a}/memory")
    resp_b = await authenticated_client.get(f"/api/v1/chats/{chat_b}/memory")
    assert len(resp_a.json()["long_term"]) == 1
    assert len(resp_b.json()["long_term"]) == 1
    assert resp_a.json()["long_term"][0]["key"] == "profile_name"


@pytest.mark.asyncio
async def test_cross_user_memory_access_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """second_authenticated_client GET on the first user's chat_id returns 404, leaking no rows."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Owned by user A"})
    chat_id = chat_resp.json()["id"]

    async with async_session_factory() as session:
        await memory.save_working_memory(
            session, authenticated_client.seeded_user_id, chat_id, "secret", "leak?",
        )

    resp = await second_authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")
    assert resp.status_code == 404
    assert "leak" not in resp.text


@pytest.mark.asyncio
async def test_short_term_message_count_matches_active_branch(
    authenticated_client: AsyncClient,
) -> None:
    """short_term_message_count equals the number of messages on the active branch."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Counted chat"})
    chat_id = chat_resp.json()["id"]

    from shared.database import async_session_factory as _factory
    from shared.models import Chat, Message

    async with _factory() as session:
        chat = await session.get(Chat, chat_id)
        msg1 = Message(chat_id=chat_id, role="user", content="Hi")
        session.add(msg1)
        await session.commit()
        await session.refresh(msg1)

        msg2 = Message(chat_id=chat_id, role="assistant", content="Hello", parent_id=msg1.id)
        session.add(msg2)
        await session.commit()
        await session.refresh(msg2)

        chat.current_leaf_message_id = msg2.id
        session.add(chat)
        await session.commit()

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")
    assert resp.status_code == 200
    assert resp.json()["short_term_message_count"] == 2


URL = "/api/v1/memory/long-term"
NOT_FOUND = "Запись памяти не найдена"
CONFLICT = "Запись с таким ключом уже существует"


async def _seed(user_id: int, key: str, value: str) -> int:
    async with async_session_factory() as session:
        row = await memory.save_long_term_memory(session, user_id, key, value)
        return row.id


async def _snapshot(user_id: int) -> dict[str, str]:
    async with async_session_factory() as session:
        rows = await memory.list_long_term_memory(session, user_id)
        return {row.key: row.value for row in rows}


async def _first_chat_id(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/chats", json={"title": "Memory chat"})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_update_long_term_memory_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated PUT returns 401."""
    resp = await client.put(f"{URL}/1", json={"value": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_long_term_memory_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated DELETE returns 401."""
    resp = await client.delete(f"{URL}/1")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_update_long_term_memory_value_only(authenticated_client: AsyncClient) -> None:
    """PUT with only a value changes the value and keeps the key."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _first_chat_id(authenticated_client)
    entry_id = await _seed(user_id, "user_name", "Alex")

    resp = await authenticated_client.put(f"{URL}/{entry_id}", json={"value": "Aleksey"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"id", "key", "value", "updated_at"}
    assert body["id"] == entry_id and body["key"] == "user_name" and body["value"] == "Aleksey"

    listed = await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")
    assert listed.json()["long_term"][0]["value"] == "Aleksey"


@pytest.mark.asyncio
async def test_update_long_term_memory_key_and_value(authenticated_client: AsyncClient) -> None:
    """The key is stripped; the value is stored verbatim."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed(user_id, "town", "x")
    value = "  line one\nline two  "

    resp = await authenticated_client.put(
        f"{URL}/{entry_id}", json={"key": "  city  ", "value": value},
    )
    assert resp.status_code == 200
    assert await _snapshot(user_id) == {"city": value}


INVALID_BODIES = [
    pytest.param({}, id="empty-body"),
    pytest.param({"key": None, "value": None}, id="both-null"),
    pytest.param({"key": "   "}, id="blank-key"),
    pytest.param({"key": ""}, id="empty-key"),
    pytest.param({"value": " \n\t "}, id="whitespace-value"),
    pytest.param({"value": ""}, id="empty-value"),
    pytest.param({"key": "k" * 201}, id="key-too-long"),
    pytest.param({"value": "v" * 50001}, id="value-too-long"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", INVALID_BODIES)
async def test_update_long_term_memory_rejects_invalid_body(
    authenticated_client: AsyncClient,
    body: dict,
) -> None:
    """Invalid bodies return 422 and change nothing."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed(user_id, "city", "Berlin")

    resp = await authenticated_client.put(f"{URL}/{entry_id}", json=body)
    assert resp.status_code == 422
    assert await _snapshot(user_id) == {"city": "Berlin"}


@pytest.mark.asyncio
async def test_update_long_term_memory_accepts_max_lengths(
    authenticated_client: AsyncClient,
) -> None:
    """Exactly-at-limit key and value are accepted."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed(user_id, "city", "Berlin")

    resp = await authenticated_client.put(
        f"{URL}/{entry_id}", json={"key": "k" * 200, "value": "v" * 50000},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_update_long_term_memory_duplicate_key_returns_409(
    authenticated_client: AsyncClient,
) -> None:
    """Renaming onto an existing key returns 409 and changes nothing."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed(user_id, "city", "Berlin")
    await _seed(user_id, "user_name", "Alex")

    resp = await authenticated_client.put(f"{URL}/{entry_id}", json={"key": "user_name"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == CONFLICT
    assert await _snapshot(user_id) == {"city": "Berlin", "user_name": "Alex"}


@pytest.mark.asyncio
async def test_update_long_term_memory_same_key_returns_200(
    authenticated_client: AsyncClient,
) -> None:
    """Sending the entry's own key with a new value is not a conflict."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed(user_id, "city", "Berlin")

    resp = await authenticated_client.put(
        f"{URL}/{entry_id}", json={"key": "city", "value": "Paris"},
    )
    assert resp.status_code == 200
    assert await _snapshot(user_id) == {"city": "Paris"}


@pytest.mark.asyncio
async def test_update_long_term_memory_cross_user_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Another user's PUT returns 404 and leaks nothing."""
    user_a = authenticated_client.seeded_user_id
    entry_id = await _seed(user_a, "secret", "top-secret-value")

    resp = await second_authenticated_client.put(f"{URL}/{entry_id}", json={"value": "pwned"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == NOT_FOUND
    assert "top-secret-value" not in resp.text
    assert await _snapshot(user_a) == {"secret": "top-secret-value"}


@pytest.mark.asyncio
async def test_update_long_term_memory_unknown_id_returns_404(
    authenticated_client: AsyncClient,
) -> None:
    """An unknown id returns the same 404 as a foreign id."""
    resp = await authenticated_client.put(f"{URL}/999999", json={"value": "x"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == NOT_FOUND


@pytest.mark.asyncio
async def test_delete_long_term_memory_returns_204_and_removes_entry(
    authenticated_client: AsyncClient,
) -> None:
    """DELETE returns 204 with no body, removes the entry, and a repeat gives 404."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _first_chat_id(authenticated_client)
    entry_id = await _seed(user_id, "city", "Berlin")

    resp = await authenticated_client.delete(f"{URL}/{entry_id}")
    assert resp.status_code == 204
    assert resp.content == b""

    listed = await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")
    assert listed.json()["long_term"] == []

    again = await authenticated_client.delete(f"{URL}/{entry_id}")
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_delete_long_term_memory_cross_user_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Another user's DELETE returns 404 and the row survives."""
    user_a = authenticated_client.seeded_user_id
    entry_id = await _seed(user_a, "city", "Berlin")

    resp = await second_authenticated_client.delete(f"{URL}/{entry_id}")
    assert resp.status_code == 404
    assert await _snapshot(user_a) == {"city": "Berlin"}


@pytest.mark.asyncio
async def test_long_term_routes_never_touch_working_memory(
    authenticated_client: AsyncClient,
) -> None:
    """A working-memory row id is not reachable through the long-term routes."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _first_chat_id(authenticated_client)
    async with async_session_factory() as session:
        row = await memory.save_working_memory(session, user_id, chat_id, "task", "draft")
        working_id = row.id

    put_resp = await authenticated_client.put(f"{URL}/{working_id}", json={"value": "x"})
    del_resp = await authenticated_client.delete(f"{URL}/{working_id}")
    assert put_resp.status_code == 404
    assert del_resp.status_code == 404

    async with async_session_factory() as session:
        rows = await memory.list_working_memory(session, chat_id)
        assert [(r.key, r.value) for r in rows] == [("task", "draft")]


@pytest.mark.asyncio
async def test_long_term_mutations_reject_foreign_origin(
    authenticated_client: AsyncClient,
) -> None:
    """A foreign Origin gets 403 on PUT and DELETE; an allowed one passes."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed(user_id, "city", "Berlin")
    evil = {"Origin": "http://evil.example"}

    put_resp = await authenticated_client.put(
        f"{URL}/{entry_id}", json={"value": "x"}, headers=evil,
    )
    del_resp = await authenticated_client.delete(f"{URL}/{entry_id}", headers=evil)
    assert put_resp.status_code == 403
    assert del_resp.status_code == 403
    assert await _snapshot(user_id) == {"city": "Berlin"}

    ok = await authenticated_client.put(
        f"{URL}/{entry_id}", json={"value": "y"}, headers={"Origin": CORS_ORIGINS[0]},
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_update_long_term_memory_requires_json_content_type(
    authenticated_client: AsyncClient,
) -> None:
    """A PUT sent as text/plain is refused with 415."""
    user_id = authenticated_client.seeded_user_id
    entry_id = await _seed(user_id, "city", "Berlin")

    resp = await authenticated_client.put(
        f"{URL}/{entry_id}",
        content=b'{"value": "x"}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    assert await _snapshot(user_id) == {"city": "Berlin"}
