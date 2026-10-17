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
