"""Tests for LMStudioClient load/unload and error handling."""

import json

import httpx
import pytest
import respx

from agent.llm_client import LMStudioClient
from agent.schemas import ModelLoadStatus

BASE_URL = "http://localhost:1234"


@pytest.fixture
def lm_client() -> LMStudioClient:
    """Fresh LM Studio client for each test."""
    return LMStudioClient(base_url=BASE_URL)


@respx.mock
@pytest.mark.asyncio
async def test_load_model_success(lm_client: LMStudioClient) -> None:
    """Successful load returns LOADED status."""
    load_route = respx.post(f"{BASE_URL}/api/v1/models/load").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "llm",
                "instance_id": "inst-test-1",
                "load_time_seconds": 0.1,
                "status": "loaded",
            },
        ),
    )

    result = await lm_client.load_model("test-model", gpu_offload=0, context_length=4096)

    assert result.status == ModelLoadStatus.LOADED
    assert result.model_id == "test-model"
    assert "loaded successfully" in result.message
    assert load_route.called
    request = load_route.calls.last.request
    assert json.loads(request.content) == {"model": "test-model"}


@respx.mock
@pytest.mark.asyncio
async def test_load_model_timeout_triggers_emergency_unload(
    lm_client: LMStudioClient,
) -> None:
    """Load timeout triggers emergency unload with a 5-second cap."""
    respx.post(f"{BASE_URL}/api/v1/models/load").mock(
        side_effect=httpx.TimeoutException("load timed out"),
    )
    emergency_route = respx.post(f"{BASE_URL}/api/v1/models/unload").mock(
        return_value=httpx.Response(200, json={"success": True}),
    )

    result = await lm_client.load_model("slow-model", gpu_offload=0, context_length=None)

    assert result.status == ModelLoadStatus.ERROR
    assert "timed out" in result.message
    assert result.model_id == "slow-model"
    assert emergency_route.called
    assert json.loads(emergency_route.calls.last.request.content) == {
        "instance_id": "slow-model",
    }


@respx.mock
@pytest.mark.asyncio
async def test_load_model_connect_error_returns_unreachable(
    lm_client: LMStudioClient,
) -> None:
    """ConnectError returns UNREACHABLE with a clear message."""
    respx.post(f"{BASE_URL}/api/v1/models/load").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )

    result = await lm_client.load_model("missing-model", gpu_offload=0, context_length=None)

    assert result.status == ModelLoadStatus.UNREACHABLE
    assert result.message == "LM Studio is not running"
    assert result.model_id == "missing-model"


@respx.mock
@pytest.mark.asyncio
async def test_unload_model_success(lm_client: LMStudioClient) -> None:
    """Successful unload returns IDLE status."""
    respx.post(f"{BASE_URL}/api/v1/models/unload").mock(
        return_value=httpx.Response(200, json={"success": True}),
    )

    result = await lm_client.unload_model("test-model")

    assert result.status == ModelLoadStatus.IDLE
    assert result.model_id == "test-model"
    assert "unloaded" in result.message


@respx.mock
@pytest.mark.asyncio
async def test_unload_sends_instance_id_from_load_response(
    lm_client: LMStudioClient,
) -> None:
    """Unload sends the instance_id captured from the load response, not the model id."""
    respx.post(f"{BASE_URL}/api/v1/models/load").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "llm",
                "instance_id": "inst-test-1",
                "load_time_seconds": 0.1,
                "status": "loaded",
            },
        ),
    )
    unload_route = respx.post(f"{BASE_URL}/api/v1/models/unload").mock(
        return_value=httpx.Response(200, json={"success": True}),
    )

    await lm_client.load_model("test-model", gpu_offload=0, context_length=None)
    await lm_client.unload_model("test-model")

    assert unload_route.called
    request = unload_route.calls.last.request
    assert json.loads(request.content) == {"instance_id": "inst-test-1"}


@respx.mock
@pytest.mark.asyncio
async def test_load_targets_v1_endpoint(lm_client: LMStudioClient) -> None:
    """Load must call the v1 endpoint and never the removed v0 endpoint."""
    v0_route = respx.post(f"{BASE_URL}/api/v0/models/load").mock(
        return_value=httpx.Response(200, json={"success": True}),
    )
    v1_route = respx.post(f"{BASE_URL}/api/v1/models/load").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "llm",
                "instance_id": "inst-test-1",
                "load_time_seconds": 0.1,
                "status": "loaded",
            },
        ),
    )

    result = await lm_client.load_model("test-model", gpu_offload=0, context_length=None)

    assert result.status == ModelLoadStatus.LOADED
    assert not v0_route.called
    assert v1_route.called


@respx.mock
@pytest.mark.asyncio
async def test_is_model_loaded_checks_list_models(lm_client: LMStudioClient) -> None:
    """is_model_loaded inspects the loaded flag from list_models."""
    respx.get(f"{BASE_URL}/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "model-a", "loaded": True},
                    {"id": "model-b", "loaded": False},
                ],
            },
        ),
    )

    assert await lm_client.is_model_loaded("model-a") is True
    assert await lm_client.is_model_loaded("model-b") is False
    assert await lm_client.is_model_loaded("model-c") is False
