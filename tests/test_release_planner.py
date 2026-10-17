"""Tests for the release plan phase: chat-model calls and build_plan over a fake ToolCaller."""

import json
from typing import Any

from agent.llm_client import ChatCompletionResult
from agent.release_command import (
    GITHUB_SERVER_NAME,
    MSG_DIRTY_TREE,
    MSG_GITHUB_PRS_SKIPPED,
    MSG_LLM_FAILED,
    MSG_NOT_MAIN,
    MSG_REMOTE_TAGS_UNKNOWN,
    MSG_VERSION_FALLBACK,
    MSG_VERSION_TAKEN,
    RELEASE_SERVER_NAME,
    StepState,
    ToolOutcome,
)
from agent.release_llm import ReleaseLlm, choose_version, draft_changelog
from agent.release_planner import MSG_NO_VERSION_TAG, MSG_READ_FAILED, build_plan


def _result(content: str | None) -> ChatCompletionResult:
    """Build a completion result with the given text."""
    return ChatCompletionResult(
        content=content, finish_reason="stop", has_reasoning=False, completion_tokens=10
    )


class FakeClient:
    """Chat client double that returns queued results and records the messages."""

    def __init__(self, answers: list[str | None]) -> None:
        self._answers = list(answers)
        self.calls: list[list[dict[str, str]]] = []

    async def complete_chat_detailed(self, **kwargs: Any) -> ChatCompletionResult:
        self.calls.append(kwargs["messages"])
        return _result(self._answers.pop(0))


def _llm(answers: list[str | None]) -> tuple[ReleaseLlm, FakeClient]:
    client = FakeClient(answers)
    return ReleaseLlm(client, "test-model", timeout=5.0), client


FULL_BODY = "### Добавлено\n- Фаза 25: поиск\n- Фаза 26: видео\n- Day34 влит"
PARTIAL_BODY = "### Добавлено\n- Фаза 25: поиск\n- Day34 влит"
GROUPS = {"25": ["feat(25-01): search"], "26": ["feat(26-01): video"]}


async def test_llm_complete_returns_text() -> None:
    llm, client = _llm(["hello"])
    assert await llm.complete("changelog", [{"role": "user", "content": "x"}], 50) == "hello"
    assert len(client.calls) == 1


async def test_llm_complete_blank_content_is_none() -> None:
    llm, _ = _llm(["   "])
    assert await llm.complete("changelog", [{"role": "user", "content": "x"}], 50) is None


async def test_llm_complete_none_content_is_none() -> None:
    llm, _ = _llm([None])
    assert await llm.complete("decision", [{"role": "user", "content": "x"}], 50) is None


async def test_llm_complete_client_error_is_none() -> None:
    class Broken:
        async def complete_chat_detailed(self, **kwargs: Any) -> ChatCompletionResult:
            raise RuntimeError("boom")

    llm = ReleaseLlm(Broken(), "m", timeout=5.0)
    assert await llm.complete("changelog", [{"role": "user", "content": "x"}], 50) is None


async def test_changelog_single_call_when_covered() -> None:
    llm, client = _llm([FULL_BODY])
    body, missing, shrunk = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == FULL_BODY
    assert missing == []
    assert shrunk == 0
    assert len(client.calls) == 1


async def test_changelog_retry_covers_missing_phase() -> None:
    llm, client = _llm([PARTIAL_BODY, FULL_BODY])
    body, missing, _ = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == FULL_BODY
    assert missing == []
    assert len(client.calls) == 2
    assert "26" in client.calls[1][-1]["content"]


async def test_changelog_retry_still_missing_is_reported() -> None:
    llm, client = _llm([PARTIAL_BODY, PARTIAL_BODY])
    body, missing, _ = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == PARTIAL_BODY
    assert missing == ["26"]
    assert len(client.calls) == 2


async def test_changelog_keeps_better_draft() -> None:
    worse = "### Добавлено\n- Фаза 25: поиск"
    llm, _ = _llm([PARTIAL_BODY, worse])
    body, missing, _ = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == PARTIAL_BODY
    assert missing == ["26"]


