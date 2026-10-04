"""Tests for the Day 24 cite run of the offline RAG evaluation script, with fakes only."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from agent import kb_search, rag
from agent.kb_indexer import run_index_job
from agent.llm_client import ChatCompletionResult
from agent.rag_cite import STRICT_INSTRUCTION
from kb_helpers import install_fake_embedder, seed_kb, seed_user, vector_for
from shared.database import async_session_factory

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rag_eval_cite", REPO_ROOT / "scripts" / "rag_eval.py")
rag_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_eval)

DOC = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}
FRAGMENT = "Водитель обязан иметь при себе водительское удостоверение и регистрационные документы."
STRICT_ANSWER = f"Нужны документы [1].\n\nЦитаты:\n[1] «{FRAGMENT}»"


class FakeClient:
    """Records completion requests and returns a scripted answer."""

    def __init__(self, answer: str = STRICT_ANSWER, finish_reason: str = "stop") -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self.answer = answer
        self.finish_reason = finish_reason

    async def complete_chat_detailed(
        self, messages: list[dict[str, Any]], model: str, temperature: float = 0.0,
        max_tokens: int = 1024, extra_body: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        self.calls.append(messages)
        return ChatCompletionResult(self.answer, self.finish_reason, False, 11)


def _chunk() -> dict[str, Any]:
    return {
        "rank": 1,
        "score": 0.8,
        "chunk_id": "1-1",
        "source": "doc.txt",
        "title": None,
        "section": "Статья 1",
        "page": 1,
        "text": FRAGMENT,
    }


def _trace(below: bool) -> dict[str, Any]:
    return {
        "verdict": "below_threshold" if below else "ok",
        "config": {"candidate_k": 2, "top_k": 5, "threshold": 0.59},
        "stages": ["threshold"], "stage_ms": {}, "skipped": [], "latency_ms": 1,
        "candidates": [
            {"chunk_id": "1-1", "file": "doc.txt", "section": "Статья 1", "rank_before": 1,
             "rank_after": None if below else 1, "cos": 0.4 if below else 0.8,
             "status": "below_threshold" if below else "in_answer"},
        ],
    }


@pytest.fixture
def scripted_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent import rag_pipeline

    async def fake(session: Any, kb: Any, question: str, config: Any, client: Any = None, model: Any = None) -> Any:
        index = int(question.split()[1].rstrip("?"))
        if index == 3:
            return [], _trace(True)
        return [_chunk()], _trace(False)

    monkeypatch.setattr(rag_pipeline, "run_retrieval_pipeline", fake)


async def _ready_kb(monkeypatch: pytest.MonkeyPatch) -> int:
    install_fake_embedder(monkeypatch)

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for(query)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, DOC, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    return kb_id


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


def _opts(tmp_path: Path, kb_id: int, **kwargs: Any) -> Any:
    return rag_eval.EvalOptions(
        kb={"a": kb_id}, fixture=_fixture(tmp_path), out=tmp_path / "out", max_tokens=8192, **kwargs
    )


def test_classify_reply_kinds() -> None:
    from agent.rag_cite import CitationResult

    ok = {"answer": "text", "error": None}
    assert rag_eval.classify_reply(True, True, None, {"answer": "Не знаю", "error": None}) == "gated"
    assert rag_eval.classify_reply(True, False, None, {"answer": "", "error": "X"}) == "error"
    assert rag_eval.classify_reply(True, False, None, {"answer": "  ", "error": None}) == "empty"
    assert rag_eval.classify_reply(True, False, CitationResult("Не знаю", model_idk=True), ok) == "model_idk"
    assert rag_eval.classify_reply(True, False, CitationResult("text"), ok) == "answer"
    assert rag_eval.classify_reply(False, False, None, ok) == "answer"
    assert rag_eval.classify_reply(False, False, None, {"answer": "", "finish_reason": "length", "error": None}) == "empty"


async def test_strict_below_threshold_makes_no_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    client = FakeClient()
    records = await rag_eval.run_cite(
        _opts(tmp_path, kb_id), client, async_session_factory, ["strict"], kb_id, 20, 0.59
    )
    gated = next(r for r in records if r["id"] == "Q03")
    assert gated["kind"] == "gated" and gated["gated"] is True
    assert gated["answer"].startswith("Не знаю") and gated["finish_reason"] is None
    assert len(client.calls) == 2


async def test_strict_off_always_calls_client_and_has_no_quotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    client = FakeClient(answer="Ответ [1]")
    records = await rag_eval.run_cite(
        _opts(tmp_path, kb_id), client, async_session_factory, ["strict_off"], kb_id, 20, 0.59
    )
    assert len(client.calls) == 3
    assert all(r["quotes"] == [] and r["kind"] == "answer" for r in records)
    below = next(r for r in records if r["id"] == "Q03")
    assert rag.NO_FRAGMENTS_INSTRUCTION in below["messages"][-1]["content"]
    first = next(r for r in records if r["id"] == "Q01")
    assert rag.RAG_INSTRUCTION in first["messages"][-1]["content"]
    assert STRICT_INSTRUCTION not in first["messages"][-1]["content"]


async def test_strict_record_has_clean_answer_quotes_and_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    records = await rag_eval.run_cite(
        _opts(tmp_path, kb_id), FakeClient(), async_session_factory, ["strict"], kb_id, 20, 0.59
    )
    first = next(r for r in records if r["id"] == "Q01")
    assert first["messages"][-1]["content"].count(STRICT_INSTRUCTION) == 1
    assert "Цитаты:" in first["raw_answer"] and "Цитаты:" not in first["answer"]
    assert first["kind"] == "answer" and first["cite_verdict"] == "ok"
    assert [q["state"] for q in first["quotes"]] == ["exact"]
    assert first["quote_counts"] == {"exact": 1, "fuzzy": 0, "unverified": 0, "auto": 0}
    assert first["sources"][0]["file"] == "doc.txt"


async def test_empty_answer_is_its_own_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    records = await rag_eval.run_cite(
        _opts(tmp_path, kb_id), FakeClient(answer="", finish_reason="length"),
        async_session_factory, ["strict"], kb_id, 20, 0.59,
    )
    first = next(r for r in records if r["id"] == "Q01")
    assert first["kind"] == "empty" and first["quotes"] == []


async def test_run_cite_writes_raw_files_in_run_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    opts = _opts(tmp_path, kb_id)
    records = await rag_eval.run_cite(
        opts, FakeClient(), async_session_factory, ["strict", "strict_off"], kb_id, 20, 0.59
    )
    assert [(r["run"], r["id"]) for r in records] == [
        (run, f"Q0{i}") for run in ("strict", "strict_off") for i in (1, 2, 3)
    ]
    loaded = rag_eval._load_raw(opts.out / "raw")
    assert len(loaded) == 6
    assert {r["run"] for r in loaded} == {"strict", "strict_off"}


def _raw(run: str, qid: str, category: str, kind: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run": run, "id": qid, "category": category, "kind": kind, "answer": "a",
        "sources": [{"rank": 1}], "quotes": [], "quote_counts": {"exact": 0, "fuzzy": 0, "unverified": 0, "auto": 0},
        "invalid_refs": 0, "completion_tokens": 10, "finish_reason": "stop",
    }
    return {**base, **extra}


def test_cite_metrics_rates_false_refusals_and_empty() -> None:
    rows = [
        _raw("strict", "Q01", "direct", "answer",
             quotes=[{"state": "exact", "auto": False, "rank": 1, "text": "t"}],
             quote_counts={"exact": 1, "fuzzy": 0, "unverified": 0, "auto": 0}),
        _raw("strict", "Q02", "direct", "gated"),
        _raw("strict", "Q03", "direct", "empty", finish_reason="length"),
        _raw("strict", "Q04", "synthesis", "answer",
             quote_counts={"exact": 0, "fuzzy": 1, "unverified": 2, "auto": 3}),
        _raw("strict", "Q09", "out_of_corpus", "gated"),
        _raw("strict", "Q10", "out_of_corpus", "model_idk"),
    ]
    metrics = rag_eval.cite_metrics(rows)
    assert metrics["false_refusals"] == 1 and metrics["false_refusal_ids"] == ["Q02"]
    assert metrics["answerable_total"] == 4 and metrics["false_refusal_rate"] == 0.25
    assert metrics["empty"] == 1
    assert metrics["ooc_idk"] == 2 and metrics["ooc_gated"] == 1 and metrics["ooc_model_idk"] == 1
    assert metrics["ooc_idk_rate"] == 1.0
    assert metrics["quotes"] == {"exact": 1, "fuzzy": 1, "unverified": 2, "auto": 3}


def test_empty_answer_is_not_a_refusal_nor_missing_quotes() -> None:
    empty = _raw("strict", "Q03", "out_of_corpus", "empty", finish_reason="length")
    assert rag_eval._idk_correct(empty) == "нет"
    assert rag_eval._quotes_present(empty) == "—"
    assert "ответ не получен (length)" in rag_eval._kind_label(empty)
    assert rag_eval.cite_metrics([empty])["ooc_idk"] == 0


def test_render_cite_md_and_summary_columns() -> None:
    raws = [
        _raw("strict", "Q01", "direct", "answer",
             quotes=[{"state": "exact", "auto": False, "rank": 1, "text": "t"}],
             quote_counts={"exact": 1, "fuzzy": 0, "unverified": 0, "auto": 0}),
        _raw("strict", "Q09", "out_of_corpus", "gated", sources=[]),
        _raw("strict_off", "Q01", "direct", "answer"),
    ]
    table = rag_eval.render_cite_md(raws, "strict")
    assert "источники есть" in table and "корректное «не знаю»" in table
    lines = table.strip().splitlines()
    assert len(lines) == 4
    assert "да (модель: 1 exact, 0 fuzzy, 0 unverified; авто: 0)" in lines[2]
    assert lines[3].count("—") >= 2 and "| да |" in lines[3]
    summary = rag_eval.render_cite_summary_md(raws).strip().splitlines()
    assert len(summary) == 4 and summary[2].startswith("| strict |") and summary[3].startswith("| strict_off |")


async def test_rerender_keeps_manual_and_judge_cells(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    import csv
    import io

    kb_id = await _ready_kb(monkeypatch)
    opts = _opts(tmp_path, kb_id)
    raws = await rag_eval.run_cite(
        opts, FakeClient(), async_session_factory, ["strict"], kb_id, 20, 0.59
    )
    fixture = rag_eval.load_fixture(opts.fixture, require_frozen=True)
    rag_eval._render_cite_outputs(opts, fixture, {}, raws)
    path = opts.out / "answers.csv"
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert list(rows[0]) == [
        "id", "category", "run", "kind", "answer", "quotes", "sources_present", "quotes_present",
        "idk_correct", "verdict", "comment", "judge_verdict", "judge_comment",
    ]
    rows[0]["verdict"], rows[0]["comment"], rows[1]["judge_verdict"] = "да", "ok", "нет"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    rag_eval._render_cite_outputs(opts, fixture, {}, raws)
    again = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert again[0]["verdict"] == "да" and again[0]["comment"] == "ok"
    assert again[1]["judge_verdict"] == "нет"
    assert "да" in (opts.out / "cite_strict.md").read_text(encoding="utf-8")


async def test_render_only_needs_no_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    opts = _opts(tmp_path, kb_id)
    await rag_eval.run_cite(opts, FakeClient(), async_session_factory, ["strict"], kb_id, 20, 0.59)

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("render-only must not build a client")

    monkeypatch.setattr("agent.llm_client.LLMClient", boom)
    args = rag_eval.build_parser().parse_args(
        ["cite", "--render-only", "--out", str(opts.out), "--fixture", str(opts.fixture)]
    )
    assert await rag_eval.cite_command(args) == 0
    for name in ("cite_strict.md", "cite_summary.md", "answers.csv", "answers.md", "run_meta.json"):
        assert (opts.out / name).exists()


def test_cite_parser_defaults() -> None:
    args = rag_eval.build_parser().parse_args(["cite"])
    assert args.max_tokens == 8192 == rag_eval.DEFAULT_CITE_MAX_TOKENS
    assert args.context_length == 16384 and args.render_only is False
    assert args.out == rag_eval.DAY24_OUT and args.meta == rag_eval.DAY23_META
    assert args.runs is None and args.kb is None and args.model is None


async def test_cite_unknown_run_and_missing_meta_exit_2(tmp_path: Path) -> None:
    bad = rag_eval.build_parser().parse_args(["cite", "--runs", "strict,bogus"])
    assert await rag_eval.cite_command(bad) == 2
    missing = rag_eval.build_parser().parse_args(["cite", "--meta", str(tmp_path / "none.json")])
    assert await rag_eval.cite_command(missing) == 2
