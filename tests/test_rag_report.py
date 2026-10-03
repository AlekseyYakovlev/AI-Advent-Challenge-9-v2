"""Structure checks for the Day 22 report and its manual verdicts."""

import csv
import json
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
REPORT: Path = REPO_ROOT / "Day22_report.md"
OUT_DIR: Path = REPO_ROOT / "eval_out" / "day22"
ALLOWED_VERDICTS: set[str] = {"верно", "частично", "неверно", "галлюцинация"}


def _report_text() -> str:
    return REPORT.read_text(encoding="utf-8")


def test_report_exists() -> None:
    assert REPORT.is_file()


def test_report_lists_every_question() -> None:
    text = _report_text()
    for index in range(1, 11):
        assert f"Q{index:02d}" in text


def test_report_mentions_required_terms() -> None:
    text = _report_text()
    for term in ("nomic", "bge-m3", "hit@1", "hit@5", "giga"):
        assert term in text


def test_report_quotes_fixture_sha256() -> None:
    meta = json.loads((OUT_DIR / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["fixture_sha256"] in _report_text()


def test_report_has_honesty_section() -> None:
    assert "Где RAG не помог или навредил" in _report_text()


def test_answers_csv_verdicts_are_filled_and_valid() -> None:
    with (OUT_DIR / "answers.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 30
    for row in rows:
        assert row["verdict"] in ALLOWED_VERDICTS
