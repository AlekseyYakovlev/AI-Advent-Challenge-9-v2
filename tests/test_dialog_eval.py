"""Tests for the dialog scenario driver: pure checks and isolation guards, no app and no network."""

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rag_dialog", REPO_ROOT / "scripts" / "rag_dialog.py")
rag_dialog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_dialog)

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "rag" / "dialog_scenarios.json"

SOURCES = [
    {"file": "koap.pdf", "section": "Глава 12 > Статья 12.9. Превышение скорости", "rank": 1},
    {"file": "koap.pdf", "section": "Глава 32 > Статья 32.2. Исполнение", "rank": 2},
]


def make_rag(**over: Any) -> dict[str, Any]:
    """A canned done.rag payload."""
    rag: dict[str, Any] = {
        "verdict": "ok",
        "gated": False,
        "sources": SOURCES,
        "quotes": [
            {"state": "exact", "auto": False},
            {"state": "fuzzy", "auto": False},
            {"state": "exact", "auto": True},
        ],
        "search": {"query": "q", "rewritten": "штраф за превышение", "condensed": True},
    }
    rag.update(over)
    return rag


def snapshot(goal: str = "Узнать штраф", **lists: Any) -> dict[str, Any]:
    """A canned task-memory snapshot."""
    data: dict[str, Any] = {
        "goal": goal,
        "clarified": [],
        "constraints": [],
        "new": {},
        "failed": False,
    }
    data.update(lists)
    return data


def test_turn_verdict_values() -> None:
    assert rag_dialog.turn_verdict("done", {"gated": True, "verdict": "below_threshold"}) == "gated"
    assert rag_dialog.turn_verdict("done", {"gated": False, "verdict": "model_idk"}) == "model_idk"
    assert rag_dialog.turn_verdict("done", {"gated": False, "verdict": "ok"}) == "ok"
    assert rag_dialog.turn_verdict("done", {"verdict": "off"}) == "no_rag"
    assert rag_dialog.turn_verdict("done", None) == "no_rag"
    assert rag_dialog.turn_verdict("error", None) == "error"


def test_memory_check_counts_found_entries() -> None:
    snap = snapshot(
        goal="Выяснить ШТРАФ",
        clarified=[{"id": 1, "text": "Скорость 95 км/ч"}, {"id": 2, "text": "ограничение 60"}],
        constraints=[{"id": 3, "text": "Отвечать только по КоАП"}],
    )
    expect = {"goal": ["штраф"], "constraints": ["коап"], "clarified": ["95", "60"]}
    assert rag_dialog.memory_check(expect, snap) == (4, 4)
    assert rag_dialog.memory_check({"constraints": ["коап"], "clarified": ["95", "60"]}, None) == (3, 0)
    assert rag_dialog.memory_check({}, snap) == (0, 0)
    assert rag_dialog.memory_check({"clarified": ["95", "770"]}, snap) == (2, 1)


def test_memory_check_accepts_plain_string_items() -> None:
    snap = snapshot(clarified=["через Госуслуги"])
    assert rag_dialog.memory_check({"clarified": ["госуслуг"]}, snap) == (1, 1)


def test_article_in_sources_exact_article_only() -> None:
    assert rag_dialog.article_in_sources("12.9", SOURCES)
    assert not rag_dialog.article_in_sources("12.19", SOURCES)
    assert not rag_dialog.article_in_sources("2.9", SOURCES)
    assert not rag_dialog.article_in_sources("12.9", [])
    assert not rag_dialog.article_in_sources("12.9", [{"section": None}])


def test_compute_checks_quote_and_source_counts() -> None:
    checks = rag_dialog.compute_checks({}, "done", "ответ", make_rag(), None)
    assert checks["sources_present"] is True
    assert (checks["quotes_model"], checks["quotes_auto"], checks["quotes_unverified"]) == (2, 1, 0)
    assert checks["verdict"] == "ok"
    assert checks["condensed"] is True
    assert checks["condensed_query"] == "штраф за превышение"


