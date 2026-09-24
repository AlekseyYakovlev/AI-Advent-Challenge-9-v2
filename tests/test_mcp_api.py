"""REST API tests for MCP server management: CRUD, ownership, masking, connect outcomes."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from agent import mcp_client, mcp_config
from shared.database import async_session_factory
from shared.models import McpServerConfig

FIXTURE: str = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
BASE: str = "/api/v1/mcp/servers"


def _payload(mode: str = "ok", **overrides: Any) -> dict[str, Any]:
    """Build a create body launching the fixture MCP server in the given mode."""
    body: dict[str, Any] = {
        "name": "fixture",
        "command": sys.executable,
        "args": [FIXTURE, mode],
        "env": {},
        "cwd": None,
        "enabled": True,
    }
    body.update(overrides)
    return body


async def _create(client: AsyncClient, **kwargs: Any) -> dict[str, Any]:
    """Create a server via the API and return its JSON."""
    mode: str = kwargs.pop("mode", "ok")
    resp = await client.post(BASE, json=_payload(mode, **kwargs))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_mcp_endpoints_require_auth(client: AsyncClient) -> None:
    """Every MCP route rejects requests without a session cookie."""
    assert (await client.get(BASE)).status_code == 401
    assert (await client.post(BASE, json=_payload())).status_code == 401
    assert (await client.post(f"{BASE}/1/connect")).status_code == 401


async def test_crud_roundtrip(authenticated_client: AsyncClient) -> None:
    """Create, list, update and delete a server."""
    created = await _create(authenticated_client)
    assert created["connection"]["status"] == "not_connected"
    assert created["args"] == [FIXTURE, "ok"]

    listed = (await authenticated_client.get(BASE)).json()
    assert [row["id"] for row in listed] == [created["id"]]

    resp = await authenticated_client.put(
        f"{BASE}/{created['id']}", json={"name": "renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"

    resp = await authenticated_client.delete(f"{BASE}/{created['id']}")
    assert resp.status_code == 204
    assert (await authenticated_client.get(BASE)).json() == []


async def test_validation_rejects_blank_command_and_bad_env_key(
    authenticated_client: AsyncClient,
) -> None:
    """Blank commands and malformed env names are rejected with 422."""
    resp = await authenticated_client.post(BASE, json=_payload(command="   "))
    assert resp.status_code == 422
    resp = await authenticated_client.post(BASE, json=_payload(env={"1BAD": "x"}))
    assert resp.status_code == 422


async def test_env_values_never_returned(authenticated_client: AsyncClient) -> None:
    """Only env key names appear in create, list, update and status responses."""
    secret = "supersecret123"
    resp = await authenticated_client.post(
        BASE, json=_payload(env={"SECRET_TOKEN": secret}),
    )
    assert resp.status_code == 201
    assert secret not in resp.text
    server_id = resp.json()["id"]
    assert resp.json()["env_keys"] == ["SECRET_TOKEN"]

    listed = await authenticated_client.get(BASE)
    assert secret not in listed.text
    assert listed.json()[0]["env_keys"] == ["SECRET_TOKEN"]

    updated = await authenticated_client.put(
        f"{BASE}/{server_id}", json={"name": "x"},
    )
    assert secret not in updated.text

    status_resp = await authenticated_client.get(f"{BASE}/{server_id}/status")
    assert secret not in status_resp.text
    assert status_resp.json()["env_keys"] == ["SECRET_TOKEN"]


async def test_update_with_masked_env_preserves_value(
    authenticated_client: AsyncClient,
) -> None:
    """A masked env value keeps the stored secret while other keys are applied."""
    created = await _create(authenticated_client, env={"SECRET_TOKEN": "supersecret123"})
    resp = await authenticated_client.put(
        f"{BASE}/{created['id']}",
        json={"env": {"SECRET_TOKEN": mcp_config.ENV_MASK, "B": "2"}},
    )
    assert resp.status_code == 200
    assert resp.json()["env_keys"] == ["B", "SECRET_TOKEN"]

    async with async_session_factory() as session:
        row = await session.get(McpServerConfig, created["id"])
        assert mcp_config.load_env(row) == {"SECRET_TOKEN": "supersecret123", "B": "2"}


async def test_update_rename_env_key_with_mask_returns_422_and_changes_nothing(
    authenticated_client: AsyncClient,
) -> None:
    """Keeping the mask on a renamed (unknown) env key is rejected; nothing is modified."""
    secret = "supersecret123"
    created = await _create(authenticated_client, env={"SECRET_TOKEN": secret})
    sid = created["id"]
    connect = await authenticated_client.post(f"{BASE}/{sid}/connect")
    assert connect.json()["connection"]["status"] == "connected"

    resp = await authenticated_client.put(
        f"{BASE}/{sid}",
        json={"name": "renamed", "env": {"RENAMED": mcp_config.ENV_MASK}},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert "RENAMED" in detail
    assert mcp_config.ENV_MASK in detail
    assert secret not in resp.text

    async with async_session_factory() as session:
        row = await session.get(McpServerConfig, sid)
        assert row is not None
        assert row.name == "fixture"
        assert mcp_config.load_env(row) == {"SECRET_TOKEN": secret}

    assert mcp_client.is_connected(authenticated_client.seeded_user_id, sid)


async def test_create_with_masked_env_value_returns_422_and_creates_nothing(
    authenticated_client: AsyncClient,
) -> None:
    """A mask literal on create has nothing to refer to, so it is rejected with 422."""
    resp = await authenticated_client.post(
        BASE, json=_payload(env={"K": mcp_config.ENV_MASK}),
    )
    assert resp.status_code == 422
    assert "K" in resp.json()["detail"]
    assert (await authenticated_client.get(BASE)).json() == []


async def test_cross_user_access_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Another user's server id yields 404 on every id route and stays untouched."""
    created = await _create(authenticated_client)
    sid = created["id"]
    other = second_authenticated_client

    assert (await other.get(f"{BASE}/{sid}/status")).status_code == 404
    assert (await other.put(f"{BASE}/{sid}", json={"name": "hijack"})).status_code == 404
    assert (await other.delete(f"{BASE}/{sid}")).status_code == 404
    assert (await other.post(f"{BASE}/{sid}/connect")).status_code == 404
    assert (await other.post(f"{BASE}/{sid}/disconnect")).status_code == 404
    assert (await other.get(BASE)).json() == []

    still_there = (await authenticated_client.get(BASE)).json()
    assert [row["id"] for row in still_there] == [sid]
    assert still_there[0]["name"] == "fixture"


