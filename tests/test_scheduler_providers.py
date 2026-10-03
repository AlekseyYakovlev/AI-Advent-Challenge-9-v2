"""Scheduler jobs carry and honour their LLM provider."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlmodel import select

from agent import providers
from agent.headless import MSG_PROVIDER_UNAVAILABLE, HeadlessRunError, run_headless_turn
from agent.scheduler import SchedulerService
from agent.state import current_chat_model, current_chat_provider_id
from agent.tools import dispatch_tool_calls
from shared.config import settings
from shared.database import async_session_factory
from shared.models import (
    Chat,
    RunStatus,
    ScheduledTask,
    ScheduledTaskStatus,
    ScheduleType,
    TaskRun,
)
from tests.test_memory_ws import _plain_content_response

ORIGIN = {"Origin": "http://localhost:8000"}
LM_URL = f"{settings.LM_STUDIO_BASE_URL}/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DOWN_URL = "http://down.example/v1/chat/completions"
NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
MODEL = "deepseek-x"


async def _make_provider(user_id: int, **overrides: Any) -> int:
    """Create a DeepSeek-style provider for the user and return its id."""
    fields: dict[str, Any] = {
        "name": "Custom DS",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "enabled": True,
    }
    fields.update(overrides)
    async with async_session_factory() as session:
        await providers.ensure_seeded(session, user_id)
        row = await providers.create_provider(session, user_id, fields)
        return row.id


async def _drop_provider(user_id: int, provider_id: int) -> None:
    async with async_session_factory() as session:
        row = await providers.get_provider(session, user_id, provider_id)
        assert row is not None
        await providers.delete_provider(session, row)


async def _disable_provider(user_id: int, provider_id: int) -> None:
    async with async_session_factory() as session:
        row = await providers.get_provider(session, user_id, provider_id)
        assert row is not None
        await providers.update_provider(session, row, {"enabled": False})


async def _run(user_id: int, provider_id: int | None) -> Any:
    async with async_session_factory() as session:
        return await run_headless_turn(session, user_id, "сделай отчёт", MODEL, provider_id)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Digest",
        "prompt": "summarize",
        "model": MODEL,
        "schedule_type": "interval",
        "interval_seconds": 60,
    }
    body.update(overrides)
    return body


async def _post(client: AsyncClient, **overrides: Any) -> httpx.Response:
    return await client.post("/api/v1/scheduler/tasks", json=_body(**overrides), headers=ORIGIN)


# --- REST create ---------------------------------------------------------------------


async def test_rest_create_stores_own_provider_id(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job created with the user's provider echoes provider_id back."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    provider_id = await _make_provider(authenticated_client.seeded_user_id)

    resp = await _post(authenticated_client, provider_id=provider_id)

    assert resp.status_code == 201, resp.text
    assert resp.json()["provider_id"] == provider_id


async def test_rest_create_without_provider_id_is_null(
    authenticated_client: AsyncClient,
) -> None:
    """Omitting provider_id keeps the legacy shape with a null value."""
    resp = await _post(authenticated_client)

    assert resp.status_code == 201, resp.text
    assert resp.json()["provider_id"] is None


async def test_rest_create_rejects_foreign_provider(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another user's provider id is reported as not found and no job is stored."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    foreign_id = await _make_provider(second_authenticated_client.seeded_user_id)

    resp = await _post(authenticated_client, provider_id=foreign_id)

    assert resp.status_code == 422
    assert resp.json()["detail"] == "Провайдер не найден"
    async with async_session_factory() as session:
        assert (await session.exec(select(ScheduledTask))).all() == []


# --- schedule_task tool --------------------------------------------------------------


async def test_schedule_task_tool_inherits_chat_provider(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chat tool stores the provider of the current turn next to the model."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    user_id = authenticated_client.seeded_user_id
    provider_id = await _make_provider(user_id)
    async with async_session_factory() as session:
        chat = Chat(title="c", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id
    call = {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "schedule_task",
            "arguments": json.dumps(
                {"schedule_type": "once", "delay_seconds": 60, "title": "t", "prompt": "p"}
            ),
        },
    }
    model_token = current_chat_model.set(MODEL)
    provider_token = current_chat_provider_id.set(provider_id)
    try:
        async with async_session_factory() as session:
            results = await dispatch_tool_calls(session, user_id, chat_id, [call])
    finally:
        current_chat_provider_id.reset(provider_token)
        current_chat_model.reset(model_token)

    assert results[0]["ok"] is True
    job_id = json.loads(results[0]["content"])["id"]
    async with async_session_factory() as session:
        row = await session.get(ScheduledTask, job_id)
    assert row is not None
    assert row.provider_id == provider_id
    assert row.model == MODEL


# --- headless routing ----------------------------------------------------------------


@respx.mock
async def test_headless_turn_calls_job_provider_with_bearer_key(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DeepSeek job goes to api.deepseek.com with the key, never to LM Studio."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    user = await seed_user("owner", "pw")
    provider_id = await _make_provider(user.id)
    ds = respx.post(DEEPSEEK_URL).mock(return_value=_plain_content_response("Отчёт готов"))
    lm = respx.post(LM_URL).mock(return_value=httpx.Response(500))

    result = await _run(user.id, provider_id)

    assert result.text == "Отчёт готов"
    assert ds.call_count == 1
    assert ds.calls[0].request.headers["Authorization"] == "Bearer sk-test"
    assert not lm.called


@respx.mock
async def test_headless_turn_without_provider_uses_lm_studio(seed_user: Any) -> None:
    """A legacy job (NULL provider_id) runs on the seeded LM Studio provider."""
    user = await seed_user("owner", "pw")
    lm = respx.post(LM_URL).mock(return_value=_plain_content_response("Готово"))

    result = await _run(user.id, None)

    assert result.text == "Готово"
    assert lm.call_count == 1
    assert "Authorization" not in lm.calls[0].request.headers


@respx.mock
async def test_headless_turn_fails_for_deleted_provider(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted provider fails the run before any request, without fallback."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    user = await seed_user("owner", "pw")
    provider_id = await _make_provider(user.id)
    await _drop_provider(user.id, provider_id)
    ds = respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(500))
    lm = respx.post(LM_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(user.id, provider_id)

    assert excinfo.value.message == MSG_PROVIDER_UNAVAILABLE
    assert excinfo.value.message == "Провайдер недоступен (удалён или отключён)"
    assert not ds.called
    assert not lm.called


@respx.mock
async def test_headless_turn_fails_for_disabled_provider(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disabled provider fails the run before any request, without fallback."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    user = await seed_user("owner", "pw")
    provider_id = await _make_provider(user.id)
    await _disable_provider(user.id, provider_id)
    ds = respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(500))
    lm = respx.post(LM_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(user.id, provider_id)

    assert excinfo.value.message == MSG_PROVIDER_UNAVAILABLE
    assert not ds.called
    assert not lm.called


@respx.mock
async def test_connect_error_on_openai_provider_names_the_server(seed_user: Any) -> None:
    """A refused connection to a non-LM-Studio provider names that server."""
    user = await seed_user("owner", "pw")
    provider_id = await _make_provider(
        user.id, name="Мой сервер", base_url="http://down.example", api_key_env=None
    )
    respx.post(DOWN_URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(user.id, provider_id)

    assert excinfo.value.message == "Модель недоступна: сервер «Мой сервер» не отвечает"


@respx.mock
async def test_connect_error_on_lm_studio_keeps_legacy_message(seed_user: Any) -> None:
    """LM Studio keeps its dedicated message."""
    user = await seed_user("owner", "pw")
    respx.post(LM_URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(user.id, None)

    assert excinfo.value.message == "Модель недоступна: LM Studio не запущен"


# --- scheduler execute_run -----------------------------------------------------------


async def _add_job(user_id: int, provider_id: int | None) -> int:
    async with async_session_factory() as session:
        task = ScheduledTask(
            user_id=user_id,
            title="job",
            prompt="do it",
            model=MODEL,
            provider_id=provider_id,
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=60,
            next_run_at=NOW - timedelta(seconds=10),
            status=ScheduledTaskStatus.ACTIVE,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


@respx.mock
async def test_execute_run_fails_when_job_provider_was_deleted(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dangling provider id ends the run failed with the UI message and sends nothing."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    user = await seed_user("owner", "pw")
    provider_id = await _make_provider(user.id)
    task_id = await _add_job(user.id, provider_id)
    await _drop_provider(user.id, provider_id)
    ds = respx.post(DEEPSEEK_URL).mock(return_value=httpx.Response(500))
    lm = respx.post(LM_URL).mock(return_value=httpx.Response(500))
    svc = SchedulerService()
    started = await svc.tick(NOW, spawn=False)
    assert len(started) == 1

    await svc.execute_run(started[0])

    async with async_session_factory() as session:
        runs = (
            await session.exec(select(TaskRun).where(TaskRun.scheduled_task_id == task_id))
        ).all()
    assert len(runs) == 1
    assert runs[0].status == RunStatus.FAILED
    assert runs[0].error == "Провайдер недоступен (удалён или отключён)"
    assert not ds.called
    assert not lm.called


@respx.mock
async def test_execute_run_uses_job_provider(
    seed_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scheduler passes the stored provider id to the headless turn."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    user = await seed_user("owner", "pw")
    provider_id = await _make_provider(user.id)
    task_id = await _add_job(user.id, provider_id)
    ds = respx.post(DEEPSEEK_URL).mock(return_value=_plain_content_response("Сделано"))
    svc = SchedulerService()
    started = await svc.tick(NOW, spawn=False)

    await svc.execute_run(started[0])

    async with async_session_factory() as session:
        runs = (
            await session.exec(select(TaskRun).where(TaskRun.scheduled_task_id == task_id))
        ).all()
    assert runs[0].status == RunStatus.SUCCESS
    assert runs[0].result_text == "Сделано"
    assert ds.call_count == 1
    assert ds.calls[0].request.headers["Authorization"] == "Bearer sk-test"
