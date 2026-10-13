"""Tests for POST /api/v1/kb/{kb_id}/files: admission, limits, classification and the add flow."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import kb_api, kb_search
from agent.events import hub
from agent.kb_api import (
    MSG_ADD_BUSY,
    MSG_ADD_NOT_READY,
    MSG_BAD_EXTENSION,
    MSG_DUPLICATE,
    MSG_EMPTY_FILE,
    MSG_FILE_TOO_LARGE,
    MSG_FILES_REQUIRED,
    MSG_TOO_MANY_FILES,
    MSG_TOTAL_TOO_LARGE,
)
from agent.kb_indexer import AddItem
from agent.kb_limits import MAX_FILES
from kb_helpers import (
    NOMIC,
    chunk_rows,
    document_rows,
    get_kb,
    install_fake_embedder,
    seed_kb,
    set_kb_phase,
    vector_for,
    wait_until,
)
from shared.database import async_session_factory
from shared.kb_storage import staging_root, uploads_dir
from shared.models import KbDocument, KbStatus

CREATE_FORM = {
    "name": "База",
    "strategy": "fixed",
    "chunk_size": "200",
    "chunk_overlap": "20",
    "embedding_model": NOMIC,
}


def _text(word: str) -> bytes:
    return (f"Документ про {word}. " * 40).encode("utf-8")


def _files(*items: tuple[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, data, "application/octet-stream")) for name, data in items]


Calls = list[tuple[int, int, Path, list[AddItem], int]]


@pytest.fixture
def add_spawned(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Replace the background addition with a recorder."""
    calls: Calls = []
    monkeypatch.setattr(
        kb_api.kb_indexer,
        "spawn_add_job",
        lambda kb_id, user_id, staging, items, unchanged: calls.append(
            (kb_id, user_id, staging, items, unchanged)
        ),
    )
    return calls


async def _add(
    client: AsyncClient,
    kb_id: int,
    files: list[Any] | None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.post(f"/api/v1/kb/{kb_id}/files", files=files, headers=headers)


async def _ready_base(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]
) -> int:
    """Create a base through the real route and job; returns its id once ready."""
    install_fake_embedder(monkeypatch)

    async def fake_query(model_id: str, text: str, *args: object) -> list[float]:
        return vector_for(text)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    resp = await client.post(
        "/api/v1/kb", data=CREATE_FORM, files=_files(*files.items())
    )
    assert resp.status_code == 202
    kb_id = resp.json()["id"]

    async def ready() -> bool:
        body = (await client.get(f"/api/v1/kb/{kb_id}")).json()
        return body["status"] == "ready"

    await wait_until(ready, timeout=10)
    return kb_id


def _assert_no_staging(user_id: int, kb_id: int) -> None:
    root = staging_root(user_id, kb_id)
    assert not root.exists() or not any(root.iterdir())


async def _docs(kb_id: int) -> list[KbDocument]:
    return await document_rows(kb_id)


async def _ready_seeded(user_id: int) -> int:
    return await seed_kb(user_id, status=KbStatus.READY)


async def test_add_to_missing_kb_is_404(
    authenticated_client: AsyncClient, add_spawned: Calls
) -> None:
    resp = await _add(authenticated_client, 9999, _files(("a.txt", b"x")))
    assert resp.status_code == 404
    assert add_spawned == []


