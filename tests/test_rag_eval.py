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
    """Importing the script must not create or touch the scratch DB (it may already exist after a live eval run)."""
    scratch_db = REPO_ROOT / "eval_out" / "day22" / "eval.db"
    before = scratch_db.stat().st_mtime_ns if scratch_db.exists() else None
    spec = importlib.util.spec_from_file_location("rag_eval_reimport", REPO_ROOT / "scripts" / "rag_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    after = scratch_db.stat().st_mtime_ns if scratch_db.exists() else None
    assert before == after


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


CAL_QUESTIONS = [
    {"id": "C01", "category": "answerable", "question": "a", "expected_sources": [{"file_contains": "FZ_N_196_FZ", "article": "26"}]},
    {"id": "C02", "category": "answerable", "question": "b", "expected_sources": [{"file_contains": "FZ_N_196_FZ", "article": "99"}]},
    {"id": "C03", "category": "out_of_corpus", "question": "c", "expected_sources": []},
    {"id": "C04", "category": "out_of_corpus", "question": "d", "expected_sources": []},
]


def _scored(source: str, section: str, score: float) -> dict[str, Any]:
    return {**_chunk(source, section), "score": score}


def _cal_results() -> dict[str, list[dict[str, Any]]]:
    return {
        "C01": [_scored(KOAP, "Статья 1. x", 0.7), _scored(FZ, "Статья 26. y", 0.65)],
        "C02": [_scored(FZ, "Статья 1. z", 0.5)],
        "C03": [_scored(FZ, "Статья 1. z", 0.4)],
        "C04": [_scored(FZ, "Статья 2. z", 0.3)],
    }


def test_compute_calibration_collects_both_classes() -> None:
    data = rag_eval.compute_calibration(CAL_QUESTIONS, _cal_results())
    assert data["gold_scores"] == [0.65]
    assert data["gold_missing"] == ["C02"]
    assert data["answerable_top1"] == [0.7, 0.5]
    assert sorted(data["ooc_top1"]) == [0.3, 0.4]
    assert data["stats"]["separable"] is True
    assert data["threshold"] == pytest.approx(0.53, abs=0.011)


def test_control_check_reports_survivors() -> None:
    rows = rag_eval.control_check(CAL_QUESTIONS, _cal_results(), 0.6)
    by_id = {row["id"]: row for row in rows}
    assert by_id["C01"]["top1"] == 0.7 and by_id["C01"]["gold"] == 0.65 and by_id["C01"]["survivors"] == 2
    assert by_id["C02"]["gold"] is None and by_id["C02"]["survivors"] == 0
    assert by_id["C03"]["survivors"] == 0


def _report(separable: bool) -> dict[str, Any]:
    return {
        "fixtures": {"calibration": {"name": "c.json", "sha256": "x"}},
        "candidate_k": 20,
        "kbs": {
            "bge": {
                "embedding_model": "bge-m3",
                "marker": "bge-m3",
                "gold_scores": [0.7, 0.6],
                "ooc_top1": [0.5, 0.4],
                "gold_missing": [],
                "threshold": 0.55,
                "stats": {"separable": separable, "method": "midpoint"},
                "control_check": [{"id": "Q01", "category": "direct", "top1": 0.7, "gold": 0.7, "survivors": 3}],
                "fts_probe": {"ooc_total": 8, "ooc_with_exempt": ["C13"], "gold_rescued_by_fts": []},
            }
        },
    }


def test_render_calibration_md_flags_inseparable() -> None:
    text = rag_eval.render_calibration_md(_report(False))
    assert "## bge (bge-m3)" in text and "0.600, 0.700" in text and "0.400, 0.500" in text
    assert "min 0.600 / median 0.650 / max 0.700" in text
    assert "threshold: 0.55" in text and "not separable" in text
    assert "not separable" not in rag_eval.render_calibration_md(_report(True))


def test_summarize_fts_probe() -> None:
    traces = {
        "C01": {"candidates": [{"file": FZ, "section": "Статья 26. y", "fts_exempt": True, "status": "in_answer"}]},
        "C02": {"candidates": [{"file": FZ, "section": "Статья 99. y", "fts_exempt": False, "status": "in_answer"}]},
        "C03": {"candidates": [{"file": FZ, "section": "Статья 1", "fts_exempt": True, "status": "in_answer"}]},
        "C04": {"candidates": []},
    }
    probe = rag_eval.summarize_fts_probe(CAL_QUESTIONS, traces)
    assert probe == {"ooc_total": 2, "ooc_with_exempt": ["C03"], "gold_rescued_by_fts": ["C01"]}


def test_marker_for_model_ids() -> None:
    assert rag_eval._marker_for("text-embedding-bge-m3") == "bge-m3"
    assert rag_eval._marker_for("text-embedding-nomic-embed-text-v1.5") == "nomic"
    assert rag_eval._marker_for("Other-Model") == "other-model"


def test_cli_parses_calibrate_arguments() -> None:
    args = rag_eval.build_parser().parse_args(["calibrate", "--kb", "bge=2", "--candidate-k", "30"])
    assert args.candidate_k == 30 and args.control_fixture == rag_eval.DEFAULT_FIXTURE
    assert args.fixture == rag_eval.DEFAULT_CALIBRATION_FIXTURE and args.allow_draft is False


def test_ablation_runs_order() -> None:
    assert list(rag_eval.ABLATION_RUNS) == [
        "baseline", "threshold", "lexical", "llm_rerank", "hybrid", "rewrite", "all"
    ]


def test_ablation_config_all_runs() -> None:
    flags = ("lexical", "llm_rerank", "hybrid", "rewrite")

    def cfg(run: str) -> Any:
        return rag_eval.ablation_config(run, top_k=5, candidate_k=20, threshold=0.59)

    base = cfg("baseline")
    assert (base.candidate_k, base.top_k, base.threshold) == (5, 5, 0.0)
    assert not any(getattr(base, flag) for flag in flags)
    for run in ("threshold", "lexical", "llm_rerank", "hybrid", "rewrite", "all"):
        config = cfg(run)
        assert (config.candidate_k, config.threshold) == (20, 0.59)
        expected = {
            "threshold": set(), "lexical": {"lexical"}, "llm_rerank": {"llm_rerank"},
            "hybrid": {"hybrid"}, "rewrite": {"rewrite"}, "all": set(flags),
        }[run]
        assert {flag for flag in flags if getattr(config, flag)} == expected


def test_count_skips_and_incomplete() -> None:
    traces = [
        {"skipped": [{"stage": "llm", "reason": "parse_failed"}, {"stage": "rewrite", "reason": "no_llm"}]},
        {"skipped": [{"stage": "llm", "reason": "parse_failed"}]},
        {"skipped": []},
    ]
    assert rag_eval.count_skips(traces) == {"llm:parse_failed": 2, "rewrite:no_llm": 1}
    rows = [
        {"answer": "ok", "finish_reason": "stop"},
        {"answer": "  ", "finish_reason": "stop"},
        {"answer": "cut", "finish_reason": "length"},
        {"answer": "", "finish_reason": None},
    ]
    assert rag_eval.count_incomplete(rows) == 3


def test_ablate_and_run_parser_max_tokens_defaults() -> None:
    parser = rag_eval.build_parser()
    assert rag_eval.DEFAULT_ABLATE_MAX_TOKENS == 4096
    assert parser.parse_args(["ablate", "--kb", "bge=2"]).max_tokens == 4096
    assert parser.parse_args(["ablate", "--kb", "bge=2"]).context_length == 16384
    assert parser.parse_args(["run", "--kb", "bge=2"]).max_tokens == 1024


def _trace(question_index: int, run: str) -> dict[str, Any]:
    below = question_index == 3
    candidates = [
        {"chunk_id": "1-1", "file": "doc.txt", "section": "Статья 1", "rank_before": 1, "rank_after": None if below else 1,
         "cos": 0.4 if below else 0.8, "status": "below_threshold" if below else "in_answer"},
        {"chunk_id": "1-2", "file": "doc.txt", "section": "Статья 2", "rank_before": 2, "rank_after": None,
         "cos": 0.3, "status": "below_threshold" if below else "outside_top_k"},
    ]
    skipped = [{"stage": "llm", "reason": "parse_failed"}] if run == "lexical" and question_index == 1 else []
    return {
        "verdict": "below_threshold" if below else "ok",
        "config": {"candidate_k": 2, "top_k": 5, "threshold": 0.59},
        "stages": ["threshold"], "stage_ms": {}, "skipped": skipped, "latency_ms": 1, "candidates": candidates,
    }


@pytest.fixture
def scripted_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent import rag_pipeline

    async def fake(session: Any, kb: Any, question: str, config: Any, client: Any = None, model: Any = None) -> Any:
        index = int(question.split()[1].rstrip("?"))
        run = "lexical" if config.lexical else "threshold"
        trace = _trace(index, run)
        if index == 3:
            return [], trace
        return [{**_chunk("doc.txt", "Статья 1"), "text": "фрагмент"}], trace

    monkeypatch.setattr(rag_pipeline, "run_retrieval_pipeline", fake)


async def test_run_ablation_writes_raw_tables_and_meta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    (kb_id,) = await _ready_kbs(monkeypatch, 1)
    client = FakeClient()
    opts = _opts(tmp_path, {"a": kb_id}, max_tokens=4096)
    await rag_eval.run_ablation(
        opts, client, async_session_factory, ["threshold", "lexical"], "a", kb_id, 20, 0.59
    )
    raw_dir = opts.out / "raw"
    assert sorted(p.name for p in raw_dir.iterdir()) == sorted(
        f"{run}_Q0{i}.json" for run in ("threshold", "lexical") for i in (1, 2, 3)
    )
    raw = json.loads((raw_dir / "lexical_Q01.json").read_text(encoding="utf-8"))
    assert raw["run"] == "lexical" and raw["finish_reason"] == "stop"
    assert raw["chunks_before"] == 2 and raw["chunks_after"] == 1
    assert raw["skipped"] == [{"stage": "llm", "reason": "parse_failed"}]
    assert raw["retrieval"]["hit1"] is True and "text" not in raw["chunks"][0]
    assert "фрагмент" not in json.dumps(raw["search"], ensure_ascii=False)
    assert raw["retrieval_latency_ms"] >= 0 and raw["answer_latency_ms"] >= 0
    below = json.loads((raw_dir / "threshold_Q03.json").read_text(encoding="utf-8"))
    assert below["verdict"] == "below_threshold" and below["chunks_after"] == 0
    assert "Фрагменты" not in below["messages"][-1]["content"]
    assert rag.NO_FRAGMENTS_INSTRUCTION in below["messages"][-1]["content"]
    md = (opts.out / "ablation.md").read_text(encoding="utf-8")
    assert "пустых/обрезанных" in md and "llm:parse_failed=1" in md
    meta = json.loads((opts.out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["max_tokens"] == 4096 and meta["context_length"] == 16384
    assert meta["incomplete_answers"] == {"threshold": 0, "lexical": 0}
    rows = list(csv.DictReader(io.StringIO((opts.out / "answers.csv").read_text(encoding="utf-8"))))
    assert list(rows[0]) == [
        "id", "category", "run", "answer", "cited_sources", "verdict", "comment", "judge_verdict", "judge_comment"
    ]
    assert len(rows) == 6 and all(not row["verdict"] and not row["judge_verdict"] for row in rows)


async def test_run_ablation_rerender_keeps_filled_verdicts_and_other_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    (kb_id,) = await _ready_kbs(monkeypatch, 1)
    opts = _opts(tmp_path, {"a": kb_id})
    await rag_eval.run_ablation(opts, FakeClient(), async_session_factory, ["threshold"], "a", kb_id, 20, 0.59)
    csv_path = opts.out / "answers.csv"
    rows = list(csv.DictReader(io.StringIO(csv_path.read_text(encoding="utf-8"))))
    rows[0]["verdict"], rows[0]["comment"], rows[1]["judge_verdict"] = "верно", "ok", "partial"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    csv_path.write_text(buffer.getvalue(), encoding="utf-8")
    await rag_eval.run_ablation(opts, FakeClient(), async_session_factory, ["lexical"], "a", kb_id, 20, 0.59)
    again = list(csv.DictReader(io.StringIO(csv_path.read_text(encoding="utf-8"))))
    assert len(again) == 6
    first = next(r for r in again if r["id"] == rows[0]["id"] and r["run"] == "threshold")
    assert first["verdict"] == "верно" and first["comment"] == "ok"
    second = next(r for r in again if r["id"] == rows[1]["id"] and r["run"] == "threshold")
    assert second["judge_verdict"] == "partial"
    assert len(list((opts.out / "raw").glob("threshold_*"))) == 3


async def test_ablate_unknown_run_exits_2(tmp_path: Path) -> None:
    args = rag_eval.build_parser().parse_args(
        ["ablate", "--kb", "a=1", "--runs", "threshold,bogus", "--db", str(tmp_path / "x.db")]
    )
    assert await rag_eval.ablate_command(args) == 2


async def test_calibrate_refuses_draft_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "keep.db"))
    monkeypatch.setenv("KB_STORAGE_DIR", str(tmp_path / "keep_kb"))
    args = rag_eval.build_parser().parse_args(
        ["calibrate", "--kb", "bge=2", "--db", str(tmp_path / "x.db"), "--out", str(tmp_path)]
    )
    with pytest.raises(SystemExit) as exc:
        await rag_eval.calibrate_command(args)
    assert exc.value.code == 2