async def test_changelog_unparseable_both_times() -> None:
    llm, client = _llm(["nonsense", "more nonsense"])
    body, missing, _ = await draft_changelog(llm, GROUPS, [], [])
    assert body is None
    assert missing == []
    assert len(client.calls) == 2


async def test_changelog_unparsed_first_draft_retry_gets_raw_text() -> None:
    llm, client = _llm(["x" * 6000, FULL_BODY])
    body, _, _ = await draft_changelog(llm, GROUPS, ["Day34"], [])
    assert body == FULL_BODY
    assistant = client.calls[1][-2]["content"]
    assert assistant == "x" * 4000


async def test_decision_valid_candidate() -> None:
    text = '{"version": "v4.1", "title": "Поиск", "rationale": "Малые правки."}'
    llm, _ = _llm([text])
    decision = await choose_version(llm, ["v5.0", "v4.1"], {"feat": 1}, FULL_BODY)
    assert decision.version == "v4.1"
    assert decision.title == "Поиск"


async def test_decision_invalid_version_rejected() -> None:
    llm, _ = _llm(['{"version": "v9.9", "title": "T", "rationale": "r"}'])
    decision = await choose_version(llm, ["v5.0", "v4.1"], {}, FULL_BODY)
    assert decision.version is None


async def test_decision_llm_failure() -> None:
    llm, _ = _llm([None])
    decision = await choose_version(llm, ["v5.0"], {}, FULL_BODY)
    assert (decision.version, decision.title, decision.rationale) == (None, None, None)


# --- build_plan ---------------------------------------------------------------

HEAD = "h" * 40
DECISION_OK = '{"version": "v4.1", "title": "Поиск и видео", "rationale": "Малые правки."}'
PLAN_BODY = "### Добавлено\n- Фаза 25: поиск\n- Фаза 26: видео\n- Day34 влит"
WRITE_PREFIXES = ("release_commit", "release_create", "release_push", "github_create")


def _ctx(**overrides: Any) -> str:
    data: dict[str, Any] = {
        "branch": "Day35",
        "head": HEAD,
        "origin_url": "https://github.com/acme/repo.git",
        "local_tags": ["v4.0", "backup/x"],
        "merged_tags": ["v4.0"],
        "remote_tags": ["v4.0"],
        "remote_error": None,
        "dirty": [],
    }
    data.update(overrides)
    return json.dumps(data)


def _commit(sha: str, subject: str, parents: int = 1, files_n: int = 2) -> dict[str, Any]:
    return {
        "sha": sha,
        "parents": parents,
        "subject": subject,
        "dirs": ["agent"],
        "planning_only": False,
        "files_n": files_n,
    }


def _page(rows: list[dict[str, Any]], next_page: int | None, total: int = 4) -> str:
    lines = [json.dumps(row) for row in rows]
    lines.append(json.dumps({"next_page": next_page, "total": total}))
    return "\n".join(lines)


PAGE1 = _page(
    [_commit("a" * 40, "feat(25-01): search"), _commit("b" * 40, "chore: bump")],
    2,
)
PAGE2 = _page(
    [
        _commit("c" * 40, "fix(26-02): video"),
        _commit("d" * 40, "Merge branch 'Day34'", parents=2),
    ],
    None,
)


class FakeCaller:
    """ToolCaller double: answers by (tool, page) and records every call."""

    def __init__(self, responses: dict[tuple[str, int | None], ToolOutcome]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, server: str, tool: str, args: dict[str, Any]) -> ToolOutcome:
        self.calls.append((server, tool, args))
        key = (tool, args.get("page"))
        if key not in self.responses:
            key = (tool, None)
        return self.responses[key]

    def tools(self) -> list[str]:
        return [tool for _, tool, _ in self.calls]


def _caller(context: str | None = None, **extra: ToolOutcome) -> FakeCaller:
    responses: dict[tuple[str, int | None], ToolOutcome] = {
        ("release_context", None): ToolOutcome(True, context or _ctx()),
        ("release_range_commits", 1): ToolOutcome(True, PAGE1),
        ("release_range_commits", 2): ToolOutcome(True, PAGE2),
    }
    for name, outcome in extra.items():
        responses[(name, None)] = outcome
    return FakeCaller(responses)


