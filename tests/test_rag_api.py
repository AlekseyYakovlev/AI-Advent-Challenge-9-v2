"""Tests for per-chat RAG config routes, chunk snippets and rag_sources in the chat tree."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import kb_indexer, rag
from agent.rag import build_rag_payload, serialize_rag_payload
from kb_helpers import seed_kb
from shared.database import async_session_factory
from shared.models import Chat, KbChunk, KbDocument, KbStatus, KnowledgeBase, Message

READY_MSG = "База знаний ещё не готова"
KB_NOT_FOUND = "База знаний не найдена"
SEARCH_DEFAULTS: dict[str, Any] = {
    "candidate_k": 20, "threshold": None, "calibrated_threshold": None,
    "effective_threshold": 0.0, "threshold_source": "none",
    "lexical": False, "llm_rerank": False, "hybrid": False, "rewrite": False,
    "strict": True, "history_turns": 3,
}


async def _chat(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/chats", json={"title": "rag"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _kb(user_id: int, status: KbStatus = KbStatus.READY) -> int:
    return await seed_kb(user_id, status=status)


async def _add_chunk(kb_id: int, chunk_id: str = "doc.txt#0", source: str = "doc.txt") -> None:
    async with async_session_factory() as session:
        doc = (await session.exec(select(KbDocument).where(KbDocument.kb_id == kb_id))).first()
        session.add(
            KbChunk(
                kb_id=kb_id, document_id=doc.id, chunk_index=0, chunk_id=chunk_id,
                text="Текст фрагмента", section="Раздел", source=source, title="Заголовок",
                char_start=0, char_end=15,
            )
        )
        await session.commit()


async def test_config_defaults(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/rag")
    assert resp.status_code == 200
    assert resp.json() == {
        "chat_id": chat_id, "mode": "off", "kb_id": None,
        "kb_name": None, "kb_status": None, "top_k": 5, **SEARCH_DEFAULTS,
    }


async def test_config_default_strict_on(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    url = f"/api/v1/chats/{chat_id}/rag"
    assert (await authenticated_client.get(url)).json()["strict"] is True
    put = await authenticated_client.put(url, json={"mode": "rag", "kb_id": kb_id, "top_k": 5})
    assert put.status_code == 200
    assert put.json()["strict"] is True


async def test_put_strict_roundtrip_and_partial_update(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    url = f"/api/v1/chats/{chat_id}/rag"
    base = {"mode": "rag", "kb_id": kb_id, "top_k": 5}
    off = await authenticated_client.put(url, json={**base, "strict": False})
    assert off.json()["strict"] is False
    omitted = await authenticated_client.put(url, json=base)
    assert omitted.json()["strict"] is False
    assert (await authenticated_client.get(url)).json()["strict"] is False
    on = await authenticated_client.put(url, json={**base, "strict": True})
    assert on.json()["strict"] is True


async def test_config_put_roundtrip(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    url = f"/api/v1/chats/{chat_id}/rag"
    put = await authenticated_client.put(url, json={"mode": "rag", "kb_id": kb_id, "top_k": 7})
    assert put.status_code == 200
    got = (await authenticated_client.get(url)).json()
    assert got["mode"] == "rag" and got["kb_id"] == kb_id and got["top_k"] == 7
    assert got["kb_status"] == "ready" and got["kb_name"] == "Тестовая база"
    assert put.json() == got


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "rag", "top_k": 0},
        {"mode": "rag", "top_k": 21},
        {"mode": "strict"},
        {"mode": "rag", "candidate_k": 0},
        {"mode": "rag", "candidate_k": 51},
        {"mode": "rag", "threshold": -0.1},
        {"mode": "rag", "threshold": 1.5},
        {"mode": "rag", "lexical": "yes-string-not-bool"},
        {"mode": "rag", "strict": "yes"},
        {"mode": "rag", "strict": 1},
    ],
)
async def test_config_validation_422(authenticated_client: AsyncClient, body: dict[str, Any]) -> None:
    chat_id = await _chat(authenticated_client)
    resp = await authenticated_client.put(f"/api/v1/chats/{chat_id}/rag", json=body)
    assert resp.status_code == 422


@pytest.mark.parametrize("status", [KbStatus.QUEUED, KbStatus.INDEXING, KbStatus.FAILED])
async def test_config_rejects_unready_kb(authenticated_client: AsyncClient, status: KbStatus) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id, status)
    resp = await authenticated_client.put(
        f"/api/v1/chats/{chat_id}/rag", json={"mode": "rag", "kb_id": kb_id, "top_k": 5}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == READY_MSG


async def test_config_foreign_and_missing_kb_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    chat_id = await _chat(authenticated_client)
    foreign = await _kb(second_authenticated_client.seeded_user_id)
    url = f"/api/v1/chats/{chat_id}/rag"
    for kb_id in (foreign, 9999):
        resp = await authenticated_client.put(url, json={"mode": "rag", "kb_id": kb_id, "top_k": 5})
        assert resp.status_code == 404
        assert resp.json()["detail"] == KB_NOT_FOUND


async def test_config_null_kb_forces_off(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    resp = await authenticated_client.put(
        f"/api/v1/chats/{chat_id}/rag", json={"mode": "rag", "kb_id": None}
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "off"


async def test_config_off_keeps_kb_attached(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    resp = await authenticated_client.put(
        f"/api/v1/chats/{chat_id}/rag", json={"mode": "off", "kb_id": kb_id, "top_k": 5}
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "off" and resp.json()["kb_id"] == kb_id


async def test_config_foreign_chat_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    chat_id = await _chat(second_authenticated_client)
    url = f"/api/v1/chats/{chat_id}/rag"
    assert (await authenticated_client.get(url)).status_code == 404
    resp = await authenticated_client.put(url, json={"mode": "off"})
    assert resp.status_code == 404


async def test_config_put_origin_rejected(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    resp = await authenticated_client.put(
        f"/api/v1/chats/{chat_id}/rag",
        json={"mode": "off"},
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403


async def test_config_kb_deleted_sets_null(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    url = f"/api/v1/chats/{chat_id}/rag"
    await authenticated_client.put(url, json={"mode": "rag", "kb_id": kb_id, "top_k": 5})
    async with async_session_factory() as session:
        await kb_indexer.delete_kb(session, await session.get(KnowledgeBase, kb_id))
    got = (await authenticated_client.get(url)).json()
    assert got["kb_id"] is None and got["kb_name"] is None
    assert got["mode"] == "rag"


async def test_snippet_owner_ok(authenticated_client: AsyncClient) -> None:
    kb_id = await _kb(authenticated_client.seeded_user_id)
    await _add_chunk(kb_id)
    url = f"/api/v1/kb/{kb_id}/chunks/doc.txt%230"
    resp = await authenticated_client.get(url, params={"file": "doc.txt"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Текст фрагмента" and body["chunk_id"] == "doc.txt#0"
    assert (await authenticated_client.get(url)).status_code == 200


async def test_snippet_file_mismatch_404(authenticated_client: AsyncClient) -> None:
    kb_id = await _kb(authenticated_client.seeded_user_id)
    await _add_chunk(kb_id)
    resp = await authenticated_client.get(
        f"/api/v1/kb/{kb_id}/chunks/doc.txt%230", params={"file": "other.txt"}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Фрагмент не найден"


async def test_snippet_foreign_missing_unknown_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    foreign = await _kb(second_authenticated_client.seeded_user_id)
    await _add_chunk(foreign)
    own = await _kb(authenticated_client.seeded_user_id)
    assert (await authenticated_client.get(f"/api/v1/kb/{foreign}/chunks/doc.txt%230")).status_code == 404
    assert (await authenticated_client.get("/api/v1/kb/9999/chunks/doc.txt%230")).status_code == 404
    assert (await authenticated_client.get(f"/api/v1/kb/{own}/chunks/nope")).status_code == 404


async def _add_message(chat_id: int, role: str, rag_sources: str | None, parent: int | None) -> int:
    async with async_session_factory() as session:
        msg = Message(
            chat_id=chat_id, parent_id=parent, role=role, content="x", rag_sources=rag_sources
        )
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        chat = await session.get(Chat, chat_id)
        chat.current_leaf_message_id = msg.id
        session.add(chat)
        await session.commit()
        return msg.id


async def test_tree_exposes_rag_sources(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    payload = build_rag_payload(
        mode="rag", kb_id=1, kb_name="KB", top_k=5,
        sources=[{"chunk_id": "doc.txt#0", "source": "doc.txt"}],
        dropped=0, context_tokens=10, warning={"code": "w"},
    )
    m1 = await _add_message(chat_id, "user", None, None)
    m2 = await _add_message(chat_id, "assistant", serialize_rag_payload(payload), m1)
    m3 = await _add_message(chat_id, "assistant", "{broken", m2)
    resp = await authenticated_client.get(f"/api/v1/chats/{chat_id}/tree")
    assert resp.status_code == 200
    by_id = {m["id"]: m for m in resp.json()}
    assert by_id[m1]["rag_sources"] is None
    assert by_id[m2]["rag_sources"]["mode"] == "rag"
    assert by_id[m2]["rag_sources"]["sources"][0]["chunk_id"] == "doc.txt#0"
    assert by_id[m2]["rag_sources"]["warning"] == {"code": "w"}
    assert by_id[m3]["rag_sources"] is None


async def test_config_defaults_include_search_settings(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    body = (await authenticated_client.get(f"/api/v1/chats/{chat_id}/rag")).json()
    for key, value in SEARCH_DEFAULTS.items():
        assert body[key] == value


async def test_put_old_fields_keep_search_settings(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    url = f"/api/v1/chats/{chat_id}/rag"
    first = await authenticated_client.put(
        url,
        json={
            "mode": "rag", "kb_id": kb_id, "top_k": 5,
            "candidate_k": 30, "threshold": 0.55, "lexical": True,
        },
    )
    assert first.status_code == 200
    second = await authenticated_client.put(url, json={"mode": "rag", "kb_id": kb_id, "top_k": 6})
    body = second.json()
    assert body["candidate_k"] == 30
    assert body["threshold"] == 0.55
    assert body["threshold_source"] == "user"
    assert body["effective_threshold"] == 0.55
    assert body["lexical"] is True
    assert body["llm_rerank"] is False


async def test_put_threshold_null_resets_to_calibrated(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    monkeypatch.setattr(rag, "CALIBRATED_THRESHOLDS", {"nomic": 0.59})
    url = f"/api/v1/chats/{chat_id}/rag"
    base = {"mode": "rag", "kb_id": kb_id, "top_k": 5}
    await authenticated_client.put(url, json={**base, "threshold": 0.7})
    reset = (await authenticated_client.put(url, json={**base, "threshold": None})).json()
    assert reset["threshold"] is None
    assert reset["threshold_source"] == "calibrated"
    assert reset["effective_threshold"] == 0.59


async def test_candidate_k_clamped_to_top_k(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    url = f"/api/v1/chats/{chat_id}/rag"
    base = {"mode": "rag", "kb_id": kb_id}
    low = (await authenticated_client.put(url, json={**base, "top_k": 10, "candidate_k": 4})).json()
    assert low["candidate_k"] == 10
    await authenticated_client.put(url, json={**base, "top_k": 5, "candidate_k": 8})
    raised = (await authenticated_client.put(url, json={**base, "top_k": 15})).json()
    assert raised["candidate_k"] == 15


async def test_history_turns_default_zero_and_range(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client)
    kb_id = await _kb(authenticated_client.seeded_user_id)
    url = f"/api/v1/chats/{chat_id}/rag"
    assert (await authenticated_client.get(url)).json()["history_turns"] == 3
    base = {"mode": "rag", "kb_id": kb_id}
    stored = await authenticated_client.put(url, json={**base, "history_turns": 0})
    assert stored.json()["history_turns"] == 0
    assert (await authenticated_client.get(url)).json()["history_turns"] == 0
    kept = await authenticated_client.put(url, json=base)
    assert kept.json()["history_turns"] == 0
    changed = await authenticated_client.put(url, json={**base, "history_turns": 7})
    assert changed.json()["history_turns"] == 7
    for bad in (-1, 11, 2.5):
        resp = await authenticated_client.put(url, json={**base, "history_turns": bad})
        assert resp.status_code == 422


async def test_calibrated_threshold_follows_kb_model(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_id = await _chat(authenticated_client)
    user_id = authenticated_client.seeded_user_id
    bge_kb = await seed_kb(user_id, status=KbStatus.READY, model="text-embedding-bge-m3")
    other_kb = await seed_kb(user_id, status=KbStatus.READY, model="mystery-model")
    monkeypatch.setattr(rag, "CALIBRATED_THRESHOLDS", {"bge-m3": 0.59})
    url = f"/api/v1/chats/{chat_id}/rag"
    first = (await authenticated_client.put(url, json={"mode": "rag", "kb_id": bge_kb})).json()
    assert first["calibrated_threshold"] == 0.59
    assert first["effective_threshold"] == 0.59
    assert first["threshold_source"] == "calibrated"
    second = (await authenticated_client.put(url, json={"mode": "rag", "kb_id": other_kb})).json()
    assert second["calibrated_threshold"] is None
    assert second["effective_threshold"] == 0.0
    assert second["threshold_source"] == "none"