async def test_connect_fixture_server_success(authenticated_client: AsyncClient) -> None:
    """Connecting a healthy server returns serverInfo and its tools."""
    created = await _create(authenticated_client)
    resp = await authenticated_client.post(f"{BASE}/{created['id']}/connect")
    assert resp.status_code == 200
    connection = resp.json()["connection"]
    assert connection["status"] == "connected"
    assert connection["server_info"]["name"] == "fixture-server"
    assert {tool["name"] for tool in connection["tools"]} == {"echo", "add"}
    assert all(isinstance(tool["input_schema"], dict) for tool in connection["tools"])

    listed = (await authenticated_client.get(BASE)).json()[0]["connection"]
    assert listed["status"] == "connected"
    assert {tool["name"] for tool in listed["tools"]} == {"echo", "add"}


async def test_connect_bad_command_reports_error_and_health_ok(
    authenticated_client: AsyncClient,
) -> None:
    """A missing executable is reported in the body and the Agent stays healthy."""
    created = await _create(
        authenticated_client, command="C:/definitely/not/here/nope.exe", args=[],
    )
    resp = await authenticated_client.post(f"{BASE}/{created['id']}/connect")
    assert resp.status_code == 200
    connection = resp.json()["connection"]
    assert connection["status"] == "error"
    assert connection["error_code"] == "COMMAND_NOT_FOUND"
    assert connection["error_message"].startswith("Команда не найдена")

    assert (await authenticated_client.get("/health")).status_code == 200


async def test_connect_exiting_server_reports_process_exited_and_health_ok(
    authenticated_client: AsyncClient,
) -> None:
    """A server that exits during handshake is classified and later connects still work."""
    bad = await _create(authenticated_client, mode="garbage")
    resp = await authenticated_client.post(f"{BASE}/{bad['id']}/connect")
    assert resp.status_code == 200
    assert resp.json()["connection"]["error_code"] == "PROCESS_EXITED"
    assert (await authenticated_client.get("/health")).status_code == 200

    good = await _create(authenticated_client, name="good")
    resp = await authenticated_client.post(f"{BASE}/{good['id']}/connect")
    assert resp.status_code == 200
    assert resp.json()["connection"]["status"] == "connected"


async def test_update_connected_server_disconnects(
    authenticated_client: AsyncClient,
) -> None:
    """Saving an edit closes the live session."""
    created = await _create(authenticated_client)
    user_id: int = authenticated_client.seeded_user_id
    await authenticated_client.post(f"{BASE}/{created['id']}/connect")
    assert mcp_client.is_connected(user_id, created["id"])

    resp = await authenticated_client.put(
        f"{BASE}/{created['id']}", json={"name": "edited"},
    )
    assert resp.status_code == 200
    assert resp.json()["connection"]["status"] == "not_connected"
    assert not mcp_client.is_connected(user_id, created["id"])