def _plan_llm(decision: str | None = DECISION_OK, body: str | None = PLAN_BODY) -> ReleaseLlm:
    return ReleaseLlm(FakeClient([body, decision]), "m", timeout=5.0)


async def _build(
    caller: FakeCaller,
    llm: ReleaseLlm | None = None,
    range_spec: str | None = None,
    has_github: bool = False,
):
    return await build_plan(
        caller,
        llm or _plan_llm(),
        chat_id=1,
        user_id=2,
        range_spec=range_spec,
        has_github=has_github,
    )


async def test_plan_reads_range_pages_and_never_writes() -> None:
    caller = _caller()
    build = await _build(caller)
    assert build.plan is not None
    pages = [a for _, t, a in caller.calls if t == "release_range_commits"]
    assert pages == [
        {"range": "v4.0..HEAD", "page": 1},
        {"range": "v4.0..HEAD", "page": 2},
    ]
    assert not any(t.startswith(WRITE_PREFIXES) for t in caller.tools())
    assert all(server == RELEASE_SERVER_NAME for server, _, _ in caller.calls)


async def test_plan_fields() -> None:
    build = await _build(_caller())
    plan = build.plan
    assert plan is not None
    assert plan.candidates == ["v5.0", "v4.1"]
    assert plan.version == "v4.1"
    assert plan.title == "Поиск и видео"
    assert plan.last_tag == "v4.0"
    assert plan.range_spec == "v4.0..HEAD"
    assert plan.owner_repo == "acme/repo"
    assert plan.head_sha == HEAD
    assert plan.branch == "Day35"
    assert plan.blocked_reason is None
    assert MSG_NOT_MAIN.format(branch="Day35") in plan.warnings
    assert all(state == StepState.PENDING for state in plan.steps.values())
    assert plan.counts == {
        "total": 4,
        "kept": 2,
        "dropped": 1,
        "feat": 1,
        "fix": 1,
        "day_merges": 1,
        "prs": 0,
        "shrunk": 0,
    }
    assert plan.changelog_body == PLAN_BODY
    assert plan.coverage_missing == []


async def test_plan_explicit_range_passes_through() -> None:
    caller = _caller()
    build = await _build(caller, range_spec="v3.0..HEAD")
    assert build.plan is not None
    assert build.plan.range_spec == "v3.0..HEAD"
    assert caller.calls[1][2]["range"] == "v3.0..HEAD"


async def test_plan_no_version_tag_notice() -> None:
    build = await _build(_caller(_ctx(merged_tags=[], local_tags=[])))
    assert build.plan is None
    assert build.notice == MSG_NO_VERSION_TAG


async def test_plan_range_without_tags_notice() -> None:
    build = await _build(_caller(_ctx(merged_tags=[], local_tags=[])), range_spec="a..b")
    assert build.notice == MSG_NO_VERSION_TAG


async def test_plan_context_failure_notice() -> None:
    caller = _caller(release_context=ToolOutcome(False, "git exploded\nsecond line"))
    build = await _build(caller)
    assert build.plan is None
    assert build.notice == MSG_READ_FAILED.format(reason="git exploded")


async def test_plan_context_parse_failure_notice() -> None:
    caller = _caller(release_context=ToolOutcome(True, "not json"))
    build = await _build(caller)
    assert build.plan is None
    assert build.notice is not None and build.notice.startswith("Не удалось прочитать")


async def test_plan_commit_page_failure_notice() -> None:
    caller = _caller()
    caller.responses[("release_range_commits", 2)] = ToolOutcome(False, "bad range")
    build = await _build(caller)
    assert build.plan is None
    assert build.notice == MSG_READ_FAILED.format(reason="bad range")


async def test_plan_nothing_to_release_notice() -> None:
    caller = _caller()
    caller.responses[("release_range_commits", 1)] = ToolOutcome(
        True, _page([_commit("a" * 40, "chore: bump")], None, 1)
    )
    build = await _build(caller)
    assert build.plan is None
    assert build.notice is not None
    assert "v4.0..HEAD" in build.notice
    assert "отфильтровано 1" in build.notice


