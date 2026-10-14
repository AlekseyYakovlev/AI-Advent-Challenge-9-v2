"""Tests for the PR review core: budget, splitting, queries, parsing and sanitising."""

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agent import review_pipeline
from agent.llm_client import ChatCompletionResult, count_tokens
from agent.providers import KIND_LM_STUDIO
from agent.rag import RagFailure
from agent.review_github import PrFile, PullRequestData
from agent.review_pipeline import (
    ANSWER_MAX_TOKENS,
    CODE_EMBEDDER_UNAVAILABLE,
    CODE_INDEX_UNAVAILABLE,
    CODE_LLM_ERROR,
    CODE_LLM_UNREACHABLE,
    CODE_LM_STUDIO_UNAVAILABLE,
    CODE_REVIEW_BAD_OUTPUT,
    CODE_PR_TOO_LARGE,
    CODE_TOP_K,
    DOCS_TOP_K,
    MAX_PARTS,
    MAX_QUERY_CHARS,
    MAX_SECTION_CHARS,
    PROMPT_OVERHEAD_TOKENS,
    RAG_SHARE,
    ReviewError,
    ReviewPart,
    build_query,
    estimate_tokens,
    parse_review,
    plan_parts,
    run_review,
    sanitize_markdown,
    split_diff_by_file,
)

ZWSP = "​"


def _patch(index: int, lines: int = 3) -> str:
    body = "\n".join(f"+value_{index}_{n} = compute({n}) # строка" for n in range(lines))
    return f"@@ -1,1 +1,{lines} @@ def func_{index}()\n{body}"


def _pr(
    files: list[PrFile], diff: str | None = None, title: str = "Заголовок"
) -> PullRequestData:
    if diff is None:
        diff = "\n".join(
            f"diff --git a/{f.filename} b/{f.filename}\n{f.patch}" for f in files if f.patch
        )
    return PullRequestData(
        repo="o/r", number=1, title=title, body="", state="open", draft=False,
        base_ref="main", base_sha="a", head_ref="f", head_sha="b",
        changed_files=len(files), files=files, diff=diff,
    )


def _file(name: str, patch: str | None) -> PrFile:
    return PrFile(name, "modified", 1, 0, patch, None)


def _available(context_length: int) -> int:
    return (
        context_length
        - PROMPT_OVERHEAD_TOKENS
        - ANSWER_MAX_TOKENS
        - int(context_length * RAG_SHARE)
    )


def test_estimate_tokens_has_cyrillic_headroom() -> None:
    text = "Привет, мир! def f(): pass"
    assert estimate_tokens(text) == math.ceil(count_tokens(text) * 1.15)


def test_split_diff_two_files_rename_and_spaces() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+x\n"
        "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\n"
        "rename to new.py\n@@ -1 +1 @@\n+y\n"
        "diff --git a/my dir/my file.py b/my dir/my file.py\n@@ -1 +1 @@\n+z"
    )
    segments = split_diff_by_file(diff)
    assert list(segments) == ["a.py", "new.py", "my dir/my file.py"]
    assert "+x" in segments["a.py"]
    assert "+y" in segments["new.py"]
    assert "+z" in segments["my dir/my file.py"]
    assert split_diff_by_file("") == {}


def test_plan_parts_single_part_when_diff_fits() -> None:
    files = [_file("a.py", _patch(1)), _file("b.py", _patch(2))]
    pr = _pr(files)
    parts, unreviewed = plan_parts(pr, 32768)
    assert len(parts) == 1
    assert parts[0].diff_text == pr.diff
    assert parts[0].files == ["a.py", "b.py"]
    assert parts[0].note is None
    assert unreviewed == []


