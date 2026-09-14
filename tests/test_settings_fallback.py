"""Settings global-to-per-chat fallback and update behaviour tests."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_settings_falls_back_to_global(client: AsyncClient) -> None:
    """Per-chat GET should inherit global settings when no override exists."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Fallback chat"})
    assert chat_resp.status_code == 201
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/v1/settings",
        json={
            "chat_id": None,
            "temperature": 0.5,
            "system_prompt": "Global prompt",
        },
    )

    resp = await client.get(f"/api/v1/settings?chat_id={chat_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["temperature"] == 0.5
    assert data["system_prompt"] == "Global prompt"
    assert data["chat_id"] is None


@pytest.mark.asyncio
async def test_per_chat_settings_override_global(client: AsyncClient) -> None:
    """Per-chat settings should override global values for that chat only."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Override chat"})
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/v1/settings",
        json={"chat_id": None, "temperature": 0.3},
    )
    await client.put(
        "/api/v1/settings",
        json={"chat_id": chat_id, "temperature": 0.9},
    )

    per_chat = await client.get(f"/api/v1/settings?chat_id={chat_id}")
    global_settings = await client.get("/api/v1/settings")

    assert per_chat.json()["temperature"] == 0.9
    assert per_chat.json()["chat_id"] == chat_id
    assert global_settings.json()["temperature"] == 0.3
    assert global_settings.json()["chat_id"] is None


@pytest.mark.asyncio
async def test_update_global_settings(client: AsyncClient) -> None:
    """Global PUT should update the row where chat_id IS NULL."""
    resp = await client.put(
        "/api/v1/settings",
        json={
            "chat_id": None,
            "context_length": 8192,
            "max_tokens": 8192,
            "strategy": "sticky",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["chat_id"] is None
    assert data["context_length"] == 8192
    assert data["max_tokens"] == 8192
    assert data["strategy"] == "sticky"


@pytest.mark.asyncio
async def test_context_length_persists(client: AsyncClient) -> None:
    """context_length should save and reload correctly."""
    await client.put(
        "/api/v1/settings",
        json={"chat_id": None, "context_length": 2048},
    )
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["context_length"] == 2048


@pytest.mark.asyncio
async def test_no_compression_strategy(client: AsyncClient) -> None:
    """no_compression strategy should be accepted and returned."""
    resp = await client.put(
        "/api/v1/settings",
        json={"chat_id": None, "strategy": "no_compression"},
    )
    assert resp.status_code == 200
    assert resp.json()["strategy"] == "no_compression"


@pytest.mark.asyncio
async def test_update_per_chat_creates_row(client: AsyncClient) -> None:
    """Per-chat PUT should create a dedicated settings row."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Settings row"})
    chat_id = chat_resp.json()["id"]

    resp = await client.put(
        "/api/v1/settings",
        json={"chat_id": chat_id, "facts_json": '{"key": "value"}'},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["chat_id"] == chat_id
    assert data["facts_json"] == '{"key": "value"}'
