"""Tests for GET/POST/PUT/DELETE /api/v1/chats/{chat_id}/invariants (per-chat, D-05)."""

import pytest
from httpx import AsyncClient


async def _create_chat(client: AsyncClient, title: str = "Chat") -> int:
    """Create a chat owned by the given authenticated client and return its id."""
    resp = await client.post("/api/v1/chats", json={"title": title})
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_chat_invariant_returns_201_with_overrides_title(
    authenticated_client: AsyncClient,
) -> None:
    """POST returns 201 and a body carrying overrides_id plus the resolved overrides_title."""
    chat_id = await _create_chat(authenticated_client)
    global_resp = await authenticated_client.post(
        "/api/v1/invariants",
        json={"title": "Без Docker", "rule_text": "Никогда не предлагай Docker"},
    )
    global_id = global_resp.json()["id"]

    resp = await authenticated_client.post(
        f"/api/v1/chats/{chat_id}/invariants",
        json={
            "title": "Тут можно Docker",
            "rule_text": "В этом чате Docker разрешён",
            "overrides_id": global_id,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {
        "id",
        "chat_id",
        "title",
        "rule_text",
        "overrides_id",
        "overrides_title",
        "created_at",
        "updated_at",
    }
    assert body["chat_id"] == chat_id
    assert body["title"] == "Тут можно Docker"
    assert body["rule_text"] == "В этом чате Docker разрешён"
    assert body["overrides_id"] == global_id
    assert body["overrides_title"] == "Без Docker"


@pytest.mark.asyncio
async def test_create_chat_invariant_unknown_overrides_id_returns_404(
    authenticated_client: AsyncClient,
) -> None:
    """POST with a nonexistent overrides_id returns 404."""
    chat_id = await _create_chat(authenticated_client)
    resp = await authenticated_client.post(
        f"/api/v1/chats/{chat_id}/invariants",
        json={"title": "X", "rule_text": "Y", "overrides_id": 999999},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_chat_invariants_other_users_chat_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A non-owner GET returns 404 (never 403) and leaks no invariant title (IDOR)."""
    chat_id = await _create_chat(authenticated_client)
    await authenticated_client.post(
        f"/api/v1/chats/{chat_id}/invariants",
        json={"title": "Secret rule", "rule_text": "secret text"},
    )

    resp = await second_authenticated_client.get(f"/api/v1/chats/{chat_id}/invariants")
    assert resp.status_code == 404
    assert "Secret rule" not in resp.text


@pytest.mark.asyncio
async def test_update_chat_invariant_from_a_different_chat_returns_404(
    authenticated_client: AsyncClient,
) -> None:
    """PUT on an invariant using a chat_id it doesn't belong to returns 404."""
    chat1_id = await _create_chat(authenticated_client, "Chat 1")
    chat2_id = await _create_chat(authenticated_client, "Chat 2")
    create_resp = await authenticated_client.post(
        f"/api/v1/chats/{chat1_id}/invariants",
        json={"title": "Rule", "rule_text": "text"},
    )
    invariant_id = create_resp.json()["id"]

    resp = await authenticated_client.put(
        f"/api/v1/chats/{chat2_id}/invariants/{invariant_id}",
        json={"title": "Changed"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_chat_invariant_returns_204_and_removes_it(
    authenticated_client: AsyncClient,
) -> None:
    """DELETE returns 204, and a follow-up GET list no longer contains the row."""
    chat_id = await _create_chat(authenticated_client)
    create_resp = await authenticated_client.post(
        f"/api/v1/chats/{chat_id}/invariants",
        json={"title": "Doomed", "rule_text": "will be deleted"},
    )
    invariant_id = create_resp.json()["id"]

    delete_resp = await authenticated_client.delete(
        f"/api/v1/chats/{chat_id}/invariants/{invariant_id}",
    )
    assert delete_resp.status_code == 204

    list_resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/invariants")
    ids = [item["id"] for item in list_resp.json()]
    assert invariant_id not in ids


@pytest.mark.asyncio
async def test_chat_invariant_routes_require_auth(client: AsyncClient) -> None:
    """Unauthenticated GET returns 401."""
    resp = await client.get("/api/v1/chats/1/invariants")
    assert resp.status_code == 401
