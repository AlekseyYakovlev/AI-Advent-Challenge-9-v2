"""Tests for the PR review core: budget, splitting, queries, parsing and sanitising."""

import math

import pytest

from agent.llm_client import count_tokens
from agent.review_github import PrFile, PullRequestData
from agent.review_pipeline import (
    ANSWER_MAX_TOKENS,
    CODE_PR_TOO_LARGE,
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
