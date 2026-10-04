"""Structure checks for the Day 24 report and its verdict sheet."""

import csv
import hashlib
import re
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
REPORT: Path = REPO_ROOT / "Day24_report.md"
OUT_DIR: Path = REPO_ROOT / "eval_out" / "day24"
FIXTURE: Path = REPO_ROOT / "tests" / "fixtures" / "rag" / "control_set.json"
ANSWER_VERDICTS: set[str] = {"да", "частично", "нет"}
ALLOWED_VERDICTS: set[str] = ANSWER_VERDICTS | {"—"}
CHECKED_RUNS: tuple[str, ...] = ("strict", "strict_baseline")
RUN_NAMES: tuple[str, ...] = ("strict", "strict_off", "strict_baseline")
HEADINGS: tuple[str, ...] = (
    "Постановка",
    "Строгий режим: что проверяет код",
    "Проверка по контрольным вопросам",
    "Отказы: «не знаю» и ложные отказы",
    "Цитаты: модель и автоподбор",
    "Строгий режим выключен: сравнение",
    "Без порога: поиск Day 23 baseline",
    "LLM-судья (DeepSeek)",
    "Где строгий режим не помог или навредил",
    "Ограничения",
)


def _report_text() -> str:
    return REPORT.read_text(encoding="utf-8")


def _section_body(title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", _report_text(), re.S | re.M)
    assert match is not None, title
    return match.group(1).strip()


def _rows() -> list[dict[str, str]]:
    with (OUT_DIR / "answers.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _fixture_hashes() -> set[str]:
    """sha256 of the file bytes and of the same bytes with LF line endings."""
    data = FIXTURE.read_bytes()
    return {
        hashlib.sha256(data).hexdigest(),
        hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
    }


def test_report_exists_and_is_long_enough() -> None:
    assert REPORT.is_file()
    assert len(_report_text().splitlines()) >= 80


def test_report_has_all_headings() -> None:
    headings = re.findall(r"^## (.+)$", _report_text(), re.M)
    for title in HEADINGS:
        assert title in headings


def test_report_lists_every_question_and_run() -> None:
    text = _report_text()
    for index in range(1, 11):
        assert f"Q{index:02d}" in text
    for run in RUN_NAMES:
        assert run in text


def test_report_quotes_fixture_sha256_and_links_output() -> None:
    text = _report_text()
    assert "sha256" in text
    assert any(digest in text for digest in _fixture_hashes())
    assert "eval_out/day24" in text


def test_report_names_false_refusals_and_auto_quotes() -> None:
    text = _report_text()
    assert "ложн" in text
    assert "подобран" in text


def test_honesty_section_is_not_empty() -> None:
    assert len(_section_body("Где строгий режим не помог или навредил")) > 0


def test_answers_csv_verdicts_are_valid() -> None:
    rows = _rows()
    assert rows
    for row in rows:
        if row["run"] not in CHECKED_RUNS:
            continue
        assert row["verdict"] in ALLOWED_VERDICTS
        if row["kind"] == "answer":
            assert row["verdict"] in ANSWER_VERDICTS
            assert row["comment"].strip()


def test_report_false_refusal_count_matches_answers_csv() -> None:
    rows = _rows()
    false_refusals = [
        row
        for row in rows
        if row["run"] == "strict"
        and row["category"] != "out_of_corpus"
        and row["kind"] in {"gated", "model_idk"}
    ]
    body = _section_body("Отказы: «не знаю» и ложные отказы")
    assert f"{len(false_refusals)} из 8" in body
    for row in false_refusals:
        assert row["id"] in body
