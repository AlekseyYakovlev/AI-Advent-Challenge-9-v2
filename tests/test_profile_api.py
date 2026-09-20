"""Tests for GET/PUT /api/v1/profile and its injection into build_system_prompt."""

import pytest
from httpx import AsyncClient

from agent.context_engine import build_system_prompt
from shared.database import async_session_factory


@pytest.mark.asyncio
async def test_get_profile_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated GET returns 401."""
    resp = await client.get("/api/v1/profile")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_profile_lazy_creates_empty_row(authenticated_client: AsyncClient) -> None:
    """Authenticated GET returns 200 with an all-blank profile on first access."""
    resp = await authenticated_client.get("/api/v1/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"id", "style", "format", "constraints", "updated_at"}
    assert body["style"] == ""
    assert body["format"] == ""
    assert body["constraints"] == ""


@pytest.mark.asyncio
async def test_put_profile_round_trips(authenticated_client: AsyncClient) -> None:
    """PUT with all three fields returns them, and a following GET returns the same values."""
    put_resp = await authenticated_client.put(
        "/api/v1/profile",
        json={
            "style": "отвечай одним предложением",
            "format": "только маркированный список",
            "constraints": "никакого кода",
        },
    )
    assert put_resp.status_code == 200
    put_body = put_resp.json()
    assert put_body["style"] == "отвечай одним предложением"
    assert put_body["format"] == "только маркированный список"
    assert put_body["constraints"] == "никакого кода"

    get_resp = await authenticated_client.get("/api/v1/profile")
    assert get_resp.status_code == 200
    get_body = get_resp.json()
    assert get_body["style"] == "отвечай одним предложением"
    assert get_body["format"] == "только маркированный список"
    assert get_body["constraints"] == "никакого кода"


@pytest.mark.asyncio
async def test_put_profile_partial_update_keeps_other_fields(
    authenticated_client: AsyncClient,
) -> None:
    """A partial PUT changes only the supplied field, leaving the others as previously saved."""
    await authenticated_client.put(
        "/api/v1/profile",
        json={
            "style": "original style",
            "format": "original format",
            "constraints": "original constraints",
        },
    )

    resp = await authenticated_client.put("/api/v1/profile", json={"style": "new style"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["style"] == "new style"
    assert body["format"] == "original format"
    assert body["constraints"] == "original constraints"


@pytest.mark.asyncio
async def test_put_profile_rejects_oversized_field(authenticated_client: AsyncClient) -> None:
    """A style field longer than PROFILE_FIELD_MAX_LENGTH (2000) returns 422."""
    resp = await authenticated_client.put(
        "/api/v1/profile",
        json={"style": "a" * 2001},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_profile_is_per_user(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """After one user saves a profile, another user's GET still returns all-blank fields."""
    await authenticated_client.put(
        "/api/v1/profile",
        json={
            "style": "отвечай одним предложением",
            "format": "только маркированный список",
            "constraints": "никакого кода",
        },
    )

    resp = await second_authenticated_client.get("/api/v1/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["style"] == ""
    assert body["format"] == ""
    assert body["constraints"] == ""


@pytest.mark.asyncio
async def test_put_profile_ignores_client_supplied_user_id(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A PUT body carrying a foreign user_id still writes to the caller's own row only."""
    other_user_id = second_authenticated_client.seeded_user_id

    resp = await authenticated_client.put(
        "/api/v1/profile",
        json={"style": "caller's style", "user_id": other_user_id},
    )
    assert resp.status_code == 200
    assert resp.json()["style"] == "caller's style"

    other_resp = await second_authenticated_client.get("/api/v1/profile")
    assert other_resp.status_code == 200
    assert other_resp.json()["style"] == ""


@pytest.mark.asyncio
async def test_saved_profile_reaches_the_system_prompt(
    authenticated_client: AsyncClient,
) -> None:
    """A saved profile's preference text appears inside the assembled system prompt (PERS-02)."""
    await authenticated_client.put(
        "/api/v1/profile",
        json={
            "style": "отвечай одним предложением",
            "format": "только маркированный список",
            "constraints": "никакого кода",
        },
    )

    chat_resp = await authenticated_client.post(
        "/api/v1/chats", json={"title": "Profile chat"},
    )
    chat_id = chat_resp.json()["id"]

    async with async_session_factory() as session:
        prompt = await build_system_prompt(session, chat_id)

    assert "User's stated preferences" in prompt
    assert "отвечай одним предложением" in prompt