async def test_disable_connected_server_disconnects_and_connect_rejected(
    authenticated_client: AsyncClient,
) -> None:
    """Disabling disconnects, and a disabled server cannot be connected."""
    created = await _create(authenticated_client)
    await authenticated_client.post(f"{BASE}/{created['id']}/connect")

    resp = await authenticated_client.put(
        f"{BASE}/{created['id']}", json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["connection"]["status"] == "not_connected"

    resp = await authenticated_client.post(f"{BASE}/{created['id']}/connect")
    assert resp.status_code == 409


async def test_delete_connected_server_cleans_registry(
    authenticated_client: AsyncClient,
) -> None:
    """Deleting a connected server closes its session."""
    created = await _create(authenticated_client)
    user_id: int = authenticated_client.seeded_user_id
    await authenticated_client.post(f"{BASE}/{created['id']}/connect")
    assert mcp_client.is_connected(user_id, created["id"])

    resp = await authenticated_client.delete(f"{BASE}/{created['id']}")
    assert resp.status_code == 204
    assert not mcp_client.is_connected(user_id, created["id"])


async def test_disconnect_endpoint(authenticated_client: AsyncClient) -> None:
    """The disconnect route returns a not_connected state."""
    created = await _create(authenticated_client)
    await authenticated_client.post(f"{BASE}/{created['id']}/connect")

    resp = await authenticated_client.post(f"{BASE}/{created['id']}/disconnect")
    assert resp.status_code == 200
    assert resp.json()["connection"]["status"] == "not_connected"
    assert not mcp_client.is_connected(authenticated_client.seeded_user_id, created["id"])


async def test_status_detects_exited_server(authenticated_client: AsyncClient) -> None:
    """A server that dies after connecting shows PROCESS_EXITED on the next status fetch."""
    created = await _create(authenticated_client, mode="die_after")
    resp = await authenticated_client.post(f"{BASE}/{created['id']}/connect")
    assert resp.json()["connection"]["status"] == "connected"

    await asyncio.sleep(2.5)

    resp = await authenticated_client.get(f"{BASE}/{created['id']}/status")
    assert resp.status_code == 200
    connection = resp.json()["connection"]
    assert connection["status"] == "error"
    assert connection["error_code"] == "PROCESS_EXITED"


async def test_status_after_failed_connect_shows_error(
    authenticated_client: AsyncClient,
) -> None:
    """The list keeps showing the last connect error for a failed server."""
    created = await _create(authenticated_client, mode="garbage")
    await authenticated_client.post(f"{BASE}/{created['id']}/connect")

    listed = (await authenticated_client.get(BASE)).json()
    row = next(item for item in listed if item["id"] == created["id"])
    assert row["connection"]["status"] == "error"


async def test_update_commit_failure_keeps_connection(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PUT whose DB commit fails leaves the live connection and stored config alone."""
    created = await _create(authenticated_client)
    user_id: int = authenticated_client.seeded_user_id
    await authenticated_client.post(f"{BASE}/{created['id']}/connect")
    assert mcp_client.is_connected(user_id, created["id"])

    async def failing_update(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(mcp_config, "update_server", failing_update)
    with pytest.raises(RuntimeError):
        await authenticated_client.put(f"{BASE}/{created['id']}", json={"name": "edited"})

    assert mcp_client.is_connected(user_id, created["id"])
    async with async_session_factory() as session:
        row = await session.get(McpServerConfig, created["id"])
        assert row is not None
        assert row.name == "fixture"


async def test_update_clears_remembered_connect_failure(
    authenticated_client: AsyncClient,
) -> None:
    """A successful edit forgets a connect error that the edit makes stale."""
    created = await _create(
        authenticated_client, command="C:/definitely/not/here/nope.exe", args=[],
    )
    user_id: int = authenticated_client.seeded_user_id
    connected = await authenticated_client.post(f"{BASE}/{created['id']}/connect")
    assert connected.json()["connection"]["status"] == "error"
    assert mcp_client.has_recorded_failure(user_id, created["id"])

    resp = await authenticated_client.put(f"{BASE}/{created['id']}", json={"name": "x"})
    assert resp.status_code == 200
    assert resp.json()["connection"]["status"] == "not_connected"
    assert not mcp_client.has_recorded_failure(user_id, created["id"])


async def test_repeated_edits_do_not_grow_locks(authenticated_client: AsyncClient) -> None:
    """Editing a never-connected server leaves no lock entry behind."""
    created = await _create(authenticated_client)
    key: mcp_client.SessionKey = (authenticated_client.seeded_user_id, created["id"])
    before: int = len(mcp_client._locks)

    for index in range(3):
        resp = await authenticated_client.put(
            f"{BASE}/{created['id']}", json={"name": f"edit-{index}"},
        )
        assert resp.status_code == 200

    assert key not in mcp_client._locks
    assert len(mcp_client._locks) == before
