"""Tests for GET/POST/PUT/DELETE /api/v1/invariants (global, shared across all users)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_global_invariants_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated GET returns 401."""
    resp = await client.get("/api/v1/invariants")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_global_invariant_returns_201_with_body(
    authenticated_client: AsyncClient,
) -> None:
    """POST returns 201 and a body carrying id, title, rule_text, created_at, updated_at."""
    resp = await authenticated_client.post(
        "/api/v1/invariants",
        json={"title": "Без Docker", "rule_text": "Никогда не предлагай Docker"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {"id", "title", "rule_text", "created_at", "updated_at"}
    assert body["title"] == "Без Docker"
    assert body["rule_text"] == "Никогда не предлагай Docker"


@pytest.mark.asyncio
async def test_create_global_invariant_rejects_empty_title(
    authenticated_client: AsyncClient,
) -> None:
    """POST with an empty title returns 422."""
    resp = await authenticated_client.post(
        "/api/v1/invariants",
        json={"title": "", "rule_text": "x"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_second_user_sees_and_edits_the_same_global_invariant(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A second, different user sees and can edit the first user's global invariant (D-02)."""
    create_resp = await authenticated_client.post(
        "/api/v1/invariants",
        json={"title": "Без Docker", "rule_text": "Никогда не предлагай Docker"},
    )
    invariant_id = create_resp.json()["id"]

    list_resp = await second_authenticated_client.get("/api/v1/invariants")
    assert list_resp.status_code == 200
    ids = [item["id"] for item in list_resp.json()]
    assert invariant_id in ids

    put_resp = await second_authenticated_client.put(
        f"/api/v1/invariants/{invariant_id}",
        json={"rule_text": "Edited by another user"},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["rule_text"] == "Edited by another user"


@pytest.mark.asyncio
async def test_update_global_invariant_unknown_id_returns_404(
    authenticated_client: AsyncClient,
) -> None:
    """PUT on an unknown id returns 404."""
    resp = await authenticated_client.put(
        "/api/v1/invariants/999999",
        json={"title": "X"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_global_invariant_returns_204_and_removes_it(
    authenticated_client: AsyncClient,
) -> None:
    """DELETE returns 204, and a follow-up GET list no longer contains the row."""
    create_resp = await authenticated_client.post(
        "/api/v1/invariants",
        json={"title": "Doomed", "rule_text": "will be deleted"},
    )
    invariant_id = create_resp.json()["id"]

    delete_resp = await authenticated_client.delete(f"/api/v1/invariants/{invariant_id}")
    assert delete_resp.status_code == 204

    list_resp = await authenticated_client.get("/api/v1/invariants")
    ids = [item["id"] for item in list_resp.json()]
    assert invariant_id not in ids


@pytest.mark.asyncio
async def test_delete_global_invariant_unknown_id_returns_404(
    authenticated_client: AsyncClient,
) -> None:
    """DELETE on an unknown id returns 404."""
    resp = await authenticated_client.delete("/api/v1/invariants/999999")
    assert resp.status_code == 404
