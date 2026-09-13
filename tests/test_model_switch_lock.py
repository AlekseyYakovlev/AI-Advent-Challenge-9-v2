"""Tests for LMStudioClient model switch lock serialization."""

import asyncio

import httpx
import pytest
import respx

from agent.llm_client import LMStudioClient

BASE_URL = "http://localhost:1234"


@pytest.fixture
def lm_client() -> LMStudioClient:
    """Fresh LM Studio client for each test."""
    return LMStudioClient(base_url=BASE_URL)


@respx.mock
@pytest.mark.asyncio
async def test_parallel_loads_execute_sequentially(lm_client: LMStudioClient) -> None:
    """Two concurrent load requests must not overlap under the switch lock."""
    call_order: list[str] = []
    load_started = asyncio.Event()
    release_first_load = asyncio.Event()

    async def slow_load(request: httpx.Request) -> httpx.Response:
        model_id = request.read().decode()
        if "model-a" in model_id:
            call_order.append("start-a")
            load_started.set()
            await release_first_load.wait()
            call_order.append("end-a")
        else:
            call_order.append("start-b")
            call_order.append("end-b")
        return httpx.Response(200, json={"success": True})

    respx.post(f"{BASE_URL}/api/v0/models/unload").mock(
        return_value=httpx.Response(200, json={"success": True}),
    )
    respx.post(f"{BASE_URL}/api/v0/models/load").mock(side_effect=slow_load)

    task_a = asyncio.create_task(
        lm_client.load_model("model-a", gpu_offload=0, context_length=None),
    )
    await load_started.wait()
    task_b = asyncio.create_task(
        lm_client.load_model("model-b", gpu_offload=0, context_length=None),
    )
    await asyncio.sleep(0.05)
    assert "start-b" not in call_order
    release_first_load.set()
    await asyncio.gather(task_a, task_b)

    assert call_order == ["start-a", "end-a", "start-b", "end-b"]
