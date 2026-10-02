"""Tests for LLMClient.complete_chat payload stability and complete_chat_detailed parsing."""

import json
from typing import Any

import httpx
import pytest
import respx

from agent.llm_client import ChatCompletionResult, LLMClient

BASE_URL = "http://localhost:1234"
URL = f"{BASE_URL}/v1/chat/completions"
CORE_KEYS = {"model", "messages", "temperature", "max_tokens", "stream"}
MESSAGES = [{"role": "user", "content": "hi"}]


@pytest.fixture
def llm_client() -> LLMClient:
    """Fresh LLM client for each test."""
    return LLMClient(base_url=BASE_URL)


def _answer(content: Any = "Hi") -> httpx.Response:
    """Build a minimal chat completion response."""
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _body(route: respx.Route) -> dict[str, Any]:
    """Decode the JSON body of the first captured request."""
    return json.loads(route.calls[0].request.content)


@respx.mock
async def test_complete_chat_payload_is_five_keys(llm_client: LLMClient) -> None:
    """complete_chat keeps the exact five-key payload and returns a string."""
    route = respx.post(URL).mock(return_value=_answer("Hello"))
    result = await llm_client.complete_chat(MESSAGES, "m", temperature=0.2, max_tokens=7)
    body = _body(route)
    assert result == "Hello"
    assert set(body) == CORE_KEYS
    assert body["stream"] is False
    assert body["max_tokens"] == 7


@respx.mock
async def test_detailed_sends_extra_body(llm_client: LLMClient) -> None:
    """extra_body fields are sent alongside the core keys."""
    route = respx.post(URL).mock(return_value=_answer())
    await llm_client.complete_chat_detailed(
        MESSAGES, "m", extra_body={"reasoning_effort": "none"}
    )
    body = _body(route)
    assert body["reasoning_effort"] == "none"
    assert CORE_KEYS <= set(body)


@respx.mock
async def test_extra_body_cannot_override_core_keys(llm_client: LLMClient) -> None:
    """Core keys always win over a same-named extra key."""
    route = respx.post(URL).mock(return_value=_answer())
    await llm_client.complete_chat_detailed(
        MESSAGES,
        "m",
        max_tokens=30,
        extra_body={"stream": True, "max_tokens": 9999, "model": "other"},
    )
    body = _body(route)
    assert body["stream"] is False
    assert body["max_tokens"] == 30
    assert body["model"] == "m"


@respx.mock
async def test_detailed_without_extra_body_sends_core_keys_only(llm_client: LLMClient) -> None:
    """No extra body means exactly the five core keys."""
    route = respx.post(URL).mock(return_value=_answer())
    await llm_client.complete_chat_detailed(MESSAGES, "m", extra_body=None)
    assert set(_body(route)) == CORE_KEYS


@respx.mock
async def test_detailed_parses_reasoning_style_answer(llm_client: LLMClient) -> None:
    """A reasoning-only answer exposes finish_reason, reasoning flag and token count."""
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "Thinking Process: x"},
                    }
                ],
                "usage": {"completion_tokens": 30},
            },
        )
    )
    result = await llm_client.complete_chat_detailed(MESSAGES, "m")
    assert result == ChatCompletionResult(
        content="", finish_reason="length", has_reasoning=True, completion_tokens=30
    )


@respx.mock
async def test_detailed_tolerates_minimal_answer(llm_client: LLMClient) -> None:
    """Missing finish_reason, reasoning and usage do not raise."""
    respx.post(URL).mock(return_value=_answer("Hi"))
    result = await llm_client.complete_chat_detailed(MESSAGES, "m")
    assert result == ChatCompletionResult("Hi", None, False, None)


@respx.mock
async def test_detailed_null_content(llm_client: LLMClient) -> None:
    """A null content value becomes None."""
    respx.post(URL).mock(return_value=_answer(None))
    result = await llm_client.complete_chat_detailed(MESSAGES, "m")
    assert result.content is None


@respx.mock
async def test_http_400_raises_status_error(llm_client: LLMClient) -> None:
    """HTTP 400 propagates as HTTPStatusError from both methods."""
    respx.post(URL).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(httpx.HTTPStatusError) as detailed:
        await llm_client.complete_chat_detailed(MESSAGES, "m")
    assert detailed.value.response.status_code == 400
    with pytest.raises(httpx.HTTPStatusError) as plain:
        await llm_client.complete_chat(MESSAGES, "m")
    assert plain.value.response.status_code == 400


@respx.mock
async def test_authorization_header_only_with_api_key() -> None:
    """The Authorization header is sent only when an api key is configured."""
    route = respx.post(URL).mock(return_value=_answer())
    await LLMClient(base_url=BASE_URL, api_key="test-key").complete_chat_detailed(MESSAGES, "m")
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-key"
    await LLMClient(base_url=BASE_URL).complete_chat_detailed(MESSAGES, "m")
    assert "Authorization" not in route.calls[1].request.headers