def test_plan_parts_splits_and_covers_every_file() -> None:
    files = [_file(f"f{i}.py", _patch(i, lines=40)) for i in range(8)]
    files.append(_file("binary.png", None))
    pr = _pr(files)
    context_length = 7000
    assert estimate_tokens(pr.diff) > _available(context_length)
    parts, unreviewed = plan_parts(pr, context_length)
    assert len(parts) > 1
    assert unreviewed == ["binary.png"]
    for part in parts:
        assert estimate_tokens(part.diff_text) <= _available(context_length)
    covered = [name for part in parts for name in part.files]
    for file in files:
        assert file.filename in covered or file.filename in unreviewed
    assert sorted(set(covered)) == sorted(f"f{i}.py" for i in range(8))
    assert len(covered) == len(set(covered))


def test_plan_parts_splits_one_huge_file_by_hunks_with_a_note() -> None:
    hunks = "\n".join(_patch(i, lines=30) for i in range(12))
    pr = _pr([_file("big.py", hunks)])
    parts, unreviewed = plan_parts(pr, 6500)
    assert len(parts) > 1
    assert unreviewed == []
    assert all(part.files == ["big.py"] for part in parts)
    assert all(part.note == "часть файла big.py" for part in parts)
    joined = "\n".join(part.diff_text for part in parts)
    for i in range(12):
        assert f"value_{i}_0" in joined


def test_plan_parts_uses_diff_segment_when_patch_is_missing() -> None:
    segment = (
        f"diff --git a/x.py b/x.py\n{_patch(1, lines=60)}\n"
        f"diff --git a/y.py b/y.py\n{_patch(2, lines=60)}"
    )
    pr = _pr([_file("x.py", None), _file("y.py", _patch(2, lines=60))], diff=segment)
    parts, unreviewed = plan_parts(pr, 8000)
    joined = "\n".join(part.diff_text for part in parts)
    assert unreviewed == []
    assert "value_1_0" in joined and "value_2_0" in joined


def test_plan_parts_too_many_parts_is_too_large() -> None:
    files = [_file(f"f{i}.py", _patch(i, lines=200)) for i in range(MAX_PARTS + 5)]
    with pytest.raises(ReviewError) as caught:
        plan_parts(_pr(files), 6000)
    assert caught.value.code == CODE_PR_TOO_LARGE


def test_plan_parts_context_too_small() -> None:
    with pytest.raises(ReviewError) as caught:
        plan_parts(_pr([_file("a.py", _patch(1))]), 3000)
    assert caught.value.code == CODE_PR_TOO_LARGE


def test_build_query_code_has_paths_names_and_is_bounded() -> None:
    patch = "@@ -1,1 +1,3 @@ def compute_total(items)\n+new_value = 1\n+" + "y" * 10000
    part = ReviewPart(["src/calc.py"], f"diff --git a/src/calc.py b/src/calc.py\n{patch}", None)
    query = build_query(_pr([_file("src/calc.py", patch)]), part, "code")
    assert len(query) <= MAX_QUERY_CHARS
    assert "src/calc.py" in query
    assert "compute_total" in query
    assert "new_value = 1" in query


def test_build_query_docs_has_title_and_paths_and_is_bounded() -> None:
    patch = "@@ -1 +1 @@\n+" + "z" * 10000
    part = ReviewPart(["a.py"], patch, None)
    query = build_query(_pr([_file("a.py", patch)], title="Мой заголовок"), part, "docs")
    assert len(query) <= MAX_QUERY_CHARS
    assert "Мой заголовок" in query and "a.py" in query and "правила проекта" in query


GOOD = (
    "### Потенциальные баги\n- `a.py:3` ошибка\n\n"
    "### Архитектурные проблемы\n\n"
    "### Рекомендации\n- `b.py:1` сделайте так\n"
)


def test_parse_review_well_formed() -> None:
    sections = parse_review(GOOD)
    assert sections is not None
    assert sections.bugs.startswith("- `a.py:3`")
    assert sections.architecture == "Замечаний нет."
    assert "b.py:1" in sections.recommendations


