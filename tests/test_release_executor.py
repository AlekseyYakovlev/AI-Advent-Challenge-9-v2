"""Tests for the confirm phase of /release with a scripted tool caller."""

import json
from typing import Any

import pytest

from agent.release_command import (
    MSG_CONFIRM_RUNNING,
    MSG_DIRTY_TREE,
    MSG_HEAD_CHANGED,
    MSG_LOCKED_AFTER_COMMIT,
    MSG_MANUAL_RELEASE_HINT,
    MSG_VERSION_FORMAT,
    MSG_VERSION_NOT_GREATER,
    MSG_VERSION_TAKEN,
    MSG_WRITE_ACCESS_MISSING,
    ReleasePlan,
    StepState,
    ToolOutcome,
)
from agent.release_executor import confirm_plan

HEAD = "a" * 40
COMMIT = "c" * 40
TODAY = "2026-10-17"
URL = "https://github.com/acme/app/releases/tag/untagged-abc"
TOKEN_ERR = "GITHUB_PERSONAL_ACCESS_TOKEN is not set; cannot create a release"


def make_plan(**overrides: Any) -> ReleasePlan:
    values: dict[str, Any] = {
        "plan_id": "p1",
        "chat_id": 1,
        "user_id": 1,
        "range_spec": "v4.0..HEAD",
        "branch": "Day35",
        "head_sha": HEAD,
        "last_tag": "v4.0",
        "candidates": ["v5.0", "v4.1"],
        "taken": ["v4.0"],
        "version": "v5.0",
        "title": "Release pipeline",
        "rationale": "r",
        "changelog_body": "### Added\n- thing",
        "counts": {},
        "warnings": [],
        "coverage_missing": [],
        "owner_repo": "acme/app",
    }
    values.update(overrides)
    return ReleasePlan(**values)


def context_text(
    head: str = HEAD,
    dirty: list[str] | None = None,
    local_tags: list[str] | None = None,
    remote_tags: list[str] | None = None,
) -> str:
    data = {
        "branch": "Day35",
        "head": head,
        "origin_url": "git@github.com:acme/app.git",
        "local_tags": local_tags if local_tags is not None else ["v4.0"],
        "merged_tags": ["v4.0"],
        "remote_tags": remote_tags if remote_tags is not None else ["v4.0"],
        "dirty": dirty or [],
    }
    return "Release context for Day35\n" + json.dumps(data)


class FakeCaller:
    """Scripted tool caller: per tool a list of outcomes (the last one repeats)."""

    def __init__(self, script: dict[str, list[ToolOutcome]] | None = None) -> None:
        defaults: dict[str, list[ToolOutcome]] = {
            "release_context": [ToolOutcome(True, context_text())],
            "release_commit_changelog": [
                ToolOutcome(True, "Committed\n" + json.dumps({"commit": COMMIT}))
            ],
            "release_create_tag": [ToolOutcome(True, "Tagged\n" + json.dumps({"tag": "v5.0"}))],
            "release_push": [ToolOutcome(True, "Pushed\n" + json.dumps({"pushed": True}))],
            "github_create_draft_release": [
                ToolOutcome(True, "Created\n" + json.dumps({"html_url": URL}))
            ],
        }
        defaults.update(script or {})
        self.script = defaults
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, server: str, tool: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((server, tool, arguments))
        seen = sum(1 for _, name, _ in self.calls if name == tool)
        outcomes = self.script[tool]
        return outcomes[min(seen, len(outcomes)) - 1]

    @property
    def tools(self) -> list[str]:
        return [tool for _, tool, _ in self.calls]


