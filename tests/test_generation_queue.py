"""Generation cap and queue for chat turns (LIM-05)."""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

import agent.ws as agent_ws
from agent import limits
from agent import state as agent_state
from agent.headless import RecordingSink
from agent.main import app
from agent.schemas import MessagePayload
from shared.config import settings
from tests.conftest import login_test_client
from tests.kb_helpers import wait_until

BASE_URL = settings.LM_STUDIO_BASE_URL
COMPLETIONS = f"{BASE_URL}/v1/chat/completions"
MODEL = "queue-test-model"
WS_ORIGIN = "http://localhost:8000"
REPLY = "Ответ готов."
AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"


def _ok_response(text: str = REPLY) -> httpx.Response:
    """Build a plain-content SSE response."""
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]})}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    return httpx.Response(200, content=("\n".join(lines) + "\n").encode())


@pytest.fixture(autouse=True)
def _quiet_background_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep title generation and fact extraction from calling the mocked upstream."""
    monkeypatch.setattr("agent.ws.schedule_title_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.ws.extract_and_update_facts", lambda *args, **kwargs: None)


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, start: float = 1000.0) -> list[float]:
    """Replace the limits clock with a controllable one and return its holder."""
    clock = [start]
    monkeypatch.setattr(limits, "_now", lambda: clock[0])
    return clock


class _FakeTurns:
    """Stand-in for the real turn: records starts, holds until released, then sends done."""

    def __init__(self) -> None:
        self.started: list[int] = []
        self.running = 0
        self.max_running = 0
        self.fail_for: set[int] = set()
        self._events: dict[int, asyncio.Event] = {}

    def event(self, chat_id: int) -> asyncio.Event:
        return self._events.setdefault(chat_id, asyncio.Event())

    def release(self, chat_id: int) -> None:
        self.event(chat_id).set()

    async def __call__(self, sink: Any, chat_id: int, payload: MessagePayload) -> None:
        self.started.append(chat_id)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await self.event(chat_id).wait()
            if chat_id in self.fail_for:
                raise RuntimeError("turn failed")
            await sink.send_json(
                {"type": "done", "message_id": 1, "stats": {"message_count": 2}}
            )
        finally:
            self.running -= 1


@pytest.fixture
def turns(monkeypatch: pytest.MonkeyPatch) -> _FakeTurns:
    """Replace the real turn with the fake one."""
    fake = _FakeTurns()
    monkeypatch.setattr(agent_ws, "_run_chat_turn", fake)
    return fake


def _start(chat_id: int, fresh_lock: bool = True) -> tuple[RecordingSink, asyncio.Task[None]]:
    """Start one inbound message for the chat as a task."""
    if fresh_lock:
        agent_state.chat_locks.pop(chat_id, None)
    sink = RecordingSink()
    payload = MessagePayload(content="x", model="m")
    task = asyncio.ensure_future(agent_ws._handle_chat_message(sink, chat_id, payload))
    return sink, task



def _frames_of(sink: RecordingSink, frame_type: str) -> list[dict[str, Any]]:
    return [f for f in sink.frames if f.get("type") == frame_type]


async def _wait_started(turns: _FakeTurns, count: int) -> None:
    await wait_until(lambda: len(turns.started) >= count)


async def _wait_waiting(count: int) -> None:
    await wait_until(lambda: limits.generation_gate.waiting == count)


async def test_excess_turns_wait_and_all_complete(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-12, D-13: with cap 1 three turns run one at a time and all finish."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    sinks_tasks = [_start(chat_id) for chat_id in (1, 2, 3)]
    await _wait_started(turns, 1)
    await _wait_waiting(2)
    assert turns.started == [1]
    for index, chat_id in enumerate((1, 2, 3)):
        turns.release(chat_id)
        await asyncio.wait_for(sinks_tasks[index][1], timeout=5)
    assert turns.started == [1, 2, 3]
    assert turns.max_running == 1
    for sink, _ in sinks_tasks:
        assert len(_frames_of(sink, "done")) == 1


async def test_queued_frame_only_for_waiters(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-14: only the turns that had to wait get a queued frame, before their done frame."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    sinks_tasks = [_start(chat_id) for chat_id in (1, 2, 3)]
    await _wait_started(turns, 1)
    await _wait_waiting(2)
    for index, chat_id in enumerate((1, 2, 3)):
        turns.release(chat_id)
        await asyncio.wait_for(sinks_tasks[index][1], timeout=5)
    first, second, third = (sink for sink, _ in sinks_tasks)
    assert _frames_of(first, "queued") == []
    for sink, chat_id in ((second, 2), (third, 3)):
        assert _frames_of(sink, "queued") == [{"type": "queued", "chat_id": chat_id}]
        types = [f["type"] for f in sink.frames]
        assert types.index("queued") < types.index("done")


async def test_queue_wait_reported_in_stats(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-15: the done frame carries the waited seconds, 0.0 without a wait."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    clock = _freeze_clock(monkeypatch)
    sinks_tasks = [_start(chat_id) for chat_id in (1, 2, 3)]
    await _wait_started(turns, 1)
    await _wait_waiting(2)
    clock[0] += 2.0
    for index, chat_id in enumerate((1, 2, 3)):
        turns.release(chat_id)
        await asyncio.wait_for(sinks_tasks[index][1], timeout=5)
    done = [_frames_of(sink, "done")[0] for sink, _ in sinks_tasks]
    assert done[0]["stats"]["queue_wait_seconds"] == 0.0
    assert done[1]["stats"]["queue_wait_seconds"] == 2.0
    assert done[2]["stats"]["queue_wait_seconds"] == 2.0
    for frame in done:
        assert frame["stats"]["message_count"] == 2


async def test_cap_zero_means_no_cap(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-12: with 0 nothing waits."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 0)
    sinks_tasks = [_start(chat_id) for chat_id in (1, 2, 3)]
    await _wait_started(turns, 3)
    assert limits.generation_gate.waiting == 0
    for chat_id in (1, 2, 3):
        turns.release(chat_id)
    await asyncio.wait_for(asyncio.gather(*(t for _, t in sinks_tasks)), timeout=5)
    for sink, _ in sinks_tasks:
        assert _frames_of(sink, "queued") == []
        assert _frames_of(sink, "done")[0]["stats"]["queue_wait_seconds"] == 0.0


async def test_cap_two_runs_two_and_queues_two(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With cap 2 and four chats exactly two run at once and two are announced as queued."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 2)
    sinks_tasks = [_start(chat_id) for chat_id in (1, 2, 3, 4)]
    await _wait_started(turns, 2)
    await _wait_waiting(2)
    assert turns.running == 2
    assert sum(len(_frames_of(sink, "queued")) for sink, _ in sinks_tasks) == 2
    for chat_id in (1, 2, 3, 4):
        turns.release(chat_id)
    await asyncio.wait_for(asyncio.gather(*(t for _, t in sinks_tasks)), timeout=5)
    assert turns.max_running == 2


async def test_turn_waiting_for_chat_lock_holds_no_slot(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock first, then slot: a turn waiting for its own chat's lock does not occupy a slot."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 2)
    first_sink, first_task = _start(10)
    await _wait_started(turns, 1)
    second_sink, second_task = _start(10, fresh_lock=False)
    await asyncio.sleep(0.05)
    assert agent_state.chat_locks[10].locked()
    assert turns.started == [10]
    other_sink, other_task = _start(20)
    await _wait_started(turns, 2)
    assert turns.started == [10, 20]
    assert limits.generation_gate.waiting == 0
    assert _frames_of(other_sink, "queued") == []
    turns.release(10)
    turns.release(20)
    await asyncio.wait_for(asyncio.gather(first_task, second_task, other_task), timeout=5)
    assert _frames_of(first_sink, "done") and _frames_of(second_sink, "done")


async def test_failing_turn_frees_its_slot(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A turn that raises gives its slot to the next waiter and the error propagates."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    turns.fail_for.add(1)
    _, task_one = _start(1)
    await _wait_started(turns, 1)
    _, task_two = _start(2)
    await _wait_waiting(1)
    turns.release(1)
    with pytest.raises(RuntimeError, match="turn failed"):
        await asyncio.wait_for(task_one, timeout=5)
    await _wait_started(turns, 2)
    assert turns.started == [1, 2]
    turns.release(2)
    await asyncio.wait_for(task_two, timeout=5)


async def test_cancelled_waiter_does_not_block_the_queue(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A waiter cancelled in the queue leaves it cleanly; the next waiter still runs."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    _, task_one = _start(1)
    await _wait_started(turns, 1)
    _, task_two = _start(2)
    await _wait_waiting(1)
    _, task_three = _start(3)
    await _wait_waiting(2)
    task_two.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_two
    await _wait_waiting(1)
    turns.release(1)
    await asyncio.wait_for(task_one, timeout=5)
    await _wait_started(turns, 2)
    assert turns.started == [1, 3]
    turns.release(3)
    await asyncio.wait_for(task_three, timeout=5)
    assert 2 not in turns.started


async def test_queue_stats_sink_passes_frames_and_does_not_mutate() -> None:
    """The wrapper only touches the stats of a done frame and works on a copy."""
    inner = RecordingSink()
    sink = agent_ws._QueueStatsSink(inner, 1.5)
    no_stats = {"type": "done", "message_id": 5}
    token = {"type": "token", "content": "a"}
    tool_call = {"type": "tool_call", "name": "t"}
    error = {"type": "error", "detail": "x"}
    for frame in (no_stats, token, tool_call, error):
        await sink.send_json(frame)
    assert inner.frames == [no_stats, tool_call, error]
    done = {"type": "done", "message_id": 6, "stats": {"message_count": 2}}
    await sink.send_json(done)
    assert done == {"type": "done", "message_id": 6, "stats": {"message_count": 2}}
    assert inner.frames[-1]["stats"] == {"message_count": 2, "queue_wait_seconds": 1.5}
    assert inner.frames[-1]["message_id"] == 6


async def test_gate_wrapper_has_no_queue_timeout(
    turns: _FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-13: a turn that waits longer than LLM_TIMEOUT for its slot still completes."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    monkeypatch.setattr(settings, "LLM_TIMEOUT", 0.05)
    _, task_one = _start(1)
    await _wait_started(turns, 1)
    sink_two, task_two = _start(2)
    await _wait_waiting(1)
    await asyncio.sleep(0.3)
    assert not task_two.done()
    turns.release(1)
    await asyncio.wait_for(task_one, timeout=5)
    await _wait_started(turns, 2)
    turns.release(2)
    await asyncio.wait_for(task_two, timeout=5)
    assert len(_frames_of(sink_two, "done")) == 1


async def _chat_and_key(client: AsyncClient) -> tuple[int, str]:
    key_resp = await client.post("/api/v1/api-keys", json={"name": "t"})
    assert key_resp.status_code == 201, key_resp.text
    chat_resp = await client.post("/api/v1/chats", json={"title": "Queue chat"})
    assert chat_resp.status_code in (200, 201), chat_resp.text
    return chat_resp.json()["id"], key_resp.json()["key"]


async def _new_chat(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/chats", json={"title": "Queue chat"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _api_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post(client: AsyncClient, chat_id: int, key: str) -> httpx.Response:
    return await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "привет", "model": MODEL},
        headers={"Authorization": f"Bearer {key}"},
    )


@respx.mock
async def test_http_turn_waits_in_the_gate_and_reports_wait(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-11, D-15: with cap 1 the second HTTP turn waits for the first and reports 2.0."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    clock = _freeze_clock(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return _ok_response()

    respx.post(COMPLETIONS).mock(side_effect=upstream)
    chat_a, key = await _chat_and_key(authenticated_client)
    chat_b = await _new_chat(authenticated_client)
    async with _api_client() as api:
        post_one = asyncio.create_task(_post(api, chat_a, key))
        await asyncio.wait_for(entered.wait(), timeout=5)
        post_two = asyncio.create_task(_post(api, chat_b, key))
        await _wait_waiting(1)
        assert len(calls) == 1
        clock[0] += 2.0
        release.set()
        resp_one, resp_two = await asyncio.wait_for(
            asyncio.gather(post_one, post_two), timeout=10
        )
    assert resp_one.status_code == 200, resp_one.text
    assert resp_two.status_code == 200, resp_two.text
    assert resp_one.json()["stats"]["queue_wait_seconds"] == 0.0
    assert resp_two.json()["stats"]["queue_wait_seconds"] == 2.0
    assert len(calls) == 2


@respx.mock
def test_websocket_done_frame_carries_queue_wait() -> None:
    """D-15: a turn that did not wait reports 0.0 and gets no queued frame."""
    respx.post(COMPLETIONS).mock(side_effect=lambda request: _ok_response())
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "WS"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            ws.send_json({"content": "hi", "model": MODEL})
            types: list[str] = []
            while True:
                frame = ws.receive_json()
                types.append(frame["type"])
                if frame["type"] in {"done", "error"}:
                    break
    assert frame["type"] == "done"
    assert frame["stats"]["queue_wait_seconds"] == 0.0
    assert "queued" not in types


def _wait_waiting_sync(count: int) -> None:
    """Poll the waiter count from the test thread and fail after 5 seconds."""
    deadline = time.monotonic() + 5.0
    while limits.generation_gate.waiting != count:
        if time.monotonic() > deadline:
            raise AssertionError("waiter count not reached in time")
        time.sleep(0.01)


@respx.mock
def test_websocket_queued_frame_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-14: a WebSocket turn that has to wait gets the queued frame first, then completes."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    clock = _freeze_clock(monkeypatch)
    respx.post(COMPLETIONS).mock(side_effect=lambda request: _ok_response())
    held = threading.Event()
    release_flag = threading.Event()

    async def hold_slot() -> None:
        async with limits.generation_gate.slot():
            held.set()
            while not release_flag.is_set():
                await asyncio.sleep(0.01)

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "WS queue"}).json()["id"]
        holder = client.portal.start_task_soon(hold_slot)
        assert held.wait(5)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}
        ) as ws:
            ws.send_json({"content": "hi", "model": MODEL})
            _wait_waiting_sync(1)
            clock[0] += 2.0
            release_flag.set()
            frames: list[dict[str, Any]] = []
            while True:
                frame = ws.receive_json()
                frames.append(frame)
                if frame["type"] in {"done", "error"}:
                    break
        holder.result(timeout=5)
    assert frames[0] == {"type": "queued", "chat_id": chat_id}
    assert frames[-1]["type"] == "done"
    assert frames[-1]["stats"]["queue_wait_seconds"] == 2.0


def test_background_work_is_outside_the_cap() -> None:
    """D-11: scheduler, titles, fact extraction and KB indexing never take a slot."""
    for name in ("headless.py", "titles.py", "scheduler.py", "kb_indexer.py"):
        source = (AGENT_DIR / name).read_text(encoding="utf-8")
        assert "generation_gate" not in source, name
        assert "run_limited_turn" not in source, name


@respx.mock
async def test_llm_timeout_is_counted_only_after_the_queue(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-13: a real turn queued for longer than LLM_TIMEOUT still completes."""
    monkeypatch.setattr(settings, "MAX_CONCURRENT_GENERATIONS", 1)
    monkeypatch.setattr(settings, "LLM_TIMEOUT", 0.2)
    calls: list[int] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _ok_response()

    respx.post(COMPLETIONS).mock(side_effect=upstream)
    chat_id, key = await _chat_and_key(authenticated_client)
    async with _api_client() as api:
        async with limits.generation_gate.slot():
            post = asyncio.create_task(_post(api, chat_id, key))
            await _wait_waiting(1)
            await asyncio.sleep(0.6)
            assert not post.done()
            assert calls == []
        resp = await asyncio.wait_for(post, timeout=10)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"]
    assert body["stats"]["queue_wait_seconds"] >= 0.6
    assert len(calls) == 1
