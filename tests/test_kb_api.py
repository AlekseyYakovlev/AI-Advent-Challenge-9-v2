"""Tests for the knowledge-base REST API: create/validate/list/get/delete/search/embeddings."""

from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlmodel import select

from agent import kb_api, kb_indexer, kb_search
from agent.embeddings import MSG_LM_STUDIO_DOWN, MSG_MODEL_NOT_EMBEDDING
from agent.kb_api import (
    MSG_BAD_EXTENSION,
    MSG_BAD_STRATEGY,
    MSG_DUPLICATE,
    MSG_EMPTY_FILE,
    MSG_FILE_TOO_LARGE,
    MSG_FILES_REQUIRED,
    MSG_NAME_REQUIRED,
    MSG_NAME_TOO_LONG,
    MSG_TOO_MANY_FILES,
    MSG_TOTAL_TOO_LARGE,
)
from agent.kb_chunking import MSG_BAD_OVERLAP, MSG_SIZE_TOO_LARGE, MSG_SIZE_TOO_SMALL
from agent.kb_limits import MAX_REQUEST_BYTES
from kb_helpers import NOMIC, RU_TEXT, install_fake_embedder, vector_for, wait_until
from shared.config import settings
from shared.database import async_session_factory
from shared.kb_storage import kb_root, uploads_dir
from shared.models import KbDocument, KnowledgeBase

BASE = settings.LM_STUDIO_BASE_URL.rstrip("/")
GIGA = "giga-embeddings-instruct-480m-0826"
GOOD = {"name": "Моя база", "strategy": "fixed", "chunk_size": "1000", "chunk_overlap": "150",
        "embedding_model": NOMIC}


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Replace the background job with a recorder."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_index_job", lambda k, u: calls.append((k, u)))
    return calls


def _files(*items: tuple[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, data, "application/octet-stream")) for name, data in items]


async def _post(
    client: AsyncClient,
    files: list[Any] | None = None,
    headers: dict[str, str] | None = None,
    **overrides: str,
) -> httpx.Response:
    data = {**GOOD, **overrides}
    return await client.post("/api/v1/kb", data=data, files=files, headers=headers)


async def _assert_nothing_left() -> None:
    async with async_session_factory() as session:
        assert (await session.exec(select(KnowledgeBase))).all() == []
        assert (await session.exec(select(KbDocument))).all() == []
    assert not any(p.is_dir() and any(p.iterdir()) for p in kb_root().glob("*/*")) if kb_root().exists() else True


