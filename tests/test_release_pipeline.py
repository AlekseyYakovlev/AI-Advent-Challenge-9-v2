"""Tests for the pure release logic: versions, parsers, filtering and prompt checks."""

import json

import pytest

from agent import release_command as rc
from agent import release_pipeline as rp
from agent.release_pipeline import CommitInfo


def _c(subject: str, parents: int = 1, planning_only: bool = False, files_n: int = 2,
       dirs: list[str] | None = None) -> CommitInfo:
    return CommitInfo(
        sha="a" * 40, parents=parents, subject=subject, dirs=dirs or ["agent"],
        planning_only=planning_only, files_n=files_n,
    )


# --- versions ---------------------------------------------------------------

def test_parse_version() -> None:
    assert rp.parse_version("v4.0") == (4, 0)
    assert rp.parse_version("v10.12") == (10, 12)
    for bad in ("4.0", "v4", "v4.0.1", "backup/x"):
        assert rp.parse_version(bad) is None


def test_last_version_tag_numeric_order() -> None:
    tags = ["v1.0", "v9.0", "v10.0", "backup/day30-before-reset"]
    assert rp.last_version_tag(tags) == "v10.0"
    assert rp.last_version_tag(["backup/x"]) is None
    assert rp.last_version_tag([]) is None


def test_version_candidates() -> None:
    assert rp.version_candidates("v4.0", set()) == ["v5.0", "v4.1"]
    assert rp.version_candidates("v4.0", {"v5.0"}) == ["v4.1"]
    assert rp.version_candidates("v4.0", {"v5.0", "v4.1"}) == []
    assert rp.version_candidates("v9.9", set()) == ["v10.0", "v9.10"]


def test_validate_version_order() -> None:
    assert rp.validate_version("v4.1", "v4.0", set()) is None
    assert rp.validate_version("4.1", "v4.0", set()) == rc.MSG_VERSION_FORMAT
    expected = rc.MSG_VERSION_NOT_GREATER.format(last="v4.0")
    assert rp.validate_version("v4.0", "v4.0", set()) == expected
    assert rp.validate_version("v3.9", "v4.0", set()) == expected
    assert rp.validate_version("v5.0", "v4.0", {"v5.0"}) == rc.MSG_VERSION_TAKEN.format(
        version="v5.0"
    )
    assert rp.validate_version("v10.0", "v9.0", set()) is None


def test_default_version() -> None:
    assert rp.default_version(["v5.0", "v4.1"], {"breaking": True, "day_merges": 0}) == "v5.0"
    assert rp.default_version(["v5.0", "v4.1"], {"breaking": False, "day_merges": 2}) == "v5.0"
    assert rp.default_version(["v5.0", "v4.1"], {"breaking": False, "day_merges": 0}) == "v4.1"
    assert rp.default_version(["v4.1"], {"breaking": True, "day_merges": 1}) == "v4.1"


def test_tag_message() -> None:
    assert rp.tag_message("v5.0", "Pipeline") == "v5.0 Week 7: Pipeline"
    assert rp.tag_message("v1.0", "A") == "v1.0 Week 3: A"
    assert rp.tag_message("v4.1", "Fixes") == "v4.1: Fixes"


# --- MCP result parsers -----------------------------------------------------

def test_parse_context() -> None:
    payload = {
        "branch": "main", "head": "abc", "origin_url": "git@h:o/r.git",
        "local_tags": ["v4.0"], "merged_tags": ["v4.0"], "remote_tags": None,
        "remote_error": "boom", "dirty": ["?? a.txt"],
    }
    ctx = rp.parse_context("summary line\n" + json.dumps(payload))
    assert ctx.branch == "main" and ctx.head == "abc" and ctx.remote_tags is None
    assert ctx.dirty == ["?? a.txt"] and ctx.remote_error == "boom"
    with pytest.raises(rp.ReleaseParseError):
        rp.parse_context("only a summary")


def test_parse_commit_page() -> None:
    row = {"sha": "s1", "parents": 1, "subject": "feat(25-01): x", "dirs": ["agent"],
           "planning_only": False, "files_n": 3}
    text = "2 commits\n" + json.dumps(row) + "\n" + json.dumps({"next_page": 2, "total": 9})
    commits, nxt = rp.parse_commit_page(text)
    assert [c.subject for c in commits] == ["feat(25-01): x"] and nxt == 2
    last = "1 commit\n" + json.dumps(row) + "\n" + json.dumps({"next_page": None, "total": 1})
    assert rp.parse_commit_page(last)[1] is None
    with pytest.raises(rp.ReleaseParseError):
        rp.parse_commit_page("s\n{not json")


