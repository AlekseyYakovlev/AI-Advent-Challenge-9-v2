"""Tests for GET/PUT /api/v1/help/config: defaults, validation, ownership and base deletion."""

import sys
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import mcp_config
from kb_helpers import seed_kb
from shared.database import async_session_factory
from shared.models import ChatRagConfig, HelpConfig, KbStatus

URL = "/api/v1/help/config"
FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")


async def _add_server(user_id: int, name: str) -> int:
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, name, sys.executable, [FIXTURE, "ok"], {}, None, True
        )
        return row.id


async def _row(user_id: int) -> HelpConfig | None:
    async with async_session_factory() as session:
        return await session.get(HelpConfig, user_id)


async def test_get_defaults(authenticated_client: AsyncClient) -> None:
    """A user without a stored row sees the documented defaults."""
    resp = await authenticated_client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {
        "kb_id": None, "kb_name": None, "kb_status": None,
        "top_k": 5, "candidate_k": 20, "threshold": 0.45, "calibrated_threshold": None,
        "lexical": False, "llm_rerank": False, "hybrid": False, "rewrite": False,
        "mcp_server_ids": [], "mcp_default": True, "mcp_missing": [],
        "tools_memory": False, "tools_tasks": False, "tools_scheduler": False,
    }
    assert await _row(authenticated_client.seeded_user_id) is None


async def test_get_default_servers_are_local_git(authenticated_client: AsyncClient) -> None:
    """The default server choice resolves to the servers named local-git."""
    user_id = authenticated_client.seeded_user_id
    git_id = await _add_server(user_id, "local-git")
    await _add_server(user_id, "Other")
    body = (await authenticated_client.get(URL)).json()
    assert body["mcp_default"] is True and body["mcp_server_ids"] == [git_id]


async def test_put_marks_ready_kb_and_roundtrips(authenticated_client: AsyncClient) -> None:
    """Marking an own ready base is stored and read back."""
    kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.READY)
    put = await authenticated_client.put(URL, json={"kb_id": kb_id})
    assert put.status_code == 200
    body = put.json()
    assert body["kb_id"] == kb_id and body["kb_name"] == "Тестовая база"
    assert body["kb_status"] == "ready"
    assert (await authenticated_client.get(URL)).json() == body


async def test_put_null_clears_and_omitted_keeps(authenticated_client: AsyncClient) -> None:
    """An explicit null clears the mark; leaving kb_id out keeps it."""
    kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.READY)
    await authenticated_client.put(URL, json={"kb_id": kb_id})
    kept = await authenticated_client.put(URL, json={"top_k": 6})
    assert kept.json()["kb_id"] == kb_id
    cleared = await authenticated_client.put(URL, json={"kb_id": None})
    assert cleared.json()["kb_id"] is None


async def test_put_foreign_kb_is_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """Another user's base and a missing base both answer 404."""
    foreign = await seed_kb(second_authenticated_client.seeded_user_id, status=KbStatus.READY)
    assert (await authenticated_client.put(URL, json={"kb_id": foreign})).status_code == 404
    assert (await authenticated_client.put(URL, json={"kb_id": 9999})).status_code == 404


async def test_put_not_ready_kb_is_422(authenticated_client: AsyncClient) -> None:
    """An own base that is still indexing cannot be marked."""
    kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.INDEXING)
    assert (await authenticated_client.put(URL, json={"kb_id": kb_id})).status_code == 422


async def test_put_threshold_null_and_unchanged(authenticated_client: AsyncClient) -> None:
    """An explicit null threshold is stored as NULL; other updates leave it alone."""
    reset = await authenticated_client.put(URL, json={"threshold": None})
    assert reset.json()["threshold"] is None
    other = await authenticated_client.put(URL, json={"top_k": 8})
    assert other.json()["threshold"] is None
    set_ = await authenticated_client.put(URL, json={"threshold": 0.6})
    assert set_.json()["threshold"] == 0.6
    assert (await authenticated_client.put(URL, json={"top_k": 9})).json()["threshold"] == 0.6


async def test_put_candidate_k_raised_to_top_k(authenticated_client: AsyncClient) -> None:
    """The candidate count never drops below top_k."""
    body = (await authenticated_client.put(URL, json={"top_k": 20, "candidate_k": 10})).json()
    assert body["top_k"] == 20 and body["candidate_k"] == 20


