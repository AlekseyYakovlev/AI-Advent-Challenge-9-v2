"""Origin and Content-Type defenses on the state-changing MCP REST routes."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, Response

from agent import mcp_client
from agent.state import CORS_ORIGINS

FIXTURE: str = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
BASE: str = "/api/v1/mcp/servers"
FOREIGN_ORIGIN: str = "http://evil.example"
ROUTES: list[str] = ["create", "update", "delete", "connect", "disconnect"]


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


async def _create(client: AsyncClient) -> int:
    """Create a server without any Origin header and return its id."""
    resp = await client.post(BASE, json=_payload())
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _call(
    client: AsyncClient, route: str, server_id: int, headers: dict[str, str]
) -> Response:
    """Issue the named mutating request with the given extra headers."""
    if route == "create":
        return await client.post(BASE, json=_payload(name="new"), headers=headers)
    if route == "update":
        return await client.put(
            f"{BASE}/{server_id}", json={"name": "hijacked"}, headers=headers
        )
    if route == "delete":
        return await client.delete(f"{BASE}/{server_id}", headers=headers)
    return await client.post(f"{BASE}/{server_id}/{route}", headers=headers)


async def _names(client: AsyncClient) -> list[str]:
    """Return the names of the user's servers via the unrestricted list route."""
    return [row["name"] for row in (await client.get(BASE)).json()]


@pytest.mark.parametrize("route", ROUTES)
async def test_foreign_origin_is_rejected_without_side_effects(
    authenticated_client: AsyncClient, route: str
) -> None:
    """A foreign Origin gets 403 on each mutating route and changes nothing."""
    client = authenticated_client
    server_id = await _create(client)
    user_id: int = client.seeded_user_id
    if route == "disconnect":
        connected = await client.post(f"{BASE}/{server_id}/connect")
        assert connected.json()["connection"]["status"] == "connected"

    resp = await _call(client, route, server_id, {"Origin": FOREIGN_ORIGIN})

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Cross-origin request rejected"
    assert await _names(client) == ["fixture"]
    assert mcp_client.is_connected(user_id, server_id) is (route == "disconnect")


async def test_null_origin_is_rejected(authenticated_client: AsyncClient) -> None:
    """The opaque `null` Origin (sandboxed iframes, file pages) is not an allowed origin."""
    resp = await authenticated_client.post(
        BASE, json=_payload(), headers={"Origin": "null"}
    )

    assert resp.status_code == 403
    assert await _names(authenticated_client) == []


@pytest.mark.parametrize("origin", CORS_ORIGINS)
@pytest.mark.parametrize("route", ROUTES)
async def test_allowed_origin_passes(
    authenticated_client: AsyncClient, route: str, origin: str
) -> None:
    """The UI's own origins pass on every mutating route."""
    server_id = await _create(authenticated_client)

    resp = await _call(authenticated_client, route, server_id, {"Origin": origin})

    assert resp.status_code == (
        {"create": 201, "delete": 204}.get(route, 200)
    ), resp.text


@pytest.mark.parametrize("route", ROUTES)
async def test_missing_origin_passes(authenticated_client: AsyncClient, route: str) -> None:
    """Non-browser clients send no Origin and are allowed."""
    server_id = await _create(authenticated_client)

    resp = await _call(authenticated_client, route, server_id, {})

    assert resp.status_code == ({"create": 201, "delete": 204}.get(route, 200)), resp.text


async def test_get_routes_ignore_origin(authenticated_client: AsyncClient) -> None:
    """Read-only routes are not covered by the origin check."""
    server_id = await _create(authenticated_client)
    headers = {"Origin": FOREIGN_ORIGIN}

    listed = await authenticated_client.get(BASE, headers=headers)
    status_resp = await authenticated_client.get(f"{BASE}/{server_id}/status", headers=headers)

    assert listed.status_code == 200
    assert status_resp.status_code == 200


@pytest.mark.parametrize("method", ["post", "put"])
async def test_non_json_content_type_is_rejected(
    authenticated_client: AsyncClient, method: str
) -> None:
    """A JSON body sent as text/plain is refused with 415 on create and update."""
    server_id = await _create(authenticated_client)
    url = BASE if method == "post" else f"{BASE}/{server_id}"
    body = _payload(name="plain") if method == "post" else {"name": "plain"}

    resp = await authenticated_client.request(
        method.upper(),
        url,
        content=json.dumps(body),
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 415
    assert await _names(authenticated_client) == ["fixture"]


@pytest.mark.parametrize("method", ["post", "put"])
async def test_missing_content_type_is_rejected(
    authenticated_client: AsyncClient, method: str
) -> None:
    """A body with no Content-Type (the simple-request trick) is refused with 415."""
    server_id = await _create(authenticated_client)
    url = BASE if method == "post" else f"{BASE}/{server_id}"
    body = _payload(name="bare") if method == "post" else {"name": "bare"}

    resp = await authenticated_client.request(
        method.upper(), url, content=json.dumps(body).encode("utf-8")
    )

    assert resp.status_code == 415
    assert await _names(authenticated_client) == ["fixture"]


async def test_json_content_type_with_charset_passes(
    authenticated_client: AsyncClient,
) -> None:
    """application/json with parameters is still a JSON body."""
    resp = await authenticated_client.post(
        BASE,
        content=json.dumps(_payload(name="charset")),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )

    assert resp.status_code == 201, resp.text
