"""Structure checks for the Day 23 report and its verdict tables."""

import csv
import hashlib
import json
import re
from pathlib import Path

from agent.rag import CALIBRATED_THRESHOLDS

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
REPORT: Path = REPO_ROOT / "Day23_report.md"
OUT_DIR: Path = REPO_ROOT / "eval_out" / "day23"
FIXTURES: Path = REPO_ROOT / "tests" / "fixtures" / "rag"
ALLOWED_VERDICTS: set[str] = {"верно", "частично", "неверно", "галлюцинация"}
HEADINGS: tuple[str, ...] = (
    "Постановка",
    "Калибровка порога",
    "Конвейер поиска",
    "Сравнение конфигураций",
    "Порог: без фильтра и с фильтром",
    "Реранкеры",
    "Гибридный поиск (FTS5 + RRF)",
    "Переписывание запроса",
    "LLM-судья (DeepSeek)",
    "Где этап не помог или навредил",
    "Ограничения",
)
RUN_LABELS: tuple[str, ...] = (
    "baseline",
    "threshold",
    "lexical",
    "llm_rerank",
    "hybrid",
    "rewrite",
    "all",
)


def _report_text() -> str:
    return REPORT.read_text(encoding="utf-8")


def _section_body(title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", _report_text(), re.S | re.M)
    assert match is not None, title
    return match.group(1).strip()


def _fixture_hashes(name: str) -> set[str]:
    """sha256 of the file bytes and of the same bytes with LF line endings."""
    data = (FIXTURES / name).read_bytes()
    return {
        hashlib.sha256(data).hexdigest(),
        hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
    }


def test_report_exists_and_is_long_enough() -> None:
    assert REPORT.is_file()
    assert len(_report_text().splitlines()) >= 100


def test_report_has_all_headings() -> None:
    headings = re.findall(r"^## (.+)$", _report_text(), re.M)
    for title in HEADINGS:
        assert title in headings


def test_report_mentions_every_run() -> None:
    text = _report_text()
    for label in RUN_LABELS:
        assert label in text


def test_report_lists_every_question() -> None:
    text = _report_text()
    for index in range(1, 11):
        assert f"Q{index:02d}" in text


def test_report_mentions_both_embedders() -> None:
    text = _report_text()
    assert "nomic" in text
    assert "bge-m3" in text


def test_report_quotes_fixture_sha256() -> None:
    text = _report_text()
    assert "sha256" in text
    for name in ("control_set.json", "calibration_set.json"):
        assert any(digest in text for digest in _fixture_hashes(name)), name


def test_report_quotes_shipped_thresholds() -> None:
    text = _report_text()
    for value in CALIBRATED_THRESHOLDS.values():
        assert f"{value:.2f}" in text


def test_report_links_eval_output() -> None:
    assert "eval_out/day23" in _report_text()


def test_honesty_section_is_not_empty() -> None:
    assert len(_section_body("Где этап не помог или навредил")) > 0


def test_answers_csv_verdicts_are_filled_and_valid() -> None:
    with (OUT_DIR / "answers.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 70
    for row in rows:
        assert row["verdict"] in ALLOWED_VERDICTS
        assert row["judge_verdict"] in ALLOWED_VERDICTS


def test_report_manual_counts_match_answers_csv() -> None:
    with (OUT_DIR / "answers.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    text = _report_text()
    for run in RUN_LABELS:
        counts = [sum(1 for r in rows if r["run"] == run and r["verdict"] == v) for v in
                  ("верно", "частично", "неверно", "галлюцинация")]
        cell = " / ".join(str(c) for c in counts)
        assert re.search(rf"^\| {run} \|.*\| {re.escape(cell)} \|", text, re.M), run


def test_report_agreement_matches_judge_meta() -> None:
    meta = json.loads((OUT_DIR / "judge_meta.json").read_text(encoding="utf-8"))
    agreement = meta["agreement"]
    assert f"{agreement['matching']} из {agreement['compared']}" in _report_text()