def test_compute_checks_unverified_quote_counted() -> None:
    rag = make_rag(quotes=[{"state": "unverified", "auto": False}])
    checks = rag_dialog.compute_checks({}, "done", "x", rag, None)
    assert checks["quotes_unverified"] == 1
    assert checks["quotes_model"] == 0


def test_goal_unchanged_none_then_true_then_false() -> None:
    spec: dict[str, Any] = {}
    first = rag_dialog.compute_checks(spec, "done", "a", make_rag(task_memory=snapshot("Цель  Один")), None)
    assert first["goal_unchanged"] is None
    same = rag_dialog.compute_checks(
        spec, "done", "a", make_rag(task_memory=snapshot("цель один")), "Цель Один"
    )
    assert same["goal_unchanged"] is True
    changed = rag_dialog.compute_checks(
        spec, "done", "a", make_rag(task_memory=snapshot("Другая цель")), "Цель Один"
    )
    assert changed["goal_unchanged"] is False
    assert changed["goal_kept"] is False


def test_keywords_and_goal_kept() -> None:
    spec = {"expect_keywords": ["штраф", "750"]}
    rag = make_rag(task_memory=snapshot())
    ok = rag_dialog.compute_checks(spec, "done", "Штраф 750 рублей", rag, "Узнать штраф")
    assert ok["keywords_ok"] is True and ok["goal_kept"] is True
    bad = rag_dialog.compute_checks(spec, "done", "Штраф есть", rag, "Узнать штраф")
    assert bad["keywords_ok"] is False and bad["goal_kept"] is False
    none = rag_dialog.compute_checks({}, "done", "x", rag, "Узнать штраф")
    assert none["keywords_ok"] is None and none["goal_kept"] is True


def test_baseline_has_no_goal_fields() -> None:
    checks = rag_dialog.compute_checks({"expect_keywords": ["a"]}, "done", "a", make_rag(), "g")
    assert checks["goal_text"] is None
    assert checks["goal_unchanged"] is None
    assert checks["goal_kept"] is None
    assert checks["memory_failed"] is None


def test_memory_ok_and_article_ok_flags() -> None:
    rag = make_rag(task_memory=snapshot(clarified=[{"id": 1, "text": "95"}]))
    spec = {"expect_memory": {"clarified": ["95"]}, "expect_article": "12.9"}
    checks = rag_dialog.compute_checks(spec, "done", "x", rag, None)
    assert checks["memory_ok"] is True and checks["article_ok"] is True
    missing = {"expect_memory": {"clarified": ["110"]}, "expect_article": "20.25"}
    checks = rag_dialog.compute_checks(missing, "done", "x", rag, None)
    assert checks["memory_ok"] is False and checks["article_ok"] is False
    empty = rag_dialog.compute_checks({"expect_article": None}, "done", "x", rag, None)
    assert empty["memory_ok"] is None and empty["article_ok"] is None


def test_memory_failed_flag_comes_from_snapshot() -> None:
    rag = make_rag(task_memory=snapshot(failed=True))
    assert rag_dialog.compute_checks({}, "done", "x", rag, None)["memory_failed"] is True


def test_gated_turn_has_complete_checks() -> None:
    rag = make_rag(gated=True, verdict="below_threshold", sources=[], quotes=[])
    checks = rag_dialog.compute_checks({"expect_article": "12.9"}, "done", "не знаю", rag, None)
    assert checks["verdict"] == "gated"
    assert checks["sources_present"] is False
    assert checks["article_ok"] is False


def test_error_turn_has_complete_checks() -> None:
    checks = rag_dialog.compute_checks(
        {"expect_memory": {"goal": ["x"]}, "expect_article": "12.9"}, "error", "", None, "g"
    )
    assert checks["verdict"] == "error"
    assert checks["sources_present"] is False
    assert checks["memory_expected"] == 1 and checks["memory_found"] == 0
    assert checks["article_ok"] is False
    assert checks["condensed_query"] is None


def test_assert_isolated_refuses_live_ports(tmp_path: Path) -> None:
    db = tmp_path / "scratch.db"
    for ui, agent in ((8000, 18001), (18000, 8001)):
        with pytest.raises(RuntimeError):
            rag_dialog.assert_isolated(db, ui, agent)
    rag_dialog.assert_isolated(db, 18000, 18001)


