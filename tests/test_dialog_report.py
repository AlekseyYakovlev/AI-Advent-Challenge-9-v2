"""Tests for the dialog report renderers: canned raw records, no app and no network."""

import argparse
import csv
import importlib.util
import io
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rag_dialog", REPO_ROOT / "scripts" / "rag_dialog.py")
rag_dialog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_dialog)

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "rag" / "dialog_scenarios.json"
EXPECTED_COLUMNS = [
    "run", "scenario", "turn", "kind", "question", "answer", "scenario_goal", "memory", "verdict",
    "sources_present", "quotes", "memory_ok", "article_ok", "goal_kept", "condensed_query",
    "judge_goal", "judge_goal_reason",
]
SCENARIOS = [
    {
        "id": "A",
        "goal": "Узнать штраф",
        "turns": [
            {"id": "A01", "kind": "direct", "text": "q1", "expect_article": "12.9"},
            {"id": "A02", "kind": "followup", "text": "а повторное?", "expect_article": "12.9"},
            {"id": "A03", "kind": "followup", "text": "а 110?", "expect_article": "12.9"},
            {"id": "A04", "kind": "direct", "text": "q4", "expect_article": None},
        ],
    }
]


def checks(**over: Any) -> dict[str, Any]:
    """Canned checks block."""
    base: dict[str, Any] = {
        "sources_present": True, "quotes_model": 2, "quotes_auto": 1, "quotes_unverified": 0,
        "verdict": "ok", "memory_expected": 2, "memory_found": 2, "memory_ok": True,
        "article_expected": "12.9", "article_ok": True, "goal_text": "штраф", "goal_unchanged": True,
        "keywords_ok": True, "goal_kept": True, "condensed": True, "condensed_query": "штраф повторно",
        "memory_failed": False,
    }
    base.update(over)
    return base


def rag(**over: Any) -> dict[str, Any]:
    """Canned rag payload."""
    base: dict[str, Any] = {
        "verdict": "ok", "gated": False,
        "sources": [{"file": "koap.pdf", "section": "Глава 12 > Статья 12.9. Скорость"}],
        "search": {"best_cosine": 0.8123, "condensed": True, "rewritten": "штраф повторно"},
    }
    base.update(over)
    return base


def turn(tid: str, kind: str, **over: Any) -> dict[str, Any]:
    """Canned turn record."""
    snapshot = {
        "goal": "штраф", "clarified": [{"id": 1, "text": "95 км/ч"}, {"id": 2, "text": "лимит 60"}],
        "constraints": [{"id": 3, "text": "только КоАП"}], "new": {"goal": True, "ids": [2]},
        "failed": False,
    }
    record: dict[str, Any] = {
        "id": tid, "kind": kind, "question": f"вопрос {tid}", "answer": f"ответ {tid}",
        "frame_type": "done", "error": None, "elapsed_s": 1.0, "rag": rag(),
        "task_memory": snapshot, "checks": checks(),
    }
    record.update(over)
    return record


def main_raw() -> dict[str, Any]:
    """Main run: ok, follow-up ok, gated follow-up with failed memory, error turn."""
    failed = {"goal": None, "clarified": [], "constraints": [], "new": {"goal": False, "ids": []}, "failed": True}
    return {
        "run": "main", "scenario": "A",
        "turns": [
            turn("A01", "direct"),
            turn("A02", "followup"),
            turn(
                "A03", "followup", rag=rag(gated=True, verdict="gated"), task_memory=failed,
                checks=checks(verdict="gated", article_ok=False, memory_failed=True),
            ),
            turn(
                "A04", "direct", frame_type="error", error="LLM_TIMEOUT: slow", rag=None,
                task_memory=None, checks=checks(verdict="error", sources_present=False, article_ok=None),
            ),
        ],
    }


def baseline_raw() -> dict[str, Any]:
    """Baseline run: no task memory, follow-up gated."""
    gated_checks = checks(
        verdict="gated", article_ok=False, condensed=False, condensed_query=None, memory_ok=None,
        goal_kept=None,
    )
    return {
        "run": "baseline", "scenario": "A",
        "turns": [
            turn("A01", "direct", task_memory=None),
            turn("A02", "followup", task_memory=None, rag=rag(gated=True), checks=gated_checks),
        ],
    }


def data_rows(markdown: str) -> list[str]:
    """Table body rows of a markdown table."""
    lines = [line for line in markdown.splitlines() if line.startswith("|")]
    return lines[2:]


def test_transcript_has_one_row_per_turn_in_order() -> None:
    text = rag_dialog.render_transcript_md(main_raw(), SCENARIOS[0])
    rows = data_rows(text)
    assert len(rows) == 4
    assert [row.split("|")[1].strip() for row in rows] == ["A01", "A02", "A03", "A04"]
    assert "Узнать штраф" in text


def test_transcript_row_has_sources_quotes_memory_and_checks() -> None:
    row = data_rows(rag_dialog.render_transcript_md(main_raw(), SCENARIOS[0]))[0]
    assert "koap.pdf ст. 12.9" in row
    assert "штраф повторно" in row
    assert "модель 2, авто 1, не подтв. 0" in row
    assert "лимит 60 (новое)" in row
    assert "95 км/ч (новое)" not in row
    assert "цель: штраф (новое)" in row
    assert "ответ" in row
    assert "цель сохранена 1" in row


def test_gated_turn_is_a_row() -> None:
    row = data_rows(rag_dialog.render_transcript_md(main_raw(), SCENARIOS[0]))[2]
    assert "шлюз: не знаю" in row