async def test_add_requires_origin(
    authenticated_client: AsyncClient, add_spawned: Calls
) -> None:
    kb_id = await _ready_seeded(authenticated_client.seeded_user_id)
    resp = await _add(
        authenticated_client,
        kb_id,
        _files(("a.txt", b"x")),
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403
    assert add_spawned == []
    resp = await _add(
        authenticated_client,
        kb_id,
        _files(("a.txt", b"x")),
        headers={"Origin": "http://localhost:8000"},
    )
    assert resp.status_code == 202


async def test_add_declared_length_over_cap_is_413(
    authenticated_client: AsyncClient, add_spawned: Calls
) -> None:
    kb_id = await _ready_seeded(authenticated_client.seeded_user_id)
    resp = await authenticated_client.post(
        f"/api/v1/kb/{kb_id}/files",
        files=_files(("a.txt", b"x")),
        headers={"Content-Length": str(10**10)},
    )
    assert resp.status_code in (400, 413)
    assert add_spawned == []


@pytest.mark.parametrize("state", [KbStatus.QUEUED, KbStatus.INDEXING, KbStatus.FAILED])
async def test_add_rejected_unless_ready(
    authenticated_client: AsyncClient, add_spawned: Calls, state: KbStatus
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await seed_kb(user_id, status=state)
    resp = await _add(authenticated_client, kb_id, _files(("a.txt", b"x")))
    assert resp.status_code == 409
    assert resp.json()["detail"] == MSG_ADD_NOT_READY
    assert add_spawned == []
    _assert_no_staging(user_id, kb_id)


async def test_add_rejected_while_adding(
    authenticated_client: AsyncClient, add_spawned: Calls
) -> None:
    kb_id = await _ready_seeded(authenticated_client.seeded_user_id)
    await set_kb_phase(kb_id, "adding")
    resp = await _add(authenticated_client, kb_id, _files(("a.txt", b"x")))
    assert resp.status_code == 409
    assert resp.json()["detail"] == MSG_ADD_BUSY
    assert add_spawned == []


async def test_claim_is_exclusive(
    authenticated_client: AsyncClient, add_spawned: Calls
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_seeded(user_id)
    first = await _add(authenticated_client, kb_id, _files(("a.txt", b"first")))
    assert first.status_code == 202
    assert (await get_kb(kb_id)).phase == "adding"
    second = await _add(authenticated_client, kb_id, _files(("b.txt", b"second")))
    assert second.status_code == 409
    assert second.json()["detail"] == MSG_ADD_BUSY
    assert len(add_spawned) == 1
    dirs = list(staging_root(user_id, kb_id).iterdir())
    assert dirs == [add_spawned[0][2]]


async def test_claim_race_loser_cleans_up(
    authenticated_client: AsyncClient, add_spawned: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_seeded(user_id)

    async def lost(session: Any, kb: int) -> bool:
        return False

    monkeypatch.setattr(kb_api, "_claim_kb_for_add", lost)
    resp = await _add(authenticated_client, kb_id, _files(("a.txt", b"x")))
    assert resp.status_code == 409
    assert resp.json()["detail"] == MSG_ADD_BUSY
    assert add_spawned == []
    _assert_no_staging(user_id, kb_id)
    assert (await get_kb(kb_id)).phase is None


async def test_claim_released_when_spawn_fails(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_seeded(user_id)

    def boom(*args: object) -> None:
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", boom)
    with pytest.raises(RuntimeError):
        await _add(authenticated_client, kb_id, _files(("a.txt", b"x")))
    assert (await get_kb(kb_id)).phase is None
    _assert_no_staging(user_id, kb_id)


def _many_files() -> list[Any]:
    return _files(*[(f"f{i}.txt", b"x") for i in range(MAX_FILES + 1)])


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("none", MSG_FILES_REQUIRED),
        ("too_many", MSG_TOO_MANY_FILES),
        ("extension", MSG_BAD_EXTENSION.format(name="a.docx")),
        ("empty", MSG_EMPTY_FILE.format(name="e.txt")),
        ("duplicate", MSG_DUPLICATE.format(name="a.txt")),
        ("per_file", MSG_FILE_TOO_LARGE.format(name="big.txt")),
        ("total", MSG_TOTAL_TOO_LARGE),
    ],
)
async def test_add_validation_leaves_nothing(
    authenticated_client: AsyncClient,
    add_spawned: Calls,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_seeded(user_id)
    before = [d.id for d in await _docs(kb_id)]
    files: list[Any] | None
    if case == "none":
        files = None
    elif case == "too_many":
        files = _many_files()
    elif case == "extension":
        files = _files(("a.docx", b"x"))
    elif case == "empty":
        files = _files(("e.txt", b""))
    elif case == "duplicate":
        files = _files(("a.txt", b"x"), ("a.txt", b"y"))
    elif case == "per_file":
        monkeypatch.setattr(kb_api, "MAX_FILE_BYTES", 1000)
        files = _files(("big.txt", b"x" * 1001))
    else:
        monkeypatch.setattr(kb_api, "MAX_TOTAL_BYTES", 1500)
        files = _files(("a.txt", b"x" * 900), ("b.txt", b"y" * 900))
    resp = await _add(authenticated_client, kb_id, files)
    assert resp.status_code == 422
    assert resp.json()["detail"] == message
    assert add_spawned == []
    _assert_no_staging(user_id, kb_id)
    assert (await get_kb(kb_id)).phase is None
    assert [d.id for d in await _docs(kb_id)] == before


async def test_new_source_is_added(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"README.md": _text("один")})
    calls: Calls = []
    monkeypatch.setattr(
        kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args)
    )
    resp = await _add(authenticated_client, kb_id, _files(("docs/API_SPEC.md", _text("два"))))
    assert resp.status_code == 202
    (_, _, _, items, unchanged) = calls[0]
    assert [(i.source, i.replaces) for i in items] == [("docs/API_SPEC.md", ())]
    assert unchanged == 0


async def test_changed_content_is_replaced(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"README.md": _text("один")})
    old_id = (await _docs(kb_id))[0].id
    calls: Calls = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args))
    resp = await _add(authenticated_client, kb_id, _files(("README.md", _text("другое"))))
    assert resp.status_code == 202
    items, unchanged = calls[0][3], calls[0][4]
    assert [(i.source, i.replaces) for i in items] == [("README.md", (old_id,))]
    assert unchanged == 0


