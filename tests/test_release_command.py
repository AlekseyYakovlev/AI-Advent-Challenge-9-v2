"""Tests for the /release grammar, plan types and WebSocket frames."""

import pytest

from agent import release_command as rc
from agent.release_command import (
    ReleaseCommand,
    ReleasePlan,
    StepState,
    is_release_command,
    parse_release,
    parse_release_frame,
    plan_frame,
    progress_frame,
    result_frame,
    step_label,
)


def _plan() -> ReleasePlan:
    return ReleasePlan(
        plan_id="p1", chat_id=1, user_id=2, range_spec="v4.0..HEAD", branch="main",
        head_sha="0123456789abcdef", last_tag="v4.0", candidates=["v5.0", "v4.1"],
        taken=[], version="v5.0", title="Pipeline", rationale="Why.",
        changelog_body="### Добавлено\n- x", counts={"kept": 3, "dropped": 1},
        warnings=[], coverage_missing=[], owner_repo="o/r",
    )


def test_parse_plain_release() -> None:
    assert parse_release("/release") == ReleaseCommand(kind="plan")
    assert parse_release("  /RELEASE  ") == ReleaseCommand(kind="plan")


def test_parse_range() -> None:
    cmd = parse_release("/release v4.0..HEAD")
    assert cmd is not None and cmd.kind == "plan" and cmd.range_spec == "v4.0..HEAD"


@pytest.mark.parametrize(
    "text", ["/release v4.0...HEAD", "/release a b", "/release -x..HEAD", "/release foo"]
)
def test_parse_usage(text: str) -> None:
    cmd = parse_release(text)
    assert cmd is not None and cmd.kind == "usage"


def test_parse_confirm_plain() -> None:
    cmd = parse_release("/release confirm")
    assert cmd == ReleaseCommand(kind="confirm")


def test_parse_confirm_version() -> None:
    cmd = parse_release("/release confirm v5.0")
    assert cmd is not None and cmd.version == "v5.0" and cmd.title is None


def test_parse_confirm_version_and_title() -> None:
    cmd = parse_release("/release confirm v5.0 Local LLM release")
    assert cmd is not None
    assert cmd.version == "v5.0" and cmd.title == "Local LLM release"


def test_parse_confirm_title_only() -> None:
    cmd = parse_release("/release confirm Новое имя")
    assert cmd is not None and cmd.version is None and cmd.title == "Новое имя"


@pytest.mark.parametrize("text", ["/releases", "hello /release", "/help"])
def test_parse_non_release(text: str) -> None:
    assert parse_release(text) is None


def test_parse_frame() -> None:
    cmd = parse_release_frame(
        {"type": "release_confirm", "plan_id": "abc", "version": "v5.0", "title": "T"}
    )
    assert cmd == ReleaseCommand(kind="confirm", version="v5.0", title="T", plan_id="abc")


def test_parse_frame_bad_fields_and_types() -> None:
    cmd = parse_release_frame({"type": "release_confirm", "plan_id": 5, "version": None})
    assert cmd == ReleaseCommand(kind="confirm")
    assert parse_release_frame(["x"]) is None
    assert parse_release_frame({"type": "other"}) is None


def test_is_release_command() -> None:
    assert is_release_command({"content": "/release"})
    assert is_release_command({"type": "release_confirm"})
    assert not is_release_command({"content": "/help"})
    assert not is_release_command([])
    assert not is_release_command({"content": 5})


def test_new_plan_defaults() -> None:
    plan = _plan()
    assert set(plan.steps) == set(rc.STEP_KEYS)
    assert all(s is StepState.PENDING for s in plan.steps.values())
    assert plan.stale is False and plan.running is False
    assert len(rc.new_plan_id()) == 32


def test_plan_frame() -> None:
    frame = plan_frame(_plan(), "v5.0 Week 7: Pipeline")
    assert frame["type"] == "release_plan" and frame["stored"] is False
    assert frame["head_sha"] == "01234567"
    assert frame["tag_message"] == "v5.0 Week 7: Pipeline"
    assert frame["blocked"] is None
    for key in ("plan_id", "range", "branch", "last_tag", "candidates", "taken", "version",
                "title", "rationale", "changelog", "counts", "warnings", "coverage_missing"):
        assert key in frame
    assert frame["steps"][1] == {
        "key": "tag", "label": "Аннотированный тег v5.0", "status": "pending",
    }


def test_progress_frame() -> None:
    frame = progress_frame(_plan(), "tag", StepState.RUNNING, "detail")
    assert frame == {
        "type": "release_progress", "plan_id": "p1", "step": "tag",
        "status": "running", "detail": "detail",
    }


def test_result_frame_locked_and_sha() -> None:
    plan = _plan()
    statuses = dict(plan.steps)
    frame = result_frame(plan, "failed", statuses, "r", "h", "text", "tm")
    assert frame["locked"] is False and frame["commit_sha"] is None
    plan.steps["commit"] = StepState.DONE
    plan.commit_sha = "abcdef0123456789"
    frame = result_frame(plan, "partial", statuses, None, None, "text", "tm")
    assert frame["locked"] is True and frame["commit_sha"] == "abcdef01"
    assert frame["tag"] == "v5.0" and frame["release_text"] == "text"
    assert frame["type"] == "release_result" and frame["stored"] is False


def test_step_labels() -> None:
    assert step_label("commit", "v5.0") == "Коммит CHANGELOG.md"
    assert step_label("push", "v5.0") == "Push ветки и тега"
    assert step_label("release", "v5.0") == "Черновик GitHub Release"


def test_copy_strings() -> None:
    assert rc.MSG_VERSION_TAKEN.format(version="v4.0") == (
        "Версия v4.0 уже существует как тег. Выберите другую."
    )
    assert rc.MSG_VERSION_NOT_GREATER.format(last="v4.0") == (
        "Версия должна быть больше последнего тега (v4.0)."
    )
    assert rc.MSG_VERSION_FORMAT == "Формат версии: vX.Y, например v4.1."
    assert rc.MSG_NO_PLAN == "Нет плана релиза. Сначала введите /release."
    assert rc.MSG_NOT_MAIN.format(branch="Day35") == "Релиз готовится из ветки Day35, а не main."