def test_error_turn_is_a_row_with_detail() -> None:
    row = data_rows(rag_dialog.render_transcript_md(main_raw(), SCENARIOS[0]))[3]
    assert "ошибка: LLM_TIMEOUT: slow" in row
    assert "нет" in row


def test_failed_memory_is_marked() -> None:
    row = data_rows(rag_dialog.render_transcript_md(main_raw(), SCENARIOS[0]))[2]
    assert "память не обновлена" in row


def test_baseline_turn_without_snapshot_shows_dash() -> None:
    row = data_rows(rag_dialog.render_transcript_md(baseline_raw(), SCENARIOS[0]))[0]
    assert row.split("|")[8].strip() == "—"


def test_pipes_and_newlines_keep_one_row_per_turn() -> None:
    raw = main_raw()
    raw["turns"][0]["question"] = "a | b\nc"
    raw["turns"][0]["task_memory"]["clarified"][0]["text"] = "x|y\nz"
    text = rag_dialog.render_transcript_md(raw, SCENARIOS[0])
    assert len(data_rows(text)) == 4
    assert "a \\| b c" in text


def test_missing_rag_renders_no_data_not_a_crash() -> None:
    raw = {"run": "main", "scenario": "A", "turns": [{"id": "A01", "kind": "direct", "frame_type": "done"}]}
    row = data_rows(rag_dialog.render_transcript_md(raw, SCENARIOS[0]))[0]
    assert "нет данных" in row


def test_followups_side_by_side() -> None:
    text = rag_dialog.render_followups_md([main_raw(), baseline_raw()], SCENARIOS)
    rows = data_rows(text)
    assert len(rows) == 2
    first = rows[0].split("|")
    assert first[1].strip() == "A02"
    assert first[5].strip() == "ответ"
    assert first[9].strip() == "шлюз: не знаю"
    assert first[7].strip() == "0.812"


def test_followups_missing_run_is_no_data() -> None:
    text = rag_dialog.render_followups_md([main_raw()], SCENARIOS)
    rows = data_rows(text)
    assert "нет данных" in rows[0]
    assert len(rows) == 2


def test_dialog_metrics_counts() -> None:
    metrics = rag_dialog.dialog_metrics(main_raw())
    assert metrics["turns"] == 4
    assert metrics["done"] == 3
    assert metrics["errors"] == 1
    assert metrics["gated"] == 1
    assert metrics["extraction_failures"] == 1
    assert metrics["article_ok"] == 2
    assert metrics["article_total"] == 3
    assert metrics["condensed"] == 4
    assert metrics["with_sources"] == 3


def test_summary_has_a_row_per_run_and_scenario() -> None:
    text = rag_dialog.render_summary_md([baseline_raw(), main_raw()])
    rows = data_rows(text)
    assert [row.split("|")[1].strip() for row in rows] == ["main", "baseline"]
    assert "2/3" in rows[0]


def test_csv_header_equals_interface_columns() -> None:
    text = rag_dialog.render_answers_csv([main_raw()], SCENARIOS)
    assert next(csv.reader(io.StringIO(text))) == EXPECTED_COLUMNS


def test_csv_has_a_row_for_every_turn_and_empty_judge_columns() -> None:
    text = rag_dialog.render_answers_csv([main_raw(), baseline_raw()], SCENARIOS)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 6
    assert {row["judge_goal"] for row in rows} == {""}
    assert rows[2]["verdict"] == "gated"
    assert rows[3]["verdict"] == "error"
    assert rows[0]["scenario_goal"] == "Узнать штраф"


def test_csv_carries_judge_columns_over() -> None:
    existing = {("main", "A", "A01"): {"judge_goal": "да", "judge_goal_reason": "по теме"}}
    rows = list(csv.DictReader(io.StringIO(rag_dialog.render_answers_csv([main_raw()], SCENARIOS, existing))))
    assert rows[0]["judge_goal"] == "да"
    assert rows[0]["judge_goal_reason"] == "по теме"
    assert rows[1]["judge_goal"] == ""


def test_no_manual_verdict_column() -> None:
    assert "user_verdict" not in EXPECTED_COLUMNS
    text = rag_dialog.render_answers_csv([main_raw()], SCENARIOS)
    assert "manual" not in text.splitlines()[0]
    assert "ручн" not in rag_dialog.render_transcript_md(main_raw(), SCENARIOS[0]).splitlines()[4]


def test_render_only_writes_seven_files_and_keeps_judge(tmp_path: Path) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ids = [s["id"] for s in fixture["scenarios"]]
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for run in ("main", "baseline"):
        for sid in ids:
            raw = main_raw() if run == "main" else baseline_raw()
            raw.update({"run": run, "scenario": sid})
            (raw_dir / f"{run}_{sid}.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    args = argparse.Namespace(out=tmp_path, fixture=FIXTURE)
    assert rag_dialog.render_only(args) == rag_dialog.EXIT_OK
    names = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert names == sorted([
        "answers.csv", "dialog_summary.md", "followups.md", "dialog_A_main.md", "dialog_A_baseline.md",
        "dialog_B_main.md", "dialog_B_baseline.md",
    ])
    sheet = tmp_path / "answers.csv"
    rows = list(csv.DictReader(io.StringIO(sheet.read_text(encoding="utf-8"))))
    rows[0]["judge_goal"] = "да"
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    assert rag_dialog.render_only(args) == rag_dialog.EXIT_OK
    again = list(csv.DictReader(io.StringIO(sheet.read_text(encoding="utf-8"))))
    assert again[0]["judge_goal"] == "да"


def test_render_only_without_raw_files_exits_2(tmp_path: Path) -> None:
    args = argparse.Namespace(out=tmp_path, fixture=FIXTURE)
    assert rag_dialog.render_only(args) == 2
