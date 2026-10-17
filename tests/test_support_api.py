"""Tests for GET/PUT /api/v1/support/config: defaults, validation, ownership, isolation from /help."""

import sys
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from agent import mcp_config
from agent.rag import calibrated_threshold
from agent.support_command import DEFAULT_SUPPORT_THRESHOLD
from kb_helpers import seed_kb
from shared.database import async_session_factory
from shared.models import HelpConfig, KbStatus, KnowledgeBase, SupportConfig

URL = "/api/v1/support/config"
HELP_URL = "/api/v1/help/config"
FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")


async def _add_server(user_id: int, name: str) -> int:
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, name, sys.executable, [FIXTURE, "ok"], {}, None, True
        )
        return row.id


async def _row(user_id: int) -> SupportConfig | None:
    async with async_session_factory() as session:
        return await session.get(SupportConfig, user_id)


async def _help_row(user_id: int) -> HelpConfig | None:
    async with async_session_factory() as session:
        return await session.get(HelpConfig, user_id)


async def test_get_defaults(authenticated_client: AsyncClient) -> None:
    """A user without a stored row sees the documented defaults."""
    resp = await authenticated_client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {
        "top_k": 5, "candidate_k": 20, "threshold": DEFAULT_SUPPORT_THRESHOLD,
        "calibrated_threshold": None,
        "lexical": False, "llm_rerank": False, "hybrid": False, "rewrite": False,
        "mcp_server_ids": [], "mcp_default": True, "mcp_missing": [],
        "tools_memory": False, "tools_tasks": False, "tools_scheduler": False,
        "help_kb_id": None, "help_kb_name": None, "help_kb_status": None,
        "support_server_present": False,
    }
    assert await _row(authenticated_client.seeded_user_id) is None


async def test_get_default_servers_are_support_only(authenticated_client: AsyncClient) -> None:
    """The default server choice resolves to the servers named support."""
    user_id = authenticated_client.seeded_user_id
    support_id = await _add_server(user_id, "support")
    await _add_server(user_id, "local-git")
    body = (await authenticated_client.get(URL)).json()
    assert body["mcp_default"] is True and body["mcp_server_ids"] == [support_id]
    assert body["support_server_present"] is True


async def test_help_kb_fields_follow_help_mark(authenticated_client: AsyncClient) -> None:
    """The /help base is shown read-only; nothing is shown without a mark."""
    assert (await authenticated_client.get(URL)).json()["help_kb_id"] is None
    kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.READY)
    await authenticated_client.put(HELP_URL, json={"kb_id": kb_id})
    body = (await authenticated_client.get(URL)).json()
    assert body["help_kb_id"] == kb_id
    assert body["help_kb_name"] == "Тестовая база" and body["help_kb_status"] == "ready"
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
    assert body["calibrated_threshold"] == calibrated_threshold(kb.embedding_model)