async def test_same_content_is_unchanged(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"README.md": _text("один")})
    calls: Calls = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args))
    resp = await _add(authenticated_client, kb_id, _files(("README.md", _text("один"))))
    assert resp.status_code == 202
    staging, items, unchanged = calls[0][2], calls[0][3], calls[0][4]
    assert items == []
    assert unchanged == 1
    assert list(staging.iterdir()) == []
    assert staging.parent == staging_root(user_id, kb_id)


async def test_mixed_request_counts(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ready_base(
        authenticated_client,
        monkeypatch,
        {"a.md": _text("а"), "b.md": _text("б")},
    )
    calls: Calls = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args))
    resp = await _add(
        authenticated_client,
        kb_id,
        _files(("new.md", _text("новый")), ("a.md", _text("изменён")), ("b.md", _text("б"))),
    )
    assert resp.status_code == 202
    items, unchanged = calls[0][3], calls[0][4]
    assert [i.source for i in items] == ["new.md", "a.md"]
    assert unchanged == 1


async def test_same_content_under_new_name_is_added(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"README.md": _text("один")})
    calls: Calls = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args))
    resp = await _add(authenticated_client, kb_id, _files(("docs/copy.md", _text("один"))))
    assert resp.status_code == 202
    assert [(i.source, i.replaces) for i in calls[0][3]] == [("docs/copy.md", ())]
    assert calls[0][4] == 0


async def test_same_basename_other_folder_is_added(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ready_base(
        authenticated_client, monkeypatch, {"docs/a/README.md": _text("один")}
    )
    calls: Calls = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args))
    resp = await _add(authenticated_client, kb_id, _files(("docs/b/README.md", _text("два"))))
    assert resp.status_code == 202
    assert [(i.source, i.replaces) for i in calls[0][3]] == [("docs/b/README.md", ())]


async def test_absent_files_are_not_touched(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ready_base(
        authenticated_client, monkeypatch, {"a.md": _text("а"), "b.md": _text("б")}
    )
    docs = {d.filename: d.id for d in await _docs(kb_id)}
    calls: Calls = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args))
    resp = await _add(authenticated_client, kb_id, _files(("a.md", _text("новое"))))
    assert resp.status_code == 202
    replaced = {doc_id for item in calls[0][3] for doc_id in item.replaces}
    assert replaced == {docs["a.md"]}
    assert docs["b.md"] not in replaced


async def test_legacy_duplicate_names_are_all_replaced(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"a.md": _text("а")})
    first = (await _docs(kb_id))[0]
    async with async_session_factory() as session:
        twin = KbDocument(
            kb_id=kb_id,
            filename="a.md",
            sha256=first.sha256,
            size_bytes=first.size_bytes,
            stored_name="twin.md",
        )
        session.add(twin)
        await session.commit()
        await session.refresh(twin)
    calls: Calls = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args))
    resp = await _add(authenticated_client, kb_id, _files(("a.md", _text("а"))))
    assert resp.status_code == 202
    assert calls[0][3][0].replaces == tuple(sorted((first.id, twin.id)))
    assert calls[0][4] == 0


async def test_add_does_not_insert_rows_or_touch_uploads(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"README.md": _text("один")})
    docs_before = [d.id for d in await _docs(kb_id)]
    uploads = uploads_dir(user_id, kb_id)
    files_before = sorted(p.name for p in uploads.iterdir() if p.is_file())
    calls: Calls = []
    monkeypatch.setattr(kb_api.kb_indexer, "spawn_add_job", lambda *args: calls.append(args))
    resp = await _add(authenticated_client, kb_id, _files(("docs/guide.md", _text("гайд"))))
    assert resp.status_code == 202
    assert [d.id for d in await _docs(kb_id)] == docs_before
    assert sorted(p.name for p in uploads.iterdir() if p.is_file()) == files_before
    staged = list(calls[0][2].iterdir())
    assert len(staged) == 1
    assert "docs" not in staged[0].name and "guide" not in staged[0].name


