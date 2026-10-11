"""Tests for GET /api/v1/network."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from shared import network
from shared.config import settings


@pytest.mark.asyncio
async def test_requires_auth(client: AsyncClient) -> None:
    """Anonymous callers get 401."""
    resp = await client.get("/api/v1/network")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_loopback_mode(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loopback mode returns no addresses and does not run discovery."""
    monkeypatch.setattr(settings, "BIND_HOST", "127.0.0.1")
    with patch("agent.network_api.reachable_urls") as urls_mock:
        resp = await authenticated_client.get("/api/v1/network")
    assert resp.status_code == 200
    assert resp.json() == {"mode": "loopback", "addresses": []}
    urls_mock.assert_not_called()


@pytest.mark.asyncio
async def test_lan_mode_uses_reachable_urls(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LAN mode returns the list from reachable_urls in order."""
    monkeypatch.setattr(settings, "BIND_HOST", "0.0.0.0")
    urls = ["http://10.0.0.2:1", "http://127.0.0.1:1"]
    with patch("agent.network_api.reachable_urls", return_value=urls) as urls_mock:
        resp = await authenticated_client.get("/api/v1/network")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "lan"
    assert body["addresses"] == urls
    urls_mock.assert_called_once_with(settings.UI_PORT)


@pytest.mark.asyncio
async def test_lan_mode_matches_shared_source(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint list equals the one the console banner uses."""
    monkeypatch.setattr(settings, "BIND_HOST", "0.0.0.0")
    resp = await authenticated_client.get("/api/v1/network")
    assert resp.json()["addresses"] == network.reachable_urls(settings.UI_PORT)


@pytest.mark.asyncio
async def test_foreign_origin_gets_no_cors_header(
    authenticated_client: AsyncClient,
) -> None:
    """A foreign Origin receives no CORS allow header."""
    resp = await authenticated_client.get(
        "/api/v1/network", headers={"Origin": "http://evil.example.com"}
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") is None


@pytest.mark.asyncio
async def test_route_registered(client: AsyncClient) -> None:
    """The route is listed in /debug/routes."""
    resp = await client.get("/debug/routes")
    assert "/api/v1/network" in resp.text