async def test_help_kb_of_other_user_is_hidden(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """A HelpConfig pointing at another user's base reveals nothing."""
    foreign = await seed_kb(second_authenticated_client.seeded_user_id, status=KbStatus.READY)
    async with async_session_factory() as session:
        session.add(HelpConfig(user_id=authenticated_client.seeded_user_id, kb_id=foreign))
        await session.commit()
    body = (await authenticated_client.get(URL)).json()
    assert body["help_kb_id"] is None and body["help_kb_name"] is None
    assert body["help_kb_status"] is None and body["calibrated_threshold"] is None


async def test_put_partial_update_keeps_other_fields(authenticated_client: AsyncClient) -> None:
    """Only sent fields change."""
    first = (await authenticated_client.put(URL, json={"top_k": 7, "lexical": True})).json()
    assert first["top_k"] == 7 and first["lexical"] is True
    second = (await authenticated_client.put(URL, json={"rewrite": True})).json()
    assert second["top_k"] == 7 and second["lexical"] is True and second["rewrite"] is True
    assert second["hybrid"] is False and second["candidate_k"] == 20
    assert (await authenticated_client.get(URL)).json() == second


async def test_put_threshold_null_and_unchanged(authenticated_client: AsyncClient) -> None:
    """An explicit null threshold is stored as NULL; other updates leave it alone."""
    assert (await authenticated_client.put(URL, json={"threshold": None})).json()["threshold"] is None
    assert (await authenticated_client.put(URL, json={"top_k": 8})).json()["threshold"] is None
    assert (await authenticated_client.get(URL)).json()["threshold"] is None
    assert (await authenticated_client.put(URL, json={"threshold": 0.6})).json()["threshold"] == 0.6


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


async def test_put_mcp_server_choice(authenticated_client: AsyncClient) -> None:
    """The server choice is whole servers: set, emptied and restored to the default."""
    server_id = await _add_server(authenticated_client.seeded_user_id, "Fixture")
    chosen = (await authenticated_client.put(URL, json={"mcp_server_ids": [server_id]})).json()
    assert chosen["mcp_default"] is False and chosen["mcp_server_ids"] == [server_id]
    empty = (await authenticated_client.put(URL, json={"mcp_server_ids": []})).json()
    assert empty["mcp_default"] is False and empty["mcp_server_ids"] == []
    default = (await authenticated_client.put(URL, json={"mcp_server_ids": None})).json()
    assert default["mcp_default"] is True


async def test_put_foreign_or_unknown_server_is_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """A server id of another user or a missing one is rejected and nothing is stored."""
    foreign = await _add_server(second_authenticated_client.seeded_user_id, "Theirs")
    assert (await authenticated_client.put(URL, json={"mcp_server_ids": [foreign]})).status_code == 404
    assert (await authenticated_client.put(URL, json={"mcp_server_ids": [99999]})).status_code == 404
    assert await _row(authenticated_client.seeded_user_id) is None


async def test_deleted_server_is_reported_missing(authenticated_client: AsyncClient) -> None:
    """A stored server that no longer exists is listed by name in mcp_missing."""
    server_id = await _add_server(authenticated_client.seeded_user_id, "Fixture")
    await authenticated_client.put(URL, json={"mcp_server_ids": [server_id]})
    assert (await authenticated_client.delete(f"/api/v1/mcp/servers/{server_id}")).status_code == 204
    body = (await authenticated_client.get(URL)).json()
    assert body["mcp_server_ids"] == [] and body["mcp_missing"] == ["Fixture"]


async def test_put_requires_json_content_type(authenticated_client: AsyncClient) -> None:
    """A write without a JSON content type is refused."""
    resp = await authenticated_client.put(
        URL, content=b'{"top_k": 5}', headers={"Content-Type": "text/plain"}
    )
    assert resp.status_code == 415


async def test_put_cross_origin_rejected(authenticated_client: AsyncClient) -> None:
    """A write from a foreign origin is refused."""
    resp = await authenticated_client.put(
        URL, json={"top_k": 5}, headers={"Origin": "http://evil.example"}
    )
    assert resp.status_code == 403


async def test_unauthenticated_is_401(client: AsyncClient) -> None:
    """Both routes require a session."""
    assert (await client.get(URL)).status_code == 401
    assert (await client.put(URL, json={"top_k": 5})).status_code == 401


async def test_put_does_not_touch_help_config(authenticated_client: AsyncClient) -> None:
    """Saving /support settings neither creates nor changes the /help row."""
    user_id = authenticated_client.seeded_user_id
    await authenticated_client.put(URL, json={"top_k": 9, "hybrid": True})
    assert await _help_row(user_id) is None
    await authenticated_client.put(HELP_URL, json={"top_k": 3})
    await authenticated_client.put(URL, json={"top_k": 11})
    help_row = await _help_row(user_id)
    assert help_row is not None and help_row.top_k == 3


async def test_user_delete_cascades_support_config(authenticated_client: AsyncClient) -> None:
    """Removing the user removes the stored /support row."""
    user_id = authenticated_client.seeded_user_id
    await authenticated_client.put(URL, json={"top_k": 6})
    assert await _row(user_id) is not None
    async with async_session_factory() as session:
        await session.exec(text("DELETE FROM user WHERE id = :id").bindparams(id=user_id))
        await session.commit()
    assert await _row(user_id) is None
