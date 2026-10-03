"""Tests for the offline RAG evaluation script: scoring, rendering and a fake end-to-end run."""

import csv
import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

from agent import kb_search, rag
from agent.kb_indexer import run_index_job
from agent.llm_client import ChatCompletionResult
from kb_helpers import install_fake_embedder, seed_kb, seed_user, vector_for
from shared.database import async_session_factory

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rag_eval", REPO_ROOT / "scripts" / "rag_eval.py")
rag_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_eval)

FZ = "19951210_20260626_FZ_N_196_FZ.pdf"
KOAP = "Kodex_N_195-FZ.pdf"


def _chunk(source: str, section: str, rank: int = 1) -> dict[str, Any]:
    return {
        "rank": rank,
        "score": 0.9 - rank / 100,
        "chunk_id": f"1-{rank}",
        "source": source,
        "title": None,
        "section": section,
        "page": rank,
        "text": f"text {rank}",
    }


def test_parse_article() -> None:
    assert rag_eval.parse_article("ФЗ-196 > Глава 4 > Статья 19. Подготовка водителей") == "19"
    assert rag_eval.parse_article("Статья 12.9. Превышение") == "12.9"
    assert rag_eval.parse_article("Статья 5. A > Статья 12.9. B") == "12.9"
    assert rag_eval.parse_article(None) is None
    assert rag_eval.parse_article("Глава 4") is None


def test_chunk_hits_requires_file_and_exact_article() -> None:
    chunk = _chunk(FZ, "ФЗ > Статья 19. Подготовка")
    assert rag_eval.chunk_hits(chunk, {"file_contains": "FZ_N_196_FZ", "article": "19"}) is True
    assert rag_eval.chunk_hits(chunk, {"file_contains": "FZ_N_196_FZ", "article": "19.1"}) is False
    assert rag_eval.chunk_hits(chunk, {"file_contains": "FZ_N_196_FZ", "article": "1"}) is False
    koap = _chunk(KOAP, "Статья 19. Другое")
    assert rag_eval.chunk_hits(koap, {"file_contains": "FZ_N_196_FZ", "article": "19"}) is False


def test_chunk_hits_falls_back_to_title() -> None:
    chunk = _chunk(FZ, "")
    chunk["section"] = None
    chunk["title"] = "Статья 26. Возраст"
    assert rag_eval.chunk_hits(chunk, {"file_contains": "FZ", "article": "26"}) is True


def test_hit_at_k_and_rank() -> None:
    chunks = [_chunk(KOAP, "Статья 1. x", 1), _chunk(FZ, "Статья 26. y", 2), _chunk(FZ, "Статья 17. z", 3)]
    expected = [{"file_contains": "FZ_N_196_FZ", "article": "26"}]
    assert rag_eval.hit_at_k(chunks, expected, 1) is False
    assert rag_eval.hit_at_k(chunks, expected, 3) is True
    assert rag_eval.first_hit_rank(chunks, expected) == 2
    assert rag_eval.hit_at_k(chunks, [], 3) is None
    assert rag_eval.first_hit_rank(chunks, [{"file_contains": "FZ", "article": "99"}]) is None


def test_all_hit_at_k_requires_every_source() -> None:
    chunks = [_chunk(FZ, "Статья 19. a", 1), _chunk(KOAP, "Статья 12.37. b", 2)]
    both = [
        {"file_contains": "FZ_N_196_FZ", "article": "19"},
        {"file_contains": "N_195", "article": "12.37"},
    ]
    assert rag_eval.all_hit_at_k(chunks, both, 1) is False
    assert rag_eval.all_hit_at_k(chunks, both, 2) is True
    assert rag_eval.all_hit_at_k(chunks, [], 2) is None


