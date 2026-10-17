"""Pure release logic: versions, commit filtering, prompts and output checks."""

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from agent.release_command import (
    MSG_VERSION_FORMAT,
    MSG_VERSION_NOT_GREATER,
    MSG_VERSION_TAKEN,
)
from shared.logger import get_logger

logger = get_logger(__name__)

TITLE_MAX_CHARS: int = 80
# A major release v(N).0 maps to course week N + 2 (v1.0 -> Week 3 ... v4.0 -> Week 6).
WEEK_OFFSET: int = 2

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)$")
_DAY_MERGE = re.compile(r"^Merge branch '(Day\d+)'")
_NOISE_TYPE = re.compile(r"^(chore|wip)\b", re.IGNORECASE)
_SCOPE_RE = re.compile(r"^\w+\((?P<scope>[^)]+)\)!?:")
_TYPE_RE = re.compile(r"^(?P<type>\w+)(?:\([^)]*\))?!?:")
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_CLOSE_RE = re.compile(r"</think\s*>", re.IGNORECASE)
_MARKUP_CHARS_RE = re.compile(r"[*`~<>]")
_HEADING_PREFIX_RE = re.compile(r"^\s*#{1,6}\s+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WHITESPACE_RE = re.compile(r"\s+")
_QUOTE_CHARS = "\"'«»“”„ "
_TRAILING_PUNCT = ".,:;!?… "


class ReleaseParseError(ValueError):
    """A tool result did not have the expected shape."""


@dataclass(frozen=True)
class ReleaseContext:
    """Repository facts returned by the release_context tool."""

    branch: str | None
    head: str
    origin_url: str | None
    local_tags: list[str]
    merged_tags: list[str]
    remote_tags: list[str] | None
    remote_error: str | None
    dirty: list[str]


@dataclass(frozen=True)
class CommitInfo:
    """One commit of the release range."""

    sha: str
    parents: int
    subject: str
    dirs: list[str]
    planning_only: bool
    files_n: int


@dataclass(frozen=True)
class FilterResult:
    """Outcome of the noise filter."""

    kept: list[CommitInfo]
    day_merges: list[str]
    day_merge_shas: list[str]
    dropped: int


# --- versions ---------------------------------------------------------------

def parse_version(tag: str) -> tuple[int, int] | None:
    """Parse "vX.Y" into numbers, or None for any other string."""
    match = _VERSION_RE.match(tag)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def last_version_tag(tags: list[str]) -> str | None:
    """Return the numerically greatest vX.Y tag, ignoring other tags."""
    best: tuple[tuple[int, int], str] | None = None
    for tag in tags:
        parsed = parse_version(tag)
        if parsed is not None and (best is None or parsed > best[0]):
            best = (parsed, tag)
    return best[1] if best else None


def version_candidates(last_tag: str, taken: set[str]) -> list[str]:
    """Return the next major and next minor versions that are not taken."""
    parsed = parse_version(last_tag)
    if parsed is None:
        return []
    major, minor = parsed
    options = [f"v{major + 1}.0", f"v{major}.{minor + 1}"]
    return [version for version in options if version not in taken]


def validate_version(version: str, last_tag: str, taken: set[str]) -> str | None:
    """Return a Russian error for an unusable manual version, or None when valid."""
    parsed = parse_version(version)
    if parsed is None:
        return MSG_VERSION_FORMAT
    last = parse_version(last_tag)
    if last is not None and parsed <= last:
        return MSG_VERSION_NOT_GREATER.format(last=last_tag)
    if version in taken:
        return MSG_VERSION_TAKEN.format(version=version)
    return None


def default_version(candidates: list[str], signals: dict[str, Any]) -> str:
    """Pick the deterministic default: major on breaking changes or merged days, else minor."""
    if len(candidates) == 1:
        return candidates[0]
    if signals.get("breaking") or int(signals.get("day_merges", 0)) > 0:
        return candidates[0]
    return candidates[-1]


# Assumption A1: minor releases carry no "Week N".
def tag_message(version: str, title: str) -> str:
    """Build the annotated tag message for a version and title."""
    parsed = parse_version(version)
    if parsed is not None and parsed[1] == 0:
        return f"{version} Week {parsed[0] + WEEK_OFFSET}: {title}"
    return f"{version}: {title}"


# --- MCP result parsers -----------------------------------------------------

def _json_lines(text: str) -> list[dict[str, Any]]:
    """Parse every line that starts with "{" as a JSON object."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ReleaseParseError("invalid_json_line") from exc
        if not isinstance(row, dict):
            raise ReleaseParseError("json_line_not_object")
        rows.append(row)
    return rows


def parse_json_tail(text: str) -> dict[str, Any]:
    """Return the last JSON object line of a write-tool result."""
    rows = _json_lines(text)
    if not rows:
        raise ReleaseParseError("no_json_line")
    return rows[-1]


def _str_list(value: Any) -> list[str]:
    """Return the string items of a JSON list, or an empty list."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def parse_context(text: str) -> ReleaseContext:
    """Parse the release_context tool result."""
    data = parse_json_tail(text)
    remote = data.get("remote_tags")
    branch = data.get("branch")
    origin = data.get("origin_url")
    error = data.get("remote_error")
    return ReleaseContext(
        branch=branch if isinstance(branch, str) else None,
        head=str(data.get("head") or ""),
        origin_url=origin if isinstance(origin, str) else None,
        local_tags=_str_list(data.get("local_tags")),
        merged_tags=_str_list(data.get("merged_tags")),
        remote_tags=_str_list(remote) if isinstance(remote, list) else None,
        remote_error=error if isinstance(error, str) else None,
        dirty=_str_list(data.get("dirty")),
    )


def parse_commit_page(text: str) -> tuple[list[CommitInfo], int | None]:
    """Parse one release_range_commits page into commits and the next page number."""
    commits: list[CommitInfo] = []
    next_page: int | None = None
    for row in _json_lines(text):
        if "next_page" in row:
            value = row.get("next_page")
            next_page = value if isinstance(value, int) else None
            continue
        try:
            commits.append(
                CommitInfo(
                    sha=str(row["sha"]),
                    parents=int(row["parents"]),
                    subject=str(row["subject"]),
                    dirs=_str_list(row.get("dirs")),
                    planning_only=bool(row.get("planning_only")),
                    files_n=int(row.get("files_n", 0)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReleaseParseError("invalid_commit_row") from exc
    return commits, next_page


def dirty_paths(lines: list[str]) -> list[str]:
    """Return every path of porcelain status lines, including untracked entries."""
    paths: list[str] = []
    for line in lines:
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.append(path)
    return paths


def parse_owner_repo(url: str | None) -> str | None:
    """Extract "owner/repo" from a GitHub-style remote URL, or None."""
    if not url:
        return None
    url = url.strip()
    if "://" in url:
        path = urlparse(url).path
    elif ":" in url:
        host, path = url.split(":", 1)
        # A one-letter "host" is a Windows drive, and a leading slash is a local path.
        if len(host) == 1 or path.startswith("/"):
            return None
    else:
        return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return path if _OWNER_REPO_RE.match(path) else None


def is_release_link(url: str | None) -> bool:
    """Return True for an https GitHub Release URL."""
    return isinstance(url, str) and url.startswith("https://github.com/") and "/releases/" in url


# --- commit logic -----------------------------------------------------------

def filter_commits(commits: list[CommitInfo]) -> FilterResult:
    """Drop noise commits, keeping DayNN merges as separate merged changes."""
    kept: list[CommitInfo] = []
    day_merges: list[str] = []
    day_merge_shas: list[str] = []
    dropped = 0
    for commit in commits:
        if commit.parents > 1:
            match = _DAY_MERGE.match(commit.subject)
            if match is None:
                dropped += 1
            elif match.group(1) not in day_merges:
                day_merges.append(match.group(1))
                day_merge_shas.append(commit.sha)
            continue
        if (
            _NOISE_TYPE.match(commit.subject)
            or commit.planning_only
            or commit.files_n == 0
        ):
            dropped += 1
            continue
        kept.append(commit)
    return FilterResult(kept, day_merges, day_merge_shas, dropped)


def _group_key(subject: str) -> str:
    """Map a commit subject to its phase number, "quick" or "other"."""
    match = _SCOPE_RE.match(subject)
    if match is None:
        return "other"
    scope = match.group("scope")
    digits = re.match(r"\d+", scope)
    if digits:
        return str(int(digits.group(0)))
    if scope.lower().startswith("quick"):
        return "quick"
    return "other"


def group_by_phase(kept: list[CommitInfo]) -> dict[str, list[str]]:
    """Group kept subjects by phase: numeric ascending, then quick, then other."""
    groups: dict[str, list[str]] = {}
    for commit in kept:
        groups.setdefault(_group_key(commit.subject), []).append(commit.subject)

    def order(key: str) -> tuple[int, int]:
        if key.isdigit():
            return (0, int(key))
        return (1, 0) if key == "quick" else (2, 0)

    return {key: groups[key] for key in sorted(groups, key=order)}


def _commit_type(subject: str) -> str:
    """Return the lower-cased conventional-commit type of a subject."""
    match = _TYPE_RE.match(subject)
    return match.group("type").lower() if match else ""


def collect_signals(result: FilterResult, prs: int) -> dict[str, int | bool]:
    """Summarise the kept commits into counters used to pick the default version."""
    types = [_commit_type(c.subject) for c in result.kept]
    breaking = any(
        "BREAKING" in c.subject or re.search(r"!:", c.subject) for c in result.kept
    )
    return {
        "feat": types.count("feat"),
        "fix": types.count("fix"),
        "kept": len(result.kept),
        "day_merges": len(result.day_merges),
        "prs": prs,
        "breaking": bool(breaking),
    }


def _cut_at_word(text: str, limit: int) -> str:
    """Cut to the limit on a word boundary; a single overlong word is hard-cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip()


def sanitize_title(raw: object) -> str | None:
    """Reduce model output to a one-line title of at most 80 characters, or None."""
    if not isinstance(raw, str):
        return None
    text = _THINK_BLOCK_RE.sub("", raw)
    closings = list(_THINK_CLOSE_RE.finditer(text))
    if closings:
        text = text[closings[-1].end():]
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    title = _HEADING_PREFIX_RE.sub("", lines[0])
    title = _CONTROL_RE.sub(" ", _MARKUP_CHARS_RE.sub("", title))
    title = _WHITESPACE_RE.sub(" ", title).strip(_QUOTE_CHARS)
    title = _cut_at_word(title, TITLE_MAX_CHARS).rstrip(_TRAILING_PUNCT)
    return title if any(ch.isalnum() for ch in title) else None