def _drain(queue: asyncio.Queue[Any]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames


async def _wait_idle(client: AsyncClient, kb_id: int, seen: list[dict[str, Any]]) -> None:
    async def idle() -> bool:
        body = (await client.get(f"/api/v1/kb/{kb_id}")).json()
        seen.append(body)
        return body["phase"] is None

    await wait_until(idle, timeout=10)


async def test_add_flow_keeps_old_and_finds_new(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"README.md": _text("один")})
    queue = hub.subscribe(user_id)
    try:
        resp = await _add(authenticated_client, kb_id, _files(("docs/guide.txt", _text("гайд"))))
        assert resp.status_code == 202
        assert resp.json()["status"] == "ready"
        assert resp.json()["phase"] == "adding"
        seen: list[dict[str, Any]] = []
        await _wait_idle(authenticated_client, kb_id, seen)
        frames = _drain(queue)
    finally:
        hub.unsubscribe(user_id, queue)
    assert all(body["status"] == "ready" for body in seen)
    assert (await authenticated_client.get(f"/api/v1/kb/{kb_id}")).json()["file_count"] == 2
    old = await authenticated_client.post(
        f"/api/v1/kb/{kb_id}/search", json={"query": _text("один").decode(), "top_k": 1}
    )
    assert old.json()["results"][0]["source"] == "README.md"
    new = await authenticated_client.post(
        f"/api/v1/kb/{kb_id}/search", json={"query": _text("гайд").decode(), "top_k": 1}
    )
    assert new.json()["results"][0]["source"] == "docs/guide.txt"
    assert frames[-1]["add_summary"] == {"added": 1, "replaced": 0, "unchanged": 0}


async def test_replace_flow_reports_replaced_one(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"docs/a.md": _text("старое")})
    queue = hub.subscribe(user_id)
    try:
        resp = await _add(authenticated_client, kb_id, _files(("docs/a.md", _text("новое"))))
        assert resp.status_code == 202
        await _wait_idle(authenticated_client, kb_id, [])
        frames = _drain(queue)
    finally:
        hub.unsubscribe(user_id, queue)
    body = (await authenticated_client.get(f"/api/v1/kb/{kb_id}")).json()
    assert body["file_count"] == 1
    assert [d.filename for d in await _docs(kb_id)] == ["docs/a.md"]
    search = await authenticated_client.post(
        f"/api/v1/kb/{kb_id}/search", json={"query": _text("новое").decode(), "top_k": 1}
    )
    assert search.json()["results"][0]["source"] == "docs/a.md"
    assert all("старое" not in chunk.text for chunk in await chunk_rows(kb_id))
    assert frames[-1]["add_summary"] == {"added": 0, "replaced": 1, "unchanged": 0}


async def test_unchanged_flow_reports_unchanged(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"a.md": _text("один")})
    before = [(c.id, c.text) for c in await chunk_rows(kb_id)]
    queue = hub.subscribe(user_id)
    try:
        resp = await _add(authenticated_client, kb_id, _files(("a.md", _text("один"))))
        assert resp.status_code == 202
        await _wait_idle(authenticated_client, kb_id, [])
        frames = _drain(queue)
    finally:
        hub.unsubscribe(user_id, queue)
    assert frames[-1]["add_summary"] == {"added": 0, "replaced": 0, "unchanged": 1}
    assert [(c.id, c.text) for c in await chunk_rows(kb_id)] == before


async def test_failed_add_flow_keeps_base(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_id = await _ready_base(authenticated_client, monkeypatch, {"a.md": _text("один")})
    fake = install_fake_embedder(monkeypatch, fail_on_batch=1)
    before = [(c.id, c.text) for c in await chunk_rows(kb_id)]
    resp = await _add(authenticated_client, kb_id, _files(("b.md", _text("два"))))
    assert resp.status_code == 202
    await _wait_idle(authenticated_client, kb_id, [])
    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.READY
    assert kb.phase is None
    assert kb.error == "Сбой эмбеддингов в тесте."
    assert kb.file_count == 1
    assert [(c.id, c.text) for c in await chunk_rows(kb_id)] == before
    search = await authenticated_client.post(
        f"/api/v1/kb/{kb_id}/search", json={"query": _text("один").decode(), "top_k": 1}
    )
    assert search.json()["results"][0]["source"] == "a.md"

    fake.fail_on_batch = None
    retry = await _add(authenticated_client, kb_id, _files(("b.md", _text("два"))))
    assert retry.status_code == 202
    assert retry.json()["error"] is None
    await _wait_idle(authenticated_client, kb_id, [])
    assert (await get_kb(kb_id)).file_count == 2
    async with async_session_factory() as session:
        assert len((await session.exec(select(KbDocument).where(KbDocument.kb_id == kb_id))).all()) == 2