def test_assert_isolated_refuses_app_db_and_repo_paths(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        rag_dialog.assert_isolated(tmp_path / "app.db", 18000, 18001)
    with pytest.raises(RuntimeError):
        rag_dialog.assert_isolated(REPO_ROOT / "eval_out" / "x.db", 18000, 18001)


def test_load_scenarios_refuses_draft(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({"status": "draft", "scenarios": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        rag_dialog.load_scenarios(draft, require_frozen=True)
    assert rag_dialog.load_scenarios(draft, require_frozen=False) == []


def test_load_scenarios_reads_frozen_fixture() -> None:
    scenarios = rag_dialog.load_scenarios(FIXTURE, require_frozen=True)
    assert [s["id"] for s in scenarios] == ["A", "B"]


class FakeSocket:
    """Yields scripted frames from recv(); a None entry blocks forever (a hung model)."""

    def __init__(self, frames: list[dict[str, Any] | None]) -> None:
        self.frames = list(frames)

    async def recv(self) -> str:
        frame = self.frames.pop(0)
        if frame is None:
            await asyncio.sleep(30)
        return json.dumps(frame)


def make_args(**over: Any) -> argparse.Namespace:
    """Dialog arguments as the parser would build them."""
    values: dict[str, Any] = {
        "kb_id": 2,
        "history_turns": 3,
        "runs": "main,baseline",
        "scenarios": None,
        "fixture": FIXTURE,
        "out": Path("unused"),
        "source_db": Path("unused.db"),
        "source_kb": Path("unused_kb"),
        "check": False,
        "force": False,
    }
    values.update(over)
    return argparse.Namespace(**values)


async def test_drain_turn_collects_tokens_until_done() -> None:
    done = {"type": "done", "message_id": 1, "rag": {"verdict": "ok"}}
    ws = FakeSocket(
        [
            {"type": "token", "content": "Штраф "},
            {"type": "tool_call", "name": "x"},
            {"type": "token", "content": "750"},
            {"type": "token", "content": " руб."},
            done,
        ]
    )
    frame_type, answer, frame, error = await rag_dialog.drain_turn(ws, 5.0)
    assert frame_type == "done"
    assert answer == "Штраф 750 руб."
    assert frame == done
    assert error is None


async def test_drain_turn_error_frame() -> None:
    ws = FakeSocket([{"type": "error", "detail": "boom", "code": "CONTEXT_OVERFLOW"}])
    frame_type, answer, _frame, error = await rag_dialog.drain_turn(ws, 5.0)
    assert frame_type == "error"
    assert answer == ""
    assert error == "CONTEXT_OVERFLOW: boom"


async def test_read_timeout_becomes_error_record() -> None:
    ws = FakeSocket([{"type": "token", "content": "часть"}, None])
    outcome = await rag_dialog.drain_turn(ws, 0.05)
    assert outcome[0] == "error"
    assert outcome[1] == "часть"
    assert outcome[3].startswith("timeout")
    record = rag_dialog.turn_record(
        {"id": "A01", "kind": "direct", "text": "q", "expect_article": "12.9"}, outcome, 0.1, None
    )
    assert record["frame_type"] == "error"
    assert record["checks"]["verdict"] == "error"
    assert record["rag"] is None and record["task_memory"] is None


def test_send_delay_keeps_under_chat_rate_limit() -> None:
    assert rag_dialog.send_delay([], 100.0) == 0.0
    nine = [float(i) for i in range(9)]
    assert rag_dialog.send_delay(nine[:8], 10.0) == 0.0
    assert rag_dialog.send_delay(nine, 10.0) == pytest.approx(50.5)
    assert rag_dialog.send_delay(nine, 100.0) == 0.0


def test_turn_record_keeps_whole_rag_and_snapshot() -> None:
    rag = make_rag(task_memory=snapshot())
    outcome = ("done", "ответ", {"type": "done", "rag": rag}, None)
    record = rag_dialog.turn_record(
        {"id": "A01", "kind": "direct", "text": "вопрос"}, outcome, 1.234, None
    )
    assert record["rag"] == rag
    assert record["task_memory"] == rag["task_memory"]
    assert record["question"] == "вопрос" and record["elapsed_s"] == 1.23
    assert set(record) == {
        "id", "kind", "question", "answer", "frame_type", "error", "elapsed_s", "rag",
        "task_memory", "checks",
    }


def test_baseline_run_disables_task_memory(tmp_path: Path) -> None:
    main_env = rag_dialog.run_config("main", make_args(), tmp_path)
    base_env = rag_dialog.run_config("baseline", make_args(), tmp_path)
    assert main_env["TASK_MEMORY_ENABLED"] == "true"
    assert base_env["TASK_MEMORY_ENABLED"] == "false"
    for env in (main_env, base_env):
        assert env["UI_PORT"] == "18000" and env["AGENT_PORT"] == "18001"
        assert Path(env["DB_PATH"]).parent == tmp_path
        assert Path(env["KB_STORAGE_DIR"]).parent == tmp_path
    with pytest.raises(ValueError):
        rag_dialog.run_config("other", make_args(), tmp_path)


def test_rag_settings_match_day24_configuration() -> None:
    settings = rag_dialog.rag_settings(make_args(kb_id=7, history_turns=2))
    assert settings["mode"] == "rag" and settings["kb_id"] == 7
    assert settings["top_k"] == 5 and settings["candidate_k"] == 20
    assert settings["threshold"] is None and settings["strict"] is True
    for flag in ("lexical", "llm_rerank", "hybrid", "rewrite"):
        assert settings[flag] is False
    assert settings["history_turns"] == 2


def test_parse_runs_and_scenarios() -> None:
    assert rag_dialog.parse_runs("main,baseline") == ["main", "baseline"]
    assert rag_dialog.parse_scenarios("A", ["A", "B"]) == ["A"]
    assert rag_dialog.parse_scenarios(None, ["A", "B"]) == ["A", "B"]
    with pytest.raises(ValueError):
        rag_dialog.parse_runs("other")
    with pytest.raises(ValueError):
        rag_dialog.parse_scenarios("C", ["A", "B"])


async def test_unknown_run_exits_with_code_2() -> None:
    assert await rag_dialog.dialog_command(make_args(runs="other")) == 2


async def test_unknown_scenario_exits_with_code_2() -> None:
    assert await rag_dialog.dialog_command(make_args(scenarios="Z")) == 2


async def test_draft_fixture_exits_with_code_2(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({"status": "draft", "scenarios": []}), encoding="utf-8")
    assert await rag_dialog.dialog_command(make_args(fixture=draft)) == 2


def test_missing_source_database_is_refused(tmp_path: Path) -> None:
    args = make_args(source_db=tmp_path / "missing.db", source_kb=tmp_path)
    assert rag_dialog.preflight_dialog(args) == 2


def test_existing_raw_file_is_detected_for_resume(tmp_path: Path) -> None:
    path = rag_dialog.raw_path(tmp_path, "main", "A")
    assert path == tmp_path / "raw" / "main_A.json"
    assert not path.exists()
    rag_dialog.write_json(path, {"run": "main"})
    assert path.exists()


def test_scenario_counts() -> None:
    turns = [
        {"frame_type": "done", "checks": {"verdict": "ok"}},
        {"frame_type": "done", "checks": {"verdict": "gated"}},
        {"frame_type": "error", "checks": {"verdict": "error"}},
    ]
    assert rag_dialog.scenario_counts(turns) == {"turns": 3, "done": 2, "errors": 1, "gated": 1}


def test_dialog_subcommand_is_registered() -> None:
    spec = importlib.util.spec_from_file_location(
        "rag_eval_dialog", REPO_ROOT / "scripts" / "rag_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.build_parser().parse_args(["dialog", "--check"])
    assert args.command == "dialog" and args.check is True
    assert args.runs == "main,baseline" and args.kb_id == 2 and args.turn_timeout == 240.0
