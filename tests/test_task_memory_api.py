"""Tests for the task-memory routes, the task_state memory field and the branch restore."""

from typing import Any

import pytest
from httpx import AsyncClient

from agent import memory, task_memory
from agent.rag import serialize_rag_payload
from agent.state import CORS_ORIGINS
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, ChatRagConfig, ChatTaskMemory, Message

EMPTY: dict[str, Any] = {"goal": None, "clarified": [], "constraints": []}
RAG_REQUIRED = "Память задачи доступна только в чатах с включённым RAG"


async def _chat(client: AsyncClient, rag_on: bool = True) -> int:
    resp = await client.post("/api/v1/chats", json={"title": "tm"})
    assert resp.status_code == 201
    chat_id: int = resp.json()["id"]
    if rag_on:
        async with async_session_factory() as session:
            session.add(ChatRagConfig(chat_id=chat_id, mode="rag", top_k=5))
            await session.commit()
    return chat_id


def _doc(goal: str | None = None, clarified: list[str] | None = None,
         constraints: list[str] | None = None) -> task_memory.TaskMemoryDoc:
    next_id = 1
    clar: list[task_memory.TaskMemoryItem] = []
    cons: list[task_memory.TaskMemoryItem] = []
    for text in clarified or []:
        clar.append(task_memory.TaskMemoryItem(id=next_id, text=text))
        next_id += 1
    for text in constraints or []:
        cons.append(task_memory.TaskMemoryItem(id=next_id, text=text))
        next_id += 1
    return task_memory.TaskMemoryDoc(goal=goal, clarified=clar, constraints=cons, next_id=next_id)


async def _seed_doc(chat_id: int, doc: task_memory.TaskMemoryDoc) -> None:
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        await task_memory.stage_doc(session, chat, doc)
        await session.commit()


async def _stored(chat_id: int) -> task_memory.TaskMemoryDoc | None:
    async with async_session_factory() as session:
        row = await session.get(ChatTaskMemory, chat_id)
        if row is None:
            return None
        return task_memory.TaskMemoryDoc.model_validate_json(row.doc_json)


def _url(chat_id: int, tail: str) -> str:
    return f"/api/v1/chats/{chat_id}/task-memory/{tail}"