async def test_create_happy_path(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    resp = await _post(
        authenticated_client, _files(("a.txt", b"hello world"), ("b.md", b"# title\nbody"))
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["file_count"] == 2
    assert body["strategy"] == "fixed"
    assert (body["chunk_size"], body["chunk_overlap"]) == (1000, 150)
    assert spawned == [(body["id"], authenticated_client.seeded_user_id)]
    async with async_session_factory() as session:
        docs = (await session.exec(select(KbDocument))).all()
    assert sorted(d.filename for d in docs) == ["a.txt", "b.md"]
    target = uploads_dir(authenticated_client.seeded_user_id, body["id"])
    on_disk = sorted(p.name for p in target.iterdir())
    assert on_disk == sorted(d.stored_name for d in docs)
    assert all("a.txt" not in n and "b.md" not in n for n in on_disk)


@pytest.mark.parametrize(
    ("overrides", "files", "detail"),
    [
        ({"name": "  "}, [("a.txt", b"x")], MSG_NAME_REQUIRED),
        ({"name": "я" * 201}, [("a.txt", b"x")], MSG_NAME_TOO_LONG),
        ({"strategy": "weird"}, [("a.txt", b"x")], MSG_BAD_STRATEGY),
        ({"chunk_size": "99"}, [("a.txt", b"x")], MSG_SIZE_TOO_SMALL),
        ({"chunk_size": "2001"}, [("a.txt", b"x")], MSG_SIZE_TOO_LARGE),
        ({"chunk_size": "300", "chunk_overlap": "151"}, [("a.txt", b"x")], MSG_BAD_OVERLAP),
        ({"chunk_overlap": "-1"}, [("a.txt", b"x")], MSG_BAD_OVERLAP),
        ({}, [], MSG_FILES_REQUIRED),
        ({}, [(f"f{i}.txt", bytes([65 + i])) for i in range(11)], MSG_TOO_MANY_FILES),
        ({}, [("a.docx", b"x")], MSG_BAD_EXTENSION.format(name="a.docx")),
        ({}, [("a.txt", b"")], MSG_EMPTY_FILE.format(name="a.txt")),
        ({}, [("a.txt", b"same"), ("b.txt", b"same")], MSG_DUPLICATE.format(name="b.txt")),
    ],
    ids=["name-empty", "name-long", "strategy", "size-small", "size-large", "overlap-high",
         "overlap-negative", "no-files", "too-many", "extension", "empty-file", "duplicate"],
)
async def test_validation_rejections_leave_nothing(
    authenticated_client: AsyncClient,
    spawned: list[tuple[int, int]],
    overrides: dict[str, str],
    files: list[tuple[str, bytes]],
    detail: str,
) -> None:
    resp = await _post(authenticated_client, _files(*files) if files else None, **overrides)
    assert resp.status_code == 422
    assert resp.json()["detail"] == detail
    assert spawned == []
    await _assert_nothing_left()


async def test_missing_model_rejected(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    resp = await _post(authenticated_client, _files(("a.txt", b"x")), embedding_model=" ")
    assert resp.status_code == 422
    await _assert_nothing_left()


async def test_per_file_cap(
    authenticated_client: AsyncClient,
    spawned: list[tuple[int, int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kb_api, "MAX_FILE_BYTES", 1000)
    resp = await _post(authenticated_client, _files(("big.txt", b"x" * 1001)))
    assert resp.status_code == 422
    assert resp.json()["detail"] == MSG_FILE_TOO_LARGE.format(name="big.txt")
    await _assert_nothing_left()


async def test_total_cap(
    authenticated_client: AsyncClient,
    spawned: list[tuple[int, int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kb_api, "MAX_TOTAL_BYTES", 1500)
    resp = await _post(
        authenticated_client, _files(("a.txt", b"a" * 800), ("b.txt", b"b" * 800))
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == MSG_TOTAL_TOO_LARGE
    await _assert_nothing_left()


async def test_structural_ignores_size_and_overlap(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    resp = await _post(
        authenticated_client,
        _files(("a.txt", b"x")),
        strategy="structural",
        chunk_size="5",
        chunk_overlap="9999",
    )
    assert resp.status_code == 202
    assert (resp.json()["chunk_size"], resp.json()["chunk_overlap"]) == (2000, 0)


async def test_path_traversal_filename_is_sanitised(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    resp = await _post(authenticated_client, _files(("../../evil.txt", b"data")))
    assert resp.status_code == 202
    async with async_session_factory() as session:
        docs = (await session.exec(select(KbDocument))).all()
    assert [d.filename for d in docs] == ["evil.txt"]
    root = kb_root().resolve()
    for path in root.parent.rglob("evil.txt"):
        raise AssertionError(f"unexpected file {path}")
    for path in root.rglob("*"):
        assert root in path.resolve().parents or path.resolve() == root


async def test_origin_checks(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    bad = await _post(
        authenticated_client, _files(("a.txt", b"x")), headers={"Origin": "http://evil.example"}
    )
    assert bad.status_code == 403
    good = await _post(
        authenticated_client, _files(("a.txt", b"x")), headers={"Origin": "http://localhost:8000"}
    )
    assert good.status_code == 202


async def test_declared_content_length_over_cap(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    resp = await authenticated_client.post(
        "/api/v1/kb",
        data=GOOD,
        files=_files(("a.txt", b"x")),
        headers={"Content-Length": str(MAX_REQUEST_BYTES + 1)},
    )
    assert resp.status_code == 413


async def test_list_and_get(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    first = (await _post(authenticated_client, _files(("a.txt", b"one")), name="Первая")).json()
    second = (await _post(authenticated_client, _files(("a.txt", b"two")), name="Вторая")).json()
    listing = (await authenticated_client.get("/api/v1/kb")).json()
    assert [kb["id"] for kb in listing] == [second["id"], first["id"]]
    got = await authenticated_client.get(f"/api/v1/kb/{first['id']}")
    assert got.status_code == 200 and got.json()["name"] == "Первая"
    assert (await authenticated_client.get("/api/v1/kb/9999")).status_code == 404


async def test_delete_queued_removes_everything(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    kb = (await _post(authenticated_client, _files(("a.txt", b"one")))).json()
    resp = await authenticated_client.delete(f"/api/v1/kb/{kb['id']}")
    assert resp.status_code == 204
    await _assert_nothing_left()
    assert not uploads_dir(authenticated_client.seeded_user_id, kb["id"]).exists()


async def test_end_to_end_index_and_search(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_embedder(monkeypatch)

    async def fake_query(model_id: str, text: str, *args: object) -> list[float]:
        return vector_for(text)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    resp = await _post(
        authenticated_client,
        _files(("doc.txt", RU_TEXT.encode("utf-8"))),
        chunk_size="200",
        chunk_overlap="20",
    )
    assert resp.status_code == 202
    kb_id = resp.json()["id"]

    async def ready() -> bool:
        return (await authenticated_client.get(f"/api/v1/kb/{kb_id}")).json()["status"] == "ready"

    await wait_until(ready, timeout=10)
    search = await authenticated_client.post(
        f"/api/v1/kb/{kb_id}/search", json={"query": "пример текста", "top_k": 5}
    )
    assert search.status_code == 200
    results = search.json()["results"]
    assert len(results) == 5
    for key in ("rank", "score", "chunk_id", "source", "section", "page", "text"):
        assert key in results[0]
    assert results[0]["source"] == "doc.txt"


async def test_search_not_ready_and_bad_bodies(
    authenticated_client: AsyncClient, spawned: list[tuple[int, int]]
) -> None:
    kb = (await _post(authenticated_client, _files(("a.txt", b"x")))).json()
    url = f"/api/v1/kb/{kb['id']}/search"
    resp = await authenticated_client.post(url, json={"query": "q"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Поиск доступен после завершения индексации"
    assert (await authenticated_client.post(url, json={"query": "q", "top_k": 21})).status_code == 422
    assert (await authenticated_client.post(url, json={"query": ""})).status_code == 422


def _models_payload() -> dict[str, Any]:
    return {
        "data": [
            {"id": NOMIC, "type": "embeddings", "state": "loaded"},
            {"id": GIGA, "type": "llm", "state": "loaded"},
        ]
    }


@respx.mock
async def test_embedding_models(authenticated_client: AsyncClient) -> None:
    respx.get(f"{BASE}/api/v0/models").mock(return_value=httpx.Response(200, json=_models_payload()))
    resp = await authenticated_client.get("/api/v1/kb/embedding-models")
    assert resp.status_code == 200
    by_id = {m["id"]: m for m in resp.json()}
    assert by_id[NOMIC]["eligible"] is True and by_id[GIGA]["eligible"] is False


@respx.mock
async def test_embedding_models_lm_studio_down(authenticated_client: AsyncClient) -> None:
    respx.get(f"{BASE}/api/v0/models").mock(side_effect=httpx.ConnectError("down"))
    resp = await authenticated_client.get("/api/v1/kb/embedding-models")
    assert resp.status_code == 503
    assert resp.json()["detail"] == MSG_LM_STUDIO_DOWN


@respx.mock
async def test_embedding_check_rejects_llm_model(authenticated_client: AsyncClient) -> None:
    respx.get(f"{BASE}/api/v0/models").mock(return_value=httpx.Response(200, json=_models_payload()))
    resp = await authenticated_client.post("/api/v1/kb/embedding-check", json={"model": GIGA})
    assert resp.status_code == 422
    assert resp.json()["detail"] == MSG_MODEL_NOT_EMBEDDING.format(model=GIGA, type="llm")


@respx.mock
async def test_embedding_check_ok(authenticated_client: AsyncClient) -> None:
    respx.get(f"{BASE}/api/v0/models").mock(return_value=httpx.Response(200, json=_models_payload()))
    respx.post(f"{BASE}/v1/embeddings").mock(
        return_value=httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1] * 768}]}
        )
    )
    resp = await authenticated_client.post("/api/v1/kb/embedding-check", json={"model": NOMIC})
    assert resp.status_code == 200
    assert resp.json() == {"model": NOMIC, "dim": 768}


@respx.mock
async def test_embedding_check_lm_studio_down(authenticated_client: AsyncClient) -> None:
    respx.get(f"{BASE}/api/v0/models").mock(side_effect=httpx.ConnectError("down"))
    resp = await authenticated_client.post("/api/v1/kb/embedding-check", json={"model": NOMIC})
    assert resp.status_code == 503


async def test_embedding_check_requires_json_content_type(
    authenticated_client: AsyncClient,
) -> None:
    resp = await authenticated_client.post(
        "/api/v1/kb/embedding-check", content=b'{"model": "x"}', headers={"Content-Type": "text/plain"}
    )
    assert resp.status_code == 415


async def test_delete_mid_job_cancels_and_cleans(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running job is cancelled by delete; a trailing failed frame is tolerated."""
    import asyncio

    block = asyncio.Event()
    install_fake_embedder(monkeypatch, block=block)
    resp = await _post(authenticated_client, _files(("doc.txt", RU_TEXT.encode("utf-8"))))
    kb_id = resp.json()["id"]

    async def embedding() -> bool:
        return (await authenticated_client.get(f"/api/v1/kb/{kb_id}")).json()["phase"] == "embedding"

    await wait_until(embedding)
    assert (await authenticated_client.delete(f"/api/v1/kb/{kb_id}")).status_code == 204
    assert (await authenticated_client.get(f"/api/v1/kb/{kb_id}")).status_code == 404
    await _assert_nothing_left()
    assert kb_indexer.kb_jobs == {}
