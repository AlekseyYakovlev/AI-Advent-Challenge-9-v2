"""Tests for scripts/files_eval.py: scenario table, automatic checks, reset guard, summary."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "files_eval.py"
CHANGED_FILES_PHRASE = "перечисли созданные или изменённые файлы"

if shutil.which("git") is None:
    pytest.skip("git is not installed", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("files_eval", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
files_eval = importlib.util.module_from_spec(_spec)
sys.modules["files_eval"] = files_eval
_spec.loader.exec_module(files_eval)


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git", "-c", "user.name=T", "-c", "user.email=t@t",
            "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args,
        ],  # fmt: skip
        cwd=repo,
        env=_clean_env(),
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return result.stdout


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _new_file_patch(path: str, lines: list[str]) -> str:
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}"
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository named files-root with five commits and a symbol used in two .py files."""
    root = tmp_path / "files-root"
    root.mkdir()
    _git(root, "init")
    (root / "a.py").write_text("cleanup_chat_caches(1)\n", encoding="utf-8")
    (root / "b.py").write_text("def cleanup_chat_caches(x):\n    pass\n", encoding="utf-8")
    (root / "c.md").write_text("cleanup_chat_caches is documented\n", encoding="utf-8")
    _commit_all(root, "feat: first change")
    for index in range(2, 6):
        (root / f"f{index}.txt").write_text(f"{index}\n", encoding="utf-8")
        _commit_all(root, f"feat: change number {index}")
    return root


def _record(answer: str) -> dict[str, Any]:
    return {"answer": answer, "status": "done"}


def test_scenarios_table() -> None:
    assert list(files_eval.SCENARIOS) == ["usages", "adr", "changelog", "readme"]
    for goal in files_eval.SCENARIOS.values():
        assert any("а" <= ch.lower() <= "я" for ch in goal)
        assert "открой файл" not in goal.lower()
    for slug in ("adr", "changelog", "readme"):
        assert CHANGED_FILES_PHRASE in files_eval.SCENARIOS[slug]
    assert "последние 50 коммитов" in files_eval.SCENARIOS["changelog"]


def test_expected_usage_files_python_only(repo: Path) -> None:
    found = files_eval.expected_usage_files(repo, "cleanup_chat_caches")
    assert found == ["a.py", "b.py"]


def test_usages_checks(repo: Path) -> None:
    good = files_eval.scenario_checks("usages", _record("Используют a.py и b.py"), "", repo)
    assert good["no_files_changed"] is True
    assert good["mentions_usage_files"] is True
    bad = files_eval.scenario_checks("usages", _record("Только a.py"), "", repo)
    assert bad["mentions_usage_files"] is False


def test_adr_checks(repo: Path) -> None:
    patch = _new_file_patch("docs/adr/0001-two-processes.md", ["# ADR", "text"])
    answer = "Создан docs/adr/0001-two-processes.md"
    checks = files_eval.scenario_checks("adr", _record(answer), patch, repo)
    assert checks["new_file_in_docs_adr"] is True
    assert checks["answer_lists_changed_files"] is True
    other = files_eval.scenario_checks("adr", _record(answer), _new_file_patch("notes.md", ["x"]), repo)
    assert other["new_file_in_docs_adr"] is False


def test_changelog_checks(repo: Path) -> None:
    subjects = ["feat: first change", "feat: change number 2", "feat: change number 3"]
    patch = _new_file_patch("CHANGELOG.md", ["# Changelog", *[f"- {s}" for s in subjects]])
    checks = files_eval.scenario_checks("changelog", _record("Создан CHANGELOG.md"), patch, repo)
    assert checks["changelog_created"] is True
    assert checks["cites_git_history"] is True
    thin = _new_file_patch("CHANGELOG.md", ["# Changelog", "- feat: first change"])
    weak = files_eval.scenario_checks("changelog", _record("CHANGELOG.md"), thin, repo)
    assert weak["cites_git_history"] is False


def test_readme_checks(repo: Path) -> None:
    patch = _new_file_patch("mcp_servers/README.md", ["# Servers", "project_git.py runs git"])
    checks = files_eval.scenario_checks("readme", _record("Создан mcp_servers/README.md"), patch, repo)
    assert checks["readme_created"] is True
    assert checks["names_server_files"] is True
    empty = files_eval.scenario_checks("readme", _record("ничего"), "", repo)
    assert empty["readme_created"] is False


def test_reset_clone_refuses_wrong_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        files_eval.reset_clone(REPO_ROOT, "HEAD")
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(ValueError):
        files_eval.reset_clone(other, "HEAD")


def test_reset_clone_restores_clean_status(repo: Path, tmp_path: Path) -> None:
    clone = tmp_path / "work" / "files-root"
    clone.parent.mkdir()
    _git(tmp_path, "clone", "--local", "--no-hardlinks", str(repo), str(clone))
    commit = _git(clone, "rev-parse", "HEAD").strip()
    (clone / "stray.txt").write_text("x\n", encoding="utf-8")
    (clone / "a.py").write_text("changed\n", encoding="utf-8")
    files_eval.reset_clone(clone, commit)
    assert _git(clone, "status", "--porcelain").strip() == ""


def test_not_run_record_and_summary(tmp_path: Path) -> None:
    scratch = tmp_path / "files_eval_xyz"
    reason = f"LM Studio down at {scratch}"
    record = files_eval.not_run_record("usages", "goal", reason)
    assert record["status"] == files_eval.NOT_RUN
    assert "LM Studio down" in record["reason"]
    out = tmp_path / "out"
    files_eval.save_not_run(out, record, scratch)
    saved = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert str(scratch) not in json.dumps(saved)
    assert "files_eval_xyz" in saved["reason"]

    done = {
        "scenario": "adr", "status": "done", "tool_call_count": 4,
        "patch": {"files": [{"path": "docs/adr/1.md"}]},
        "checks": {"new_file_in_docs_adr": True, "answer_lists_changed_files": False},
    }  # fmt: skip
    text = files_eval.render_summary([saved | {"scenario": "usages"}, done])
    assert "| Сценарий | Статус |" in text
    assert text.count("\n| usages") == 1 and text.count("\n| adr") == 1
    assert "not run" in text and "done" in text
    assert "new_file_in_docs_adr ✓" in text and "answer_lists_changed_files ✗" in text
