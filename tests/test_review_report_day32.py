"""Structure and consistency checks of the Day 32 report against the judge files."""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
REPORT: Path = REPO_ROOT / "Day32_report.md"
OUT_DIR: Path = REPO_ROOT / "eval_out" / "day32"
JUDGE_DIR: Path = OUT_DIR / "judge"
NOT_RUN: str = "не запускалось"
RUNS: tuple[str, ...] = ("local", "deepseek")
HEADINGS: tuple[str, ...] = (
    "Задание",
    "Как устроен пайплайн",
    "Безопасность раннера",
    "Контрольный набор",
    "Условия запуска",
    "Проверка пайплайна на тестовом PR",
    "Результаты",
    "Что дал поиск",
    "Тексты ревью",
    "Что не запускалось и ограничения",
    "Тесты",
    "Вывод",
)
REVIEW_HEADINGS: tuple[str, ...] = ("Потенциальные баги", "Архитектурные проблемы", "Рекомендации")
SECRET_PATTERNS: tuple[str, ...] = (
    r"aia_[A-Za-z0-9_-]{20,}",
    r"ghp_[A-Za-z0-9]{10,}",
    r"github_pat_[A-Za-z0-9_]{10,}",
    r"sk-[A-Za-z0-9]{20,}",
)

pytestmark = pytest.mark.skipif(
    not JUDGE_DIR.is_dir(), reason="eval_out/day32/judge/ is absent: the control run has not been made"
)


def _text() -> str:
    return REPORT.read_text(encoding="utf-8")


def _judge(run: str) -> list[dict]:
    path = JUDGE_DIR / f"{run}.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["prs"]


def _section(title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", _text(), re.S | re.M)
    assert match is not None, title
    return match.group(1)


def test_report_exists_is_long_and_mostly_cyrillic() -> None:
    text = _text()
    assert len(text.splitlines()) >= 150
    letters = [c for c in text if c.isalpha()]
    cyrillic = [c for c in letters if "\u0400" <= c <= "\u04ff"]
    assert len(cyrillic) / len(letters) > 0.5


def test_report_has_twelve_sections_in_order() -> None:
    headings = re.findall(r"^## (.+)$", _text(), re.M)
    assert [h for h in headings if h in HEADINGS] == list(HEADINGS)


def test_report_links_every_control_pr() -> None:
    prs_path = OUT_DIR / "prs.json"
    if not prs_path.is_file():
        pytest.skip("prs.json is absent")
    text = _text()
    for pr_id, entry in json.loads(prs_path.read_text(encoding="utf-8")).items():
        assert entry["url"] in text, pr_id


def _report_pr_summary() -> dict[str, dict[str, tuple[int, int, int]]]:
    """Rows of the per-PR table: pr -> run -> (found, expected, false)."""
    body = _section("Результаты")
    start = body.index("#### Итог по PR")
    table = body[start : body.index("####", start + 5)]
    lines = [l for l in table.splitlines() if l.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows: dict[str, dict[str, tuple[int, int, int]]] = {}
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        row = dict(zip(header, cells))
        entry: dict[str, tuple[int, int, int]] = {}
        for run in RUNS:
            found = re.fullmatch(r"найдено (\d+) из (\d+)", row[f"{run}: найдено"])
            false = re.fullmatch(r"ложных: (\d+)", row[f"{run}: ложных"])
            if found and false:
                entry[run] = (int(found.group(1)), int(found.group(2)), int(false.group(1)))
        rows[row["PR"]] = entry
    return rows


def test_pr_summary_equals_totals_from_judge_files() -> None:
    rows = _report_pr_summary()
    for run in RUNS:
        for record in _judge(run):
            if record["judge_status"] != "ok":
                assert run not in rows[record["id"]]
                continue
            found = sum(1 for f in record["findings"] if f["found"])
            assert rows[record["id"]][run] == (found, len(record["findings"]), record["false_remarks"])


def test_every_not_run_record_is_marked_in_report() -> None:
    text = _text()
    for run in RUNS:
        for record in _judge(run):
            if record["judge_status"] == NOT_RUN or record["status"] == NOT_RUN:
                assert NOT_RUN in text
    run_files = sorted(OUT_DIR.glob("*/*.json"))
    for path in run_files:
        if path.parent.name == "judge":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") == NOT_RUN:
            assert NOT_RUN in text


def test_same_provider_sentence_present_when_deepseek_was_judged() -> None:
    judged = [r for r in _judge("deepseek") if r["judge_status"] == "ok"]
    if judged:
        assert "судья и автор ревью — один и тот же провайдер" in _section("Результаты")


def test_review_texts_hold_the_three_headings_for_ok_records() -> None:
    section = _section("Тексты ревью")
    for run in RUNS:
        for record in _judge(run):
            if record["status"] != "ok":
                continue
            marker = f"{record['id']} ("
            assert marker in section and f"прогон `{run}`" in section
    for title in REVIEW_HEADINGS:
        assert section.count(f"##### {title}") >= sum(
            1 for run in RUNS for r in _judge(run) if r["status"] == "ok"
        )


def test_report_states_the_main_merge_limitation() -> None:
    text = _section("Что не запускалось и ограничения")
    assert "не слита в `main`" in text
    assert "`Day31`" in text and "`Day32`" in text


def test_no_secret_in_report_or_eval_out() -> None:
    files = [REPORT, *[p for p in OUT_DIR.rglob("*") if p.is_file()]]
    for path in files:
        content = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            assert re.search(pattern, content) is None, f"{path.name}: {pattern}"