@pytest.mark.parametrize(
    "payload",
    [{"top_k": 0}, {"top_k": 21}, {"candidate_k": 51}, {"threshold": 1.5}, {"lexical": "yes"}],
)
async def test_put_validation_422(authenticated_client: AsyncClient, payload: dict[str, Any]) -> None:
    """Out-of-range numbers and non-boolean flags are rejected."""
    assert (await authenticated_client.put(URL, json=payload)).status_code == 422


async def test_put_flags_roundtrip(authenticated_client: AsyncClient) -> None:
    """Search stage switches and built-in tool switches are stored."""
    payload = {
        "lexical": True, "hybrid": True, "tools_memory": True, "tools_scheduler": True,
    }
    body = (await authenticated_client.put(URL, json=payload)).json()
    for name, value in payload.items():
        assert body[name] is value
    assert body["rewrite"] is False and body["tools_tasks"] is False


async def test_put_mcp_server_choice(authenticated_client: AsyncClient) -> None:
    """The server choice is whole servers: set, emptied and restored to the default."""
    user_id = authenticated_client.seeded_user_id
    server_id = await _add_server(user_id, "Fixture")
    chosen = (await authenticated_client.put(URL, json={"mcp_server_ids": [server_id]})).json()
    assert chosen["mcp_default"] is False and chosen["mcp_server_ids"] == [server_id]
    empty = (await authenticated_client.put(URL, json={"mcp_server_ids": []})).json()
    assert empty["mcp_default"] is False and empty["mcp_server_ids"] == []
    default = (await authenticated_client.put(URL, json={"mcp_server_ids": None})).json()
    assert default["mcp_default"] is True


async def test_put_mcp_server_of_other_user_is_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """A server id of another user is rejected."""
    foreign = await _add_server(second_authenticated_client.seeded_user_id, "Theirs")
    resp = await authenticated_client.put(URL, json={"mcp_server_ids": [foreign]})
    assert resp.status_code == 404


async def test_deleted_server_is_reported_missing(authenticated_client: AsyncClient) -> None:
    """A stored server that no longer exists is listed by name in mcp_missing."""
    user_id = authenticated_client.seeded_user_id
    server_id = await _add_server(user_id, "Fixture")
    await authenticated_client.put(URL, json={"mcp_server_ids": [server_id]})
    assert (await authenticated_client.delete(f"/api/v1/mcp/servers/{server_id}")).status_code == 204
    body = (await authenticated_client.get(URL)).json()
    assert body["mcp_server_ids"] == [] and body["mcp_missing"] == ["Fixture"]


async def test_unauthenticated_is_401(client: AsyncClient) -> None:
    """Both routes require a session."""
    assert (await client.get(URL)).status_code == 401
    assert (await client.put(URL, json={"top_k": 5})).status_code == 401


async def test_put_cross_origin_rejected(authenticated_client: AsyncClient) -> None:
    """A write from a foreign origin is refused."""
    resp = await authenticated_client.put(
        URL, json={"top_k": 5}, headers={"Origin": "http://evil.example"}
    )
    assert resp.status_code == 403


async def test_other_user_does_not_see_mark(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """The mark is per user: the other account keeps the defaults."""
    kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.READY)
    await authenticated_client.put(URL, json={"kb_id": kb_id})
    assert (await second_authenticated_client.get(URL)).json()["kb_id"] is None


async def test_delete_marked_kb_clears_mark(authenticated_client: AsyncClient) -> None:
    """Deleting the marked base through the route sets the stored mark to NULL."""
    user_id = authenticated_client.seeded_user_id
    kb_id = await seed_kb(user_id, status=KbStatus.READY)
    await authenticated_client.put(URL, json={"kb_id": kb_id})
    assert (await authenticated_client.delete(f"/api/v1/kb/{kb_id}")).status_code == 204
    assert (await authenticated_client.get(URL)).json()["kb_id"] is None
    row = await _row(user_id)
    assert row is not None and row.kb_id is None


async def test_put_never_touches_chat_rag_config(authenticated_client: AsyncClient) -> None:
    """The /help settings are independent of any chat's RAG row."""
    kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.READY)
    await authenticated_client.put(URL, json={"kb_id": kb_id, "top_k": 7, "lexical": True})
    async with async_session_factory() as session:
        assert (await session.exec(select(ChatRagConfig))).all() == []
