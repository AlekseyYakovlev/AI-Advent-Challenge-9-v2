"""Access-control tests: 401 without a session, per-user isolation, IDOR-safe 404s."""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from shared.database import async_session_factory
from shared.models import Chat, Settings

UNAUTH_ROUTES: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/v1/chats", None),
    ("POST", "/api/v1/chats", {"title": "Nope"}),
    ("DELETE", "/api/v1/chats/1", None),
    ("GET", "/api/v1/chats/1/tree", None),
    ("GET", "/api/v1/chats/1/stats", None),
    ("POST", "/api/v1/chats/1/branch", {"message_id": 1}),
    ("GET", "/api/v1/settings", None),
    ("PUT", "/api/v1/settings", {"chat_id": None}),
    ("GET", "/api/v1/lm-studio/models", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body",
    UNAUTH_ROUTES,
    ids=[f"{m} {p}" for m, p, _ in UNAUTH_ROUTES],
)
async def test_rest_routes_require_session_cookie(
    client: AsyncClient,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    """Every /api/v1 route (except login) must 401 with no session cookie."""
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_and_debug_routes_stay_open(client: AsyncClient) -> None:
    """The supervisor's health/debug probes must never require a session."""
    health_resp = await client.get("/health")
    assert health_resp.status_code == 200

    debug_resp = await client.get("/debug/routes")
    assert debug_resp.status_code == 200


@pytest.mark.asyncio
async def test_created_chat_is_owned_by_creator(
    authenticated_client: AsyncClient,
) -> None:
    """A chat created through an authenticated client is stored with the creator's user_id."""
    resp = await authenticated_client.post("/api/v1/chats", json={"title": "Mine"})
    assert resp.status_code == 201
    chat_id = resp.json()["id"]

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        assert chat.user_id == authenticated_client.seeded_user_id


@pytest.mark.asyncio
async def test_list_chats_is_per_user(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """User B's chat list must never contain user A's chats."""
    create_resp = await authenticated_client.post(
        "/api/v1/chats", json={"title": "A's chat"},
    )
    a_chat_id = create_resp.json()["id"]

    list_resp = await second_authenticated_client.get("/api/v1/chats")
    assert list_resp.status_code == 200
    b_chat_ids = [chat["id"] for chat in list_resp.json()]
    assert b_chat_ids == []
    assert a_chat_id not in b_chat_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("GET", "/tree", None),
        ("GET", "/stats", None),
        ("POST", "/branch", {"message_id": 1}),
        ("DELETE", "", None),
    ],
    ids=["tree", "stats", "branch", "delete"],
)
async def test_idor_on_foreign_chat_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
    method: str,
    suffix: str,
    body: dict | None,
) -> None:
    """User B touching user A's chat id always gets 404, never 403, and A's chat survives."""
    create_resp = await authenticated_client.post(
        "/api/v1/chats", json={"title": "A's chat"},
    )
    a_chat_id = create_resp.json()["id"]

    resp = await second_authenticated_client.request(
        method, f"/api/v1/chats/{a_chat_id}{suffix}", json=body,
    )
    assert resp.status_code == 404

    async with async_session_factory() as session:
        chat = await session.get(Chat, a_chat_id)
        assert chat is not None


@pytest.mark.asyncio
async def test_idor_on_foreign_chat_settings_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """User B cannot read or write settings scoped to user A's chat id."""
    create_resp = await authenticated_client.post(
        "/api/v1/chats", json={"title": "A's chat"},
    )
    a_chat_id = create_resp.json()["id"]

    get_resp = await second_authenticated_client.get(
        f"/api/v1/settings?chat_id={a_chat_id}",
    )
    assert get_resp.status_code == 404

    put_resp = await second_authenticated_client.put(
        "/api/v1/settings",
        json={"chat_id": a_chat_id, "temperature": 0.99},
    )
    assert put_resp.status_code == 404

    async with async_session_factory() as session:
        result = await session.exec(
            select(Settings).where(Settings.chat_id == a_chat_id),
        )
        row = result.first()
        assert row is None or row.temperature != 0.99


@pytest.mark.asyncio
async def test_global_settings_are_per_user(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """User A's global settings must never leak into user B's response."""
    put_resp = await authenticated_client.put(
        "/api/v1/settings",
        json={"chat_id": None, "temperature": 0.11},
    )
    assert put_resp.status_code == 200

    get_resp = await second_authenticated_client.get("/api/v1/settings")
    assert get_resp.status_code == 200
    assert get_resp.json()["temperature"] == 0.7


@pytest.mark.asyncio
async def test_global_fallback_still_works_within_one_user(
    authenticated_client: AsyncClient,
) -> None:
    """The per-chat -> global settings fallback still holds for a single user."""
    global_resp = await authenticated_client.put(
        "/api/v1/settings",
        json={"chat_id": None, "temperature": 0.33},
    )
    assert global_resp.status_code == 200

    chat_resp = await authenticated_client.post(
        "/api/v1/chats", json={"title": "Fallback chat"},
    )
    chat_id = chat_resp.json()["id"]

    settings_resp = await authenticated_client.get(
        f"/api/v1/settings?chat_id={chat_id}",
    )
    assert settings_resp.status_code == 200
    assert settings_resp.json()["temperature"] == 0.33