def test_parse_json_tail() -> None:
    assert rp.parse_json_tail('done\n{"sha": "x"}') == {"sha": "x"}
    with pytest.raises(rp.ReleaseParseError):
        rp.parse_json_tail("no json")


def test_dirty_paths() -> None:
    lines = ["?? new.txt", " M agent/ws.py", "R  a -> b"]
    assert rp.dirty_paths(lines) == ["new.txt", "agent/ws.py", "b"]


def test_parse_owner_repo() -> None:
    full = "AlekseyYakovlev/AI-Advent-Challenge-9-v2"
    assert rp.parse_owner_repo(f"git@AiAdventAgentV2:{full}.git") == full
    assert rp.parse_owner_repo("https://github.com/o/r.git") == "o/r"
    assert rp.parse_owner_repo("https://github.com/o/r") == "o/r"
    assert rp.parse_owner_repo("ssh://git@github.com/o/r.git") == "o/r"
    assert rp.parse_owner_repo("C:/repos/bare.git") is None
    assert rp.parse_owner_repo(None) is None


def test_is_release_link() -> None:
    assert rp.is_release_link("https://github.com/o/r/releases/tag/x")
    assert not rp.is_release_link("http://github.com/o/r/releases/tag/x")
    assert not rp.is_release_link("https://evil.com/o/r/releases/tag/x")
    assert not rp.is_release_link(None)


# --- commit logic -----------------------------------------------------------

def _fixture() -> list[CommitInfo]:
    return [
        _c("feat(25-04): add thing"),
        _c("Merge branch 'Day32': stuff", parents=2),
        _c("chore: merge executor worktree", parents=2),
        _c("chore(x): y"),
        _c("wip: z"),
        _c("docs(25-01): plan notes", planning_only=True),
        _c("fix(26): empty", files_n=0),
        _c("docs(26): update USER_GUIDE", dirs=["docs"]),
        _c("feat(25-04)!: breaking"),
    ]


def test_filter_commits() -> None:
    result = rp.filter_commits(_fixture())
    assert [c.subject for c in result.kept] == [
        "feat(25-04): add thing", "docs(26): update USER_GUIDE", "feat(25-04)!: breaking",
    ]
    assert result.day_merges == ["Day32"]
    assert len(result.day_merge_shas) == 1
    assert result.dropped == 5


def test_group_by_phase_order() -> None:
    kept = [
        _c("refactor: d"), _c("docs(quick-re4): c"), _c("fix(26): e"),
        _c("feat(25-04): a"), _c("fix(25): b"), _c("feat(9): f"),
    ]
    groups = rp.group_by_phase(kept)
    assert list(groups) == ["9", "25", "26", "quick", "other"]
    assert groups["25"] == ["feat(25-04): a", "fix(25): b"]
    assert groups["quick"] == ["docs(quick-re4): c"]
    assert groups["other"] == ["refactor: d"]


def test_collect_signals() -> None:
    result = rp.filter_commits(_fixture())
    signals = rp.collect_signals(result, 2)
    assert signals["feat"] == 2 and signals["fix"] == 0
    assert signals["kept"] == 3 and signals["day_merges"] == 1 and signals["prs"] == 2
    assert signals["breaking"] is True
    calm = rp.collect_signals(rp.filter_commits([_c("fix(1): a")]), 0)
    assert calm["breaking"] is False and calm["fix"] == 1
    loud = rp.collect_signals(rp.filter_commits([_c("feat: BREAKING change")]), 0)
    assert loud["breaking"] is True


def test_sanitize_title() -> None:
    assert rp.sanitize_title("<think>x</think>**«Local LLM»**\nsecond line") == "Local LLM"
    assert rp.sanitize_title("") is None
    assert rp.sanitize_title(None) is None
    long = " ".join(["слово"] * 40)
    cut = rp.sanitize_title(long)
    assert cut is not None and len(cut) <= 80 and cut.endswith("слово")


# --- prompts, LLM output parsing, coverage, section ---------------------------

GROUPS = {"25": ["feat(25-01): a", "fix(25-02): b"], "quick": ["docs(quick-x): c"],
          "other": ["refactor: d"]}


