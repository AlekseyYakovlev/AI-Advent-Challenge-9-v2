"""Confirm phase of /release: re-validate, then write in order with resumable steps."""

from typing import Any, Literal

from agent.command_turn import notice_frame
from agent.release_command import (
    MSG_CONFIRM_RUNNING,
    MSG_DIRTY_TREE,
    MSG_HEAD_CHANGED,
    MSG_LOCKED_AFTER_COMMIT,
    MSG_MANUAL_RELEASE_HINT,
    MSG_WRITE_ACCESS_MISSING,
    STEP_KEYS,
    FrameSender,
    ReleasePlan,
    StepState,
    ToolCaller,
    ToolOutcome,
    progress_frame,
    result_frame,
)
from agent.release_pipeline import (
    ReleaseParseError,
    changelog_section,
    dirty_paths,
    is_release_link,
    parse_context,
    parse_json_tail,
    sanitize_title,
    tag_message,
    validate_version,
)
from shared.logger import get_logger

logger = get_logger(__name__)

UNAVAILABLE_MARKERS: tuple[str, ...] = (
    "GITHUB_PERSONAL_ACCESS_TOKEN is not set",
    "(401)",
    "(403)",
    "(404)",
    "not a GitHub repository",
)
DETAIL_MAX_CHARS: int = 500
NOT_GITHUB_REASON = "origin is not a GitHub repository"
DETACHED_REASON = "detached HEAD cannot be pushed"
NO_LINK_REASON = "the answer has no release link"
BAD_ANSWER_REASON = "unexpected answer of the server"

_TOOLS: dict[str, tuple[str, str]] = {
    "commit": ("release", "release_commit_changelog"),
    "tag": ("release", "release_create_tag"),
    "push": ("release", "release_push"),
    "release": ("release", "github_create_draft_release"),
}

Outcome = Literal["done", "partial", "failed", "blocked"]


