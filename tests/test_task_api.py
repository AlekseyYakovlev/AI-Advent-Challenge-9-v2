"""Tests for GET /api/v1/chats/{chat_id}/tasks."""

import pytest
from httpx import AsyncClient

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
