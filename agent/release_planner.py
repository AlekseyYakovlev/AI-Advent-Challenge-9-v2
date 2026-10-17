"""Plan phase of /release: reads through MCP, filters commits, asks the model; never writes."""

import json
from dataclasses import dataclass
from typing import Any

from agent.release_command import (
    GITHUB_SERVER_NAME,
    MSG_DIRTY_TREE,
    MSG_GITHUB_PRS_SKIPPED,
    MSG_LLM_FAILED,
    MSG_NOT_MAIN,
    MSG_NOTHING_TO_RELEASE,
    MSG_REMOTE_TAGS_UNKNOWN,
    MSG_VERSION_FALLBACK,
    MSG_VERSION_TAKEN,
    RELEASE_SERVER_NAME,
    ReleasePlan,
    ToolCaller,
    new_plan_id,
)
from agent.release_llm import ReleaseLlm, choose_version, draft_changelog
from agent.release_pipeline import (
    CommitInfo,
    ReleaseContext,
    ReleaseParseError,
    collect_signals,
    default_version,
    dirty_paths,
    filter_commits,
    group_by_phase,
    last_version_tag,
    parse_commit_page,
    parse_context,
    parse_owner_repo,
    version_candidates,
)
from shared.logger import get_logger

logger = get_logger(__name__)

MSG_NO_VERSION_TAG = (
    "В репозитории нет тега вида vX.Y, достижимого из HEAD. "
    "Укажите диапазон явно: /release v4.0..HEAD"
)
MSG_READ_FAILED = "Не удалось прочитать репозиторий: {reason}"

MAX_COMMIT_PAGES = 40
PR_TITLE_MAX_CHARS = 200
DIRTY_PATHS_SHOWN = 30


@dataclass(frozen=True)
class PlanBuild:
    """Outcome of the plan phase: a plan, or a notice explaining why there is none."""

    plan: ReleasePlan | None
    notice: str | None


def _read_failed(text: str) -> PlanBuild:
    """Build the notice for a failed or unparseable read."""
    lines = text.strip().splitlines()
    reason = lines[0] if lines else "empty result"
    return PlanBuild(None, MSG_READ_FAILED.format(reason=reason))


async def _read_context(call: ToolCaller) -> ReleaseContext | PlanBuild:
    """Read and parse the repository context."""
    outcome = await call(RELEASE_SERVER_NAME, "release_context", {})
    if not outcome.ok:
        return _read_failed(outcome.text)
    try:
        return parse_context(outcome.text)
    except ReleaseParseError as exc:
        return _read_failed(str(exc))


async def _read_commits(call: ToolCaller, range_spec: str) -> list[CommitInfo] | PlanBuild:
    """Read every page of the release range."""
    commits: list[CommitInfo] = []
    page: int | None = 1
    while page is not None and page <= MAX_COMMIT_PAGES:
        outcome = await call(
            RELEASE_SERVER_NAME, "release_range_commits", {"range": range_spec, "page": page}
        )
        if not outcome.ok:
            return _read_failed(outcome.text)
        try:
            rows, page = parse_commit_page(outcome.text)
        except ReleaseParseError as exc:
            return _read_failed(str(exc))
        commits.extend(rows)
    return commits


def _pr_titles(items: list[Any], range_shas: set[str], skip_shas: set[str]) -> list[str]:
    """Return titles of merged PRs whose merge commit is in the range and not a DayNN merge."""
    titles: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sha = item.get("merge_commit_sha")
        title = item.get("title")
        if not item.get("merged_at") or not isinstance(sha, str) or not isinstance(title, str):
            continue
        if sha in range_shas and sha not in skip_shas:
            titles.append(title.strip()[:PR_TITLE_MAX_CHARS])
    return titles


