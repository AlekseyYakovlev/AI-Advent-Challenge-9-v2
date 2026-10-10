"""Structure and consistency checks of the Day 25 report against the run outputs."""

import csv
import hashlib
import json
import re
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
REPORT: Path = REPO_ROOT / "Day25_report.md"
OUT_DIR: Path = REPO_ROOT / "eval_out" / "day25"
FIXTURE: Path = REPO_ROOT / "tests" / "fixtures" / "rag" / "dialog_scenarios.json"
JUDGE_VERDICTS: set[str] = {"да", "частично", "нет", "—"}
RUN_NAMES: tuple[str, ...] = ("main", "baseline")
HEADINGS: tuple[str, ...] = (
    "Постановка",
    "Память задачи: что делает код",
    "Сценарий A: дело водителя",
    "Сценарий B: изучение ФЗ-196",
    "Уточняющие вопросы: с памятью и без",
    "Удержание цели",
    "LLM-судья (DeepSeek)",
    "Отказы и сбои",
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


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _fixture_hashes() -> set[str]:
    """sha256 of the file bytes and of the same bytes with LF line endings."""
    data = FIXTURE.read_bytes()
    return {
        hashlib.sha256(data).hexdigest(),
        hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
    }


def test_report_exists_and_is_long_enough() -> None:
    assert REPORT.is_file()
    assert len(_report_text().splitlines()) >= 120


def test_report_has_all_headings_in_order() -> None:
    headings = re.findall(r"^## (.+)$", _report_text(), re.M)
    positions = [headings.index(title) for title in HEADINGS]
    assert positions == sorted(positions)


def test_report_lists_every_turn_and_run() -> None:
    text = _report_text()
    for scenario in _fixture()["scenarios"]:
        for turn in scenario["turns"]:
            assert turn["id"] in text
    for run in RUN_NAMES:
        assert run in text


def test_report_quotes_fixture_sha256_and_links_output() -> None:
    text = _report_text()
    assert "sha256" in text
    assert any(digest in text for digest in _fixture_hashes())
    assert "eval_out/day25" in text
    assert "dialog_scenarios.json" in text


def test_report_names_the_canonical_followup_and_the_baseline_switch() -> None:
    text = _report_text()
    assert "а за повторное?" in text
    assert "TASK_MEMORY_ENABLED" in text


def test_answers_csv_rows_match_fixture_turns() -> None:
    rows = _rows()
    for scenario in _fixture()["scenarios"]:
        for run in RUN_NAMES:
            count = sum(1 for r in rows if r["run"] == run and r["scenario"] == scenario["id"])
            assert count == len(scenario["turns"])


def test_answers_csv_has_no_manual_verdict_column() -> None:
    columns = _rows()[0].keys()
    assert not [name for name in columns if "user_verdict" in name or "manual" in name]


def test_judge_values_are_valid() -> None:
    for row in _rows():
        value = row["judge_goal"]
        if not value:
            continue
        assert value in JUDGE_VERDICTS or value.startswith("ошибка")


def test_judge_meta_rubric_when_present() -> None:
    path = OUT_DIR / "judge_meta.json"
    if path.is_file():
        assert json.loads(path.read_text(encoding="utf-8"))["rubric"] == "goal_adherence"
    else:
        assert "не запускался" in _section_body("LLM-судья (DeepSeek)")


def test_failures_section_is_not_empty() -> None:
    assert len(_section_body("Отказы и сбои")) > 0


def test_gated_count_in_report_matches_answers_csv() -> None:
    gated = [r["turn"] for r in _rows() if r["run"] == "main" and r["verdict"] == "gated"]
    body = _section_body("Отказы и сбои")
    match = re.search(r"main, шлюз кода \(gated\): (\d+) из 24", body)
    assert match is not None
    assert int(match.group(1)) == len(gated)
    for turn in gated:
        assert turn in body


def test_error_turn_count_in_report_matches_answers_csv() -> None:
    errors = [r for r in _rows() if r["verdict"] == "error"]
    match = re.search(r"Ходы с ошибкой \(`error`\): (\d+) из 48", _section_body("Отказы и сбои"))
    assert match is not None
    assert int(match.group(1)) == len(errors)
