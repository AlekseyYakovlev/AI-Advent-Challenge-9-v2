"""Add-user account-creation tests: authenticated-only creation, duplicates, and isolation."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from agent.main import app
from shared.auth import verify_password
from shared.database import async_session_factory
from shared.models import User

DUPLICATE_USERNAME_DETAIL = "Пользователь с таким именем уже существует"


@pytest.mark.asyncio
async def test_unauthenticated_create_user_returns_401_and_creates_no_row(
    client: AsyncClient,
) -> None:
    """POST /api/v1/auth/users with no session cookie is rejected and writes nothing (D-06)."""
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "ghostwriter", "password": "somepassword"},
    )
    assert resp.status_code == 401

    async with async_session_factory() as session:
        result = await session.exec(select(User).where(User.username == "ghostwriter"))
        assert result.first() is None


@pytest.mark.asyncio
async def test_authenticated_create_user_returns_201_without_password_hash(
    authenticated_client: AsyncClient,
) -> None:
    """A logged-in user can create a new account; the response never leaks password_hash."""
    resp = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "newperson", "password": "newpersonpass"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert body["username"] == "newperson"
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_created_user_password_is_hashed_and_verifiable(
    authenticated_client: AsyncClient,
) -> None:
    """The stored hash is not the plaintext password and verifies with shared.auth.verify_password."""
    resp = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "hashcheck", "password": "hashcheckpass"},
    )
    assert resp.status_code == 201

    async with async_session_factory() as session:
        result = await session.exec(select(User).where(User.username == "hashcheck"))
        row = result.first()
        assert row is not None
        assert row.password_hash != "hashcheckpass"
        assert verify_password("hashcheckpass", row.password_hash)


@pytest.mark.asyncio
async def test_duplicate_username_returns_409_with_exact_message_and_no_second_row(
    authenticated_client: AsyncClient,
) -> None:
    """Creating a user with an existing username is rejected with the exact Russian message."""
    first = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "dupeuser", "password": "firstpassword"},
    )
    assert first.status_code == 201

    second = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "dupeuser", "password": "secondpassword"},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == DUPLICATE_USERNAME_DETAIL

    async with async_session_factory() as session:
        result = await session.exec(select(User).where(User.username == "dupeuser"))
        assert len(result.all()) == 1


@pytest.mark.asyncio
async def test_new_account_can_log_in(authenticated_client: AsyncClient) -> None:
    """A freshly created account authenticates through the real login route."""
    create_resp = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "loginable", "password": "loginablepass"},
    )
    assert create_resp.status_code == 201

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as new_client:
        login_resp = await new_client.post(
            "/api/v1/auth/login",
            json={"username": "loginable", "password": "loginablepass"},
        )
        assert login_resp.status_code == 200
        assert "session_id" in login_resp.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_new_account_is_isolated_from_creators_chats(
    authenticated_client: AsyncClient,
) -> None:
    """AUTH-04 end-to-end: the new account sees neither the creator's chat list nor its tree."""
    chat_resp = await authenticated_client.post(
        "/api/v1/chats", json={"title": "Creator's chat"},
    )
    assert chat_resp.status_code == 201
    creator_chat_id = chat_resp.json()["id"]

    create_resp = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "isolateduser", "password": "isolatedpass"},
    )
    assert create_resp.status_code == 201

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as new_client:
        login_resp = await new_client.post(
            "/api/v1/auth/login",
            json={"username": "isolateduser", "password": "isolatedpass"},
        )
        assert login_resp.status_code == 200

        chats_resp = await new_client.get("/api/v1/chats")
        assert chats_resp.status_code == 200
        assert chats_resp.json() == []

        tree_resp = await new_client.get(f"/api/v1/chats/{creator_chat_id}/tree")
        assert tree_resp.status_code == 404


@pytest.mark.asyncio
async def test_new_account_has_flat_privilege_and_can_create_further_accounts(
    authenticated_client: AsyncClient,
) -> None:
    """AUTH-02: a freshly created account is equal-privilege and can itself add users."""
    create_resp = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "flatprivuser", "password": "flatprivpass"},
    )
    assert create_resp.status_code == 201

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as new_client:
        login_resp = await new_client.post(
            "/api/v1/auth/login",
            json={"username": "flatprivuser", "password": "flatprivpass"},
        )
        assert login_resp.status_code == 200

        grandchild_resp = await new_client.post(
            "/api/v1/auth/users",
            json={"username": "grandchilduser", "password": "grandchildpass"},
        )
        assert grandchild_resp.status_code == 201


@pytest.mark.asyncio
async def test_empty_username_or_password_returns_422(
    authenticated_client: AsyncClient,
) -> None:
    """Empty username/password fails Pydantic min_length=1 validation."""
    empty_username = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "", "password": "somepassword"},
    )
    assert empty_username.status_code == 422

    empty_password = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "someusername", "password": ""},
    )
    assert empty_password.status_code == 422


@pytest.mark.asyncio
async def test_one_character_password_is_accepted(
    authenticated_client: AsyncClient,
) -> None:
    """D-12: no password policy beyond non-empty — a one-character password is accepted."""
    resp = await authenticated_client.post(
        "/api/v1/auth/users",
        json={"username": "shortpassuser", "password": "x"},
    )
    assert resp.status_code == 201