async def test_memory_task_state_null_without_rag(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client, rag_on=False)
    body = (await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")).json()
    assert body["task_state"] is None
    async with async_session_factory() as session:
        session.add(ChatRagConfig(chat_id=chat_id, mode="off"))
        await session.commit()
    body = (await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")).json()
    assert body["task_state"] is None


async def test_memory_task_state_empty_and_stored(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    url = f"/api/v1/chats/{chat_id}/memory"
    assert (await authenticated_client.get(url)).json()["task_state"] == EMPTY
    await _seed_doc(chat_id, _doc("узнать штраф", ["про самокат"], ["только КоАП"]))
    async with async_session_factory() as session:
        await memory.save_working_memory(
            session, authenticated_client.seeded_user_id, chat_id, "task", "draft"
        )
    body = (await authenticated_client.get(url)).json()
    assert body["task_state"]["goal"] == "узнать штраф"
    assert [i["text"] for i in body["task_state"]["clarified"]] == ["про самокат"]
    assert [i["text"] for i in body["task_state"]["constraints"]] == ["только КоАП"]
    assert [w["key"] for w in body["working"]] == ["task"]
    assert "штраф" not in str(body["working"])


async def test_memory_task_state_null_when_disabled(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_id = await _chat(authenticated_client)
    monkeypatch.setattr(settings, "TASK_MEMORY_ENABLED", False)
    body = (await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")).json()
    assert body["task_state"] is None


async def test_put_goal_strips_and_stores(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    resp = await authenticated_client.put(_url(chat_id, "goal"), json={"goal": "  узнать штраф  "})
    assert resp.status_code == 200
    assert resp.json()["goal"] == "узнать штраф"
    stored = await _stored(chat_id)
    assert stored is not None and stored.goal == "узнать штраф"


@pytest.mark.parametrize("goal", ["", "   ", "x" * 301])
async def test_put_goal_rejects_blank_and_long(
    authenticated_client: AsyncClient, goal: str
) -> None:
    chat_id = await _chat(authenticated_client)
    resp = await authenticated_client.put(_url(chat_id, "goal"), json={"goal": goal})
    assert resp.status_code == 422
    assert await _stored(chat_id) is None


async def test_delete_item_removes_only_that_id(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    await _seed_doc(chat_id, _doc("g", ["a", "b"], ["c"]))
    resp = await authenticated_client.delete(_url(chat_id, "items/2"))
    assert resp.status_code == 200
    body = resp.json()
    assert [i["id"] for i in body["clarified"]] == [1]
    assert [i["id"] for i in body["constraints"]] == [3]
    resp = await authenticated_client.delete(_url(chat_id, "items/3"))
    assert resp.json()["constraints"] == []
    assert resp.json()["goal"] == "g"


async def test_delete_unknown_item_is_404(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    await _seed_doc(chat_id, _doc("g", ["a"]))
    resp = await authenticated_client.delete(_url(chat_id, "items/99"))
    assert resp.status_code == 404
    stored = await _stored(chat_id)
    assert stored is not None and len(stored.clarified) == 1


async def test_reset_clears_row(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    await _seed_doc(chat_id, _doc("g", ["a"], ["c"]))
    resp = await authenticated_client.post(_url(chat_id, "reset"), json={})
    assert resp.status_code == 200
    assert resp.json() == EMPTY
    assert await _stored(chat_id) is None
    body = (await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")).json()
    assert body["task_state"] == EMPTY


async def test_mutations_other_users_chat_is_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    chat_id = await _chat(authenticated_client)
    await _seed_doc(chat_id, _doc("g", ["a"]))
    other = second_authenticated_client
    assert (await other.put(_url(chat_id, "goal"), json={"goal": "x"})).status_code == 404
    assert (await other.delete(_url(chat_id, "items/1"))).status_code == 404
    assert (await other.post(_url(chat_id, "reset"), json={})).status_code == 404
    stored = await _stored(chat_id)
    assert stored is not None and stored.goal == "g" and len(stored.clarified) == 1


async def test_two_users_have_isolated_task_memory(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    mine = await _chat(authenticated_client)
    theirs = await _chat(second_authenticated_client)
    await authenticated_client.put(_url(mine, "goal"), json={"goal": "моя цель"})
    await second_authenticated_client.put(_url(theirs, "goal"), json={"goal": "чужая цель"})
    mine_body = (await authenticated_client.get(f"/api/v1/chats/{mine}/memory")).json()
    their_body = (await second_authenticated_client.get(f"/api/v1/chats/{theirs}/memory")).json()
    assert mine_body["task_state"]["goal"] == "моя цель"
    assert their_body["task_state"]["goal"] == "чужая цель"
    leak = await second_authenticated_client.get(f"/api/v1/chats/{mine}/memory")
    assert leak.status_code == 404


async def test_mutations_conflict_when_rag_off(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client, rag_on=False)
    responses = [
        await authenticated_client.put(_url(chat_id, "goal"), json={"goal": "x"}),
        await authenticated_client.delete(_url(chat_id, "items/1")),
        await authenticated_client.post(_url(chat_id, "reset"), json={}),
    ]
    for resp in responses:
        assert resp.status_code == 409
        assert resp.json()["detail"] == RAG_REQUIRED


async def test_mutations_reject_foreign_origin(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    await _seed_doc(chat_id, _doc("g", ["a"]))
    evil = {"Origin": "http://evil.example"}
    assert (
        await authenticated_client.put(_url(chat_id, "goal"), json={"goal": "x"}, headers=evil)
    ).status_code == 403
    assert (
        await authenticated_client.delete(_url(chat_id, "items/1"), headers=evil)
    ).status_code == 403
    assert (
        await authenticated_client.post(_url(chat_id, "reset"), json={}, headers=evil)
    ).status_code == 403
    stored = await _stored(chat_id)
    assert stored is not None and stored.goal == "g" and len(stored.clarified) == 1
    ok = await authenticated_client.put(
        _url(chat_id, "goal"), json={"goal": "y"}, headers={"Origin": CORS_ORIGINS[0]}
    )
    assert ok.status_code == 200


async def test_put_and_post_require_json_content_type(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    put = await authenticated_client.put(
        _url(chat_id, "goal"), content=b'{"goal": "x"}', headers={"Content-Type": "text/plain"}
    )
    post = await authenticated_client.post(
        _url(chat_id, "reset"), content=b"{}", headers={"Content-Type": "text/plain"}
    )
    assert put.status_code == 415
    assert post.status_code == 415
    assert await _stored(chat_id) is None


async def _add_message(
    chat_id: int, parent_id: int | None, role: str, snapshot: dict[str, Any] | None = None
) -> int:
    payload = serialize_rag_payload({"task_memory": snapshot}) if snapshot is not None else None
    async with async_session_factory() as session:
        message = Message(
            chat_id=chat_id, parent_id=parent_id, role=role, content=f"{role} text",
            rag_sources=payload,
        )
        session.add(message)
        await session.commit()
        await session.refresh(message)
        return message.id  # type: ignore[return-value]


async def _tree(chat_id: int) -> tuple[int, int, int, int]:
    """user1 -> assistant1 (S1) -> user2 -> assistant2 (S2); live row equals S2."""
    s1 = task_memory.build_snapshot(_doc("цель 1", ["a"]), None, failed=False)
    s2 = task_memory.build_snapshot(_doc("цель 2", ["a", "b"], ["c"]), None, failed=False)
    u1 = await _add_message(chat_id, None, "user")
    a1 = await _add_message(chat_id, u1, "assistant", s1)
    u2 = await _add_message(chat_id, a1, "user")
    a2 = await _add_message(chat_id, u2, "assistant", s2)
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        chat.current_leaf_message_id = a2
        session.add(chat)
        await session.commit()
    await _seed_doc(chat_id, _doc("цель 2", ["a", "b"], ["c"]))
    return u1, a1, u2, a2


async def _branch(client: AsyncClient, chat_id: int, message_id: int) -> int:
    resp = await client.post(f"/api/v1/chats/{chat_id}/branch", json={"message_id": message_id})
    return resp.status_code


async def test_branch_restores_older_and_newer_snapshot(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    _, a1, _, a2 = await _tree(chat_id)
    assert await _branch(authenticated_client, chat_id, a1) == 200
    doc = await _stored(chat_id)
    assert doc is not None and doc.goal == "цель 1" and [i.text for i in doc.clarified] == ["a"]
    assert doc.constraints == []
    assert await _branch(authenticated_client, chat_id, a2) == 200
    doc = await _stored(chat_id)
    assert doc is not None and doc.goal == "цель 2" and len(doc.clarified) == 2
    assert [i.text for i in doc.constraints] == ["c"]


async def test_branch_without_snapshot_clears_memory(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    u1 = await _add_message(chat_id, None, "user")
    a1 = await _add_message(chat_id, u1, "assistant")
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        chat.current_leaf_message_id = u1
        session.add(chat)
        await session.commit()
    await _seed_doc(chat_id, _doc("g", ["a"]))
    assert await _branch(authenticated_client, chat_id, a1) == 200
    assert await _stored(chat_id) is None
    body = (await authenticated_client.get(f"/api/v1/chats/{chat_id}/memory")).json()
    assert body["task_state"] == EMPTY


async def test_branch_skips_assistant_without_snapshot(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    s1 = task_memory.build_snapshot(_doc("старая цель", ["a"]), None, failed=False)
    u1 = await _add_message(chat_id, None, "user")
    a1 = await _add_message(chat_id, u1, "assistant", s1)
    u2 = await _add_message(chat_id, a1, "user")
    a2 = await _add_message(chat_id, u2, "assistant")
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        chat.current_leaf_message_id = u1
        session.add(chat)
        await session.commit()
    assert await _branch(authenticated_client, chat_id, a2) == 200
    doc = await _stored(chat_id)
    assert doc is not None and doc.goal == "старая цель"


async def test_noop_branch_keeps_manual_edit(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    _, _, _, a2 = await _tree(chat_id)
    resp = await authenticated_client.put(_url(chat_id, "goal"), json={"goal": "правка"})
    assert resp.status_code == 200
    assert await _branch(authenticated_client, chat_id, a2) == 200
    doc = await _stored(chat_id)
    assert doc is not None and doc.goal == "правка"


async def test_branch_restore_failure_keeps_leaf(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_id = await _chat(authenticated_client)
    _, a1, _, a2 = await _tree(chat_id)

    async def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("restore failed")

    monkeypatch.setattr(task_memory, "restore_from_path", boom)
    transport_client = authenticated_client
    from httpx import ASGITransport
    from httpx import AsyncClient as Client

    from agent.main import app

    async with Client(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as quiet:
        quiet.cookies = transport_client.cookies
        resp = await quiet.post(f"/api/v1/chats/{chat_id}/branch", json={"message_id": a1})
    assert resp.status_code == 500
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None and chat.current_leaf_message_id == a2
    doc = await _stored(chat_id)
    assert doc is not None and doc.goal == "цель 2"


async def test_branch_foreign_chat_and_message_are_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    chat_id = await _chat(authenticated_client)
    other_chat = await _chat(second_authenticated_client)
    _, a1, _, _ = await _tree(chat_id)
    other_msg = await _add_message(other_chat, None, "user")
    assert await _branch(second_authenticated_client, chat_id, a1) == 404
    assert await _branch(authenticated_client, chat_id, other_msg) == 404
