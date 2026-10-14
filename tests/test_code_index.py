"""Tests for the incremental code knowledge base built from a git base branch."""

import asyncio
import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from agent import code_index, kb_search
from agent.code_index import (
    CODE_BASE_UNAVAILABLE,
    CODE_EMBEDDER_UNAVAILABLE,
    CodeIndexError,
    is_indexable,
    refresh_code_index,
)
from kb_helpers import (
    NOMIC,
    FakeEmbedder,
    chunk_rows,
    document_rows,
    get_kb,
    seed_user,
    vector_for,
    wait_until,
)
from shared.database import async_session_factory
from shared.kb_storage import index_path, read_index_bytes
from shared.models import KbChunk, KbStatus, KbStrategy, KnowledgeBase, ReviewCodeIndex
from test_code_git import Repos, run_git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

REPO_KEY = "aleksey/demo"


@pytest.fixture(autouse=True)
def _fresh_code_index_state() -> None:
    code_index.reset_state()


@pytest.fixture
def repos(tmp_path: Path) -> Repos:
    return Repos(tmp_path)


def install_embedder(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> FakeEmbedder:
    """Replace model loading and embedding of the code index with a recording fake."""
    fake = FakeEmbedder(**kwargs)
    monkeypatch.setattr(code_index, "ensure_embedding_model", fake.ensure)
    monkeypatch.setattr(code_index, "embed_passages", fake.embed)
    return fake


def seed_repo(repos: Repos) -> str:
    """Three indexable files and one excluded file on main."""
    return repos.commit(
        {
            "agent/ws.py": "def handle():\n    return 'ws'\n",
            "ui/static/app.js": "function start() {\n  return 1;\n}\n",
            "pytest.ini": "[pytest]\nasyncio_mode = auto\n",
            "README.md": "# Demo\n",
        }
    )


async def refresh(
    user_id: int,
    repos: Repos,
    base_ref: str = "main",
    expected: str | None = None,
) -> code_index.CodeIndexState:
    return await refresh_code_index(user_id, str(repos.work), base_ref, expected, NOMIC)


async def links(user_id: int | None = None) -> list[ReviewCodeIndex]:
    async with async_session_factory() as session:
        statement = select(ReviewCodeIndex)
        if user_id is not None:
            statement = statement.where(ReviewCodeIndex.user_id == user_id)
        return list((await session.exec(statement)).all())


async def all_kbs() -> list[KnowledgeBase]:
    async with async_session_factory() as session:
        return list((await session.exec(select(KnowledgeBase))).all())


# ------------------------------------------------------------ is_indexable


@pytest.mark.parametrize(
    "path,size",
    [
        ("agent/ws.py", 100),
        ("ui/static/app.js", 100),
        ("ui/static/index.html", 100),
        ("pytest.ini", 10),
        ("requirements.txt", 10),
        (".github/workflows/pr-review.yml", 100),
        ("tests/test_rag.py", 100),
        ("data/config.json", 5_000),
    ],
)
def test_is_indexable_accepts(path: str, size: int) -> None:
    assert is_indexable(path, size)


@pytest.mark.parametrize(
    "path,size",
    [
        ("README.md", 10),
        ("docs/API_SPEC.md", 10),
        (".planning/STATE.md", 10),
        ("eval_out/x.json", 10),
        ("tests/fixtures/review/prs/a.py", 10),
        (".env", 10),
        (".env.example", 10),
        ("app.db", 10),
        ("key.pem", 10),
        ("x.min.js", 10),
        ("package-lock.json", 10),
        ("a.png", 10),
        ("big.py", 300_000),
        ("big.json", 50_000),
        ("web/node_modules/pkg/index.js", 10),
    ],
)
def test_is_indexable_rejects(path: str, size: int) -> None:
    assert not is_indexable(path, size)


# ------------------------------------------------------------- first build


async def test_first_refresh_builds_a_ready_base(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    tip = seed_repo(repos)
    state = await refresh(user_id, repos)
    assert state.commit == tip and state.changed == 3 and state.removed == 0
    assert state.file_count == 3 and state.repo_key == REPO_KEY
    kb = await get_kb(state.kb_id)
    assert kb is not None and kb.user_id == user_id
    assert kb.status == KbStatus.READY and kb.strategy == KbStrategy.FIXED
    assert kb.file_count == 3 and kb.dim is not None
    link = (await links(user_id))[0]
    assert link.indexed_commit == tip and link.kb_id == kb.id and link.base_ref == "main"
    docs = await document_rows(kb.id)
    assert sorted(d.filename for d in docs) == ["agent/ws.py", "pytest.ini", "ui/static/app.js"]
    assert all(len(d.sha256) == 40 for d in docs)
    chunks = await chunk_rows(kb.id)
    assert len(chunks) == kb.chunk_count > 0
    assert read_index_bytes(index_path(user_id, kb.id)).ntotal == len(chunks)


async def test_unpushed_working_tree_edit_does_not_change_the_index(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    await refresh(user_id, repos)
    (repos.work / "agent" / "ws.py").write_text("def other():\n    return 0\n")
    (repos.work / "agent" / "new.py").write_text("x = 1\n")
    state = await refresh(user_id, repos)
    assert state.changed == 0 and state.removed == 0


# ------------------------------------------------------------- incremental


async def test_refresh_without_new_commit_embeds_nothing(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    fake = install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    first = await refresh(user_id, repos)
    before = [c.id for c in await chunk_rows(first.kb_id)]
    calls = len(fake.batch_sizes)
    second = await refresh(user_id, repos)
    assert second.changed == 0 and second.removed == 0 and second.kb_id == first.kb_id
    assert len(fake.batch_sizes) == calls
    assert [c.id for c in await chunk_rows(first.kb_id)] == before


async def test_edit_add_delete_updates_only_the_difference(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    fake = install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    first = await refresh(user_id, repos)
    old_rows = {c.source: c.id for c in await chunk_rows(first.kb_id)}
    fake.batch_sizes.clear()
    tip = repos.commit(
        {
            "agent/ws.py": "def handle():\n    return 'changed websocket'\n",
            "agent/review.py": "def review_pull_request():\n    return 'verdict'\n",
            "ui/static/app.js": None,
        }
    )
    state = await refresh(user_id, repos)
    assert state.commit == tip and state.changed == 2 and state.removed == 1
    chunks = await chunk_rows(first.kb_id)
    assert sum(fake.batch_sizes) == sum(1 for c in chunks if c.source != "pytest.ini")
    assert not any(c.source == "ui/static/app.js" for c in chunks)
    assert {d.filename for d in await document_rows(first.kb_id)} == {
        "agent/ws.py", "agent/review.py", "pytest.ini",
    }  # fmt: skip
    kept = [c.id for c in chunks if c.source == "pytest.ini"]
    assert kept == [old_rows["pytest.ini"]]
    assert read_index_bytes(index_path(user_id, first.kb_id)).ntotal == len(chunks)

    async def fake_query(model: str, text: str, base: object, prefix: object) -> list[float]:
        return vector_for(text)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    target = next(c for c in chunks if c.source == "agent/review.py")
    kb = await get_kb(first.kb_id)
    assert kb is not None
    async with async_session_factory() as session:
        found = await kb_search.search_kb(session, kb, target.text, 3)
    assert found[0]["source"] == "agent/review.py"


async def test_commit_that_only_deletes_a_file(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    fake = install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    first = await refresh(user_id, repos)
    fake.batch_sizes.clear()
    repos.commit({"pytest.ini": None})
    state = await refresh(user_id, repos)
    assert state.changed == 0 and state.removed == 1
    assert fake.batch_sizes == []
    chunks = await chunk_rows(first.kb_id)
    assert not any(c.source == "pytest.ini" for c in chunks)
    assert read_index_bytes(index_path(user_id, first.kb_id)).ntotal == len(chunks)
    assert len(await document_rows(first.kb_id)) == 2


# ---------------------------------------------------------------- failures


async def test_embedder_failure_keeps_the_previous_index(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    first_tip = seed_repo(repos)
    first = await refresh(user_id, repos)
    path = index_path(user_id, first.kb_id)
    index_bytes = path.read_bytes()
    chunk_ids = [c.id for c in await chunk_rows(first.kb_id)]
    docs = [(d.id, d.sha256) for d in await document_rows(first.kb_id)]
    install_embedder(monkeypatch, fail_on_batch=1)
    repos.commit({"agent/ws.py": "def handle():\n    return 'new'\n", "pytest.ini": None})
    with pytest.raises(CodeIndexError) as exc:
        await refresh(user_id, repos)
    assert exc.value.code == CODE_EMBEDDER_UNAVAILABLE and exc.value.detail
    assert path.read_bytes() == index_bytes
    assert not path.with_suffix(".pending").exists()
    assert [c.id for c in await chunk_rows(first.kb_id)] == chunk_ids
    assert [(d.id, d.sha256) for d in await document_rows(first.kb_id)] == docs
    assert (await links(user_id))[0].indexed_commit == first_tip


async def test_failed_first_build_leaves_nothing_behind(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch, fail_on_batch=1)
    user_id = await seed_user()
    seed_repo(repos)
    with pytest.raises(CodeIndexError) as exc:
        await refresh(user_id, repos)
    assert exc.value.code == CODE_EMBEDDER_UNAVAILABLE
    assert await all_kbs() == [] and await links() == []


async def test_failure_inside_the_commit_rolls_back_a_first_build(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(code_index, "write_index_pending", boom)
    with pytest.raises(OSError):
        await refresh(user_id, repos)
    assert await all_kbs() == [] and await links() == []
    assert not index_path(user_id, 1).parent.exists()


async def test_repository_mismatch_is_refused_before_any_fetch(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)

    async def no_fetch(*args: object) -> str:
        raise AssertionError("fetch must not run")

    monkeypatch.setattr(code_index, "fetch_base_commit", no_fetch)
    with pytest.raises(CodeIndexError) as exc:
        await refresh(user_id, repos, expected="other/repo")
    assert exc.value.code == CODE_BASE_UNAVAILABLE
    assert "other/repo" in exc.value.detail and REPO_KEY in exc.value.detail


async def test_expected_repository_match_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    state = await refresh(user_id, repos, expected="Aleksey/Demo")
    assert state.repo_key == REPO_KEY


async def test_unknown_base_branch_is_base_unavailable(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    with pytest.raises(CodeIndexError) as exc:
        await refresh(user_id, repos, base_ref="nope")
    assert exc.value.code == CODE_BASE_UNAVAILABLE


# ------------------------------------------------------- several base refs


async def test_a_different_base_ref_gets_its_own_base(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    main_state = await refresh(user_id, repos)
    run_git(repos.work, "checkout", "-q", "-b", "Day32")
    repos.commit({"agent/extra.py": "def extra():\n    return 1\n"})
    day_state = await refresh(user_id, repos, base_ref="Day32")
    assert day_state.kb_id != main_state.kb_id
    assert day_state.file_count == 4 and main_state.file_count == 3
    main_kb = await get_kb(main_state.kb_id)
    assert main_kb is not None and main_kb.file_count == 3
    assert len(await links(user_id)) == 2


async def test_sixth_base_ref_removes_the_least_recently_updated(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    states = []
    for number in range(6):
        run_git(repos.work, "push", "-q", "origin", f"HEAD:refs/heads/b{number}")
        states.append(await refresh(user_id, repos, base_ref=f"b{number}"))
    kept = {link.base_ref for link in await links(user_id)}
    assert kept == {f"b{number}" for number in range(1, 6)}
    assert await get_kb(states[0].kb_id) is None
    assert not index_path(user_id, states[0].kb_id).exists()
    assert await get_kb(states[5].kb_id) is not None


async def test_concurrent_refreshes_of_one_base_stay_consistent(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    first, second = await asyncio.gather(refresh(user_id, repos), refresh(user_id, repos))
    assert first.kb_id == second.kb_id
    assert len(await all_kbs()) == 1 and len(await links(user_id)) == 1
    docs = await document_rows(first.kb_id)
    assert len({d.filename for d in docs}) == len(docs) == 3
    chunks = await chunk_rows(first.kb_id)
    assert read_index_bytes(index_path(user_id, first.kb_id)).ntotal == len(chunks)


async def test_two_users_are_isolated(monkeypatch: pytest.MonkeyPatch, repos: Repos) -> None:
    install_embedder(monkeypatch)
    alice = await seed_user("alice")
    bob = await seed_user("bob")
    seed_repo(repos)
    a_state = await refresh(alice, repos)
    b_state = await refresh(bob, repos)
    assert a_state.kb_id != b_state.kb_id
    repos.commit({"agent/ws.py": "def handle():\n    return 'later'\n"})
    await refresh(alice, repos)
    async with async_session_factory() as session:
        a_link = await code_index.latest_index(session, alice)
        b_link = await code_index.latest_index(session, bob, REPO_KEY)
        none_link = await code_index.latest_index(session, bob, "other/repo")
    assert a_link is not None and a_link.kb_id == a_state.kb_id
    assert b_link is not None and b_link.kb_id == b_state.kb_id
    assert none_link is None
    bob_docs = {d.filename: d.sha256 for d in await document_rows(b_state.kb_id)}
    alice_docs = {d.filename: d.sha256 for d in await document_rows(a_state.kb_id)}
    assert bob_docs["agent/ws.py"] != alice_docs["agent/ws.py"]
    async with async_session_factory() as session:
        foreign = (
            await session.exec(select(KbChunk).where(KbChunk.kb_id == b_state.kb_id))
        ).all()
        owner = await session.get(KnowledgeBase, b_state.kb_id)
    assert foreign and owner is not None and owner.user_id == bob


# ----------------------------------------------------------------- the job


async def test_job_reports_progress_and_rejects_a_second_start(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    block = asyncio.Event()
    install_embedder(monkeypatch, block=block)
    user_id = await seed_user()
    seed_repo(repos)
    assert code_index.spawn_refresh_job(user_id, str(repos.work), "main", NOMIC) is True
    assert code_index.get_refresh_progress(user_id).running is True
    assert code_index.spawn_refresh_job(user_id, str(repos.work), "main", NOMIC) is False
    await wait_until(lambda: code_index.get_refresh_progress(user_id).phase == "embed")
    block.set()
    await wait_until(lambda: not code_index.get_refresh_progress(user_id).running)
    progress = code_index.get_refresh_progress(user_id)
    assert progress.error is None
    assert len(await links(user_id)) == 1


async def test_job_failure_is_stored_as_a_readable_error(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)
    assert code_index.spawn_refresh_job(user_id, str(repos.work), "missing", NOMIC) is True
    await wait_until(lambda: not code_index.get_refresh_progress(user_id).running)
    progress = code_index.get_refresh_progress(user_id)
    assert progress.error and "missing" in progress.error
    assert code_index.spawn_refresh_job(user_id, str(repos.work), "main", NOMIC) is True
    await wait_until(lambda: not code_index.get_refresh_progress(user_id).running)
    assert code_index.get_refresh_progress(user_id).error is None


async def test_job_unexpected_exception_gets_a_fixed_text(
    monkeypatch: pytest.MonkeyPatch, repos: Repos
) -> None:
    install_embedder(monkeypatch)
    user_id = await seed_user()
    seed_repo(repos)

    async def boom(*args: object) -> str:
        raise RuntimeError("secret path /home/x")

    monkeypatch.setattr(code_index, "fetch_base_commit", boom)
    code_index.spawn_refresh_job(user_id, str(repos.work), "main", NOMIC)
    await wait_until(lambda: not code_index.get_refresh_progress(user_id).running)
    progress = code_index.get_refresh_progress(user_id)
    assert progress.error == code_index.MSG_UNEXPECTED


def test_module_never_opens_files() -> None:
    source = Path(code_index.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "open(" not in code and "read_text" not in code and "read_bytes" not in code
