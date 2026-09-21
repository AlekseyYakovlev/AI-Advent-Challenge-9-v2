"""Tests for GET /api/v1/chats/{chat_id}/tasks."""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import tasks
from shared.database import async_session_factory
from shared.models import Chat, Task, TaskState, TaskTransition


@pytest.mark.asyncio
async def test_get_chat_tasks_returns_tasks_with_history(
    authenticated_client: AsyncClient,
) -> None:
    """GET returns a chat's tasks with embedded, oldest-first transition history."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Tasks chat"})
    chat_id = chat_resp.json()["id"]

    async with async_session_factory() as session:
        task = Task(
            user_id=user_id,
            chat_id=chat_id,
            title="Ship Day 13",
            description="Build the task state machine",
            goal="All five TASK reqs pass",
            state=TaskState.PLANNING,
        )
        session.add(task)
        await session.flush()
        session.add(
            TaskTransition(task_id=task.id, from_state=None, to_state=TaskState.PLANNING, note=""),
        )
        await session.commit()

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    item = body[0]
    for field in (
        "id",
        "title",
        "description",
        "goal",
        "state",
        "is_paused",
        "delegate_to",
        "created_at",
        "updated_at",
        "history",
    ):
        assert field in item
    assert len(item["history"]) == 1
    assert item["history"][0]["from_state"] is None
    assert item["history"][0]["to_state"] == "planning"


@pytest.mark.asyncio
async def test_get_chat_tasks_empty_list_for_new_chat(authenticated_client: AsyncClient) -> None:
    """A chat with no tasks returns 200 and an empty list."""
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Empty chat"})
    chat_id = chat_resp.json()["id"]

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_chat_tasks_other_users_chat_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A non-owner GET returns 404 (never 403) and leaks no task title."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Owned by A"})
    chat_id = chat_resp.json()["id"]

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        task = Task(
            user_id=user_id,
            chat_id=chat_id,
            title="Secret task title",
            description="secret",
            goal="secret goal",
            state=TaskState.PLANNING,
        )
        session.add(task)
        await session.commit()

    resp = await second_authenticated_client.get(f"/api/v1/chats/{chat_id}/tasks")
    assert resp.status_code == 404
    assert "Secret task title" not in resp.text


@pytest.mark.asyncio
async def test_get_chat_tasks_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated GET returns 401."""
    resp = await client.get("/api/v1/chats/1/tasks")
    assert resp.status_code == 401


async def _create_task_row(user_id: int, chat_id: int, title: str = "Task") -> int:
    """Insert a Task row (and its creation transition) directly via the DB, bypassing tools."""
    async with async_session_factory() as session:
        task = Task(
            user_id=user_id,
            chat_id=chat_id,
            title=title,
            description="Description",
            goal="Goal",
            state=TaskState.PLANNING,
        )
        session.add(task)
        await session.flush()
        session.add(
            TaskTransition(task_id=task.id, from_state=None, to_state=TaskState.PLANNING, note=""),
        )
        await session.commit()
        await session.refresh(task)
        return task.id


@pytest.mark.asyncio
async def test_pause_endpoint_sets_is_paused(authenticated_client: AsyncClient) -> None:
    """POST /pause sets is_paused True and leaves state unchanged."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Pause chat"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/pause")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_paused"] is True
    assert body["state"] == "planning"

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is True
        assert task.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_resume_endpoint_clears_is_paused(authenticated_client: AsyncClient) -> None:
    """POST /resume clears is_paused after a prior pause."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Resume chat"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    await authenticated_client.post(f"/api/v1/tasks/{task_id}/pause")
    resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_paused"] is False

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is False


@pytest.mark.asyncio
async def test_cancel_endpoint_sets_cancelled_state_and_appends_transition(
    authenticated_client: AsyncClient,
) -> None:
    """POST /cancel moves the task to cancelled and appends a TaskTransition row."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Cancel chat"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "cancelled"

    async with async_session_factory() as session:
        result = await session.exec(
            select(TaskTransition).where(TaskTransition.task_id == task_id),
        )
        rows = list(result.all())
        assert len(rows) == 2
        newest = max(rows, key=lambda row: row.id)
        assert newest.to_state is TaskState.CANCELLED
        assert newest.from_state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_cancel_endpoint_response_includes_history(
    authenticated_client: AsyncClient,
) -> None:
    """The cancel endpoint's response embeds the new cancellation entry in history."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "History chat"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    body = resp.json()
    assert any(h["to_state"] == "cancelled" for h in body["history"])


@pytest.mark.asyncio
async def test_pause_endpoint_other_users_task_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A non-owner POST /pause returns 404 (never 403) and leaves is_paused False."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Owned by A"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    resp = await second_authenticated_client.post(f"/api/v1/tasks/{task_id}/pause")
    assert resp.status_code == 404

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is False


@pytest.mark.asyncio
async def test_resume_endpoint_other_users_task_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A non-owner POST /resume returns 404 (never 403) and leaves is_paused False."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Owned by A2"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    resp = await second_authenticated_client.post(f"/api/v1/tasks/{task_id}/resume")
    assert resp.status_code == 404

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is False


