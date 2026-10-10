"""Offline RAG evaluation: build comparable knowledge bases and score retrieval and answers.

Usage:
  python scripts/rag_eval.py build-kbs [--db PATH] [--embedder LABEL=MODEL_ID ...] [--pdf PATH ...]
  python scripts/rag_eval.py run --kb LABEL=ID [--kb LABEL=ID ...] [--provider lmstudio|deepseek]
                                 [--model ID] [--top-k 5] [--modes off,rag] [--only-kb LABEL]
  python scripts/rag_eval.py calibrate --kb LABEL=ID [--kb LABEL=ID ...] [--candidate-k 20]
  python scripts/rag_eval.py ablate --kb LABEL=ID [--runs baseline,threshold,...] [--threshold F]
                                    [--max-tokens 4096] [--provider lmstudio|deepseek]
  python scripts/rag_eval.py cite [--runs strict,strict_off,strict_baseline] [--max-tokens 8192]
                                  [--render-only]
  python scripts/rag_eval.py dialog [--runs main,baseline] [--scenarios A,B] [--check] [--force]

build-kbs indexes the corpus PDFs once per embedder with identical strategy, chunk size and
overlap into a scratch database (never app.db). run answers every control-set question without
and with retrieval at temperature 0, scores retrieval hit@k against the expected sources and
writes raw outputs, retrieval.md, answers.md, answers.csv (empty verdict column) and run_meta.json.

calibrate measures per-embedder score distributions on the frozen calibration set and derives the
relevance threshold; ablate runs the retrieval-pipeline configurations over the control set through
the same pipeline as the chat and writes ablation.md, answers.md, answers.csv and run_meta.json;
cite answers the control questions with the chat's strict-mode gate and citation rules and writes
eval_out/day24 (cite_*.md, cite_summary.md, answers.csv with a manual verdict column).

Exit codes: 0 ok, 1 run error, 2 preflight failure.
"""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_FIXTURE: Path = REPO_ROOT / "tests" / "fixtures" / "rag" / "control_set.json"
DEFAULT_OUT: Path = REPO_ROOT / "eval_out" / "day22"
DEFAULT_DB: Path = DEFAULT_OUT / "eval.db"
DEFAULT_MODEL: str = "qwen/qwen3.5-9b"
HIT_KS: tuple[int, ...] = (1, 3, 5)
VERDICTS: tuple[str, ...] = ("верно", "частично", "неверно", "галлюцинация")
ARTICLE_IN_SECTION_RE: re.Pattern[str] = re.compile(r"Статья\s+(\d+(?:[._-]\d+)*)")
CITATION_RE: re.Pattern[str] = re.compile(r"\[(\d+)\]")
THINK_RE: re.Pattern[str] = re.compile(r"<think>.*?</think>", re.DOTALL)
EVAL_SYSTEM_PROMPT: str = "Ты — ассистент. Отвечай по-русски кратко и по существу."
DEFAULT_EMBEDDERS: tuple[str, ...] = (
    "nomic=text-embedding-nomic-embed-text-v1.5",
    "bge=text-embedding-bge-m3",
)
RAG_DIR: Path = Path(r"C:\Projects\RAG")
DEFAULT_PDFS: tuple[Path, ...] = (
    RAG_DIR / "19951210_20260626_FZ_N_196_FZ.pdf",
    RAG_DIR
    / (
        "Kodex_ot_30_12_2001_N_195-FZ_Kodex_Rossiyskoy_Federatsii_ob_administrativnyh_"
        "pravonarusheniyah_s..._Text.pdf"
    ),
)
EVAL_USERNAME: str = "rag_eval"
ANSWER_TABLE_CHARS: int = 300
EXIT_OK, EXIT_ERROR, EXIT_PREFLIGHT = 0, 1, 2
DEFAULT_CALIBRATION_FIXTURE: Path = REPO_ROOT / "tests" / "fixtures" / "rag" / "calibration_set.json"
DAY23_OUT: Path = REPO_ROOT / "eval_out" / "day23"
DAY23_DB: Path = DAY23_OUT / "eval.db"
DEFAULT_CANDIDATE_K: int = 20
# The Day 22 baseline answers were produced at 4096 (eval_out/day22/run_meta.json). The same
# budget keeps the ablation comparable and fixes how many fragments rag_budget lets through.
DEFAULT_ABLATE_MAX_TOKENS = 4096
ABLATION_RUNS: dict[str, dict[str, bool]] = {
    "baseline": {},
    "threshold": {},
    "lexical": {"lexical": True},
    "llm_rerank": {"llm_rerank": True},
    "hybrid": {"hybrid": True},
    "rewrite": {"rewrite": True},
    "all": {"lexical": True, "llm_rerank": True, "hybrid": True, "rewrite": True},
}
JUDGE_COLUMNS: tuple[str, ...] = ("verdict", "comment", "judge_verdict", "judge_comment")
DAY24_OUT: Path = REPO_ROOT / "eval_out" / "day24"
DAY23_META: Path = DAY23_OUT / "run_meta.json"
# At 4096 the reasoning model returned empty strict answers: the budget went to reasoning
# before the quotes section was written.
DEFAULT_CITE_MAX_TOKENS = 8192
MEANING_VERDICTS: tuple[str, ...] = ("да", "частично", "нет")
# strict: Day 23 embedder winner with its calibrated threshold, strict mode on (primary run).
# strict_off: same retrieval, Phase 15 prompt (comparison row).
# strict_baseline: strict mode on the plain top-k retrieval, only the model's own refusal applies.
CITE_RUNS: dict[str, dict[str, Any]] = {
    "strict": {"strict": True, "retrieval": "threshold"},
    "strict_off": {"strict": False, "retrieval": "threshold"},
    "strict_baseline": {"strict": True, "retrieval": "baseline"},
}


def parse_article(text: str | None) -> str | None:
    """Return the deepest article number in a breadcrumb, or None."""
    if not text:
        return None
    matches = ARTICLE_IN_SECTION_RE.findall(text)
    return matches[-1] if matches else None


def chunk_hits(chunk: dict[str, Any], expected: dict[str, Any]) -> bool:
    """True when the chunk comes from the expected file and exact article."""
    if expected["file_contains"] not in (chunk.get("source") or ""):
        return False
    article = parse_article(chunk.get("section"))
    if article is None:
        article = parse_article(chunk.get("title"))
    return article == expected["article"]


def hit_at_k(
    chunks: list[dict[str, Any]], expected_sources: list[dict[str, Any]], k: int
) -> bool | None:
    """True when any expected source appears within the first k chunks; None when none expected."""
    if not expected_sources:
        return None
    return any(chunk_hits(chunk, exp) for chunk in chunks[:k] for exp in expected_sources)


def all_hit_at_k(
    chunks: list[dict[str, Any]], expected_sources: list[dict[str, Any]], k: int
) -> bool | None:
    """True when every expected source appears within the first k chunks."""
    if not expected_sources:
        return None
    return all(any(chunk_hits(chunk, exp) for chunk in chunks[:k]) for exp in expected_sources)


def first_hit_rank(
    chunks: list[dict[str, Any]], expected_sources: list[dict[str, Any]]
) -> int | None:
    """1-based rank of the first chunk matching any expected source."""
    if not expected_sources:
        return None
    for rank, chunk in enumerate(chunks, 1):
        if any(chunk_hits(chunk, exp) for exp in expected_sources):
            return rank
    return None


def load_fixture(path: Path, require_frozen: bool) -> dict[str, Any]:
    """Load the control set; a draft fixture is refused unless frozen is not required."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if require_frozen and data.get("status") != "frozen":
        print(f"preflight: control set {path.name} is not frozen (status={data.get('status')})")
        raise SystemExit(EXIT_PREFLIGHT)
    return data


def fixture_sha256(path: Path) -> str:
    """SHA-256 of the fixture file bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mark(value: bool | None) -> str:
    """Render a hit flag as 1, 0 or n/a."""
    if value is None:
        return "n/a"
    return "1" if value else "0"


def _score(value: float | None) -> str:
    """Render a similarity score with three decimals."""
    return "n/a" if value is None else f"{value:.3f}"