def test_load_fixture_requires_frozen(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"status": "draft", "questions": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        rag_eval.load_fixture(path, require_frozen=True)
    assert exc.value.code == 2
    assert rag_eval.load_fixture(path, require_frozen=False)["status"] == "draft"
    assert len(rag_eval.fixture_sha256(path)) == 64


def test_render_retrieval_table() -> None:
    rows = [
        {
            "id": "Q01",
            "category": "direct",
            "kb": {"a": {"hit1": True, "hit3": True, "hit5": True, "hitk": True, "rank": 1, "top1": 0.5}},
        },
        {
            "id": "Q09",
            "category": "out_of_corpus",
            "kb": {"a": {"hit1": None, "hit3": None, "hit5": None, "hitk": None, "rank": None, "top1": 0.25}},
        },
    ]
    table = rag_eval.render_retrieval_table(rows, ["a"], 5)
    lines = table.strip().splitlines()
    assert "a hit@1" in lines[0] and "a hit@5" in lines[0] and "a top1 score" in lines[0]
    assert "| Q01 | direct | 1 | 1 | 1 | 1 | 1 | 0.500 |" == lines[2]
    assert "| Q09 | out_of_corpus | n/a | n/a | n/a | n/a | n/a | 0.250 |" == lines[3]


def test_render_answers_csv_has_empty_verdict() -> None:
    rows = [
        {"id": "Q01", "category": "direct", "mode": "rag", "kb": "a", "answer": "18, [1]", "cited_sources": "x"}
    ]
    parsed = list(csv.reader(io.StringIO(rag_eval.render_answers_csv(rows))))
    assert parsed[0] == ["id", "category", "mode", "kb", "answer", "cited_sources", "verdict", "comment"]
    assert parsed[1][6] == "" and parsed[1][7] == ""
    assert parsed[1][4] == "18, [1]"


def test_render_answers_table_truncates() -> None:
    rows = [{"id": "Q1", "category": "direct", "mode": "off", "kb": "", "answer": "я" * 500}]
    table = rag_eval.render_answers_table(rows)
    assert "я" * 300 in table and "я" * 301 not in table


def test_importing_module_creates_no_db() -> None:
    assert not (REPO_ROOT / "eval_out" / "day22" / "eval.db").exists()


DOC = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}


