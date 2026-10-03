"""Authentication and per-user isolation tests for the knowledge-base API."""

import pytest
from httpx import AsyncClient

from kb_helpers import NOMIC, seed_kb
from shared.database import async_session_factory
from shared.models import KnowledgeBase

KB_ROUTES = [
    ("GET", "/api/v1/kb", None),
    ("POST", "/api/v1/kb", None),
    ("GET", "/api/v1/kb/1", None),
    ("DELETE", "/api/v1/kb/1", None),
    ("POST", "/api/v1/kb/1/search", {"query": "q"}),
    ("GET", "/api/v1/kb/embedding-models", None),
    ("POST", "/api/v1/kb/embedding-check", {"model": NOMIC}),
]


@pytest.mark.parametrize(("method", "path", "body"), KB_ROUTES)
async def test_unauthenticated_requests_get_401(
    client: AsyncClient, method: str, path: str, body: dict | None
) -> None:
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [("GET", "", None), ("DELETE", "", None), ("POST", "/search", {"query": "q"})],
    ids=["get", "delete", "search"],
)
async def test_foreign_kb_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
    method: str,
    suffix: str,
    body: dict | None,
) -> None:
    kb_id = await seed_kb(authenticated_client.seeded_user_id)
    resp = await second_authenticated_client.request(method, f"/api/v1/kb/{kb_id}{suffix}", json=body)
    assert resp.status_code == 404
    async with async_session_factory() as session:
        assert await session.get(KnowledgeBase, kb_id) is not None


async def test_list_excludes_other_users_kbs(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    kb_id = await seed_kb(authenticated_client.seeded_user_id)
    own = (await authenticated_client.get("/api/v1/kb")).json()
    other = (await second_authenticated_client.get("/api/v1/kb")).json()
    assert [kb["id"] for kb in own] == [kb_id]
    assert other == []