@pytest.mark.asyncio
async def test_cancel_endpoint_other_users_task_returns_404(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """A non-owner POST /cancel returns 404 (never 403) and leaves state unchanged."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Owned by A3"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    resp = await second_authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert resp.status_code == 404

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.PLANNING


@pytest.mark.asyncio
async def test_task_control_endpoints_require_auth(client: AsyncClient) -> None:
    """Unauthenticated POSTs to all three control endpoints return 401."""
    for suffix in ("pause", "resume", "cancel"):
        resp = await client.post(f"/api/v1/tasks/1/{suffix}")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_task_id_returns_404(authenticated_client: AsyncClient) -> None:
    """POST /pause for a nonexistent task id returns 404."""
    resp = await authenticated_client.post("/api/v1/tasks/999999/pause")
    assert resp.status_code == 404


async def _advance_task_to_done(user_id: int, chat_id: int, task_id: int) -> None:
    """Drive a planning-state task through the legal graph to done."""
    async with async_session_factory() as session:
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.EXECUTION)
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.VALIDATION)
        await tasks.transition_task(session, user_id, chat_id, task_id, TaskState.DONE)


@pytest.mark.asyncio
async def test_cancel_endpoint_on_done_task_returns_409(
    authenticated_client: AsyncClient,
) -> None:
    """Cancelling an already-done task returns 409 with an explanatory detail, state unchanged."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Done chat"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)
    await _advance_task_to_done(user_id, chat_id, task_id)

    resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert resp.status_code == 409
    assert resp.json()["detail"]

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.state is TaskState.DONE


@pytest.mark.asyncio
async def test_cancel_endpoint_on_already_cancelled_task_returns_409(
    authenticated_client: AsyncClient,
) -> None:
    """Cancelling an already-cancelled task returns 409."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Cancel twice"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    first = await authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert first.status_code == 200

    second = await authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert second.status_code == 409
    assert second.json()["detail"]


@pytest.mark.asyncio
async def test_pause_endpoint_on_done_task_returns_409(authenticated_client: AsyncClient) -> None:
    """Pausing an already-done task returns 409, is_paused stays False."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Pause done"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)
    await _advance_task_to_done(user_id, chat_id, task_id)

    resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/pause")
    assert resp.status_code == 409
    assert resp.json()["detail"]

    async with async_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.is_paused is False


@pytest.mark.asyncio
async def test_resume_endpoint_on_non_paused_task_returns_409(
    authenticated_client: AsyncClient,
) -> None:
    """Resuming a task that was never paused returns 409."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Resume no-op"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/resume")
    assert resp.status_code == 409
    assert resp.json()["detail"]


@pytest.mark.asyncio
async def test_resume_endpoint_on_paused_non_terminal_task_returns_200(
    authenticated_client: AsyncClient,
) -> None:
    """Resuming a genuinely paused, non-terminal task still succeeds with is_paused False."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Resume legal"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    pause_resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/pause")
    assert pause_resp.status_code == 200

    resume_resp = await authenticated_client.post(f"/api/v1/tasks/{task_id}/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["is_paused"] is False


@pytest.mark.asyncio
async def test_cancel_endpoint_other_users_done_task_returns_404_not_409(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Another user's task_id yields 404, not 409, even when the operation would be illegal."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Owned, done"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)
    await _advance_task_to_done(user_id, chat_id, task_id)

    resp = await second_authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tasks_list_exposes_rejected_history_after_refused_cancel(
    authenticated_client: AsyncClient,
) -> None:
    """The tasks-list payload carries rejected/rejection_reason on the refused-cancel entry."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Reject list"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)
    await _advance_task_to_done(user_id, chat_id, task_id)

    refused = await authenticated_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert refused.status_code == 409

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/tasks")
    assert resp.status_code == 200
    history = resp.json()[0]["history"]
    rejected_entries = [h for h in history if h["rejected"] is True]
    assert len(rejected_entries) == 1
    entry = rejected_entries[0]
    assert entry["rejection_reason"]
    assert entry["from_state"] == "done"
    assert entry["to_state"] == "cancelled"


@pytest.mark.asyncio
async def test_tasks_list_exposes_self_loop_history_after_refused_resume(
    authenticated_client: AsyncClient,
) -> None:
    """A refused resume persists a self-loop rejected=True row (from_state == to_state)."""
    user_id = authenticated_client.seeded_user_id
    chat_resp = await authenticated_client.post("/api/v1/chats", json={"title": "Self-loop"})
    chat_id = chat_resp.json()["id"]
    task_id = await _create_task_row(user_id, chat_id)

    refused = await authenticated_client.post(f"/api/v1/tasks/{task_id}/resume")
    assert refused.status_code == 409

    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/tasks")
    assert resp.status_code == 200
    history = resp.json()[0]["history"]
    rejected_entries = [h for h in history if h["rejected"] is True]
    assert len(rejected_entries) == 1
    entry = rejected_entries[0]
    assert entry["rejection_reason"]
    assert entry["from_state"] == entry["to_state"]