def test_parse_review_strips_think_and_accepts_variants() -> None:
    text = "<think>рассуждение\n### Рекомендации\n</think>\n" + GOOD.replace(
        "###", "##"
    ).replace("Потенциальные баги", "ПОТЕНЦИАЛЬНЫЕ БАГИ")
    sections = parse_review(text)
    assert sections is not None
    assert "рассуждение" not in sections.recommendations


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "### Потенциальные баги\n- x\n### Рекомендации\n- y",
        GOOD + "\n### Рекомендации\n- ещё",
    ],
)
def test_parse_review_bad_format(text: str | None) -> None:
    assert parse_review(text) is None


def test_sanitize_mentions_images_and_html() -> None:
    text = (
        "Привет @user и ![x](http://e/a.png) <img src=x> "
        "<script>alert(1)</script> <details>d</details>"
    )
    result = sanitize_markdown(text)
    assert f"@{ZWSP}user" in result
    assert "![" not in result and "http://e/a.png" not in result
    assert "<img" not in result and "<script" not in result and "<details" not in result
    assert "alert(1)" not in result


def test_sanitize_keeps_code() -> None:
    text = "Вот `<div>@user</div>` и\n```html\n<img src=x>\n@user\n```\n"
    result = sanitize_markdown(text)
    assert "`<div>@user</div>`" in result
    assert "<img src=x>\n@user" in result


def test_sanitize_truncates() -> None:
    result = sanitize_markdown("а" * (MAX_SECTION_CHARS + 5000))
    assert len(result) <= MAX_SECTION_CHARS
    assert result.endswith("…текст сокращён.")


DOCS_KB = SimpleNamespace(id=1)
CODE_KB = SimpleNamespace(id=2)
CONTEXT = 32768


def _chunks(kind: str, count: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": index,
            "source": f"{kind}/file{index}.md" if kind == "docs" else f"src/mod{index}.py",
            "section": f"Раздел {index}" if kind == "docs" else f"func_{index} (L{index}-{index + 5})",
            "title": "t",
            "text": f"Текст фрагмента {kind} {index}",
            "score": 0.9 - index / 100,
        }
        for index in range(count)
    ]