async def _read_prs(
    call: ToolCaller, owner_repo: str, range_shas: set[str], skip_shas: set[str]
) -> list[str] | None:
    """Read merged PR titles from GitHub; None when the source is unavailable."""
    owner, _, repo = owner_repo.partition("/")
    outcome = await call(
        GITHUB_SERVER_NAME,
        "list_pull_requests",
        {"owner": owner, "repo": repo, "state": "closed", "perPage": 100},
    )
    if not outcome.ok:
        return None
    try:
        data = json.loads(outcome.text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return _pr_titles(data, range_shas, skip_shas)


def _previous_tag(tags: list[str], last_tag: str) -> str:
    """Return the version tag before last_tag, or last_tag when there is none."""
    older = [tag for tag in tags if tag != last_tag]
    return last_version_tag(older) or last_tag


def _code_rationale(version: str, signals: dict[str, Any]) -> str:
    """Build a one-sentence Russian rationale from the signals."""
    return (
        f"Версия {version} выбрана по правилу: новых функций {signals['feat']}, "
        f"исправлений {signals['fix']}, слитых DayNN {signals['day_merges']}."
    )


async def _decide(
    llm: ReleaseLlm,
    candidates: list[str],
    signals: dict[str, Any],
    body: str,
    warnings: list[str],
) -> tuple[str, str, str]:
    """Return (version, title, rationale) with the version limited to the candidates."""
    decision = await choose_version(llm, candidates, signals, body)
    version = decision.version
    if version is None:
        version = default_version(candidates, signals)
        warnings.append(MSG_VERSION_FALLBACK)
    title = decision.title or f"Релиз {version}"
    rationale = decision.rationale or _code_rationale(version, signals)
    return version, title, rationale


def _blocked_reason(ctx: ReleaseContext, last_tag: str, candidates: list[str]) -> str | None:
    """Return why the plan cannot be confirmed, or None."""
    paths = dirty_paths(ctx.dirty)
    if paths:
        lines = [MSG_DIRTY_TREE, *paths[:DIRTY_PATHS_SHOWN]]
        if len(paths) > DIRTY_PATHS_SHOWN:
            lines.append(f"… и ещё {len(paths) - DIRTY_PATHS_SHOWN}")
        return "\n".join(lines)
    if not candidates:
        major = version_candidates(last_tag, set())
        return MSG_VERSION_TAKEN.format(version=major[0] if major else last_tag)
    return None


async def build_plan(
    call: ToolCaller,
    llm: ReleaseLlm,
    *,
    chat_id: int,
    user_id: int,
    range_spec: str | None,
    has_github: bool,
) -> PlanBuild:
    """Read the repository, draft the changelog and return a plan; nothing is written."""
    ctx = await _read_context(call)
    if isinstance(ctx, PlanBuild):
        return ctx
    last_tag = last_version_tag(ctx.merged_tags)
    if last_tag is None and range_spec is not None:
        last_tag = last_version_tag(ctx.local_tags)
    if last_tag is None:
        return PlanBuild(None, MSG_NO_VERSION_TAG)
    range_spec = range_spec or f"{last_tag}..HEAD"
    commits = await _read_commits(call, range_spec)
    if isinstance(commits, PlanBuild):
        return commits

    result = filter_commits(commits)
    warnings: list[str] = []
    owner_repo = parse_owner_repo(ctx.origin_url)
    prs: list[str] = []
    if has_github and owner_repo:
        found = await _read_prs(
            call, owner_repo, {c.sha for c in commits}, set(result.day_merge_shas)
        )
        if found is None:
            warnings.append(MSG_GITHUB_PRS_SKIPPED)
        else:
            prs = found
    if not result.kept and not result.day_merges and not prs:
        example = _previous_tag(ctx.merged_tags or ctx.local_tags, last_tag)
        return PlanBuild(
            None,
            MSG_NOTHING_TO_RELEASE.format(
                range=range_spec, dropped=result.dropped, example=f"{example}..HEAD"
            ),
        )

    if ctx.remote_tags is None:
        warnings.append(MSG_REMOTE_TAGS_UNKNOWN)
    taken = set(ctx.local_tags) | set(ctx.remote_tags or [])
    candidates = version_candidates(last_tag, taken)
    body, missing, shrunk = await draft_changelog(
        llm, group_by_phase(result.kept), result.day_merges, prs
    )
    if body is None:
        return PlanBuild(None, MSG_LLM_FAILED)
    signals = collect_signals(result, len(prs))
    version, title, rationale = "", "", ""
    if candidates:
        version, title, rationale = await _decide(llm, candidates, signals, body, warnings)
    if ctx.branch != "main":
        warnings.append(MSG_NOT_MAIN.format(branch=ctx.branch or "detached HEAD"))

    counts = {
        "total": len(commits),
        "kept": len(result.kept),
        "dropped": result.dropped,
        "feat": int(signals["feat"]),
        "fix": int(signals["fix"]),
        "day_merges": len(result.day_merges),
        "prs": len(prs),
        "shrunk": shrunk,
    }
    plan = ReleasePlan(
        plan_id=new_plan_id(),
        chat_id=chat_id,
        user_id=user_id,
        range_spec=range_spec,
        branch=ctx.branch,
        head_sha=ctx.head,
        last_tag=last_tag,
        candidates=candidates,
        taken=sorted(taken),
        version=version,
        title=title,
        rationale=rationale,
        changelog_body=body,
        counts=counts,
        warnings=warnings,
        coverage_missing=missing,
        owner_repo=owner_repo,
        blocked_reason=_blocked_reason(ctx, last_tag, candidates),
    )
    logger.info("release_plan_built", chat_id=chat_id, **counts)
    return PlanBuild(plan, None)