async def test_plan_dirty_tree_blocks() -> None:
    caller = _caller(_ctx(dirty=["?? notes.txt", " M agent/ws.py"]))
    build = await _build(caller)
    assert build.plan is not None
    reason = build.plan.blocked_reason
    assert reason is not None
    assert reason.startswith(MSG_DIRTY_TREE)
    assert "notes.txt" in reason and "agent/ws.py" in reason


async def test_plan_taken_remote_tag_excluded() -> None:
    build = await _build(_caller(_ctx(remote_tags=["v4.0", "v5.0"])))
    assert build.plan is not None
    assert build.plan.candidates == ["v4.1"]
    assert "v5.0" in build.plan.taken


async def test_plan_remote_tags_unknown_warning() -> None:
    build = await _build(_caller(_ctx(remote_tags=None, remote_error="offline")))
    assert build.plan is not None
    assert MSG_REMOTE_TAGS_UNKNOWN in build.plan.warnings


async def test_plan_all_candidates_taken_blocks() -> None:
    build = await _build(_caller(_ctx(remote_tags=["v4.0", "v5.0", "v4.1"])))
    assert build.plan is not None
    assert build.plan.candidates == []
    assert build.plan.version == ""
    assert build.plan.blocked_reason is not None
    assert MSG_VERSION_TAKEN.format(version="v5.0") in build.plan.blocked_reason


async def test_plan_invalid_version_falls_back_to_default() -> None:
    decision = '{"version": "v9.9", "title": "T", "rationale": "r"}'
    build = await _build(_caller(), _plan_llm(decision))
    assert build.plan is not None
    assert build.plan.version == "v5.0"
    assert MSG_VERSION_FALLBACK in build.plan.warnings


async def test_plan_missing_title_uses_fallback_title() -> None:
    build = await _build(_caller(), _plan_llm('{"version": "v4.1"}'))
    assert build.plan is not None
    assert build.plan.title == "Релиз v4.1"
    assert build.plan.rationale


async def test_plan_changelog_failure_notice() -> None:
    llm = ReleaseLlm(FakeClient([None, None]), "m", timeout=5.0)
    build = await _build(_caller(), llm)
    assert build.plan is None
    assert build.notice == MSG_LLM_FAILED


def _prs(items: list[dict[str, Any]]) -> ToolOutcome:
    return ToolOutcome(True, json.dumps(items))


async def test_plan_github_prs_added_and_deduplicated() -> None:
    prs = [
        {"number": 1, "title": "Add cache", "merged_at": "2026-01-01", "merge_commit_sha": "a" * 40},
        {"number": 2, "title": "Not merged", "merged_at": None, "merge_commit_sha": "c" * 40},
        {"number": 3, "title": "Outside", "merged_at": "2026-01-01", "merge_commit_sha": "e" * 40},
        {"number": 4, "title": "Day merge", "merged_at": "2026-01-01", "merge_commit_sha": "d" * 40},
    ]
    caller = _caller(list_pull_requests=_prs(prs))
    build = await _build(caller, has_github=True)
    assert build.plan is not None
    assert build.plan.counts["prs"] == 1
    github = [c for c in caller.calls if c[1] == "list_pull_requests"]
    assert github == [
        (
            GITHUB_SERVER_NAME,
            "list_pull_requests",
            {"owner": "acme", "repo": "repo", "state": "closed", "perPage": 100},
        )
    ]


async def test_plan_github_failure_adds_warning() -> None:
    caller = _caller(list_pull_requests=ToolOutcome(False, "no access"))
    build = await _build(caller, has_github=True)
    assert build.plan is not None
    assert MSG_GITHUB_PRS_SKIPPED in build.plan.warnings


async def test_plan_github_unexpected_shape_adds_warning() -> None:
    caller = _caller(list_pull_requests=ToolOutcome(True, '{"oops": 1}'))
    build = await _build(caller, has_github=True)
    assert build.plan is not None
    assert MSG_GITHUB_PRS_SKIPPED in build.plan.warnings


async def test_plan_without_github_makes_no_github_call() -> None:
    caller = _caller()
    await _build(caller, has_github=False)
    assert "list_pull_requests" not in caller.tools()