def _fixture(tmp_path: Path) -> Path:
    questions = [
        {
            "id": f"Q0{i}",
            "category": category,
            "question": f"Вопрос {i}?",
            "expected_answer": "x",
            "expected_sources": [{"file_contains": "doc", "article": "1"}] if category != "out_of_corpus" else [],
        }
        for i, category in ((1, "direct"), (2, "synthesis"), (3, "out_of_corpus"))
    ]
    path = tmp_path / "control_set.json"
    path.write_text(
        json.dumps({"version": 1, "status": "frozen", "questions": questions}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class FakeClient:
    """Records every completion request."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_chat_detailed(
        self, messages: list[dict[str, Any]], model: str, temperature: float = 0.0,
        max_tokens: int = 1024, extra_body: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
        return ChatCompletionResult("<think>hm</think>Ответ [1] и [9]", "stop", True, 7)


async def _ready_kbs(monkeypatch: pytest.MonkeyPatch, count: int) -> list[int]:
    install_fake_embedder(monkeypatch)

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for(query)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    user_id = await seed_user()
    ids: list[int] = []
    for _ in range(count):
        kb_id = await seed_kb(user_id, DOC, chunk_size=120, chunk_overlap=10)
        await run_index_job(kb_id, user_id)
        ids.append(kb_id)
    return ids


def _opts(tmp_path: Path, kb: dict[str, int], **kwargs: Any) -> Any:
    return rag_eval.EvalOptions(kb=kb, fixture=_fixture(tmp_path), out=tmp_path / "out", **kwargs)


async def test_run_eval_writes_raw_and_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (kb_id,) = await _ready_kbs(monkeypatch, 1)
    client = FakeClient()
    opts = _opts(tmp_path, {"a": kb_id})
    await rag_eval.run_eval(opts, client, async_session_factory)
    names = sorted(p.name for p in (opts.out / "raw").iterdir())
    assert names == sorted(
        [f"off_none_Q0{i}.json" for i in (1, 2, 3)] + [f"rag_a_Q0{i}.json" for i in (1, 2, 3)]
    )
    for name in ("retrieval.md", "answers.md", "answers.csv", "run_meta.json"):
        assert (opts.out / name).exists()
    rag_call = next(c for c in client.calls if "=== Фрагменты из базы знаний" in c["messages"][-1]["content"])
    assert "Вопрос: " in rag_call["messages"][-1]["content"]
    assert len([c for c in client.calls if "Фрагменты" not in c["messages"][-1]["content"]]) == 3
    assert all(c["temperature"] == 0.0 for c in client.calls)
    raw = json.loads((opts.out / "raw" / "rag_a_Q01.json").read_text(encoding="utf-8"))
    assert raw["answer"] == "Ответ [1] и [9]"
    assert len(raw["cited_sources"]) == 1 and raw["fragments"]
    assert "text" not in raw["chunks"][0]
    meta = json.loads((opts.out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["temperature"] == 0.0 and meta["top_k"] == 5 and meta["modes"] == ["off", "rag"]
    assert len(meta["fixture_sha256"]) == 64
    assert meta["kbs"]["a"]["chunk_size"] == 120 and meta["kbs"]["a"]["dim"] == 8
    assert meta["kbs"]["a"]["strategy"] == "fixed" and meta["identical_chunking"] is True
    rows = list(csv.DictReader(io.StringIO((opts.out / "answers.csv").read_text(encoding="utf-8"))))
    assert len(rows) == 6 and all(row["verdict"] == "" for row in rows)


async def test_run_eval_only_kb_accumulates_across_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first, second = await _ready_kbs(monkeypatch, 2)
    kb = {"a": first, "b": second}
    client = FakeClient()
    opts = _opts(tmp_path, kb, only_kb="a")
    await rag_eval.run_eval(opts, client, async_session_factory)
    raw = opts.out / "raw"
    assert sorted(p.name for p in raw.glob("rag_*")) == [f"rag_a_Q0{i}.json" for i in (1, 2, 3)]
    assert len(list(raw.glob("off_none_*"))) == 3
    opts_b = _opts(tmp_path, kb, only_kb="b", modes=["rag"])
    await rag_eval.run_eval(opts_b, client, async_session_factory)
    assert len(list(raw.glob("rag_b_*"))) == 3 and len(list(raw.glob("off_none_*"))) == 3
    retrieval = (opts.out / "retrieval.md").read_text(encoding="utf-8")
    assert "a hit@1" in retrieval and "b hit@1" in retrieval
    meta = json.loads((opts.out / "run_meta.json").read_text(encoding="utf-8"))
    assert set(meta["kbs"]) == {"a", "b"} and meta["modes"] == ["off", "rag"]


async def test_run_eval_records_retrieval_failure_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (kb_id,) = await _ready_kbs(monkeypatch, 1)

    async def failing(*args: object) -> list[dict[str, Any]]:
        raise rag.RagFailure("embedder_unavailable", "down")

    monkeypatch.setattr(rag, "retrieve", failing)
    client = FakeClient()
    opts = _opts(tmp_path, {"a": kb_id}, modes=["rag"])
    await rag_eval.run_eval(opts, client, async_session_factory)
    raw = json.loads((opts.out / "raw" / "rag_a_Q02.json").read_text(encoding="utf-8"))
    assert raw["retrieval"]["warning"] == "embedder_unavailable"
    assert len(client.calls) == 3
    assert all("Фрагменты" not in c["messages"][-1]["content"] for c in client.calls)


async def test_run_eval_refuses_draft_fixture(tmp_path: Path) -> None:
    path = tmp_path / "draft.json"
    path.write_text(json.dumps({"status": "draft", "questions": []}), encoding="utf-8")
    opts = rag_eval.EvalOptions(kb={"a": 1}, fixture=path, out=tmp_path / "out")
    with pytest.raises(SystemExit) as exc:
        await rag_eval.run_eval(opts, FakeClient(), async_session_factory)
    assert exc.value.code == 2


def test_cli_parses_both_subcommands() -> None:
    parser = rag_eval.build_parser()
    run = parser.parse_args(["run", "--kb", "a=1", "--only-kb", "a"])
    assert run.model == "qwen/qwen3.5-9b" and run.top_k == 5 and run.modes == "off,rag"
    build = parser.parse_args(["build-kbs"])
    assert build.strategy == "structural" and build.chunk_size == 1000
