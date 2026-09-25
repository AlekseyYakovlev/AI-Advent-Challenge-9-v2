"""Context length limits and defaults on the settings API and model."""

import pytest
from httpx import AsyncClient

from shared.models import Settings


@pytest.mark.asyncio
async def test_context_length_accepts_upper_bound(authenticated_client: AsyncClient) -> None:
    """32768 is accepted and persisted."""
    resp = await authenticated_client.put(
        "/api/v1/settings",
        json={"chat_id": None, "context_length": 32768},
    )
    assert resp.status_code == 200
    assert resp.json()["context_length"] == 32768

    get_resp = await authenticated_client.get("/api/v1/settings")
    assert get_resp.json()["context_length"] == 32768


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [32769, 511])
async def test_context_length_out_of_range_rejected(
    authenticated_client: AsyncClient,
    value: int,
) -> None:
    """Values above 32768 or below 512 fail validation."""
    resp = await authenticated_client.put(
        "/api/v1/settings",
        json={"chat_id": None, "context_length": value},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_fresh_settings_default_to_16384(authenticated_client: AsyncClient) -> None:
    """A user without a settings row gets the 16384 default."""
    resp = await authenticated_client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["context_length"] == 16384


def test_settings_model_default_is_16384() -> None:
    """The SQLModel default matches the API default."""
    assert Settings().context_length == 16384
