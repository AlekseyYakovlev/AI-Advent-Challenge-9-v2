"""Offline RAG evaluation: build comparable knowledge bases and score retrieval and answers.

Usage:
  python scripts/rag_eval.py build-kbs [--db PATH] [--embedder LABEL=MODEL_ID ...] [--pdf PATH ...]
  python scripts/rag_eval.py run --kb LABEL=ID [--kb LABEL=ID ...] [--provider lmstudio|deepseek]
                                 [--model ID] [--top-k 5] [--modes off,rag] [--only-kb LABEL]

build-kbs indexes the corpus PDFs once per embedder with identical strategy, chunk size and
overlap into a scratch database (never app.db). run answers every control-set question without
and with retrieval at temperature 0, scores retrieval hit@k against the expected sources and
writes raw outputs, retrieval.md, answers.md, answers.csv (empty verdict column) and run_meta.json.

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
import sys
from dataclasses import dataclass, field
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
