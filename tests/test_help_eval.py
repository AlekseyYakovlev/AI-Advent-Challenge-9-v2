"""Tests for the pure functions of scripts/help_eval.py and the shape of the control set."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "help_eval.py"
REAL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "help" / "control_set.json"

_spec = importlib.util.spec_from_file_location("help_eval", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
help_eval = importlib.util.module_from_spec(_spec)
sys.modules["help_eval"] = help_eval
_spec.loader.exec_module(help_eval)


def _item(index: int, category: str, **extra: Any) -> dict[str, Any]:
    """One well-formed control question."""
    item: dict[str, Any] = {
        "id": f"q{index}",
        "category": category,
        "question": f"вопрос {index}?",
        "expected_source": "README.md" if category == "docs" else "none",
        "expected_tool": "none" if category == "docs" else "git_current_branch",
        "key_facts": [f"факт {index}"],
    }
    item.update(extra)
    return item


def _control_set(status: str = "frozen") -> dict[str, Any]:
    """A valid control set with the required category counts."""
    categories = ["docs"] * 5 + ["git"] * 2 + ["mixed"] * 2 + ["out_of_corpus"]
    return {
        "version": 1,
        "status": status,
        "frozen_at": "2026-10-13T00:00:00Z" if status == "frozen" else None,
        "questions": [_item(i, c) for i, c in enumerate(categories)],
    }


def test_validate_accepts_a_well_formed_set() -> None:
    """A set with the required categories and fields has no errors."""
    assert help_eval.validate_control_set(_control_set()) == []


def test_validate_names_a_wrong_count() -> None:
    """Dropping a question is reported as a count and a category error."""
    data = _control_set()
    data["questions"].pop()
    errors = help_eval.validate_control_set(data)
    assert any(e.startswith("count") for e in errors)
    assert any(e.startswith("categories") for e in errors)


def test_validate_names_a_duplicate_id_and_a_missing_field() -> None:
    """A repeated id and an empty key_facts list are named."""
    data = _control_set()
    data["questions"][1]["id"] = data["questions"][0]["id"]
    data["questions"][2]["key_facts"] = []
    errors = help_eval.validate_control_set(data)
    assert any(e.startswith("ids") for e in errors)
    assert any("missing key_facts" in e for e in errors)


def test_load_refuses_a_draft_set_unless_allowed(tmp_path: Path) -> None:
    """A draft fixture is refused for a graded run and accepted when drafts are allowed."""
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(_control_set("draft")), encoding="utf-8")
    with pytest.raises(SystemExit):
        help_eval.load_control_set(path, require_frozen=True)
    assert len(help_eval.load_control_set(path, require_frozen=False)) == 10


def test_load_returns_ten_items_for_a_frozen_set(tmp_path: Path) -> None:
    """A frozen valid set loads in full."""
    path = tmp_path / "frozen.json"
    path.write_text(json.dumps(_control_set()), encoding="utf-8")
    assert len(help_eval.load_control_set(path)) == 10


def test_read_env_value_strips_quotes_and_ignores_comments(tmp_path: Path) -> None:
    """Quoted and plain values are read, commented and absent names give None."""
    env = tmp_path / ".env"
    env.write_text(
        "# SECRET_A=hidden\nSECRET_B=\"quoted-value\"\nexport SECRET_C=plain # note\nEMPTY=\n",
        encoding="utf-8",
    )
    assert help_eval.read_env_value(env, "SECRET_B") == "quoted-value"
    assert help_eval.read_env_value(env, "SECRET_C") == "plain"
    assert help_eval.read_env_value(env, "SECRET_A") is None
    assert help_eval.read_env_value(env, "EMPTY") is None
    assert help_eval.read_env_value(tmp_path / "missing", "SECRET_B") is None


def test_scrub_replaces_every_secret() -> None:
    """Each given secret is replaced, empty secrets are skipped."""
    text = "key=abc123 and again abc123, other=zzz"
    assert help_eval.scrub(text, ["abc123", "", "zzz"]) == "key=*** and again ***, other=***"


def test_git_snapshot_reads_a_repository(tmp_path: Path) -> None:
    """Branch, head, status lines and commit subjects come from list-argv git calls."""
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "first commit")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    snap = help_eval.git_snapshot(tmp_path)
    assert snap["branch"] == "main"
    assert snap["head"]
    assert snap["status_short"] == ["?? b.txt"]
    assert snap["recent_commits"] == ["first commit"]


def test_expected_answer_uses_key_facts_for_docs_and_git_state_for_git() -> None:
    """Documentation items return their facts; git items are built from the snapshot."""
    snapshot = {
        "branch": "Day31",
        "head": "abc1234",
        "status_short": [" M agent/ws.py", "?? new.txt"],
        "recent_commits": ["one", "two", "three", "four"],
    }
    docs = _item(0, "docs")
    assert help_eval.expected_answer(docs, snapshot) == "факт 0"
    branch = _item(1, "git", expected_from="git_branch")
    assert "Day31" in help_eval.expected_answer(branch, snapshot)
    log = _item(2, "git", expected_from="git_log")
    assert help_eval.expected_answer(log, snapshot).endswith("one; two; three")
    mixed = _item(3, "mixed", expected_from="git_status")
    text = help_eval.expected_answer(mixed, snapshot)
    assert "agent/ws.py" in text and "new.txt" in text and "факт 3" in text
    clean = help_eval.expected_answer(mixed, {**snapshot, "status_short": []})
    assert clean.startswith(help_eval.CLEAN_TREE_TEXT)


def test_question_record_reads_a_success_body() -> None:
    """Answer, sources, verified quotes, tool calls, warnings and verdict are extracted."""
    body = {
        "content": "ответ",
        "rag": {
            "verdict": "ok",
            "sources": [
                {"file": "README.md", "score": 0.61},
                {"file": "docs/API_SPEC.md", "score": 0.5},
                {"file": "README.md", "score": 0.4},
            ],
            "quotes": [{"state": "exact"}, {"auto": True, "state": "exact"}, {"state": "missing"}],
        },
        "tool_calls": [{"name": "mcp__local-git__git_status", "tool": "git_status", "ok": True}],
        "help": {"kb_name": "project-docs", "warnings": [{"server": "x", "text": "сервер недоступен"}]},
    }
    record = help_eval.question_record(_item(0, "docs"), body, 1.234)
    assert record["answer"] == "ответ"
    assert record["sources"] == ["README.md", "docs/API_SPEC.md"]
    assert (record["quotes"], record["quotes_auto"], record["quotes_unverified"]) == (1, 1, 1)
    assert record["tool_calls"] == [{"name": "git_status", "ok": True}]
    assert record["warnings"] == ["сервер недоступен"]
    assert record["verdict"] == "ok" and record["stored"] is True
    assert record["best_score"] == 0.61 and record["elapsed_s"] == 1.23


def test_question_record_marks_a_notice_and_an_error() -> None:
    """A stored:false notice carries its kind; a failed request carries the error text."""
    notice = {"chat_id": 1, "stored": False, "help": {"kind": "no_base", "text": "нет базы"}}
    record = help_eval.question_record(_item(0, "docs"), notice, 0.1)
    assert record["kind"] == "no_base" and record["stored"] is False and record["answer"] == "нет базы"
    failed = help_eval.question_record(_item(0, "docs"), None, 0.1, error="HTTP 502: bad")
    assert failed["error"] == "HTTP 502: bad" and failed["answer"] is None and failed["sources"] == []


def test_threshold_decision_keeps_midpoints_or_reports_overlap() -> None:
    """Keep when separated at the current value, move to the midpoint, or report overlap."""
    assert help_eval.threshold_decision([0.6, 0.55], [0.3], 0.45)[0] == 0.45
    value, _ = help_eval.threshold_decision([0.5, 0.4], [0.2], 0.45)
    assert value == 0.3
    value, reason = help_eval.threshold_decision([0.35, 0.6], [0.5], 0.45)
    assert (value, reason) == (0.45, "not separable")


def test_pick_deepseek_model_prefers_flash() -> None:
    """The flash model is preferred, then any flash id, then a non-reasoner id."""
    assert help_eval.pick_deepseek_model(["deepseek-reasoner", "deepseek-flash"]) == "deepseek-flash"
    assert help_eval.pick_deepseek_model(["x-reasoner", "v4-flash-2"]) == "v4-flash-2"
    assert help_eval.pick_deepseek_model(["x-reasoner", "plain"]) == "plain"
    assert help_eval.pick_deepseek_model([]) is None


def test_isolation_guard_refuses_live_ports_and_app_db(tmp_path: Path) -> None:
    """Building the run environment on the live ports or on app.db raises."""
    ok = help_eval.isolated_overrides(tmp_path)
    assert ok["UI_PORT"] == "18000" and ok["AGENT_PORT"] == "18001"
    assert Path(ok["DB_PATH"]).parent == tmp_path
    with pytest.raises(RuntimeError):
        help_eval.isolated_overrides(tmp_path, ui_port=int("80" + "00"))
    with pytest.raises(RuntimeError):
        help_eval.isolated_overrides(tmp_path, agent_port=int("80" + "01"))
    with pytest.raises(RuntimeError):
        help_eval.isolated_overrides(tmp_path, db_name="app.db")


def test_spike_entry_flags() -> None:
    """The spike booleans reflect sources, a verified quote and the expected tool call."""
    item = _item(1, "git")
    record = {
        "answer": "a", "sources": [], "quotes": 0, "warnings": [], "error": None,
        "tool_calls": [{"name": "git_current_branch", "ok": True}],
    }
    entry = help_eval.spike_entry(item, record)
    assert entry["has_sources"] is False and entry["has_verified_quote"] is False
    assert entry["called_expected_tool"] is True
    docs_entry = help_eval.spike_entry(_item(0, "docs"), {**record, "tool_calls": []})
    assert docs_entry["called_expected_tool"] is None


def test_default_threshold_is_read_from_the_source() -> None:
    """The default is read without importing application modules."""
    assert 0.0 < help_eval.default_help_threshold() < 1.0


def test_pick_spike_ids_defaults_to_first_of_each_category() -> None:
    """The default spike uses the first docs, git and mixed items; unknown ids are refused."""
    items = _control_set()["questions"]
    assert help_eval.pick_spike_ids(items, None) == ["q0", "q5", "q7"]
    assert help_eval.pick_spike_ids(items, "q1,q2") == ["q1", "q2"]
    with pytest.raises(ValueError):
        help_eval.pick_spike_ids(items, "nope")


def test_real_control_set_is_frozen_and_valid() -> None:
    """The committed control set is frozen with a date and passes the shape check."""
    data = json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))
    assert data["status"] == "frozen" and data["frozen_at"]
    assert help_eval.validate_control_set(data) == []
    assert len(help_eval.load_control_set(REAL_FIXTURE, require_frozen=True)) == 10