def render_retrieval_table(rows: list[dict[str, Any]], labels: list[str], top_k: int) -> str:
    """Markdown table of retrieval hits per question and KB label.

    Each row is {"id", "category", "kb": {label: {"hit1", "hit3", "hit5", "hitk", "rank", "top1"}}}.
    """
    header = ["id", "category"]
    for label in labels:
        header += [f"{label} {name}" for name in ("hit@1", "hit@3", "hit@5", f"hit@{top_k}")]
        header += [f"{label} rank", f"{label} top1 score"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        cells = [str(row["id"]), str(row["category"])]
        for label in labels:
            stats = row.get("kb", {}).get(label)
            if stats is None:
                cells += ["n/a"] * 6
                continue
            cells += [_mark(stats.get(key)) for key in ("hit1", "hit3", "hit5", "hitk")]
            rank = stats.get("rank")
            cells += ["n/a" if rank is None else str(rank), _score(stats.get("top1"))]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    """Make text safe for a single Markdown table cell."""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_answers_table(rows: list[dict[str, Any]]) -> str:
    """Markdown table of answers (truncated); full text lives in the raw files."""
    header = ["id", "category", "mode", "kb", "answer", "cited_sources", "verdict", "comment"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        answer = row.get("answer") or ""
        if len(answer) > ANSWER_TABLE_CHARS:
            answer = answer[:ANSWER_TABLE_CHARS] + "…"
        cells = [
            str(row["id"]),
            str(row["category"]),
            str(row["mode"]),
            str(row.get("kb") or ""),
            _cell(answer),
            _cell(str(row.get("cited_sources") or "")),
            str(row.get("verdict") or ""),
            _cell(str(row.get("comment") or "")),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_answers_csv(rows: list[dict[str, Any]]) -> str:
    """CSV of answers with an empty verdict column for manual grading."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["id", "category", "mode", "kb", "answer", "cited_sources", "verdict", "comment"])
    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["category"],
                row["mode"],
                row.get("kb") or "",
                row.get("answer") or "",
                row.get("cited_sources") or "",
                "",
                "",
            ]
        )
    return buffer.getvalue()


@dataclass
class EvalOptions:
    """Resolved settings of one evaluation run."""

    kb: dict[str, int]
    provider: str = "lmstudio"
    model: str = DEFAULT_MODEL
    top_k: int = 5
    modes: list[str] = field(default_factory=lambda: ["off", "rag"])
    context_length: int = 16384
    max_tokens: int = 1024
    fixture: Path = DEFAULT_FIXTURE
    out: Path = DEFAULT_OUT
    allow_draft: bool = False
    only_kb: str | None = None


def score_question(
    chunks: list[dict[str, Any]], question: dict[str, Any], top_k: int
) -> dict[str, Any]:
    """Retrieval metrics for one question; synthesis questions need every expected source."""
    expected = question.get("expected_sources") or []
    check = all_hit_at_k if question["category"] == "synthesis" else hit_at_k
    return {
        "hit1": check(chunks, expected, 1),
        "hit3": check(chunks, expected, 3),
        "hit5": check(chunks, expected, 5),
        "hitk": check(chunks, expected, top_k),
        "any_hitk": hit_at_k(chunks, expected, top_k),
        "rank": first_hit_rank(chunks, expected),
        "top1": chunks[0]["score"] if chunks else None,
    }


def _message_tokens(messages: list[dict[str, Any]]) -> int:
    """Token count of all message contents."""
    from agent.llm_client import count_tokens

    return sum(count_tokens(str(message["content"])) for message in messages)


def _cited_sources(answer: str, kept: list[dict[str, Any]]) -> list[str]:
    """Map [N] markers in the answer to the fragments they point at."""
    cited: list[str] = []
    for marker in CITATION_RE.findall(answer):
        index = int(marker)
        if 1 <= index <= len(kept):
            chunk = kept[index - 1]
            label = chunk.get("section") or chunk.get("title") or ""
            entry = f"[{index}] {chunk['source']} / {label}"
            if entry not in cited:
                cited.append(entry)
    return cited


async def _ask(client: Any, opts: EvalOptions, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """One completion at temperature 0 with reasoning spans stripped."""
    import httpx

    sent = [{"role": m["role"], "content": m["content"]} for m in messages]
    try:
        result = await client.complete_chat_detailed(
            sent, opts.model, temperature=0.0, max_tokens=opts.max_tokens
        )
    except httpx.HTTPError as exc:
        return {
            "answer": "",
            "error": f"{type(exc).__name__}: {exc}",
            "finish_reason": None,
            "has_reasoning": False,
            "completion_tokens": None,
            "sent": sent,
        }
    answer = THINK_RE.sub("", result.content or "").strip()
    return {
        "answer": answer,
        "error": None,
        "finish_reason": result.finish_reason,
        "has_reasoning": result.has_reasoning,
        "completion_tokens": result.completion_tokens,
        "sent": sent,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a UTF-8 JSON file."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _mean(values: list[bool | None]) -> float | None:
    """Mean of the non-None flags, or None when there are none."""
    flags = [value for value in values if value is not None]
    return sum(1 for value in flags if value) / len(flags) if flags else None


def _load_raw(raw_dir: Path) -> list[dict[str, Any]]:
    """Read every raw answer file."""
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(raw_dir.glob("*.json"))]


def _render_outputs(
    opts: EvalOptions, fixture: dict[str, Any], kb_meta: dict[str, Any]
) -> dict[str, Any]:
    """Render tables and run_meta.json from the complete set of raw files."""
    raws = _load_raw(opts.out / "raw")
    by_key = {(raw["mode"], raw.get("kb") or "", raw["id"]): raw for raw in raws}
    labels = sorted({raw["kb"] for raw in raws if raw["mode"] == "rag"})
    retrieval_rows: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    for question in fixture["questions"]:
        qid = question["id"]
        entry: dict[str, Any] = {"id": qid, "category": question["category"], "kb": {}}
        for mode, label in [("off", "")] + [("rag", name) for name in labels]:
            raw = by_key.get((mode, label, qid))
            if raw is None:
                continue
            if mode == "rag":
                entry["kb"][label] = raw["retrieval"]
            answer_rows.append(
                {
                    "id": qid,
                    "category": question["category"],
                    "mode": mode,
                    "kb": label,
                    "answer": raw["answer"],
                    "cited_sources": "; ".join(raw.get("cited_sources") or []),
                }
            )
        retrieval_rows.append(entry)
    (opts.out / "retrieval.md").write_text(
        render_retrieval_table(retrieval_rows, labels, opts.top_k), encoding="utf-8"
    )
    (opts.out / "answers.md").write_text(render_answers_table(answer_rows), encoding="utf-8")
    (opts.out / "answers.csv").write_text(
        render_answers_csv(answer_rows), encoding="utf-8", newline=""
    )
    meta_path = opts.out / "run_meta.json"
    previous: dict[str, Any] = (
        json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    )
    kbs = {**previous.get("kbs", {}), **kb_meta}
    shared = {(m["strategy"], m["chunk_size"], m["chunk_overlap"]) for m in kbs.values()}
    meta = {
        "fixture": opts.fixture.name,
        "fixture_sha256": fixture_sha256(opts.fixture),
        "provider": opts.provider,
        "model": opts.model,
        "top_k": opts.top_k,
        "modes": sorted(set(previous.get("modes", [])) | set(opts.modes)),
        "temperature": 0.0,
        "context_length": opts.context_length,
        "max_tokens": opts.max_tokens,
        "identical_chunking": len(shared) <= 1,
        "kbs": kbs,
    }
    _write_json(meta_path, meta)
    summary: dict[str, Any] = {}
    for label in labels:
        stats = [
            by_key[("rag", label, q["id"])]["retrieval"]
            for q in fixture["questions"]
            if ("rag", label, q["id"]) in by_key and q["category"] != "out_of_corpus"
        ]
        summary[label] = {f"hit{k}": _mean([stat[f"hit{k}"] for stat in stats]) for k in HIT_KS}
    return summary


async def run_eval(opts: EvalOptions, client: Any, session_factory: Any) -> dict[str, Any]:
    """Answer every control-set question per mode and KB label and write all outputs."""
    from agent.rag import RagFailure, build_rag_block, merge_rag_block, rag_budget, retrieve
    from shared.models import KnowledgeBase

    fixture = load_fixture(opts.fixture, require_frozen=not opts.allow_draft)
    raw_dir = opts.out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    labels = [label for label in opts.kb if opts.only_kb in (None, label)]
    kb_meta: dict[str, Any] = {}
    async with session_factory() as session:
        kbs = {label: await session.get(KnowledgeBase, opts.kb[label]) for label in labels}
        for label, kb in kbs.items():
            if kb is not None:
                kb_meta[label] = {
                    "kb_id": kb.id,
                    "strategy": getattr(kb.strategy, "value", kb.strategy),
                    "chunk_size": kb.chunk_size,
                    "chunk_overlap": kb.chunk_overlap,
                    "embedding_model": kb.embedding_model,
                    "dim": kb.dim,
                    "chunk_count": kb.chunk_count,
                }
        for question in fixture["questions"]:
            qid = question["id"]
            base = [
                {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                {"role": "user", "content": question["question"]},
            ]
            common = {"id": qid, "category": question["category"], "question": question["question"]}
            if "off" in opts.modes:
                reply = await _ask(client, opts, [dict(m) for m in base])
                _write_json(
                    raw_dir / f"off_none_{qid}.json",
                    {
                        **common,
                        "mode": "off",
                        "kb": "",
                        "messages": reply.pop("sent"),
                        "cited_sources": [],
                        **reply,
                    },
                )
            if "rag" not in opts.modes:
                continue
            for label in labels:
                warning: str | None = None
                try:
                    chunks = await retrieve(session, kbs[label], question["question"], opts.top_k)
                except RagFailure as failure:
                    chunks, warning = [], failure.code
                messages = [dict(m) for m in base]
                budget = rag_budget(opts.context_length, _message_tokens(messages), opts.max_tokens)
                block, kept, dropped = build_rag_block(chunks, budget)
                if block:
                    merge_rag_block(messages, block)
                reply = await _ask(client, opts, messages)
                retrieval = {
                    **score_question(chunks, question, opts.top_k),
                    "warning": warning,
                    "dropped": dropped,
                }
                _write_json(
                    raw_dir / f"rag_{label}_{qid}.json",
                    {
                        **common,
                        "mode": "rag",
                        "kb": label,
                        "messages": reply.pop("sent"),
                        "retrieval": retrieval,
                        "chunks": [{k: v for k, v in c.items() if k != "text"} for c in chunks],
                        "fragments": [c["text"] for c in kept],
                        "cited_sources": _cited_sources(reply["answer"], kept),
                        **reply,
                    },
                )
    summary = _render_outputs(opts, fixture, kb_meta)
    for label, stats in summary.items():
        rendered = " ".join(
            f"hit@{k}={'n/a' if stats[f'hit{k}'] is None else format(stats[f'hit{k}'], '.2f')}"
            for k in HIT_KS
        )
        print(f"{label}: {rendered}")
    return {"summary": summary, "out": str(opts.out)}


def _parse_pairs(items: list[str], what: str) -> dict[str, str]:
    """Parse repeated LABEL=VALUE arguments."""
    pairs: dict[str, str] = {}
    for item in items:
        label, sep, value = item.partition("=")
        if not sep or not label or not value:
            print(f"preflight: bad {what} '{item}', expected LABEL=VALUE")
            raise SystemExit(EXIT_PREFLIGHT)
        pairs[label] = value
    return pairs


def _use_scratch_storage(db: Path) -> None:
    """Point settings at a scratch database and KB storage before any project import."""
    db.parent.mkdir(parents=True, exist_ok=True)
    os.environ["DB_PATH"] = str(db)
    os.environ["KB_STORAGE_DIR"] = str(db.with_name(f"{db.stem}_kb"))


async def build_kbs(args: argparse.Namespace) -> int:
    """Index the corpus once per embedder with identical chunking settings."""
    import secrets

    _use_scratch_storage(args.db)
    pdfs = [Path(p) for p in (args.pdf or DEFAULT_PDFS)]
    missing = [str(p) for p in pdfs if not p.is_file()]
    if missing:
        print(f"preflight: missing PDF files: {', '.join(missing)}")
        return EXIT_PREFLIGHT
    embedders = _parse_pairs(args.embedder or list(DEFAULT_EMBEDDERS), "--embedder")

    from sqlmodel import select

    from agent.kb_indexer import run_index_job
    from shared.auth import hash_password
    from shared.database import async_session_factory, init_db
    from shared.kb_storage import uploads_dir
    from shared.models import KbDocument, KbStatus, KbStrategy, KnowledgeBase, User

    await init_db()
    async with async_session_factory() as session:
        user = (await session.exec(select(User).where(User.username == EVAL_USERNAME))).first()
        if user is None:
            user = User(
                username=EVAL_USERNAME, password_hash=hash_password(secrets.token_urlsafe(16))
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        user_id: int = user.id
    strategy = KbStrategy(args.strategy)
    failed = False
    for label, model_id in embedders.items():
        async with async_session_factory() as session:
            kb = KnowledgeBase(
                user_id=user_id,
                name=f"day22-{label}",
                status=KbStatus.QUEUED,
                strategy=strategy,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                embedding_model=model_id,
                file_count=len(pdfs),
            )
            session.add(kb)
            await session.commit()
            await session.refresh(kb)
            target = uploads_dir(user_id, kb.id)
            target.mkdir(parents=True, exist_ok=True)
            for index, pdf in enumerate(pdfs):
                data = pdf.read_bytes()
                stored = f"{index}.bin"
                (target / stored).write_bytes(data)
                session.add(
                    KbDocument(
                        kb_id=kb.id,
                        filename=pdf.name,
                        sha256=hashlib.sha256(data).hexdigest(),
                        size_bytes=len(data),
                        stored_name=stored,
                    )
                )
            await session.commit()
            kb_id: int = kb.id
        await run_index_job(kb_id, user_id)
        async with async_session_factory() as session:
            done = await session.get(KnowledgeBase, kb_id)
        print(f"{label}={kb_id} {done.status.value} {done.chunk_count} {done.dim}")
        failed = failed or done.status != KbStatus.READY
    return EXIT_ERROR if failed else EXIT_OK


async def run_command(args: argparse.Namespace) -> int:
    """Preflight, then run the evaluation against LM Studio or DeepSeek."""
    import httpx

    _use_scratch_storage(args.db)
    kb_ids = {label: int(value) for label, value in _parse_pairs(args.kb, "--kb").items()}
    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    if not modes or any(mode not in ("off", "rag") for mode in modes):
        print("preflight: --modes must be a comma list of off,rag")
        return EXIT_PREFLIGHT
    if args.only_kb and args.only_kb not in kb_ids:
        print(f"preflight: --only-kb {args.only_kb} is not among --kb labels")
        return EXIT_PREFLIGHT
    opts = EvalOptions(
        kb=kb_ids,
        provider=args.provider,
        model=args.model,
        top_k=args.top_k,
        modes=modes,
        context_length=args.context_length,
        max_tokens=args.max_tokens,
        fixture=args.fixture,
        out=args.out,
        allow_draft=args.allow_draft,
        only_kb=args.only_kb,
    )
    load_fixture(opts.fixture, require_frozen=not opts.allow_draft)

    from agent.llm_client import LLMClient
    from agent.providers import DEEPSEEK_BASE_URL
    from shared.config import settings
    from shared.database import async_session_factory
    from shared.models import KbStatus, KnowledgeBase

    if args.provider == "deepseek":
        base_url = args.base_url or DEEPSEEK_BASE_URL
        api_key = settings.DEEPSEEK_API_KEY
        if not api_key:
            print("preflight: DEEPSEEK_API_KEY is not set")
            return EXIT_PREFLIGHT
    else:
        base_url = args.base_url or os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234")
        api_key = ""
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                (await http.get(f"{base_url.rstrip('/')}/v1/models")).raise_for_status()
        except httpx.HTTPError as exc:
            print(f"preflight: LM Studio is not reachable at {base_url} ({type(exc).__name__})")
            return EXIT_PREFLIGHT
    async with async_session_factory() as session:
        for label, kb_id in kb_ids.items():
            if args.only_kb and label != args.only_kb:
                continue
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None or kb.status != KbStatus.READY:
                print(f"preflight: knowledge base {label}={kb_id} does not exist or is not ready")
                return EXIT_PREFLIGHT
    await run_eval(opts, LLMClient(base_url, api_key), async_session_factory)
    return EXIT_OK


def _kb_ids(items: list[str]) -> dict[str, int]:
    """Parse repeated LABEL=ID arguments into integer KB ids."""
    try:
        return {label: int(value) for label, value in _parse_pairs(items, "--kb").items()}
    except ValueError:
        print("preflight: --kb ids must be integers")
        raise SystemExit(EXIT_PREFLIGHT) from None


def _fmt_stats(values: list[float]) -> str:
    """min/median/max of a score list for Markdown."""
    if not values:
        return "n/a"
    return f"min {min(values):.3f} / median {statistics.median(values):.3f} / max {max(values):.3f}"


def _matches(chunk: dict[str, Any], question: dict[str, Any]) -> bool:
    """True when the chunk matches any expected source of the question."""
    return any(chunk_hits(chunk, exp) for exp in question.get("expected_sources") or [])


def compute_calibration(
    questions: list[dict[str, Any]], results_by_question: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Score distributions of one KB on the calibration set and the threshold derived from them."""
    from agent.rag_rank import choose_threshold

    gold_scores: list[float] = []
    gold_missing: list[str] = []
    answerable_top1: list[float] = []
    ooc_top1: list[float] = []
    rows: list[dict[str, Any]] = []
    for question in questions:
        results = sorted(results_by_question.get(question["id"], []), key=lambda c: -c["score"])
        top1 = results[0]["score"] if results else None
        gold: float | None = None
        if question["category"] == "out_of_corpus":
            if top1 is not None:
                ooc_top1.append(top1)
        else:
            if top1 is not None:
                answerable_top1.append(top1)
            matching = [c["score"] for c in results if _matches(c, question)]
            if matching:
                gold = max(matching)
                gold_scores.append(gold)
            else:
                gold_missing.append(question["id"])
        rows.append({"id": question["id"], "category": question["category"], "top1": top1, "gold": gold})
    threshold, stats = choose_threshold(gold_scores, ooc_top1)
    return {
        "gold_scores": gold_scores,
        "gold_missing": gold_missing,
        "answerable_top1": answerable_top1,
        "ooc_top1": ooc_top1,
        "threshold": threshold,
        "stats": stats,
        "rows": rows,
    }


def control_check(
    questions: list[dict[str, Any]],
    results_by_question: dict[str, list[dict[str, Any]]],
    threshold: float,
) -> list[dict[str, Any]]:
    """How the calibrated threshold behaves on the control questions (reporting only)."""
    rows: list[dict[str, Any]] = []
    for question in questions:
        results = sorted(results_by_question.get(question["id"], []), key=lambda c: -c["score"])
        matching = [c["score"] for c in results if _matches(c, question)]
        rows.append(
            {
                "id": question["id"],
                "category": question["category"],
                "top1": results[0]["score"] if results else None,
                "gold": max(matching) if matching else None,
                "survivors": sum(1 for c in results if c["score"] >= threshold),
            }
        )
    return rows


def _trace_matches(candidate: dict[str, Any], question: dict[str, Any]) -> bool:
    """Match a pipeline-trace candidate (file/section keys) against the expected sources."""
    return _matches({"source": candidate.get("file"), "section": candidate.get("section")}, question)


def summarize_fts_probe(
    questions: list[dict[str, Any]], traces: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Which questions got FTS-exempt candidates when hybrid is on at the chosen threshold."""
    ooc_exempt: list[str] = []
    gold_rescued: list[str] = []
    for question in questions:
        candidates = (traces.get(question["id"]) or {}).get("candidates", [])
        exempt = [c for c in candidates if c.get("fts_exempt")]
        if question["category"] == "out_of_corpus":
            if exempt:
                ooc_exempt.append(question["id"])
            continue
        survived = any(
            _trace_matches(c, question)
            for c in candidates
            if not c.get("fts_exempt") and c.get("status") != "below_threshold"
        )
        if not survived and any(_trace_matches(c, question) for c in exempt):
            gold_rescued.append(question["id"])
    return {
        "ooc_total": sum(1 for q in questions if q["category"] == "out_of_corpus"),
        "ooc_with_exempt": ooc_exempt,
        "gold_rescued_by_fts": gold_rescued,
    }


def render_calibration_md(data: dict[str, Any]) -> str:
    """Markdown report of the calibration: distributions, threshold and control behaviour."""
    lines = ["# Calibration", ""]
    fixtures = data.get("fixtures", {})
    for name, info in fixtures.items():
        lines.append(f"- {name}: {info['name']} sha256 {info['sha256']}")
    lines += [f"- candidate_k: {data.get('candidate_k')}", ""]
    for label, kb in data["kbs"].items():
        stats = kb["stats"]
        lines += [f"## {label} ({kb['embedding_model']})", ""]
        lines.append(f"- answerable gold scores: {', '.join(f'{v:.3f}' for v in sorted(kb['gold_scores']))}")
        lines.append(f"  - {_fmt_stats(kb['gold_scores'])}")
        lines.append(f"- out-of-corpus top-1 scores: {', '.join(f'{v:.3f}' for v in sorted(kb['ooc_top1']))}")
        lines.append(f"  - {_fmt_stats(kb['ooc_top1'])}")
        if kb["gold_missing"]:
            lines.append(f"- answerable without a matching candidate: {', '.join(kb['gold_missing'])}")
        lines.append(f"- threshold: {kb['threshold']} (method {stats.get('method')}, marker {kb['marker']})")
        if not stats.get("separable"):
            lines.append("- classes are **not separable** by a single cosine cut-off")
        lines += ["", "Control set behaviour at this threshold:", ""]
        lines += ["| id | category | top1 | gold | survivors |", "|---|---|---|---|---|"]
        for row in kb["control_check"]:
            lines.append(
                f"| {row['id']} | {row['category']} | {_score(row['top1'])} | "
                f"{_score(row['gold'])} | {row['survivors']} |"
            )
        probe = kb["fts_probe"]
        lines += [
            "",
            f"FTS probe (hybrid on): out-of-corpus questions with an exempt candidate "
            f"{len(probe['ooc_with_exempt'])}/{probe['ooc_total']} "
            f"({', '.join(probe['ooc_with_exempt']) or '-'}); answerable gold rescued only by the "
            f"exemption: {', '.join(probe['gold_rescued_by_fts']) or '-'}",
            "",
        ]
    return "\n".join(lines)


def _marker_for(model_id: str) -> str:
    """CALIBRATED_THRESHOLDS key for an embedding model id."""
    lowered = model_id.lower()
    for marker in ("bge-m3", "nomic"):
        if marker in lowered:
            return marker
    return lowered


async def _lm_studio_reachable(base_url: str) -> bool:
    """True when the LM Studio models endpoint answers."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            (await http.get(f"{base_url.rstrip('/')}/v1/models")).raise_for_status()
    except httpx.HTTPError as exc:
        print(f"preflight: LM Studio is not reachable at {base_url} ({type(exc).__name__})")
        return False
    return True


async def _search_all(
    session: Any, kb: Any, questions: list[dict[str, Any]], candidate_k: int
) -> dict[str, list[dict[str, Any]]]:
    """Vector search per question; chunk text is dropped, scores and metadata kept."""
    from agent.rag import retrieve_vectors

    found: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        results, _ = await retrieve_vectors(session, kb, question["question"], candidate_k)
        found[question["id"]] = [{k: v for k, v in c.items() if k != "text"} for c in results]
    return found


async def _calibrate_kb(
    session: Any, kb: Any, calibration: dict[str, Any], control: dict[str, Any], candidate_k: int
) -> dict[str, Any]:
    """Full calibration of one KB: distributions, threshold, control check and FTS probe."""
    from agent.rag_pipeline import PipelineConfig, run_retrieval_pipeline

    questions = calibration["questions"]
    found = await _search_all(session, kb, questions, candidate_k)
    result = compute_calibration(questions, found)
    threshold = result["threshold"]
    control_found = await _search_all(session, kb, control["questions"], candidate_k)
    config = PipelineConfig(
        candidate_k=candidate_k,
        top_k=5,
        threshold=threshold,
        threshold_source="calibrated",
        hybrid=True,
    )
    traces: dict[str, dict[str, Any]] = {}
    for question in questions:
        _, traces[question["id"]] = await run_retrieval_pipeline(
            session, kb, question["question"], config
        )
    return {
        "embedding_model": kb.embedding_model,
        "marker": _marker_for(kb.embedding_model),
        **result,
        "control_check": control_check(control["questions"], control_found, threshold),
        "fts_probe": summarize_fts_probe(questions, traces),
    }


async def calibrate_command(args: argparse.Namespace) -> int:
    """Measure score distributions per KB on the calibration set and derive thresholds."""
    _use_scratch_storage(args.db)
    kb_ids = _kb_ids(args.kb)
    calibration = load_fixture(args.fixture, require_frozen=not args.allow_draft)
    control = load_fixture(args.control_fixture, require_frozen=True)

    from agent.rag import RagFailure
    from shared.database import async_session_factory, init_db
    from shared.models import KbStatus, KnowledgeBase

    await init_db()
    base_url = os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234")
    if not await _lm_studio_reachable(base_url):
        return EXIT_PREFLIGHT
    report: dict[str, Any] = {
        "fixtures": {
            "calibration": {"name": args.fixture.name, "sha256": fixture_sha256(args.fixture)},
            "control": {"name": args.control_fixture.name, "sha256": fixture_sha256(args.control_fixture)},
        },
        "candidate_k": args.candidate_k,
        "kbs": {},
    }
    async with async_session_factory() as session:
        for label, kb_id in kb_ids.items():
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None or kb.status != KbStatus.READY:
                print(f"preflight: knowledge base {label}={kb_id} does not exist or is not ready")
                return EXIT_PREFLIGHT
            try:
                report["kbs"][label] = await _calibrate_kb(
                    session, kb, calibration, control, args.candidate_k
                )
            except RagFailure as failure:
                print(f"error: retrieval failed for {label}={kb_id} ({failure.code})")
                return EXIT_ERROR
    args.out.mkdir(parents=True, exist_ok=True)
    _write_json(args.out / "calibration.json", report)
    (args.out / "calibration.md").write_text(render_calibration_md(report), encoding="utf-8")
    for label, kb in report["kbs"].items():
        print(
            f'CALIBRATED_THRESHOLDS["{kb["marker"]}"] = {kb["threshold"]}'
            f'  # separable={bool(kb["stats"].get("separable"))} ({label})'
        )
    return EXIT_OK


def _add_calibrate_parser(sub: Any) -> None:
    """Register the calibrate subcommand."""
    calibrate = sub.add_parser("calibrate", help="derive the relevance threshold per embedder")
    calibrate.add_argument("--db", type=Path, default=DAY23_DB)
    calibrate.add_argument("--kb", action="append", required=True, metavar="LABEL=ID")
    calibrate.add_argument("--fixture", type=Path, default=DEFAULT_CALIBRATION_FIXTURE)
    calibrate.add_argument("--control-fixture", type=Path, default=DEFAULT_FIXTURE)
    calibrate.add_argument("--candidate-k", type=int, default=DEFAULT_CANDIDATE_K)
    calibrate.add_argument("--out", type=Path, default=DAY23_OUT)
    calibrate.add_argument("--allow-draft", action="store_true", help="testing only")


def ablation_config(
    run: str, top_k: int, candidate_k: int, threshold: float, threshold_source: str = "calibrated"
) -> Any:
    """PipelineConfig of one ablation run; baseline is the plain Day 22 top-k."""
    from agent.rag_pipeline import PipelineConfig

    if run == "baseline":
        return PipelineConfig(candidate_k=top_k, top_k=top_k, threshold=0.0)
    flags = ABLATION_RUNS[run]
    return PipelineConfig(
        candidate_k=candidate_k,
        top_k=top_k,
        threshold=threshold,
        threshold_source=threshold_source,
        lexical=flags.get("lexical", False),
        llm_rerank=flags.get("llm_rerank", False),
        hybrid=flags.get("hybrid", False),
        rewrite=flags.get("rewrite", False),
    )


def count_skips(traces: list[dict[str, Any]]) -> dict[str, int]:
    """Count skipped pipeline stages keyed by 'stage:reason'."""
    counts: dict[str, int] = {}
    for trace in traces:
        for skip in trace.get("skipped") or []:
            key = f"{skip['stage']}:{skip['reason']}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def count_incomplete(rows: list[dict[str, Any]]) -> int:
    """Answers that are empty or were cut off by the token limit."""
    return sum(
        1
        for row in rows
        if not (row.get("answer") or "").strip() or row.get("finish_reason") == "length"
    )


def _mean_num(values: list[float]) -> float | None:
    """Arithmetic mean, or None for an empty list."""
    return sum(values) / len(values) if values else None


def _num(value: float | None, digits: int = 0) -> str:
    """Render a mean with fixed decimals or n/a."""
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_ablation_md(raws: list[dict[str, Any]]) -> str:
    """Summary table with one row per ablation run."""
    header = [
        "run", "hit@1", "hit@3", "hit@5", "chunks before", "chunks after", "retrieval ms",
        "answer ms", "below_threshold", "пустых/обрезанных", "skipped",
    ]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for run in ABLATION_RUNS:
        rows = [raw for raw in raws if raw["run"] == run]
        if not rows:
            continue
        in_corpus = [raw["retrieval"] for raw in rows if raw["category"] != "out_of_corpus"]
        hits = [_num(_mean([r[f"hit{k}"] for r in in_corpus]), 2) for k in HIT_KS]
        skips = count_skips(rows)
        cells = [
            run,
            *hits,
            _num(_mean_num([float(r["chunks_before"]) for r in rows]), 1),
            _num(_mean_num([float(r["chunks_after"]) for r in rows]), 1),
            _num(_mean_num([float(r["retrieval_latency_ms"]) for r in rows])),
            _num(_mean_num([float(r["answer_latency_ms"]) for r in rows])),
            str(sum(1 for r in rows if r["verdict"] == "below_threshold")),
            str(count_incomplete(rows)),
            ", ".join(f"{key}={n}" for key, n in sorted(skips.items())) or "-",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


ANSWER_COLUMNS: tuple[str, ...] = (
    "id", "category", "run", "answer", "cited_sources", *JUDGE_COLUMNS
)


def render_ablation_answers_md(rows: list[dict[str, Any]]) -> str:
    """Markdown answers sheet (answers truncated); full text lives in the raw files."""
    lines = ["| " + " | ".join(ANSWER_COLUMNS) + " |", "|" + "|".join("---" for _ in ANSWER_COLUMNS) + "|"]
    for row in rows:
        answer = row.get("answer") or ""
        if len(answer) > ANSWER_TABLE_CHARS:
            answer = answer[:ANSWER_TABLE_CHARS] + "…"
        cells = [_cell(str(row.get(col) or "")) for col in ANSWER_COLUMNS]
        cells[ANSWER_COLUMNS.index("answer")] = _cell(answer)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_ablation_answers_csv(rows: list[dict[str, Any]]) -> str:
    """CSV answers sheet; verdict and judge columns are empty unless carried over."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ANSWER_COLUMNS)
    for row in rows:
        writer.writerow([row.get(col) or "" for col in ANSWER_COLUMNS])
    return buffer.getvalue()


def _load_existing_verdicts(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Filled verdict/comment/judge cells of an existing answers.csv keyed by (id, run)."""
    if not path.exists():
        return {}
    existing: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            kept = {col: row.get(col) or "" for col in JUDGE_COLUMNS if row.get(col)}
            if kept:
                existing[(row["id"], row["run"])] = kept
    return existing


def _render_ablation_outputs(
    opts: EvalOptions, fixture: dict[str, Any], meta: dict[str, Any]
) -> dict[str, Any]:
    """Render tables and run_meta.json from the complete set of raw files."""
    raws = _load_raw(opts.out / "raw")
    by_key = {(raw["run"], raw["id"]): raw for raw in raws}
    carried = _load_existing_verdicts(opts.out / "answers.csv")
    answer_rows: list[dict[str, Any]] = []
    for question in fixture["questions"]:
        for run in ABLATION_RUNS:
            raw = by_key.get((run, question["id"]))
            if raw is None:
                continue
            answer_rows.append(
                {
                    "id": question["id"],
                    "category": question["category"],
                    "run": run,
                    "answer": raw["answer"],
                    "cited_sources": "; ".join(raw.get("cited_sources") or []),
                    **carried.get((question["id"], run), {}),
                }
            )
    (opts.out / "ablation.md").write_text(render_ablation_md(raws), encoding="utf-8")
    (opts.out / "answers.md").write_text(render_ablation_answers_md(answer_rows), encoding="utf-8")
    (opts.out / "answers.csv").write_text(
        render_ablation_answers_csv(answer_rows), encoding="utf-8", newline=""
    )
    present = [run for run in ABLATION_RUNS if any(raw["run"] == run for raw in raws)]
    meta = {
        **meta,
        "runs": present,
        "incomplete_answers": {
            run: count_incomplete([raw for raw in raws if raw["run"] == run]) for run in present
        },
    }
    _write_json(opts.out / "run_meta.json", meta)
    return meta


def _failed_trace(config: Any, code: str) -> dict[str, Any]:
    """Trace stand-in for a retrieval that raised RagFailure."""
    return {
        "verdict": "kb_unavailable",
        "config": {"candidate_k": config.candidate_k, "top_k": config.top_k, "threshold": config.threshold},
        "stages": [],
        "stage_ms": {},
        "skipped": [],
        "candidates": [],
        "warning": code,
    }


async def _ablate_question(
    session: Any,
    kb: Any,
    client: Any,
    opts: EvalOptions,
    run: str,
    config: Any,
    question: dict[str, Any],
) -> dict[str, Any]:
    """Run one question through the pipeline and the answer model; return the raw record."""
    from agent.rag import (
        RagFailure,
        build_rag_block,
        merge_no_fragments_note,
        merge_rag_block,
        rag_budget,
    )
    from agent.rag_pipeline import VERDICT_BELOW_THRESHOLD, mark_over_budget, run_retrieval_pipeline

    started = time.perf_counter()
    warning: str | None = None
    try:
        chunks, trace = await run_retrieval_pipeline(
            session, kb, question["question"], config, client, opts.model
        )
    except RagFailure as failure:
        chunks, trace, warning = [], _failed_trace(config, failure.code), failure.code
    retrieval_ms = int((time.perf_counter() - started) * 1000)
    verdict = trace.pop("verdict")
    messages = [
        {"role": "system", "content": EVAL_SYSTEM_PROMPT},
        {"role": "user", "content": question["question"]},
    ]
    kept: list[dict[str, Any]] = []
    dropped = 0
    if verdict == VERDICT_BELOW_THRESHOLD:
        merge_no_fragments_note(messages)
    else:
        budget = rag_budget(opts.context_length, _message_tokens(messages), opts.max_tokens)
        block, kept, dropped = build_rag_block(chunks, budget)
        if block:
            merge_rag_block(messages, block)
        mark_over_budget(trace, len(kept))
    answer_started = time.perf_counter()
    reply = await _ask(client, opts, messages)
    answer_ms = int((time.perf_counter() - answer_started) * 1000)
    return {
        "run": run,
        "id": question["id"],
        "category": question["category"],
        "question": question["question"],
        "config": trace["config"],
        "search": trace,
        "retrieval": {**score_question(chunks, question, opts.top_k), "warning": warning, "dropped": dropped},
        "chunks_before": len(trace["candidates"]),
        "chunks_after": len(kept),
        "retrieval_latency_ms": retrieval_ms,
        "answer_latency_ms": answer_ms,
        "skipped": trace.get("skipped", []),
        "verdict": verdict,
        "messages": reply.pop("sent"),
        "chunks": [{k: v for k, v in c.items() if k != "text"} for c in chunks],
        "fragments": [c["text"] for c in kept],
        "cited_sources": _cited_sources(reply["answer"], kept),
        **reply,
    }


async def run_ablation(
    opts: EvalOptions,
    client: Any,
    session_factory: Any,
    runs: list[str],
    kb_label: str,
    kb_id: int,
    candidate_k: int,
    threshold: float,
    threshold_source: str = "calibrated",
) -> dict[str, Any]:
    """Answer every control question under each ablation run and write all outputs."""
    from shared.models import KnowledgeBase

    fixture = load_fixture(opts.fixture, require_frozen=not opts.allow_draft)
    raw_dir = opts.out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    async with session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        kb_meta = {
            "label": kb_label,
            "kb_id": kb_id,
            "embedding_model": kb.embedding_model,
            "strategy": getattr(kb.strategy, "value", kb.strategy),
            "chunk_size": kb.chunk_size,
            "chunk_overlap": kb.chunk_overlap,
        }
        for run in runs:
            config = ablation_config(run, opts.top_k, candidate_k, threshold, threshold_source)
            for question in fixture["questions"]:
                raw = await _ablate_question(session, kb, client, opts, run, config, question)
                _write_json(raw_dir / f"{run}_{question['id']}.json", raw)
    calibration_sha: str | None = None
    calibration_json = opts.out / "calibration.json"
    if calibration_json.exists():
        recorded = json.loads(calibration_json.read_text(encoding="utf-8"))
        calibration_sha = recorded.get("fixtures", {}).get("calibration", {}).get("sha256")
    meta = {
        "fixture": opts.fixture.name,
        "fixture_sha256": fixture_sha256(opts.fixture),
        "calibration_fixture_sha256": calibration_sha,
        "provider": opts.provider,
        "model": opts.model,
        "temperature": 0.0,
        "max_tokens": opts.max_tokens,
        "context_length": opts.context_length,
        "top_k": opts.top_k,
        "candidate_k": candidate_k,
        "threshold": threshold,
        "threshold_source": threshold_source,
        "kb": kb_meta,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return _render_ablation_outputs(opts, fixture, meta)


def _parse_runs(text: str | None) -> list[str] | None:
    """Comma list of run names in canonical order; None when a name is unknown."""
    if not text:
        return list(ABLATION_RUNS)
    names = [name.strip() for name in text.split(",") if name.strip()]
    if not names or any(name not in ABLATION_RUNS for name in names):
        return None
    return [run for run in ABLATION_RUNS if run in names]


async def ablate_command(args: argparse.Namespace) -> int:
    """Preflight, then run the ablation configurations through the shared pipeline."""
    runs = _parse_runs(args.runs)
    if runs is None:
        print(f"preflight: --runs must be a comma list of {', '.join(ABLATION_RUNS)}")
        return EXIT_PREFLIGHT
    if len(args.kb) != 1:
        print("preflight: ablate takes exactly one --kb LABEL=ID")
        return EXIT_PREFLIGHT
    _use_scratch_storage(args.db)
    (kb_label, kb_id), = _kb_ids(args.kb).items()
    load_fixture(args.fixture, require_frozen=True)

    from agent.llm_client import LLMClient
    from agent.providers import DEEPSEEK_BASE_URL
    from agent.rag import resolve_threshold
    from shared.config import settings
    from shared.database import async_session_factory, init_db
    from shared.models import KbStatus, KnowledgeBase

    await init_db()
    if args.provider == "deepseek":
        base_url = args.base_url or DEEPSEEK_BASE_URL
        api_key = settings.DEEPSEEK_API_KEY
        if not api_key:
            print("preflight: DEEPSEEK_API_KEY is not set")
            return EXIT_PREFLIGHT
    else:
        base_url = args.base_url or os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234")
        api_key = ""
        if not await _lm_studio_reachable(base_url):
            return EXIT_PREFLIGHT
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        if kb is None or kb.status != KbStatus.READY:
            print(f"preflight: knowledge base {kb_label}={kb_id} does not exist or is not ready")
            return EXIT_PREFLIGHT
        threshold, source = resolve_threshold(args.threshold, kb.embedding_model)
    if threshold == 0.0 and args.threshold is None:
        print(
            "preflight: no calibrated threshold for this embedder; run calibrate and store the "
            "constant first, or pass --threshold"
        )
        return EXIT_PREFLIGHT
    opts = EvalOptions(
        kb={kb_label: kb_id},
        provider=args.provider,
        model=args.model,
        top_k=args.top_k,
        context_length=args.context_length,
        max_tokens=args.max_tokens,
        fixture=args.fixture,
        out=args.out,
    )
    await run_ablation(
        opts,
        LLMClient(base_url, api_key),
        async_session_factory,
        runs,
        kb_label,
        kb_id,
        max(args.candidate_k, args.top_k),
        threshold,
        source,
    )
    print(f"ablation written to {opts.out} (runs: {', '.join(runs)})")
    return EXIT_OK


def _add_ablate_parser(sub: Any) -> None:
    """Register the ablate subcommand."""
    ablate = sub.add_parser("ablate", help="compare retrieval-pipeline configurations")
    ablate.add_argument("--db", type=Path, default=DAY23_DB)
    ablate.add_argument("--kb", action="append", required=True, metavar="LABEL=ID")
    ablate.add_argument("--provider", choices=("lmstudio", "deepseek"), default="lmstudio")
    ablate.add_argument("--model", default=DEFAULT_MODEL)
    ablate.add_argument("--base-url", default="")
    ablate.add_argument("--top-k", type=int, default=5)
    ablate.add_argument("--candidate-k", type=int, default=DEFAULT_CANDIDATE_K)
    ablate.add_argument("--threshold", type=float, default=None)
    ablate.add_argument("--runs", default=None, help="comma list; default all: " + ",".join(ABLATION_RUNS))
    ablate.add_argument("--context-length", type=int, default=16384)
    ablate.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_ABLATE_MAX_TOKENS,
        help="answer budget; 4096 is the Day 22 baseline value (1024 left most qwen3.5-9b "
        "answers empty because the budget went to reasoning) and it enters rag_budget",
    )
    ablate.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ablate.add_argument("--out", type=Path, default=DAY23_OUT)


def classify_reply(strict: bool, gated: bool, result: Any, reply: dict[str, Any]) -> str:
    """Kind of one cite record: gated, model_idk, empty, error or answer."""
    if reply.get("error"):
        return "error"
    if strict and gated:
        return "gated"
    if not (reply.get("answer") or "").strip():
        return "empty"
    if strict and result is not None and result.model_idk:
        return "model_idk"
    return "answer"


async def _cite_question(
    session: Any,
    kb: Any,
    client: Any,
    opts: EvalOptions,
    run: str,
    strict: bool,
    config: Any,
    question: dict[str, Any],
) -> dict[str, Any]:
    """Run one question through the chat's gate and citation rules; return the raw record."""
    from agent.rag import (
        RagFailure,
        build_rag_block,
        merge_no_fragments_note,
        merge_rag_block,
        rag_budget,
        sources_from_chunks,
    )
    from agent.rag_cite import build_idk_reply, process_answer
    from agent.rag_pipeline import VERDICT_BELOW_THRESHOLD, mark_over_budget, run_retrieval_pipeline

    started = time.perf_counter()
    warning: str | None = None
    try:
        chunks, trace = await run_retrieval_pipeline(
            session, kb, question["question"], config, client, opts.model
        )
    except RagFailure as failure:
        chunks, trace, warning = [], _failed_trace(config, failure.code), failure.code
    retrieval_ms = int((time.perf_counter() - started) * 1000)
    verdict = trace.pop("verdict")
    messages = [
        {"role": "system", "content": EVAL_SYSTEM_PROMPT},
        {"role": "user", "content": question["question"]},
    ]
    kept: list[dict[str, Any]] = []
    dropped = 0
    below = verdict == VERDICT_BELOW_THRESHOLD
    if below:
        if not strict:
            merge_no_fragments_note(messages)
    elif warning is None:
        budget = rag_budget(opts.context_length, _message_tokens(messages), opts.max_tokens)
        block, kept, dropped = build_rag_block(chunks, budget, strict=strict)
        if block:
            merge_rag_block(messages, block)
        mark_over_budget(trace, len(kept))
    gated = strict and warning is None and (below or not kept)
    answer_started = time.perf_counter()
    result = None
    if gated:
        reply: dict[str, Any] = {
            "answer": build_idk_reply(trace),
            "error": None,
            "finish_reason": None,
            "has_reasoning": False,
            "completion_tokens": None,
            "sent": [],
        }
        raw_answer = reply["answer"]
    elif warning is not None and strict:
        reply = {
            "answer": "",
            "error": f"retrieval: {warning}",
            "finish_reason": None,
            "has_reasoning": False,
            "completion_tokens": None,
            "sent": [],
        }
        raw_answer = ""
    else:
        reply = await _ask(client, opts, messages)
        raw_answer = reply["answer"]
        if strict and kept and not reply["error"]:
            result = process_answer(question["question"], raw_answer, kept)
            reply["answer"] = result.answer
    answer_ms = int((time.perf_counter() - answer_started) * 1000)
    fields = result.payload_fields(kept) if result is not None else {}
    quotes = fields.get("quotes", [])
    counts = {"exact": 0, "fuzzy": 0, "unverified": 0, "auto": 0}
    for quote in quotes:
        counts["auto" if quote["auto"] else quote["state"]] += 1
    cite_verdict = "below_threshold" if below else ("model_idk" if result and result.model_idk else "ok")
    return {
        "run": run,
        "id": question["id"],
        "category": question["category"],
        "question": question["question"],
        "strict": strict,
        "gated": gated,
        "kind": classify_reply(strict, gated, result, reply),
        "cite_verdict": cite_verdict,
        "config": trace["config"],
        "search": trace,
        "retrieval": {**score_question(chunks, question, opts.top_k), "warning": warning, "dropped": dropped},
        "chunks_before": len(trace["candidates"]),
        "chunks_after": len(kept),
        "retrieval_latency_ms": retrieval_ms,
        "answer_latency_ms": answer_ms,
        "skipped": trace.get("skipped", []),
        "verdict": verdict,
        "messages": reply.pop("sent"),
        "chunks": [{k: v for k, v in c.items() if k != "text"} for c in chunks],
        "fragments": [c["text"] for c in kept],
        "sources": sources_from_chunks(kept),
        "raw_answer": raw_answer,
        "quotes": quotes,
        "cited_ranks": fields.get("cited_ranks", []),
        "invalid_refs": fields.get("invalid_refs", 0),
        "answer_supported": fields.get("answer_supported"),
        "answer_empty": fields.get("answer_empty", False),
        "quote_counts": counts,
        "cited_sources": _cited_sources(reply["answer"], kept),
        **reply,
    }


async def run_cite(
    opts: EvalOptions,
    client: Any,
    session_factory: Any,
    runs: list[str],
    kb_id: int,
    candidate_k: int,
    threshold: float,
    threshold_source: str = "calibrated",
) -> list[dict[str, Any]]:
    """Answer every control question under each cite run and write the raw records."""
    from shared.models import KnowledgeBase

    fixture = load_fixture(opts.fixture, require_frozen=not opts.allow_draft)
    raw_dir = opts.out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    async with session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        for run in runs:
            spec = CITE_RUNS[run]
            config = ablation_config(
                spec["retrieval"], opts.top_k, candidate_k, threshold, threshold_source
            )
            for question in fixture["questions"]:
                raw = await _cite_question(
                    session, kb, client, opts, run, spec["strict"], config, question
                )
                _write_json(raw_dir / f"{run}_{question['id']}.json", raw)
                records.append(raw)
    return records


CITE_ANSWER_COLUMNS: tuple[str, ...] = (
    "id", "category", "run", "kind", "answer", "quotes", "sources_present", "quotes_present",
    "idk_correct", *JUDGE_COLUMNS,
)
REFUSAL_KINDS: tuple[str, ...] = ("gated", "model_idk")


def _kind_label(raw: dict[str, Any]) -> str:
    """Kind column text; an empty answer is reported as not received, never as a refusal."""
    if raw["kind"] == "empty":
        return f"ответ не получен ({raw.get('finish_reason') or 'пусто'})"
    return str(raw["kind"])


def _sources_present(raw: dict[str, Any]) -> str:
    """Automatic check: at least one source reached the answer; gated replies have none by design."""
    if raw["kind"] == "gated":
        return "—"
    return "да" if raw.get("sources") else "нет"


def _quotes_present(raw: dict[str, Any]) -> str:
    """Automatic check: the answer carries quotes, with the count by state."""
    if raw["kind"] in ("gated", "model_idk", "empty", "error"):
        return "—"
    counts = raw.get("quote_counts") or {}
    if not raw.get("quotes"):
        return "нет"
    return (
        f"да (модель: {counts.get('exact', 0)} exact, {counts.get('fuzzy', 0)} fuzzy, "
        f"{counts.get('unverified', 0)} unverified; авто: {counts.get('auto', 0)})"
    )


def _idk_correct(raw: dict[str, Any]) -> str:
    """Automatic check of the refusal: right on out-of-corpus questions, false on answerable ones."""
    refused = raw["kind"] in REFUSAL_KINDS
    if raw["category"] == "out_of_corpus":
        return "да" if refused else "нет"
    return "ложный отказ" if refused else "—"


def _quotes_cell(raw: dict[str, Any]) -> str:
    """Quotes of one record as '[rank] «text» (state[, auto][, rebound])' joined by ' | '."""
    parts: list[str] = []
    for quote in raw.get("quotes") or []:
        flags = [quote["state"]]
        if quote.get("auto"):
            flags.append("auto")
        if quote.get("rebound"):
            flags.append("rebound")
        parts.append(f"[{quote.get('rank')}] «{quote['text']}» ({', '.join(flags)})")
    return " | ".join(parts)


def cite_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Abstention and quote-state counters of one cite run."""
    ooc = [r for r in rows if r["category"] == "out_of_corpus"]
    answerable = [r for r in rows if r["category"] != "out_of_corpus"]
    refused_ooc = [r for r in ooc if r["kind"] in REFUSAL_KINDS]
    false_refusals = [r for r in answerable if r["kind"] in REFUSAL_KINDS]
    quote_totals = {"exact": 0, "fuzzy": 0, "unverified": 0, "auto": 0}
    for raw in rows:
        for key, value in (raw.get("quote_counts") or {}).items():
            quote_totals[key] += value
    tokens = [float(r["completion_tokens"]) for r in rows if r.get("completion_tokens") is not None]
    return {
        "total": len(rows),
        "answers": sum(1 for r in rows if r["kind"] == "answer"),
        "gated": sum(1 for r in rows if r["kind"] == "gated"),
        "model_idk": sum(1 for r in rows if r["kind"] == "model_idk"),
        "empty": sum(1 for r in rows if r["kind"] == "empty"),
        "error": sum(1 for r in rows if r["kind"] == "error"),
        "ooc_total": len(ooc),
        "ooc_idk": len(refused_ooc),
        "ooc_gated": sum(1 for r in refused_ooc if r["kind"] == "gated"),
        "ooc_model_idk": sum(1 for r in refused_ooc if r["kind"] == "model_idk"),
        "ooc_idk_rate": len(refused_ooc) / len(ooc) if ooc else None,
        "answerable_total": len(answerable),
        "false_refusals": len(false_refusals),
        "false_refusal_ids": [r["id"] for r in false_refusals],
        "false_refusal_rate": len(false_refusals) / len(answerable) if answerable else None,
        "quotes": quote_totals,
        "invalid_refs": sum(int(r.get("invalid_refs") or 0) for r in rows),
        "mean_completion_tokens": _mean_num(tokens),
    }


def render_cite_md(
    raws: list[dict[str, Any]],
    run: str,
    carried: dict[tuple[str, str], dict[str, str]] | None = None,
) -> str:
    """Per-question check table of one run; the meaning verdict column is filled by hand."""
    header = [
        "id", "category", "kind", "источники есть", "цитаты есть",
        "смысл совпадает с цитатами (" + "/".join(MEANING_VERDICTS) + ")", "корректное «не знаю»",
    ]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for raw in (r for r in raws if r["run"] == run):
        manual = (carried or {}).get((raw["id"], run), {}).get("verdict", "")
        cells = [
            raw["id"], raw["category"], _kind_label(raw), _sources_present(raw),
            _quotes_present(raw), manual, _idk_correct(raw),
        ]
        lines.append("| " + " | ".join(_cell(str(cell)) for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def _share(count: int, total: int) -> str:
    """'n/total' counter text."""
    return f"{count}/{total}"


def render_cite_summary_md(raws: list[dict[str, Any]]) -> str:
    """One summary row per run: abstention counters and quote counts by state."""
    header = [
        "run", "ответов", "gated", "model_idk", "пустых", "ошибок", "«не знаю» на вне корпуса",
        "ложные отказы", "цитаты модели exact", "fuzzy", "unverified", "авто-цитаты",
        "невалидных ссылок", "ср. токенов ответа",
    ]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for run in CITE_RUNS:
        rows = [raw for raw in raws if raw["run"] == run]
        if not rows:
            continue
        metrics = cite_metrics(rows)
        refusals = _share(metrics["false_refusals"], metrics["answerable_total"])
        if metrics["false_refusal_ids"]:
            refusals += " (" + ", ".join(metrics["false_refusal_ids"]) + ")"
        cells = [
            run,
            str(metrics["answers"]),
            str(metrics["gated"]),
            str(metrics["model_idk"]),
            str(metrics["empty"]),
            str(metrics["error"]),
            _share(metrics["ooc_idk"], metrics["ooc_total"]),
            refusals,
            str(metrics["quotes"]["exact"]),
            str(metrics["quotes"]["fuzzy"]),
            str(metrics["quotes"]["unverified"]),
            str(metrics["quotes"]["auto"]),
            str(metrics["invalid_refs"]),
            _num(metrics["mean_completion_tokens"]),
        ]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def render_cite_answers_csv(rows: list[dict[str, Any]]) -> str:
    """CSV answers sheet; manual and judge columns are empty unless carried over."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CITE_ANSWER_COLUMNS)
    for row in rows:
        writer.writerow([row.get(col) or "" for col in CITE_ANSWER_COLUMNS])
    return buffer.getvalue()


def render_cite_answers_md(rows: list[dict[str, Any]]) -> str:
    """Markdown answers sheet (answers truncated); full text lives in the raw files."""
    columns = CITE_ANSWER_COLUMNS
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        cells = {col: str(row.get(col) or "") for col in columns}
        if len(cells["answer"]) > ANSWER_TABLE_CHARS:
            cells["answer"] = cells["answer"][:ANSWER_TABLE_CHARS] + "…"
        if len(cells["quotes"]) > ANSWER_TABLE_CHARS:
            cells["quotes"] = cells["quotes"][:ANSWER_TABLE_CHARS] + "…"
        lines.append("| " + " | ".join(_cell(cells[col]) for col in columns) + " |")
    return "\n".join(lines) + "\n"


def _render_cite_outputs(
    opts: EvalOptions, fixture: dict[str, Any], meta: dict[str, Any], raws: list[dict[str, Any]]
) -> dict[str, Any]:
    """Render the tables, the answers sheet and run_meta.json from the raw records."""
    by_key = {(raw["run"], raw["id"]): raw for raw in raws}
    carried = _load_existing_verdicts(opts.out / "answers.csv")
    present = [run for run in CITE_RUNS if any(raw["run"] == run for raw in raws)]
    answer_rows: list[dict[str, Any]] = []
    for question in fixture["questions"]:
        for run in present:
            raw = by_key.get((run, question["id"]))
            if raw is None:
                continue
            answer_rows.append(
                {
                    "id": question["id"],
                    "category": question["category"],
                    "run": run,
                    "kind": _kind_label(raw),
                    "answer": raw["answer"],
                    "quotes": _quotes_cell(raw),
                    "sources_present": _sources_present(raw),
                    "quotes_present": _quotes_present(raw),
                    "idk_correct": _idk_correct(raw),
                    **carried.get((question["id"], run), {}),
                }
            )
    for run in present:
        (opts.out / f"cite_{run}.md").write_text(render_cite_md(raws, run, carried), encoding="utf-8")
    (opts.out / "cite_summary.md").write_text(render_cite_summary_md(raws), encoding="utf-8")
    (opts.out / "answers.md").write_text(render_cite_answers_md(answer_rows), encoding="utf-8")
    (opts.out / "answers.csv").write_text(
        render_cite_answers_csv(answer_rows), encoding="utf-8", newline=""
    )
    meta = {
        **meta,
        "runs": present,
        "kind_counts": {
            run: {
                kind: sum(1 for r in raws if r["run"] == run and r["kind"] == kind)
                for kind in ("answer", "gated", "model_idk", "empty", "error")
            }
            for run in present
        },
    }
    _write_json(opts.out / "run_meta.json", meta)
    return meta


def _parse_cite_runs(text: str | None) -> list[str] | None:
    """Comma list of cite run names in canonical order; None when a name is unknown."""
    if not text:
        return list(CITE_RUNS)
    names = [name.strip() for name in text.split(",") if name.strip()]
    if not names or any(name not in CITE_RUNS for name in names):
        return None
    return [run for run in CITE_RUNS if run in names]


def _read_day23_meta(path: Path) -> dict[str, Any] | None:
    """Day 23 run_meta.json with a winner block, or None."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if data.get("winner") else None


async def _cite_render_only(args: argparse.Namespace) -> int:
    """Re-render the tables from existing raw files without any model call."""
    raw_dir = args.out / "raw"
    if not raw_dir.is_dir() or not any(raw_dir.glob("*.json")):
        print(f"preflight: no raw files in {raw_dir}")
        return EXIT_PREFLIGHT
    fixture = load_fixture(args.fixture, require_frozen=True)
    meta_path = args.out / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    opts = EvalOptions(kb={}, fixture=args.fixture, out=args.out)
    _render_cite_outputs(opts, fixture, meta, _load_raw(raw_dir))
    print(f"day 24 tables re-rendered in {args.out}")
    return EXIT_OK


async def cite_command(args: argparse.Namespace) -> int:
    """Preflight, then run the frozen control questions through the chat's gate and citation rules."""
    runs = _parse_cite_runs(args.runs)
    if runs is None:
        print(f"preflight: --runs must be a comma list of {', '.join(CITE_RUNS)}")
        return EXIT_PREFLIGHT
    if args.render_only:
        return await _cite_render_only(args)
    day23 = _read_day23_meta(args.meta)
    if day23 is None:
        print(f"preflight: {args.meta} is missing or has no winner block")
        return EXIT_PREFLIGHT
    fixture = load_fixture(args.fixture, require_frozen=True)
    if args.kb:
        (kb_label, kb_id), = _kb_ids([args.kb]).items()
    else:
        kb_label, kb_id = day23["winner"]["kb_label"], int(day23["winner"]["kb_id"])
    model = args.model or day23.get("model") or DEFAULT_MODEL
    top_k = args.top_k or int(day23.get("top_k", 5))
    candidate_k = max(args.candidate_k or int(day23.get("candidate_k", DEFAULT_CANDIDATE_K)), top_k)
    _use_scratch_storage(args.db)

    from agent.llm_client import LLMClient
    from agent.rag import resolve_threshold
    from shared.database import async_session_factory, init_db
    from shared.models import KbStatus, KnowledgeBase

    await init_db()
    base_url = args.base_url or os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234")
    if not await _lm_studio_reachable(base_url):
        return EXIT_PREFLIGHT
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        if kb is None or kb.status != KbStatus.READY:
            print(f"preflight: knowledge base {kb_label}={kb_id} does not exist or is not ready")
            return EXIT_PREFLIGHT
        threshold, source = resolve_threshold(args.threshold, kb.embedding_model)
        kb_meta = {
            "label": kb_label,
            "kb_id": kb_id,
            "embedding_model": kb.embedding_model,
            "strategy": getattr(kb.strategy, "value", kb.strategy),
            "chunk_size": kb.chunk_size,
            "chunk_overlap": kb.chunk_overlap,
        }
    if threshold == 0.0 and args.threshold is None:
        print("preflight: no calibrated threshold for this embedder; pass --threshold")
        return EXIT_PREFLIGHT
    opts = EvalOptions(
        kb={kb_label: kb_id},
        model=model,
        top_k=top_k,
        context_length=args.context_length,
        max_tokens=args.max_tokens,
        fixture=args.fixture,
        out=args.out,
    )
    await run_cite(
        opts, LLMClient(base_url, ""), async_session_factory, runs, kb_id, candidate_k, threshold, source
    )
    meta = {
        "fixture": args.fixture.name,
        "fixture_sha256": fixture_sha256(args.fixture),
        "provider": "lmstudio",
        "model": model,
        "temperature": 0.0,
        "max_tokens": args.max_tokens,
        "context_length": args.context_length,
        "top_k": top_k,
        "candidate_k": candidate_k,
        "threshold": threshold,
        "threshold_source": source,
        "kb": kb_meta,
        "winner": day23["winner"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # A partial --runs re-run keeps the records of the other runs already on disk.
    _render_cite_outputs(opts, fixture, meta, _load_raw(opts.out / "raw"))
    print(f"day 24 results written to {opts.out} (runs: {', '.join(runs)})")
    return EXIT_OK


def _add_cite_parser(sub: Any) -> None:
    """Register the cite subcommand."""
    cite = sub.add_parser("cite", help="check citations and «не знаю» on the control questions")
    cite.add_argument("--db", type=Path, default=DAY23_DB)
    cite.add_argument("--meta", type=Path, default=DAY23_META)
    cite.add_argument("--kb", default=None, metavar="LABEL=ID", help="default: winner of the meta file")
    cite.add_argument("--model", default=None, help="default: model of the meta file")
    cite.add_argument("--base-url", default="")
    cite.add_argument("--top-k", type=int, default=None)
    cite.add_argument("--candidate-k", type=int, default=None)
    cite.add_argument("--threshold", type=float, default=None)
    cite.add_argument("--runs", default=None, help="comma list; default all: " + ",".join(CITE_RUNS))
    cite.add_argument("--context-length", type=int, default=16384)
    cite.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_CITE_MAX_TOKENS,
        help="answer budget; 8192 leaves room for reasoning before the quotes section",
    )
    cite.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    cite.add_argument("--out", type=Path, default=DAY24_OUT)
    cite.add_argument(
        "--render-only", action="store_true", help="re-render tables from raw files, no model calls"
    )


def _add_dialog_parser(sub: Any) -> None:
    """Register the dialog subcommand."""
    dialog = sub.add_parser(
        "dialog", help="drive the frozen multi-turn scenarios through the real chat on an isolated copy"
    )
    dialog.add_argument("--runs", default="main,baseline", help="comma list: main, baseline")
    dialog.add_argument("--scenarios", default=None, help="comma list of scenario ids; default all")
    dialog.add_argument(
        "--fixture", type=Path, default=REPO_ROOT / "tests" / "fixtures" / "rag" / "dialog_scenarios.json"
    )
    dialog.add_argument("--out", type=Path, default=REPO_ROOT / "eval_out" / "day25")
    dialog.add_argument("--source-db", type=Path, default=DAY23_DB)
    dialog.add_argument("--source-kb", type=Path, default=DAY23_DB.parent / "eval_kb")
    dialog.add_argument("--kb-id", type=int, default=2)
    dialog.add_argument("--model", default=DEFAULT_MODEL)
    dialog.add_argument("--history-turns", type=int, default=3)
    dialog.add_argument("--max-tokens", type=int, default=DEFAULT_CITE_MAX_TOKENS)
    dialog.add_argument("--context-length", type=int, default=16384)
    dialog.add_argument("--turn-timeout", type=float, default=240.0)
    dialog.add_argument(
        "--check", action="store_true", help="start the isolated app, log in, verify the KB; no chat"
    )
    dialog.add_argument("--force", action="store_true", help="re-run scenarios whose raw file exists")


async def dialog_command(args: argparse.Namespace) -> int:
    """Run the dialog sub-command (implemented in the sibling module rag_dialog)."""
    import rag_dialog

    return await rag_dialog.dialog_command(args)


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    parser = argparse.ArgumentParser(description="Offline RAG evaluation runner.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-kbs", help="index the corpus once per embedder")
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--strategy", default="structural")
    build.add_argument("--chunk-size", type=int, default=1000)
    build.add_argument("--chunk-overlap", type=int, default=150)
    build.add_argument("--embedder", action="append", metavar="LABEL=MODEL_ID")
    build.add_argument("--pdf", action="append", type=Path)
    run = sub.add_parser("run", help="score retrieval and collect answers")
    run.add_argument("--db", type=Path, default=DEFAULT_DB)
    run.add_argument("--kb", action="append", required=True, metavar="LABEL=ID")
    run.add_argument("--provider", choices=("lmstudio", "deepseek"), default="lmstudio")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--base-url", default="")
    run.add_argument("--top-k", type=int, default=5)
    run.add_argument("--modes", default="off,rag")
    run.add_argument("--context-length", type=int, default=16384)
    run.add_argument("--max-tokens", type=int, default=1024)
    run.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    run.add_argument("--allow-draft", action="store_true", help="testing only")
    run.add_argument("--only-kb", metavar="LABEL", default=None)
    _add_calibrate_parser(sub)
    _add_ablate_parser(sub)
    _add_cite_parser(sub)
    _add_dialog_parser(sub)
    return parser


def main() -> int:
    """Entry point."""
    args = build_parser().parse_args()
    handlers = {
        "build-kbs": build_kbs,
        "run": run_command,
        "calibrate": calibrate_command,
        "ablate": ablate_command,
        "cite": cite_command,
        "dialog": dialog_command,
    }
    return asyncio.run(handlers[args.command](args))


if __name__ == "__main__":
    sys.exit(main())
