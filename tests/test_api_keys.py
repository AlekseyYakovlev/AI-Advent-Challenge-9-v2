"""API key management tests: create, list, revoke, cap, hash-only storage and cookie-only guards."""

import hashlib
from typing import Any

from httpx import AsyncClient
from sqlmodel import select

from agent.dependencies import MSG_SESSION_ONLY
from shared.database import async_session_factory
from shared.models import ApiKey, User

BASE: str = "/api/v1/api-keys"
LIST_KEYS: set[str] = {"id", "name", "key_prefix", "created_at", "last_used_at"}


async def _create_key(client: AsyncClient, name: str = "t") -> dict[str, Any]:
    """Create a key through the cookie client and return the create response JSON."""
    resp = await client.post(BASE, json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bearer(key: str) -> dict[str, str]:
    """Build the Authorization header for a key."""
    return {"Authorization": f"Bearer {key}"}


async def _count_keys() -> int:
    """Return the number of ApiKey rows in the test database."""
    async with async_session_factory() as session:
        return len((await session.exec(select(ApiKey))).all())


async def test_create_and_list_returns_metadata_only(authenticated_client: AsyncClient) -> None:
    """Create returns the full key once; the list never contains it."""
    resp = await authenticated_client.post(BASE, json={"name": "laptop"})
    assert resp.status_code == 201
    created = resp.json()
    assert created["key"].startswith("aia_")
    assert len(created["key"]) == 47
    assert created["key_prefix"] == created["key"][:8]
    assert created["last_used_at"] is None
    assert resp.headers["cache-control"] == "no-store"

    listed = (await authenticated_client.get(BASE)).json()
    assert len(listed) == 1
    assert set(listed[0].keys()) == LIST_KEYS
    assert created["key"] not in str(listed)


async def test_only_hash_is_stored(authenticated_client: AsyncClient) -> None:
    """The row holds the sha256 hash and no column equals the full key."""
    key = (await _create_key(authenticated_client))["key"]
    async with async_session_factory() as session:
        row = (await session.exec(select(ApiKey))).one()
    assert row.key_hash == hashlib.sha256(key.encode()).hexdigest()
    for value in (row.name, row.key_hash, row.key_prefix):
        assert value != key


async def test_name_validation(authenticated_client: AsyncClient) -> None:
    """Blank and over-long names are 422; names are stripped; duplicates are allowed."""
    for bad in ("", "   ", "x" * 101):
        resp = await authenticated_client.post(BASE, json={"name": bad})
        assert resp.status_code == 422
    assert (await _create_key(authenticated_client, "  laptop  "))["name"] == "laptop"
    await _create_key(authenticated_client, "laptop")
    assert await _count_keys() == 2


async def test_cap_of_twenty_keys(authenticated_client: AsyncClient) -> None:
    """The 21st key is refused with 409; revoking one frees a slot."""
    ids = [(await _create_key(authenticated_client, f"k{i}"))["id"] for i in range(20)]
    resp = await authenticated_client.post(BASE, json={"name": "extra"})
    assert resp.status_code == 409
    assert "20" in resp.json()["detail"]

    assert (await authenticated_client.delete(f"{BASE}/{ids[0]}")).status_code == 204
    assert (await authenticated_client.post(BASE, json={"name": "again"})).status_code == 201


async def test_revoke_deletes_row_and_key_stops_working(
    authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """Revoke removes the row at once and the very next key request gets 401."""
    created = await _create_key(authenticated_client)
    ok = await client.get("/api/v1/chats", headers=_bearer(created["key"]))
    assert ok.status_code == 200

    resp = await authenticated_client.delete(f"{BASE}/{created['id']}")
    assert resp.status_code == 204
    assert await _count_keys() == 0
    assert (await authenticated_client.get(BASE)).json() == []
    revoked = await client.get("/api/v1/chats", headers=_bearer(created["key"]))
    assert revoked.status_code == 401


async def test_ownership_of_keys(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """A foreign or missing key id is 404 and lists are per user."""
    mine = await _create_key(authenticated_client, "mine")
    theirs = await _create_key(second_authenticated_client, "theirs")

    resp = await second_authenticated_client.delete(f"{BASE}/{mine['id']}")
    assert resp.status_code == 404
    assert (await client.get("/api/v1/chats", headers=_bearer(mine["key"]))).status_code == 200
    assert (await authenticated_client.delete(f"{BASE}/999999")).status_code == 404

    assert [k["id"] for k in (await authenticated_client.get(BASE)).json()] == [mine["id"]]
    assert [k["id"] for k in (await second_authenticated_client.get(BASE)).json()] == [theirs["id"]]


async def test_key_management_is_cookie_only(
    authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """A valid key gets 403 on all three key routes, with or without the cookie."""
    created = await _create_key(authenticated_client)
    headers = _bearer(created["key"])
    target = f"{BASE}/{created['id']}"

    for caller in (client, authenticated_client):
        post = await caller.post(BASE, json={"name": "x"}, headers=headers)
        get = await caller.get(BASE, headers=headers)
        delete = await caller.delete(target, headers=headers)
        for resp in (post, get, delete):
            assert resp.status_code == 403
            assert resp.json()["detail"] == MSG_SESSION_ONLY
    assert await _count_keys() == 1


async def test_add_user_and_logout_are_cookie_only(
    authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """A valid key cannot create accounts or log out; the cookie session survives."""
    key = (await _create_key(authenticated_client))["key"]
    headers = _bearer(key)

    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "intruder", "password": "intruderpass"},
        headers=headers,
    )
    assert resp.status_code == 403
    async with async_session_factory() as session:
        found = (await session.exec(select(User).where(User.username == "intruder"))).first()
    assert found is None

    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 403
    assert (await authenticated_client.get("/api/v1/auth/me")).status_code == 200


async def test_invalid_key_is_401_not_403(client: AsyncClient) -> None:
    """An invalid key and no credentials both answer 401 on the key routes."""
    resp = await client.get(BASE, headers=_bearer("aia_" + "x" * 43))
    assert resp.status_code == 401
    assert (await client.get(BASE)).status_code == 401


async def test_delete_without_body_or_content_type_and_csrf_guards(
    authenticated_client: AsyncClient,
) -> None:
    """DELETE needs only the Origin check; POST also needs a JSON content type."""
    created = await _create_key(authenticated_client)
    target = f"{BASE}/{created['id']}"

    foreign = await authenticated_client.delete(target, headers={"Origin": "http://evil.example.com"})
    assert foreign.status_code == 403
    assert await _count_keys() == 1

    resp = await authenticated_client.delete(target)
    assert "content-type" not in resp.request.headers
    assert resp.status_code == 204
    assert await _count_keys() == 0

    plain = await authenticated_client.post(
        BASE,
        content=b'{"name": "x"}',
        headers={"Content-Type": "text/plain"},
    )
    assert plain.status_code == 415
    assert await _count_keys() == 0