def _first_line(text: str) -> str:
    """Return the first non-empty line of a tool answer, cut for display."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:DETAIL_MAX_CHARS]
    return ""


def _apply_overrides(plan: ReleasePlan, version: str | None, title: str | None) -> str | None:
    """Apply a version/title override while nothing is committed; return a lock hint otherwise."""
    new_version = version.strip() if version and version.strip() else None
    new_title = sanitize_title(title) if title else None
    if plan.steps["commit"] == StepState.DONE:
        differs = (new_version and new_version != plan.version) or (
            new_title and new_title != plan.title
        )
        return MSG_LOCKED_AFTER_COMMIT if differs else None
    if new_version:
        plan.version = new_version
    if new_title:
        plan.title = new_title
    return None


async def _revalidate(plan: ReleasePlan, call: ToolCaller) -> str | None:
    """Re-check the repository before any write; return a blocking reason or None."""
    outcome = await call("release", "release_context", {})
    if not outcome.ok:
        return _first_line(outcome.text) or BAD_ANSWER_REASON
    try:
        context = parse_context(outcome.text)
    except ReleaseParseError:
        return BAD_ANSWER_REASON
    paths = dirty_paths(context.dirty)
    if paths:
        return MSG_DIRTY_TREE + "\n" + "\n".join(paths)
    committed = plan.steps["commit"] == StepState.DONE
    expected = plan.commit_sha if committed else plan.head_sha
    if context.head != expected:
        return MSG_HEAD_CHANGED
    if not committed:
        taken = set(context.local_tags) | set(context.remote_tags or [])
        return validate_version(plan.version, plan.last_tag, taken)
    return None


def _step_args(plan: ReleasePlan, key: str, message: str) -> dict[str, Any]:
    """Build the tool arguments of one write step."""
    if key == "commit":
        return {"version": plan.version, "section": plan.release_text}
    if key == "tag":
        return {"version": plan.version, "message": message, "commit": plan.commit_sha}
    if key == "push":
        return {"branch": plan.branch, "tag": plan.version}
    owner, _, repo = (plan.owner_repo or "").partition("/")
    return {
        "owner": owner,
        "repo": repo,
        "tag": plan.version,
        "name": message,
        "body": plan.release_text,
        "commit": plan.commit_sha,
    }


def _read_result(plan: ReleasePlan, key: str, outcome: ToolOutcome) -> str | None:
    """Store what a successful step reports; return a failure detail when the answer is unusable."""
    if key not in ("commit", "release"):
        return None
    try:
        data = parse_json_tail(outcome.text)
    except ReleaseParseError:
        return BAD_ANSWER_REASON
    if key == "commit":
        sha = data.get("commit")
        if not isinstance(sha, str) or not sha:
            return BAD_ANSWER_REASON
        plan.commit_sha = sha
        return None
    url = data.get("html_url")
    if not isinstance(url, str) or not is_release_link(url):
        return NO_LINK_REASON
    plan.release_url = url
    return None


async def _run_step(
    plan: ReleasePlan, key: str, call: ToolCaller, message: str
) -> tuple[StepState, str | None]:
    """Run one write step and return its state and failure detail."""
    if key == "push" and plan.branch is None:
        return StepState.FAILED, DETACHED_REASON
    if key == "release" and plan.owner_repo is None:
        return StepState.UNAVAILABLE, NOT_GITHUB_REASON
    server, tool = _TOOLS[key]
    outcome = await call(server, tool, _step_args(plan, key, message))
    if not outcome.ok:
        detail = _first_line(outcome.text)
        unavailable = key == "release" and any(m in outcome.text for m in UNAVAILABLE_MARKERS)
        return (StepState.UNAVAILABLE if unavailable else StepState.FAILED), detail
    detail = _read_result(plan, key, outcome)
    if detail is not None:
        return StepState.FAILED, detail
    return StepState.DONE, None


async def _write_steps(
    plan: ReleasePlan, call: ToolCaller, send: FrameSender, message: str
) -> tuple[dict[str, StepState], str | None, str | None]:
    """Run the steps in order, skipping done ones; stop at the first failure."""
    statuses = dict(plan.steps)
    for key in STEP_KEYS:
        if plan.steps[key] == StepState.DONE:
            statuses[key] = StepState.SKIPPED
            await send(progress_frame(plan, key, StepState.SKIPPED))
            continue
        await send(progress_frame(plan, key, StepState.RUNNING))
        state, detail = await _run_step(plan, key, call, message)
        plan.steps[key] = state
        statuses[key] = state
        await send(progress_frame(plan, key, state, detail))
        if state != StepState.DONE:
            return statuses, key, detail
    return statuses, None, None


async def _finish(
    plan: ReleasePlan,
    send: FrameSender,
    statuses: dict[str, StepState],
    failed_key: str | None,
    detail: str | None,
    lock_hint: str | None,
    message: str,
) -> Outcome:
    """Pick the outcome and hint, then send the final result frame."""
    hint: str | None = None
    outcome: Outcome = "done"
    if failed_key == "release":
        outcome = "partial"
        if statuses["release"] == StepState.UNAVAILABLE:
            hint = MSG_WRITE_ACCESS_MISSING
        else:
            hint = MSG_MANUAL_RELEASE_HINT.format(tag=plan.version)
    elif failed_key is not None:
        outcome = "failed"
    if lock_hint:
        hint = f"{hint} {lock_hint}" if hint else lock_hint
    await send(
        result_frame(plan, outcome, statuses, detail, hint, plan.release_text or "", message)
    )
    logger.info(
        "release_confirm_finished",
        chat_id=plan.chat_id,
        outcome=outcome,
        steps={key: state.value for key, state in statuses.items()},
    )
    return outcome


async def _confirm(
    plan: ReleasePlan,
    call: ToolCaller,
    send: FrameSender,
    version: str | None,
    title: str | None,
    today_iso: str,
) -> Outcome:
    lock_hint = _apply_overrides(plan, version, title)
    reason = await _revalidate(plan, call)
    if reason is not None:
        message = tag_message(plan.version, plan.title)
        await send(
            result_frame(
                plan, "blocked", dict(plan.steps), reason, lock_hint, plan.release_text or "", message
            )
        )
        logger.info("release_confirm_finished", chat_id=plan.chat_id, outcome="blocked")
        return "blocked"
    if plan.steps["commit"] != StepState.DONE:
        plan.release_text = changelog_section(plan.version, today_iso, plan.changelog_body)
    message = tag_message(plan.version, plan.title)
    statuses, failed_key, detail = await _write_steps(plan, call, send, message)
    return await _finish(plan, send, statuses, failed_key, detail, lock_hint, message)


async def confirm_plan(
    plan: ReleasePlan,
    call: ToolCaller,
    send: FrameSender,
    *,
    version: str | None,
    title: str | None,
    today_iso: str,
) -> str:
    """Confirm a plan: returns "done", "partial", "failed", "blocked" or "running"."""
    if plan.running:
        await send(notice_frame("release", MSG_CONFIRM_RUNNING))
        return "running"
    plan.running = True
    try:
        return await _confirm(plan, call, send, version, title, today_iso)
    finally:
        plan.running = False