def test_build_changelog_messages_layout() -> None:
    messages, shrunk = rp.build_changelog_messages(GROUPS, ["Day32"], ["PR #5: Title"])
    assert shrunk == 0
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    system = messages[0]["content"]
    for heading in rp.ALLOWED_HEADINGS:
        assert heading in system
    assert "<commits>" in system
    user = messages[1]["content"]
    assert "<commits>" in user and "</commits>" in user
    assert "Фаза 25:" in user and "Быстрые задачи:" in user and "Прочее:" in user
    assert "- feat(25-01): a" in user and "Day32" in user and "PR #5: Title" in user


def test_build_changelog_messages_shrinks() -> None:
    groups = {"25": [f"feat(25-01): item {i}" for i in range(40)] + ["test(25-01): t1"]}
    messages, shrunk = rp.build_changelog_messages(groups, [], [], budget_chars=700)
    user = messages[1]["content"]
    assert "test(25-01): t1" not in user
    assert "… и ещё" in user
    assert shrunk == 1 + (40 - rp.GROUP_SUBJECT_CAP)
    assert user.count("- feat(25-01): item") == rp.GROUP_SUBJECT_CAP


def test_parse_changelog_accepts_and_strips() -> None:
    raw = (
        "<think>hm</think>\n```markdown\nПредисловие\n### Добавлено\n- one\n"
        "### Исправлено\n- two\n```"
    )
    assert rp.parse_changelog(raw) == "### Добавлено\n- one\n### Исправлено\n- two"
    assert rp.parse_changelog("reasoning</think>\n### Изменено\n- x") == "### Изменено\n- x"


@pytest.mark.parametrize(
    "raw",
    [None, "", "just text", "### Прочее\n- x", "### Добавлено\n- a\n## Release\n- b",
     "### Добавлено\nno items here"],
)
def test_parse_changelog_rejects(raw: str | None) -> None:
    assert rp.parse_changelog(raw) is None


def test_changelog_coverage() -> None:
    body = "### Добавлено\n- Фаза 26: x\n- слияние Day 32"
    assert rp.changelog_coverage(body, ["25", "26"], ["Day32"]) == ["25"]
    assert rp.changelog_coverage("фазы 25 и (26), day32", ["25", "26"], ["Day32"]) == []
    assert rp.changelog_coverage("", ["25"], ["Day33"]) == ["25", "Day33"]


def test_build_retry_messages() -> None:
    base = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    out = rp.build_retry_messages(base, "draft", ["25", "Day32"])
    assert out[:2] == base
    assert out[2] == {"role": "assistant", "content": "draft"}
    assert out[3]["role"] == "user"
    assert "25" in out[3]["content"] and "Day32" in out[3]["content"]
    assert len(base) == 2


def test_build_decision_messages() -> None:
    signals = {"feat": 3, "fix": 1, "breaking": False}
    messages = rp.build_decision_messages(["v5.0", "v4.1"], signals, "body" * 2000)
    assert messages[0]["role"] == "system" and "JSON" in messages[0]["content"]
    user = messages[1]["content"]
    assert "v5.0" in user and "v4.1" in user and "feat" in user
    assert len(user) < 6000


def test_parse_decision_valid_and_fenced() -> None:
    text = '{"version": "v5.0", "title": "Pipeline", "rationale": "A. B."}'
    assert rp.parse_decision(text, ["v5.0", "v4.1"]) == rp.Decision("v5.0", "Pipeline", "A. B.")
    fenced = "<think>x</think>\n```json\n" + text + "\n```"
    assert rp.parse_decision(fenced, ["v5.0", "v4.1"]).version == "v5.0"


def test_parse_decision_rejections() -> None:
    bad = '{"version": "v6.0", "title": "T", "rationale": "R"}'
    decision = rp.parse_decision(bad, ["v5.0", "v4.1"])
    assert decision.version is None and decision.title == "T"
    assert rp.parse_decision("not json", ["v5.0"]) == rp.Decision(None, None, None)
    assert rp.parse_decision(None, ["v5.0"]) == rp.Decision(None, None, None)


def test_changelog_section() -> None:
    assert rp.changelog_section("v5.0", "2026-10-17", "  ### Добавлено\n- x \n") == (
        "## [v5.0] - 2026-10-17\n\n### Добавлено\n- x\n"
    )
