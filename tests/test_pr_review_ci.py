"""Tests for scripts/pr_review_ci.py and .github/workflows/pr-review.yml without a network."""

import importlib.util
import pathlib
import re
import sys
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "pr_review_ci.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pr-review.yml"
API_KEY = "aia_" + "k" * 30
TOKEN = "ghs_" + "t" * 30
SHA = "a" * 40

_spec = importlib.util.spec_from_file_location("pr_review_ci", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
ci = importlib.util.module_from_spec(_spec)
sys.modules["pr_review_ci"] = ci
_spec.loader.exec_module(ci)


def make_review(**over: Any) -> dict[str, Any]:
    """Build a review reply."""
    review: dict[str, Any] = {
        "repo": "o/r", "pr_number": 7, "head_sha": SHA, "head_changed": False,
        "base_ref": "main", "base_commit": "b" * 40,
        "provider": "deepseek", "model": "deepseek-chat",
        "sections": {"bugs": "- `a.py:1` ошибка", "architecture": "", "recommendations": "  "},
        "sources": {
            "docs": [{"file": "docs/ARCHITECTURE.md", "section": "Данные"}],
            "code": [{"file": "agent/ws.py", "section": None, "lines": "120-164"},
                     {"file": "agent/main.py", "section": None, "lines": None}],
        },
        "docs_used": True, "parts": 1, "unreviewed_files": [], "notes": [],
    }
    review.update(over)
    return review


class Fake:
    """Records calls and answers from a script."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, url: str, key: Any, body: Any, timeout: float,
                 extra_headers: Any = None) -> Any:
        self.calls.append((method, url, body))
        answer = self.answers.pop(0) if self.answers else ci.Reply(200, [])
        return answer(method, url) if callable(answer) else answer


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """Set a complete environment and return the summary path."""
    summary = tmp_path / "summary.md"
    values = {"APP_URL": "http://127.0.0.1:8001", "API_KEY": API_KEY, "GITHUB_TOKEN": TOKEN,
              "REPO": "o/r", "PR_NUMBER": "7", "HEAD_SHA": SHA,
              "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_API_URL": "https://gh.test"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return summary


def test_build_comment_structure() -> None:
    text = ci.build_comment(make_review())
    assert text.splitlines()[0] == "<!-- ai-pr-review -->"
    assert "## Ревью Pull Request" in text
    assert "Коммит: `aaaaaaa`" in text
    assert "Модель: `deepseek/deepseek-chat`" in text
    positions = [text.index(f"### {t}") for t in
                 ("Потенциальные баги", "Архитектурные проблемы", "Рекомендации")]
    assert positions == sorted(positions)
    assert text.count("Замечаний нет.") == 2
    assert "<details><summary>Источники</summary>" in text
    assert "`agent/ws.py:120-164`" in text
    assert "- `agent/main.py`" in text
    assert "Документация" in text and "Код" in text


def test_empty_source_lists_render_no() -> None:
    text = ci.build_comment(make_review(sources={"docs": [], "code": []}))
    assert text.count("\nнет\n") == 2


def test_notes_unreviewed_and_docs_not_used() -> None:
    text = ci.build_comment(make_review(notes=["Часть 1 из 2"], unreviewed_files=["big.bin"],
                                        docs_used=False))
    header = text[:text.index("### Потенциальные баги")]
    assert "Часть 1 из 2" in header
    assert "big.bin" in header
    assert "Документация не использовалась." in header


def test_defuse_mentions_and_images() -> None:
    out = ci.defuse("привет @octocat ![x](http://evil/p.png) и a@ b")
    assert "@octocat" not in out
    assert "@​octocat" in out
    assert "![" not in out
    assert "a@ b" in out


def test_truncation_cuts_sources_first() -> None:
    code = [{"file": f"f{i}.py", "section": None, "lines": "1-2"} for i in range(5000)]
    text = ci.build_comment(make_review(sources={"docs": [], "code": code}))
    assert len(text) <= ci.COMMENT_MAX_CHARS
    assert "Список источников сокращён." in text
    assert "Текст ревью сокращён" not in text
    assert "- `f0.py:1-2`" in text or "нет" in text


def test_truncation_cuts_sections_proportionally() -> None:
    big = "слово " * 20000
    sections = {"bugs": big, "architecture": big, "recommendations": big}
    text = ci.build_comment(make_review(sections=sections))
    assert len(text) <= ci.COMMENT_MAX_CHARS
    assert text.startswith(ci.MARKER)
    assert "Текст ревью сокращён до допустимого размера комментария." in text
    for title in ("Потенциальные баги", "Архитектурные проблемы", "Рекомендации"):
        assert f"### {title}" in text


@pytest.mark.parametrize("reply,needle", [
    (lambda: ci.Reply(None, None, "URLError: x"), "приложение недоступно по адресу http://app"),
    (lambda: ci.Reply(401, {"detail": "no"}), "ключ API отклонён"),
    (lambda: ci.Reply(403, {"detail": "no"}), "ключ API отклонён"),
    (lambda: ci.Reply(503, {"detail": "d", "code": "GITHUB_MCP_UNAVAILABLE"}),
     "сервер GitHub не настроен или не подключён"),
    (lambda: ci.Reply(503, {"detail": "d", "code": "LM_STUDIO_UNAVAILABLE"}), "LM Studio недоступен"),
    (lambda: ci.Reply(503, {"detail": "d", "code": "EMBEDDER_UNAVAILABLE"}), "LM Studio недоступен"),
    (lambda: ci.Reply(502, {"detail": "boom", "code": "LLM_ERROR"}), "модель вернула ошибку: boom."),
    (lambda: ci.Reply(502, {"detail": "boom", "code": "REVIEW_BAD_OUTPUT"}), "модель вернула ошибку"),
    (lambda: ci.Reply(422, {"detail": "big", "code": "PR_TOO_LARGE"}),
     "Ревью не выполнено: big (код PR_TOO_LARGE)."),
    (lambda: ci.Reply(500, "<html>"), "Ревью не выполнено: приложение ответило HTTP 500."),
])
def test_failure_line(reply: Any, needle: str) -> None:
    assert needle in ci.failure_line("http://app", reply())


def test_failure_detail_cut_to_300() -> None:
    line = ci.failure_line("u", ci.Reply(502, {"detail": "x" * 1000, "code": "LLM_ERROR"}))
    assert "x" * 300 in line and "x" * 301 not in line


def test_find_own_comment() -> None:
    forged = {"id": 9, "body": ci.MARKER + "\nfake", "user": {"login": "someone"}}
    own_old = {"id": 3, "body": ci.MARKER + "\nold", "user": {"login": ci.BOT_LOGIN}}
    own_new = {"id": 5, "body": ci.MARKER + "\nnew", "user": {"login": ci.BOT_LOGIN}}
    plain = {"id": 11, "body": "hello", "user": {"login": ci.BOT_LOGIN}}
    assert ci.find_own_comment([forged]) is None
    assert ci.find_own_comment([forged, own_old, plain, own_new]) == own_new


def test_publish_creates_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake([ci.Reply(200, []), ci.Reply(201, {})])
    monkeypatch.setattr(ci, "call", fake)
    assert ci.publish("https://gh.test", "o/r", 7, TOKEN, "text").status == 201
    assert fake.calls[0][1].endswith("/repos/o/r/issues/7/comments?per_page=100&page=1")
    assert fake.calls[1][:2] == ("POST", "https://gh.test/repos/o/r/issues/7/comments")


def test_publish_follows_pages_and_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = [{"id": i, "body": "x", "user": {"login": "u"}} for i in range(100)]
    own = {"id": 500, "body": ci.MARKER, "user": {"login": ci.BOT_LOGIN}}
    fake = Fake([ci.Reply(200, page1), ci.Reply(200, [own]), ci.Reply(200, {})])
    monkeypatch.setattr(ci, "call", fake)
    ci.publish("https://gh.test", "o/r", 7, TOKEN, "new text")
    assert "page=2" in fake.calls[1][1]
    assert fake.calls[2] == ("PATCH", "https://gh.test/repos/o/r/issues/comments/500",
                             {"body": "new text"})


def test_main_success(monkeypatch: pytest.MonkeyPatch, env: pathlib.Path,
                      capsys: pytest.CaptureFixture[str]) -> None:
    fake = Fake([ci.Reply(200, make_review()), ci.Reply(200, []), ci.Reply(201, {})])
    monkeypatch.setattr(ci, "call", fake)
    assert ci.main([]) == 0
    method, url, body = fake.calls[0]
    assert (method, url) == ("POST", "http://127.0.0.1:8001/api/v1/reviews")
    assert body == {"repo": "o/r", "pr_number": 7, "head_sha": SHA}
    summary = env.read_text(encoding="utf-8")
    assert summary.startswith(ci.MARKER)
    assert fake.calls[2][2]["body"].startswith(ci.MARKER)
    out = capsys.readouterr().out + summary
    assert API_KEY not in out and TOKEN not in out


def test_main_review_failure_scrubs_echoed_secrets(
        monkeypatch: pytest.MonkeyPatch, env: pathlib.Path,
        capsys: pytest.CaptureFixture[str]) -> None:
    body = {"detail": f"bad {API_KEY} and {TOKEN}", "code": "LLM_ERROR"}
    monkeypatch.setattr(ci, "call", Fake([ci.Reply(502, body)]))
    assert ci.main([]) == 1
    out = capsys.readouterr().out + env.read_text(encoding="utf-8")
    assert "модель вернула ошибку" in out
    assert API_KEY not in out and TOKEN not in out


def test_main_comment_forbidden(monkeypatch: pytest.MonkeyPatch, env: pathlib.Path) -> None:
    fake = Fake([ci.Reply(200, make_review()), ci.Reply(200, []), ci.Reply(403, {})])
    monkeypatch.setattr(ci, "call", fake)
    assert ci.main([]) == 1
    summary = env.read_text(encoding="utf-8")
    assert ("Комментарий не опубликован: GitHub ответил HTTP 403. Проверьте права токена "
            "задачи (pull-requests: write; при отказе добавьте issues: write).") in summary
    assert "### Потенциальные баги" in summary


@pytest.mark.parametrize("missing", ["API_KEY", "REPO", "PR_NUMBER"])
def test_main_missing_variable(monkeypatch: pytest.MonkeyPatch, env: pathlib.Path,
                               capsys: pytest.CaptureFixture[str], missing: str) -> None:
    monkeypatch.delenv(missing)
    fake = Fake([])
    monkeypatch.setattr(ci, "call", fake)
    assert ci.main([]) == 1
    assert f"Ревью не выполнено: не задана переменная {missing}." in capsys.readouterr().out
    assert fake.calls == []


@pytest.mark.parametrize("name,value", [("REPO", "evil/../x y"), ("PR_NUMBER", "7; rm")])
def test_main_rejects_bad_format(monkeypatch: pytest.MonkeyPatch, env: pathlib.Path,
                                 name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    fake = Fake([])
    monkeypatch.setattr(ci, "call", fake)
    assert ci.main([]) == 1
    assert fake.calls == []


def test_main_default_app_url(monkeypatch: pytest.MonkeyPatch, env: pathlib.Path) -> None:
    monkeypatch.setenv("APP_URL", "")
    fake = Fake([ci.Reply(None, None, "x")])
    monkeypatch.setattr(ci, "call", fake)
    assert ci.main([]) == 1
    assert fake.calls[0][1] == "http://127.0.0.1:8001/api/v1/reviews"


def test_script_imports_only_stdlib() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    modules = {a or b for a, b in re.findall(r"^(?:import (\w+)|from (\w+) import)", source, flags=re.M)}
    assert modules <= set(sys.stdlib_module_names)


# --- workflow source checks ---------------------------------------------------

def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    return [ln for ln in _workflow().splitlines() if not ln.lstrip().startswith("#")]


def test_workflow_action_pinned_by_sha() -> None:
    uses = [ln for ln in _code_lines() if "uses:" in ln]
    assert uses and all(re.search(r"@[0-9a-f]{40}\b", ln) for ln in uses)
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in _workflow()
    assert "persist-credentials: false" in _workflow()


def test_workflow_triggers_and_guards() -> None:
    text = "\n".join(_code_lines())
    assert "pull_request_target" not in text
    assert "types: [opened, synchronize, reopened, ready_for_review]" in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    assert "draft == false" in text
    assert "cancel-in-progress: false" in text
    assert "continue-on-error" not in text


def test_workflow_runner_and_permissions() -> None:
    text = "\n".join(_code_lines())
    runs_on = [ln for ln in text.splitlines() if "runs-on:" in ln]
    assert runs_on and "self-hosted" in runs_on[0] and "pr-review" in runs_on[0]
    block = re.search(r"^permissions:\n((?:[ ]+\S.*\n)+)", text + "\n", flags=re.M)
    assert block is not None
    keys = [ln.split(":")[0].strip() for ln in block.group(1).splitlines()]
    assert keys == ["contents", "pull-requests"]
    assert "contents: read" in text and "pull-requests: write" in text


def test_workflow_no_expression_in_run_and_no_literal_secret() -> None:
    for ln in _code_lines():
        if re.match(r"\s*-?\s*run:", ln):
            assert "${{" not in ln
    assert "python scripts/pr_review_ci.py" in _workflow()
    assert "secrets.PR_REVIEW_API_KEY" in _workflow()
    assert "vars.PR_REVIEW_APP_URL" in _workflow()
    assert "aia_" not in _workflow()