class Sink:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def __call__(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    def result(self) -> dict[str, Any]:
        return [f for f in self.frames if f["type"] == "release_result"][-1]

    def progress(self) -> list[tuple[str, str]]:
        return [
            (f["step"], f["status"]) for f in self.frames if f["type"] == "release_progress"
        ]


def statuses(frame: dict[str, Any]) -> dict[str, str]:
    return {row["key"]: row["status"] for row in frame["steps"]}


async def run(
    plan: ReleasePlan,
    call: FakeCaller,
    sink: Sink,
    version: str | None = None,
    title: str | None = None,
) -> str:
    return await confirm_plan(plan, call, sink, version=version, title=title, today_iso=TODAY)


SECTION = "## [v5.0] - 2026-10-17\n\n### Added\n- thing\n"
TAG_MSG = "v5.0 Week 7: Release pipeline"


async def test_happy_path_writes_in_order() -> None:
    plan, call, sink = make_plan(), FakeCaller(), Sink()
    assert await run(plan, call, sink) == "done"
    assert call.tools == [
        "release_context",
        "release_commit_changelog",
        "release_create_tag",
        "release_push",
        "github_create_draft_release",
    ]
    args = {tool: arguments for _, tool, arguments in call.calls}
    assert args["release_commit_changelog"] == {"version": "v5.0", "section": SECTION}
    assert args["release_create_tag"] == {"version": "v5.0", "message": TAG_MSG, "commit": COMMIT}
    assert args["release_push"] == {"branch": "Day35", "tag": "v5.0"}
    assert args["github_create_draft_release"] == {
        "owner": "acme",
        "repo": "app",
        "tag": "v5.0",
        "name": TAG_MSG,
        "body": SECTION,
        "commit": COMMIT,
    }
    assert all(state == StepState.DONE for state in plan.steps.values())
    assert plan.release_url == URL
    assert plan.commit_sha == COMMIT
    assert sink.progress() == [
        ("commit", "running"), ("commit", "done"),
        ("tag", "running"), ("tag", "done"),
        ("push", "running"), ("push", "done"),
        ("release", "running"), ("release", "done"),
    ]  # fmt: skip
    result = sink.result()
    assert result["outcome"] == "done"
    assert result["release_url"] == URL
    assert plan.running is False


async def test_missing_token_makes_release_unavailable_and_partial() -> None:
    plan, sink = make_plan(), Sink()
    call = FakeCaller({"github_create_draft_release": [ToolOutcome(False, TOKEN_ERR)]})
    assert await run(plan, call, sink) == "partial"
    assert plan.steps["release"] == StepState.UNAVAILABLE
    assert plan.steps["push"] == StepState.DONE
    result = sink.result()
    assert result["outcome"] == "partial"
    assert result["hint"] == MSG_WRITE_ACCESS_MISSING
    assert result["release_text"] == SECTION
    assert statuses(result)["release"] == "unavailable"
    assert not [t for t in call.tools if "delete" in t or "rollback" in t]


async def test_retry_after_partial_skips_done_steps() -> None:
    plan, sink = make_plan(), Sink()
    first = FakeCaller({"github_create_draft_release": [ToolOutcome(False, TOKEN_ERR)]})
    assert await run(plan, first, sink) == "partial"
    second, sink2 = FakeCaller({"release_context": [ToolOutcome(True, context_text(COMMIT))]}), Sink()
    assert await run(plan, second, sink2) == "done"
    assert second.tools == ["release_context", "github_create_draft_release"]
    assert statuses(sink2.result()) == {
        "commit": "skipped", "tag": "skipped", "push": "skipped", "release": "done",
    }  # fmt: skip


async def test_overrides_after_commit_are_ignored() -> None:
    plan, sink = make_plan(), Sink()
    first = FakeCaller({"github_create_draft_release": [ToolOutcome(False, TOKEN_ERR)]})
    await run(plan, first, sink)
    second, sink2 = FakeCaller({"release_context": [ToolOutcome(True, context_text(COMMIT))]}), Sink()
    await run(plan, second, sink2, version="v4.1", title="Another")
    assert "release_commit_changelog" not in second.tools
    args = second.calls[-1][2]
    assert args["tag"] == "v5.0"
    assert args["name"] == TAG_MSG
    assert plan.version == "v5.0"
    assert sink2.result()["hint"] and MSG_LOCKED_AFTER_COMMIT in sink2.result()["hint"]


async def test_dirty_tree_blocks_without_writes() -> None:
    plan, sink = make_plan(), Sink()
    call = FakeCaller({"release_context": [ToolOutcome(True, context_text(dirty=["?? x.txt"]))]})
    assert await run(plan, call, sink) == "blocked"
    assert call.tools == ["release_context"]
    reason = sink.result()["reason"]
    assert reason.startswith(MSG_DIRTY_TREE)
    assert "x.txt" in reason


async def test_head_changed_blocks_before_commit() -> None:
    plan, sink = make_plan(), Sink()
    call = FakeCaller({"release_context": [ToolOutcome(True, context_text(head="b" * 40))]})
    assert await run(plan, call, sink) == "blocked"
    assert call.tools == ["release_context"]
    assert sink.result()["reason"].startswith(MSG_HEAD_CHANGED)


async def test_head_after_commit_is_compared_with_commit_sha() -> None:
    plan, sink = make_plan(), Sink()
    first = FakeCaller({"release_push": [ToolOutcome(False, "push rejected")]})
    assert await run(plan, first, sink) == "failed"
    stale = FakeCaller({"release_context": [ToolOutcome(True, context_text(head=HEAD))]})
    assert await run(plan, stale, Sink()) == "blocked"
    assert stale.tools == ["release_context"]


@pytest.mark.parametrize(
    ("version", "remote", "expected"),
    [
        ("v4.0", ["v4.0"], MSG_VERSION_NOT_GREATER.format(last="v4.0")),
        ("v5.0", ["v4.0", "v5.0"], MSG_VERSION_TAKEN.format(version="v5.0")),
        ("5.0", ["v4.0"], MSG_VERSION_FORMAT),
    ],
)
async def test_version_validation_blocks(version: str, remote: list[str], expected: str) -> None:
    plan, sink = make_plan(), Sink()
    call = FakeCaller(
        {"release_context": [ToolOutcome(True, context_text(remote_tags=remote))]}
    )
    assert await run(plan, call, sink, version=version) == "blocked"
    assert call.tools == ["release_context"]
    assert sink.result()["reason"].startswith(expected)


async def test_plan_time_block_is_rechecked_not_trusted() -> None:
    plan = make_plan(blocked_reason="Версия v5.0 уже существует как тег. Выберите другую.")
    call, sink = FakeCaller(), Sink()
    assert await run(plan, call, sink) == "done"


async def test_commit_failure_stops_the_chain() -> None:
    plan, sink = make_plan(), Sink()
    call = FakeCaller(
        {"release_commit_changelog": [ToolOutcome(False, "nothing to commit\nmore detail")]}
    )
    assert await run(plan, call, sink) == "failed"
    assert sink.result()["reason"] == "nothing to commit"
    assert plan.steps["commit"] == StepState.FAILED
    for key in ("tag", "push", "release"):
        assert plan.steps[key] == StepState.PENDING
    assert "release_create_tag" not in call.tools


async def test_push_failure_then_retry_resumes_at_push() -> None:
    plan, sink = make_plan(), Sink()
    first = FakeCaller({"release_push": [ToolOutcome(False, "rejected")]})
    assert await run(plan, first, sink) == "failed"
    assert plan.steps["tag"] == StepState.DONE
    second = FakeCaller({"release_context": [ToolOutcome(True, context_text(COMMIT))]})
    assert await run(plan, second, Sink()) == "done"
    assert second.tools == [
        "release_context", "release_push", "github_create_draft_release",
    ]  # fmt: skip


async def test_non_github_origin_makes_release_unavailable() -> None:
    plan, sink = make_plan(owner_repo=None), Sink()
    call = FakeCaller()
    assert await run(plan, call, sink) == "partial"
    assert plan.steps["release"] == StepState.UNAVAILABLE
    assert "github_create_draft_release" not in call.tools
    assert sink.result()["reason"] == "origin is not a GitHub repository"


async def test_other_release_failure_gives_manual_hint() -> None:
    plan, sink = make_plan(), Sink()
    call = FakeCaller({"github_create_draft_release": [ToolOutcome(False, "boom (500)")]})
    assert await run(plan, call, sink) == "partial"
    assert plan.steps["release"] == StepState.FAILED
    assert sink.result()["hint"] == MSG_MANUAL_RELEASE_HINT.format(tag="v5.0")


async def test_running_plan_is_refused() -> None:
    plan, call, sink = make_plan(running=True), FakeCaller(), Sink()
    assert await run(plan, call, sink) == "running"
    assert call.calls == []
    assert sink.frames[0]["type"] == "help_notice"
    assert sink.frames[0]["text"] == MSG_CONFIRM_RUNNING


async def test_running_flag_reset_after_exception() -> None:
    class Boom(FakeCaller):
        async def __call__(self, server: str, tool: str, arguments: dict[str, Any]) -> ToolOutcome:
            raise RuntimeError("x")

    plan = make_plan()
    with pytest.raises(RuntimeError):
        await run(plan, Boom(), Sink())
    assert plan.running is False


async def test_title_override_is_sanitised_and_empty_keeps_plan_title() -> None:
    plan, call, sink = make_plan(), FakeCaller(), Sink()
    await run(plan, call, sink, title="  New   title  ")
    assert plan.title == "New title"
    plan2, call2 = make_plan(), FakeCaller()
    await run(plan2, call2, Sink(), title="\x00\x01")
    assert plan2.title == "Release pipeline"
