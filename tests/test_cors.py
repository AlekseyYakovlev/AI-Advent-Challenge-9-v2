"""CORS middleware configuration tests."""

import pytest
from httpx import AsyncClient

from agent.state import CORS_ORIGINS


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", CORS_ORIGINS)
async def test_cors_allows_ui_origins(client: AsyncClient, origin: str) -> None:
    """Preflight requests from UI origins should receive CORS headers."""
    resp = await client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == origin


@pytest.mark.asyncio
async def test_cors_rejects_unknown_origin(client: AsyncClient) -> None:
    """Unknown origins must not be reflected once cookies carry real auth."""
    resp = await client.get(
        "/health",
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.headers.get("access-control-allow-origin") is None