class FakeClient:
    """Records every completion call and replays scripted replies."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def complete_chat_detailed(self, **kwargs: Any) -> ChatCompletionResult:
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return ChatCompletionResult(reply, "stop", False, 10)


@pytest.fixture
def retrieval(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, Any, str]]:
    """Replace the retrieval pipeline with a fake that serves the two bases."""
    calls: list[tuple[int, Any, str]] = []

    async def fake(
        session: Any, kb: Any, question: str, config: Any, client: Any = None, model: Any = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        calls.append((kb.id, config, question))
        kind = "docs" if kb.id == DOCS_KB.id else "code"
        return _chunks(kind)[: config.top_k], {}

    monkeypatch.setattr(review_pipeline, "run_retrieval_pipeline", fake)
    return calls


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://llm/v1/chat/completions")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status, request=request))


def _small_pr() -> PullRequestData:
    return _pr([_file("a.py", _patch(1)), _file("b.py", _patch(2))])


def _three_part_pr() -> PullRequestData:
    return _pr([_file(f"f{i}.py", _patch(i, lines=60)) for i in range(3)])


async def test_one_part_review_uses_one_call_with_both_sources(
    retrieval: list[tuple[int, Any, str]],
) -> None:
    client = FakeClient([GOOD])
    result = await run_review(None, _small_pr(), DOCS_KB, CODE_KB, client, "m", CONTEXT)
    assert len(client.calls) == 1
    assert result.parts == 1 and result.docs_used is True
    user = client.calls[0]["messages"][1]["content"]
    assert "diff --git a/a.py" in user and "<documentation>" in user and "<code>" in user
    assert result.sections.bugs.startswith("- `a.py:3`")
    assert result.sources_docs and all(set(item) == {"file", "section"} for item in result.sources_docs)
    assert result.sources_code[0]["lines"] == "0-5"
    assert len(result.sources_docs) <= DOCS_TOP_K and len(result.sources_code) <= CODE_TOP_K


async def test_retrieval_uses_separate_quotas_and_no_llm_stages(
    retrieval: list[tuple[int, Any, str]],
) -> None:
    await run_review(None, _small_pr(), DOCS_KB, CODE_KB, FakeClient([GOOD]), "m", CONTEXT)
    by_kb = {kb_id: config for kb_id, config, _ in retrieval}
    assert by_kb[DOCS_KB.id].top_k == DOCS_TOP_K and by_kb[DOCS_KB.id].threshold == 0.35
    assert by_kb[CODE_KB.id].top_k == CODE_TOP_K and by_kb[CODE_KB.id].threshold == 0.0
    for config in by_kb.values():
        assert not (config.lexical or config.llm_rerank or config.hybrid or config.rewrite)


async def test_three_parts_make_four_calls_and_merge_has_no_diff(
    retrieval: list[tuple[int, Any, str]],
) -> None:
    pr = _three_part_pr()
    parts, _ = plan_parts(pr, 8000)
    assert len(parts) == 3
    client = FakeClient([GOOD])
    result = await run_review(None, pr, DOCS_KB, CODE_KB, client, "m", 8000)
    assert result.parts == 3
    assert len(client.calls) == 4
    assert len(retrieval) == 6
    merge_user = client.calls[-1]["messages"][1]["content"]
    assert "<partial_review>" in merge_user and "diff --git" not in merge_user
    assert merge_user.count("<partial_review>") == 3


async def test_every_model_call_is_tool_less_and_reasoning_off(
    retrieval: list[tuple[int, Any, str]],
) -> None:
    client = FakeClient([GOOD])
    await run_review(None, _three_part_pr(), DOCS_KB, CODE_KB, client, "m", 8000)
    for call in client.calls:
        assert "tools" not in call and "tool_choice" not in call
        assert call["extra_body"] == {"reasoning_effort": "none"}


async def test_without_docs_base_the_review_still_runs(
    retrieval: list[tuple[int, Any, str]],
) -> None:
    client = FakeClient([GOOD])
    result = await run_review(None, _small_pr(), None, CODE_KB, client, "m", CONTEXT)
    assert result.docs_used is False
    assert "Документация не использовалась: база документации не выбрана." in result.notes
    assert [kb_id for kb_id, _, _ in retrieval] == [CODE_KB.id]
    assert "<documentation>" not in client.calls[0]["messages"][1]["content"]


async def test_docs_failure_continues_without_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(session: Any, kb: Any, question: str, config: Any, *a: Any) -> Any:
        if kb.id == DOCS_KB.id:
            raise RagFailure("kb_not_ready", "x")
        return _chunks("code"), {}

    monkeypatch.setattr(review_pipeline, "run_retrieval_pipeline", fake)
    result = await run_review(None, _small_pr(), DOCS_KB, CODE_KB, FakeClient([GOOD]), "m", CONTEXT)
    assert result.docs_used is False
    assert "Документация не использовалась: база документации недоступна." in result.notes


@pytest.mark.parametrize("failing_kb", [DOCS_KB, CODE_KB])
async def test_embedder_failure_stops_the_review(
    monkeypatch: pytest.MonkeyPatch, failing_kb: Any
) -> None:
    async def fake(session: Any, kb: Any, question: str, config: Any, *a: Any) -> Any:
        if kb.id == failing_kb.id:
            raise RagFailure("embedder_unavailable", "x")
        return _chunks("code"), {}

    monkeypatch.setattr(review_pipeline, "run_retrieval_pipeline", fake)
    with pytest.raises(review_pipeline.ReviewError) as caught:
        await run_review(None, _small_pr(), DOCS_KB, CODE_KB, FakeClient([GOOD]), "m", CONTEXT)
    assert caught.value.code == CODE_EMBEDDER_UNAVAILABLE


async def test_code_index_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(session: Any, kb: Any, question: str, config: Any, *a: Any) -> Any:
        if kb.id == CODE_KB.id:
            raise RagFailure("index_corrupt", "x")
        return _chunks("docs"), {}

    monkeypatch.setattr(review_pipeline, "run_retrieval_pipeline", fake)
    with pytest.raises(review_pipeline.ReviewError) as caught:
        await run_review(None, _small_pr(), DOCS_KB, CODE_KB, FakeClient([GOOD]), "m", CONTEXT)
    assert caught.value.code == CODE_INDEX_UNAVAILABLE


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (httpx.ConnectError("no"), KIND_LM_STUDIO, CODE_LM_STUDIO_UNAVAILABLE),
        (httpx.ConnectError("no"), "deepseek", CODE_LLM_UNREACHABLE),
        (httpx.ReadTimeout("slow"), None, CODE_LLM_UNREACHABLE),
        (_http_error(500), None, CODE_LLM_ERROR),
    ],
)
async def test_model_errors_are_mapped(
    retrieval: list[tuple[int, Any, str]], error: Exception, kind: str | None, code: str
) -> None:
    with pytest.raises(review_pipeline.ReviewError) as caught:
        await run_review(
            None, _small_pr(), DOCS_KB, CODE_KB, FakeClient([error]), "m", CONTEXT, kind
        )
    assert caught.value.code == code
    assert "boom" not in caught.value.detail and "no" != caught.value.detail


async def test_http_error_detail_holds_status_only(
    retrieval: list[tuple[int, Any, str]],
) -> None:
    with pytest.raises(review_pipeline.ReviewError) as caught:
        await run_review(None, _small_pr(), DOCS_KB, CODE_KB, FakeClient([_http_error(503)]), "m", CONTEXT)
    assert "503" in caught.value.detail and "boom" not in caught.value.detail


async def test_rejected_reasoning_control_is_retried_without_it(
    retrieval: list[tuple[int, Any, str]],
) -> None:
    client = FakeClient([_http_error(400), GOOD])
    await run_review(None, _small_pr(), DOCS_KB, CODE_KB, client, "m", CONTEXT)
    assert [call["extra_body"] for call in client.calls] == [{"reasoning_effort": "none"}, None]


async def test_bad_reply_gets_one_repair_call_then_fails(
    retrieval: list[tuple[int, Any, str]],
) -> None:
    client = FakeClient(["просто текст", GOOD])
    result = await run_review(None, _small_pr(), DOCS_KB, CODE_KB, client, "m", CONTEXT)
    assert len(client.calls) == 2
    assert "трёх разделов" in client.calls[1]["messages"][-1]["content"]
    assert result.sections.bugs

    failing = FakeClient(["плохо", None])
    with pytest.raises(review_pipeline.ReviewError) as caught:
        await run_review(None, _small_pr(), DOCS_KB, CODE_KB, failing, "m", CONTEXT)
    assert caught.value.code == CODE_REVIEW_BAD_OUTPUT
    assert len(failing.calls) == 2


async def test_unreviewed_files_are_named(retrieval: list[tuple[int, Any, str]]) -> None:
    files = [_file(f"f{i}.py", _patch(i, lines=60)) for i in range(2)]
    files.append(_file("image.png", None))
    result = await run_review(None, _pr(files), DOCS_KB, CODE_KB, FakeClient([GOOD]), "m", 8000)
    assert result.unreviewed_files == ["image.png"]
    assert any("image.png" in note for note in result.notes)


async def test_output_is_sanitised(retrieval: list[tuple[int, Any, str]]) -> None:
    reply = GOOD.replace("ошибка", "ошибка @octocat ![i](http://e/i.png) <img src=x>")
    result = await run_review(None, _small_pr(), DOCS_KB, CODE_KB, FakeClient([reply]), "m", CONTEXT)
    assert f"@{ZWSP}octocat" in result.sections.bugs
    assert "![" not in result.sections.bugs and "<img" not in result.sections.bugs


def test_review_pipeline_does_not_import_tool_modules() -> None:
    source = Path(review_pipeline.__file__).read_text(encoding="utf-8")
    code_lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    text = "\n".join(code_lines)
    for forbidden in ("agent.headless", "agent.tools", "agent.mcp_tools", "agent.ws"):
        assert forbidden not in text
