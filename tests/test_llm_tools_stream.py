"""Tests for stream_chat's tools parameter and tool_calls delta accumulation."""

import json

import httpx
import pytest
import respx

from agent.llm_client import LLMClient

BASE_URL = "http://localhost:1234"


@pytest.fixture
def llm_client() -> LLMClient:
    """Fresh LLM client for each test."""
    return LLMClient(base_url=BASE_URL)


def _sse_body(lines: list[str]) -> bytes:
    """Join raw SSE `data: ...` lines (without trailing newlines) into a byte body."""
    return ("\n".join(lines) + "\n").encode()


@respx.mock
@pytest.mark.asyncio
async def test_stream_chat_without_tools_yields_strings(llm_client: LLMClient) -> None:
    """No tools -> plain string tokens, and no `tools` key in the outbound payload."""
    body = _sse_body(
        [
            'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":" world"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ],
    )
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=body),
    )

    tokens = []
    async for item in llm_client.stream_chat(
        messages=[{"role": "user", "content": "hi"}],
        model="qwen/qwen3.5-9b",
        temperature=0.0,
        max_tokens=100,
    ):
        tokens.append(item)

    assert all(isinstance(t, str) for t in tokens)
    assert "".join(tokens) == "Hello world"
    sent_payload = json.loads(route.calls.last.request.content)
    assert "tools" not in sent_payload


@respx.mock
@pytest.mark.asyncio
async def test_stream_chat_with_tools_yields_tool_calls_event(llm_client: LLMClient) -> None:
    """Live-captured sequence from 02-RESEARCH.md yields exactly one tool_calls event."""
    body = _sse_body(
        [
            'data: {"choices":[{"delta":{"role":"assistant","reasoning_content":"The"},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"id":"rH3YTa6yXyq1CjS1cCrvwo0YwfhsVXQr","type":"function",'
            '"function":{"name":"save_long_term_memory","arguments":""}}]},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"type":"function",'
            '"function":{"arguments":"{\\"key\\":\\"favorite_color\\",'
            '\\"content\\":\\"Blue\\"}"}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ],
    )
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=body),
    )
    tools = [{"type": "function", "function": {"name": "save_long_term_memory"}}]

    events = []
    async for item in llm_client.stream_chat(
        messages=[{"role": "user", "content": "remember my favorite color is blue"}],
        model="qwen/qwen3.5-9b",
        temperature=0.0,
        max_tokens=100,
        tools=tools,
    ):
        events.append(item)

    tool_call_events = [e for e in events if e["type"] == "tool_calls"]
    assert len(tool_call_events) == 1
    calls = tool_call_events[0]["tool_calls"]
    assert len(calls) == 1
    call = calls[0]
    assert call["id"] == "rH3YTa6yXyq1CjS1cCrvwo0YwfhsVXQr"
    assert call["function"]["name"] == "save_long_term_memory"
    assert json.loads(call["function"]["arguments"]) == {
        "key": "favorite_color",
        "content": "Blue",
    }


@respx.mock
@pytest.mark.asyncio
async def test_tool_call_arguments_fragmented_across_chunks_reassemble(
    llm_client: LLMClient,
) -> None:
    """Argument JSON split across three chunks for the same index reassembles fully."""
    body = _sse_body(
        [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_frag",'
            '"type":"function","function":{"name":"save_working_memory","arguments":""}}]},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"type":"function",'
            '"function":{"arguments":"{\\"key\\":\\"current_"}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"type":"function",'
            '"function":{"arguments":"task_step\\",\\"content\\":\\"dra"}}]},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"type":"function",'
            '"function":{"arguments":"fting intro\\"}"}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ],
    )
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=body),
    )
    tools = [{"type": "function", "function": {"name": "save_working_memory"}}]

    events = [
        item
        async for item in llm_client.stream_chat(
            messages=[{"role": "user", "content": "note the task step"}],
            model="qwen/qwen3.5-9b",
            temperature=0.0,
            max_tokens=100,
            tools=tools,
        )
    ]

    tool_call_events = [e for e in events if e["type"] == "tool_calls"]
    assert len(tool_call_events) == 1
    call = tool_call_events[0]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {
        "key": "current_task_step",
        "content": "drafting intro",
    }


@respx.mock
@pytest.mark.asyncio
async def test_two_tool_calls_accumulate_by_index(llm_client: LLMClient) -> None:
    """Interleaved chunks for index 0 and 1 come back as two complete, distinct calls."""
    body = _sse_body(
        [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_lt",'
            '"type":"function","function":{"name":"save_long_term_memory","arguments":""}}]},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"call_wk",'
            '"type":"function","function":{"name":"save_working_memory","arguments":""}}]},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"type":"function",'
            '"function":{"arguments":"{\\"key\\":\\"user_name\\",\\"content\\":\\"Alex\\"}"}}]},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"type":"function",'
            '"function":{"arguments":"{\\"key\\":\\"current_task_step\\",'
            '\\"content\\":\\"drafting\\"}"}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ],
    )
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=body),
    )
    tools = [
        {"type": "function", "function": {"name": "save_long_term_memory"}},
        {"type": "function", "function": {"name": "save_working_memory"}},
    ]

    events = [
        item
        async for item in llm_client.stream_chat(
            messages=[{"role": "user", "content": "remember two things"}],
            model="qwen/qwen3.5-9b",
            temperature=0.0,
            max_tokens=100,
            tools=tools,
        )
    ]

    tool_call_events = [e for e in events if e["type"] == "tool_calls"]
    assert len(tool_call_events) == 1
    calls = tool_call_events[0]["tool_calls"]
    assert len(calls) == 2
    by_id = {c["id"]: c for c in calls}
    assert by_id["call_lt"]["function"]["name"] == "save_long_term_memory"
    assert json.loads(by_id["call_lt"]["function"]["arguments"]) == {
        "key": "user_name",
        "content": "Alex",
    }
    assert by_id["call_wk"]["function"]["name"] == "save_working_memory"
    assert json.loads(by_id["call_wk"]["function"]["arguments"]) == {
        "key": "current_task_step",
        "content": "drafting",
    }


@respx.mock
@pytest.mark.asyncio
async def test_reasoning_content_deltas_are_ignored(llm_client: LLMClient) -> None:
    """delta.reasoning_content chunks produce no yielded content events."""
    body = _sse_body(
        [
            'data: {"choices":[{"delta":{"role":"assistant","reasoning_content":"Thinking..."},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"reasoning_content":"more thinking"},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"Done."},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ],
    )
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=body),
    )
    tools = [{"type": "function", "function": {"name": "save_long_term_memory"}}]

    events = [
        item
        async for item in llm_client.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen/qwen3.5-9b",
            temperature=0.0,
            max_tokens=100,
            tools=tools,
        )
    ]

    content_events = [e for e in events if e["type"] == "content"]
    assert len(content_events) == 1
    assert content_events[0]["content"] == "Done."
