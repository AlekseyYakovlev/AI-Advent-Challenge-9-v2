"""Git and project-file MCP server for one repository, served over stdio.

Usage:
    python mcp_servers/project_git.py --root=<repository> [--max-chars=18000] [--timeout=10]
        [--allow-write] [--allow-release]

Without --allow-write every tool only reads. With it, write_file and edit_file are
registered and write files inside the root. With --allow-release the release tools are
registered: release_context, release_range_commits, release_commit_changelog,
release_create_tag, release_push and github_create_draft_release.

The server is local-only by default. Under --allow-release it also makes HTTPS calls to
api.github.com (github_create_draft_release) with the token from its own environment.

This file is self-contained on purpose. It does not use the application logger or
settings modules from the shared package: that logger writes JSON to stdout, and stdout is
the MCP protocol channel here. Diagnostics go to stderr through ``sys.stderr.write``. This
is the documented exception to the project's logging rule, so the file has no print calls.
"""

import argparse
import ast
import asyncio
import contextlib
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import NoReturn

import httpx
import psutil
from mcp.server.fastmcp import FastMCP

DEFAULT_MAX_CHARS: int = 18000
MIN_MAX_CHARS: int = 1000
APP_RESULT_CAP: int = 20000
DEFAULT_TIMEOUT: float = 10.0
HARD_CAP_BYTES: int = 4 * 1024 * 1024
STDERR_KEEP_BYTES: int = 65536
MAX_REF_LENGTH: int = 255
READ_CHUNK_BYTES: int = 65536
NARROW_PATH_ADVICE: str = 'Call again with a narrower "path" (a file or folder) to see the rest.'
READ_WINDOW_LINES: int = 300
MAX_READ_BYTES: int = 2_000_000
BINARY_SNIFF_BYTES: int = 8000
MAX_PATTERN_CHARS: int = 200
MAX_SEARCH_HITS: int = 200
HIT_TEXT_CHARS: int = 200
MAX_USAGE_HITS: int = 300
USAGE_LINE_CHARS: int = 160
MAX_PARSE_BYTES: int = 1_000_000
USAGES_TIME_BUDGET_S: float = 20.0
SEARCH_ADVICE: str = "Narrow the search with glob or a more specific pattern."
MAX_WRITE_BYTES: int = 200_000
WRITE_ADVICE: str = "The file was saved in full; call read_file to review it."
RESERVED_NAMES: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)
_SHORT_GIT: re.Pattern[str] = re.compile(r"^git~\d+$", re.IGNORECASE)
_COMMIT_ID = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
RELEASE_PAGE_SIZE: int = 50
RELEASE_NETWORK_TIMEOUT: float = 90.0
MAX_RELEASE_PAGES: int = 40
MAX_COMMIT_DIRS: int = 10
MAX_SECTION_CHARS: int = 100000
GITHUB_API: str = "https://api.github.com"
GITHUB_TOKEN_ENV: str = "GITHUB_PERSONAL_ACCESS_TOKEN"
GITHUB_TIMEOUT: float = 30.0
MAX_RELEASE_BODY_CHARS: int = 125000
MAX_RELEASE_NAME_CHARS: int = 200
GITHUB_HEADERS: dict[str, str] = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
_GITHUB_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
MAX_TAG_MESSAGE_CHARS: int = 200
CHANGELOG_HEADER: str = (
    "# Changelog\n\n"
    "Все значимые изменения проекта записываются в этот файл.\n"
    "Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).\n"
)
_VERSION_TAG = re.compile(r"v(\d+)\.(\d+)")
_release_registered: bool = False


class ToolInputError(ValueError):
    """Raised when a tool argument is rejected; the message names the reason and the fix."""


@dataclass(frozen=True)
class GitResult:
    """Outcome of one child process run."""

    returncode: int
    text: str
    stderr: str
    capped: bool


@dataclass(frozen=True)
class ServerConfig:
    """Validated launch settings shared by every tool."""

    root: str
    git: str
    max_chars: int
    timeout: float
    allow_write: bool = False
    allow_release: bool = False


_config: ServerConfig | None = None

server = FastMCP("local-git", log_level="WARNING")


def configure(
    root: str,
    git: str,
    max_chars: int,
    timeout: float,
    allow_write: bool = False,
    allow_release: bool = False,
) -> None:
    """Store the validated launch settings for the tools."""
    global _config
    _config = ServerConfig(
        root=root,
        git=git,
        max_chars=max_chars,
        timeout=timeout,
        allow_write=allow_write,
        allow_release=allow_release,
    )


def get_config() -> ServerConfig:
    """Return the launch settings or fail when the server is not configured."""
    if _config is None:
        raise RuntimeError("server is not configured")
    return _config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate the launch arguments."""
    parser = argparse.ArgumentParser(
        prog="project_git.py",
        description="Git and project-file MCP server over stdio (read-only unless --allow-write).",
        allow_abbrev=False,
    )
    parser.add_argument("--root", default=os.getcwd(), help="repository folder (default: cwd)")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"result size limit in characters (minimum {MIN_MAX_CHARS})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="per-command timeout in seconds (greater than 0)",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="register write_file and edit_file (writes inside --root)",
    )
    parser.add_argument(
        "--allow-release",
        action="store_true",
        help="expose the release tools (commit CHANGELOG.md, tag, push, draft GitHub Release)",
    )
    args = parser.parse_args(argv)
    if args.max_chars < MIN_MAX_CHARS:
        parser.error(f"--max-chars must be at least {MIN_MAX_CHARS}")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    return args


def fail(message: str) -> NoReturn:
    """Write the reason as the last stderr line and exit with code 1."""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
    sys.exit(1)


def git_env() -> dict[str, str]:
    """Build the child environment: no inherited GIT_ variables, no prompts, no pager."""
    env = dict(os.environ)
    for key in [name for name in env if name.upper().startswith("GIT_")]:
        del env[key]
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["LC_ALL"] = "C"
    return env


def base_argv(git: str) -> list[str]:
    """Return the fixed leading arguments for every git command."""
    return [
        git,
        "--no-pager",
        "--literal-pathspecs",
        "-c",
        "core.quotepath=off",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "color.ui=false",
    ]


def _kill_tree(pid: int) -> None:
    """Kill a process and all its descendants; the git launcher spawns the real git."""
    try:
        parent = psutil.Process(pid)
        victims = parent.children(recursive=True)
    except psutil.Error:
        return
    for child in victims:
        with contextlib.suppress(psutil.Error):
            child.kill()
    with contextlib.suppress(psutil.Error):
        parent.kill()


async def _drain_stderr(stream: asyncio.StreamReader, sink: bytearray) -> None:
    """Read stderr to EOF, keeping only the first STDERR_KEEP_BYTES."""
    while True:
        chunk = await stream.read(READ_CHUNK_BYTES)
        if not chunk:
            return
        room = STDERR_KEEP_BYTES - len(sink)
        if room > 0:
            sink.extend(chunk[:room])


async def _read_stdout(stream: asyncio.StreamReader, pid: int) -> tuple[bytes, bool]:
    """Read stdout to EOF or to HARD_CAP_BYTES; on the cap kill the process tree."""
    buf = bytearray()
    while True:
        chunk = await stream.read(READ_CHUNK_BYTES)
        if not chunk:
            return bytes(buf), False
        room = HARD_CAP_BYTES - len(buf)
        if len(chunk) > room:
            buf.extend(chunk[:room])
            _kill_tree(pid)
            return bytes(buf), True
        buf.extend(chunk)


async def run_command(
    argv: list[str], *, cwd: str, env: dict[str, str], timeout: float, label: str
) -> GitResult:
    """Run a fixed argv without a shell, bounded in time and output size."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    assert proc.stdout is not None and proc.stderr is not None
    err_sink = bytearray()
    out_holder: list[tuple[bytes, bool]] = []

    async def work() -> None:
        err_task = asyncio.create_task(_drain_stderr(proc.stderr, err_sink))
        try:
            out_holder.append(await _read_stdout(proc.stdout, proc.pid))
            await proc.wait()
            await err_task
        finally:
            if not err_task.done():
                err_task.cancel()

    try:
        await asyncio.wait_for(work(), timeout)
    except TimeoutError:
        _kill_tree(proc.pid)
        await proc.wait()
        raise ToolInputError(
            f"{label} timed out after {timeout:g}s; narrow the call with path or raise --timeout"
        ) from None
    out_bytes, capped = out_holder[0]
    return GitResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        text=out_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n"),
        stderr=bytes(err_sink).decode("utf-8", errors="replace").replace("\r\n", "\n"),
        capped=capped,
    )


async def run_git(args: list[str]) -> GitResult:
    """Run one git command in the configured repository."""
    cfg = get_config()
    return await run_command(
        base_argv(cfg.git) + args,
        cwd=cfg.root,
        env=git_env(),
        timeout=cfg.timeout,
        label="git " + args[0],
    )


def resolve_inside_root(root_real: str, raw: str) -> str:
    """Validate a user path and return it relative to the root with '/' separators."""
    if raw == "":
        raise ToolInputError("path must not be empty; omit it to cover the whole repository")
    if raw.startswith("-"):
        raise ToolInputError(
            'path must not start with "-"; use a relative path such as agent/ws.py'
        )
    if any(ch in raw for ch in "\x00\r\n"):
        raise ToolInputError("path contains control characters")
    drive, rest = os.path.splitdrive(raw)
    unc = drive.startswith(("\\\\", "//"))
    if ":" in rest or (drive and not unc and rest[:1] not in ("\\", "/")):
        raise ToolInputError(
            "drive-relative paths and ':' are not allowed; use a relative path such as agent/ws.py"
        )
    outside = ToolInputError(
        "path must stay inside the repository root; use a relative path such as agent/ws.py"
    )
    try:
        target = os.path.realpath(os.path.join(root_real, raw))
        common = os.path.commonpath([os.path.normcase(root_real), os.path.normcase(target)])
        if common != os.path.normcase(root_real):
            raise outside
        relative = os.path.relpath(target, root_real)
    except (ValueError, OSError):
        raise outside from None
    return "." if relative == "." else relative.replace(os.sep, "/")


def check_ref_syntax(raw: str) -> None:
    """Reject refs that could be read as options or contain unsafe characters."""
    if raw.startswith("-"):
        raise ToolInputError(
            'ref must not start with "-"; use a branch, tag, commit hash or HEAD~N'
        )
    if (
        raw == ""
        or len(raw) > MAX_REF_LENGTH
        or "\\" in raw
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw)
    ):
        raise ToolInputError(
            "ref contains characters that are not allowed; use a branch, tag, commit hash or HEAD~N"
        )


async def resolve_ref(raw: str) -> str:
    """Resolve a user ref to a full commit hash using git itself."""
    check_ref_syntax(raw)
    result = await run_git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", raw + "^{commit}"]
    )
    commit = result.text.strip()
    if result.returncode != 0 or not _COMMIT_ID.fullmatch(commit):
        raise ToolInputError(
            f"unknown ref '{raw}'; use a branch, tag, short commit hash or HEAD~N"
        )
    return commit


async def resolve_ref_or_range(raw: str) -> str:
    """Resolve one ref, or both sides of an A..B / A...B range, to commit hashes."""
    for separator in ("...", ".."):
        if separator in raw:
            left, _, right = raw.partition(separator)
            if not left or not right:
                raise ToolInputError("range must have a ref on both sides, for example main...HEAD")
            return await resolve_ref(left) + separator + await resolve_ref(right)
    return await resolve_ref(raw)


def truncation_marker(
    shown: int,
    total: int,
    *,
    lower_bound: bool = False,
    unit: str = "characters",
    advice: str = NARROW_PATH_ADVICE,
) -> str:
    """Build the marker that tells the caller how much was shown and how to narrow the call."""
    of = "of more than" if lower_bound else "of"
    return f"[output truncated: showing {shown:,} {of} {total:,} {unit}. {advice}]"


def _cut_at_line(text: str, budget: int) -> str:
    """Return at most budget characters of text, ending at a line boundary when possible."""
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    cut = text[:budget]
    newline = cut.rfind("\n")
    return cut[:newline] if newline > 0 else cut


def fit_text(
    summary: str,
    body: str,
    max_chars: int,
    *,
    lower_bound: bool = False,
    advice: str = NARROW_PATH_ADVICE,
) -> str:
    """Join summary and body within max_chars, adding a truncation marker when cut."""
    summary = summary[: max_chars // 2]
    if not body:
        return summary
    full = summary + "\n" + body
    if len(full) <= max_chars and not lower_bound:
        return full
    total = len(body)
    reserve = len(truncation_marker(total, total, lower_bound=lower_bound, advice=advice))
    budget = max_chars - len(summary) - 1 - 1 - reserve
    kept = _cut_at_line(body, budget)
    marker = truncation_marker(len(kept), total, lower_bound=lower_bound, advice=advice)
    return summary + "\n" + (kept + "\n" if kept else "") + marker


def _shrink_stat(stat: str, budget: int) -> str:
    """Keep whole file lines of a diff stat, then a 'more files' line and the totals line."""
    if len(stat) <= budget:
        return stat
    lines = stat.splitlines()
    if len(lines) < 2:
        return stat[:budget]
    files, totals = lines[:-1], lines[-1]
    for keep in range(len(files), -1, -1):
        hidden = len(files) - keep
        block = "\n".join(files[:keep] + [f"... {hidden} more files not shown", totals])
        if len(block) <= budget or keep == 0:
            return block
    return totals


def compose_truncated_diff(
    summary: str, stat: str, patch: str, max_chars: int, *, lower_bound: bool = False
) -> str:
    """Compose summary, per-file stat, patch text and a marker within max_chars."""
    summary = summary[: max_chars // 2]
    stat_block = _shrink_stat(stat, min(len(stat), max_chars // 4))
    header = "\n".join([summary] + ([stat_block] if stat_block else [])) + "\n\n"
    if len(header) + len(patch) <= max_chars and not lower_bound:
        return header + patch
    total = len(patch)
    reserve = len(truncation_marker(total, total, lower_bound=lower_bound)) + 1
    kept = _cut_at_line(patch, max_chars - len(header) - reserve)
    marker = truncation_marker(len(kept), total, lower_bound=lower_bound)
    return header + (kept + "\n" if kept else "") + marker


def _last_line(text: str, fallback: str) -> str:
    """Return the last non-empty line of text, or the fallback."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else fallback


def _git_error(result: GitResult, name: str) -> ToolInputError:
    """Build the tool error for a failed git command from its last stderr line."""
    return ToolInputError(
        f"git {name} failed: {_last_line(result.stderr, f'exit code {result.returncode}')}"
    )


def _plural(count: int, singular: str, plural: str) -> str:
    """Return '1 file' or 'N files' style text."""
    return f"1 {singular}" if count == 1 else f"{count} {plural}"


def _normalise_path(path: str | None) -> str | None:
    """Return the root-relative path, or None when no path was given."""
    if not path:
        return None
    return resolve_inside_root(get_config().root, path)


async def _normalise_ref(ref: str | None) -> str | None:
    """Return a hash (or hash range) for a ref, or None when no ref was given."""
    if not ref:
        return None
    return await resolve_ref_or_range(ref)


@server.tool(structured_output=False)
async def git_current_branch() -> str:
    """LOCAL working copy (not the GitHub remote): report the branch that is checked out now.

    Takes no parameters. The answer is read at the moment of the call, so it follows a
    `git checkout` made after the server started. Output is short and never truncated.
    """
    named = await run_git(["symbolic-ref", "--short", "-q", "HEAD"])
    if named.returncode == 0 and named.text.strip():
        label = f"Branch: {named.text.strip()}"
    else:
        short = await run_git(["rev-parse", "--short", "HEAD"])
        if short.returncode != 0 or not short.text.strip():
            if "unknown revision" in short.stderr or "ambiguous argument" in short.stderr:
                return "Branch: unknown (no commits yet)"
            raise _git_error(short, "rev-parse")
        label = f"Detached HEAD at {short.text.strip()}"
    has_commit = await run_git(["rev-parse", "--verify", "-q", "HEAD"])
    return label + (" (no commits yet)" if has_commit.returncode != 0 else "")


@server.tool(structured_output=False)
async def git_status() -> str:
    """LOCAL working copy (not the GitHub remote): list changed and untracked files.

    Takes no parameters. The first line is a summary (for example "3 changed files" or
    "Working tree clean"); the body is `git status --porcelain=v1 --branch`: a `## branch`
    line, then one `XY path` line per file, where X is the index state, Y is the working tree
    state and `??` marks an untracked file. Long output is truncated with a marker.
    """
    result = await run_git(
        ["status", "--porcelain=v1", "--branch", "--untracked-files=normal"]
    )
    if result.returncode != 0:
        raise _git_error(result, "status")
    changed = [line for line in result.text.splitlines() if line and not line.startswith("##")]
    summary = "Working tree clean" if not changed else _plural(
        len(changed), "changed file", "changed files"
    )
    return fit_text(
        summary,
        result.text.rstrip("\n"),
        get_config().max_chars,
        lower_bound=result.capped,
        advice='Use git_diff or git_tracked_files with "path" to look at one folder.',
    )


@server.tool(structured_output=False)
async def git_diff(
    staged: bool = False, ref: str | None = None, path: str | None = None
) -> str:
    """LOCAL working copy (not the GitHub remote): show a diff of tracked files.

    Parameters: `staged` (bool) shows what is staged for commit; `ref` is a branch, tag, commit
    hash or range; `path` limits the diff to a file or folder relative to the repository root.
    Without arguments it shows unstaged changes; `ref="HEAD"` shows all uncommitted changes;
    `ref="main...HEAD"` shows what a branch adds. Untracked files appear only in git_status.
    The first line is a summary. A diff that does not fit is truncated with a marker, after a
    per-file stat; call again with a narrower `path` to see the rest.
    """
    cfg = get_config()
    rel = _normalise_path(path)
    if staged and ref and ".." in ref:
        raise ToolInputError(
            "staged cannot be combined with a ref range; use a single ref or drop staged"
        )
    resolved = await _normalise_ref(ref)
    selectors: list[str] = []
    if staged:
        selectors.append("--cached")
    if resolved:
        selectors.append(resolved)
    if rel is not None:
        selectors += ["--", rel]
    flags = ["--no-color", "--no-ext-diff", "--no-textconv"]
    result = await run_git(["diff", *flags, *selectors])
    if result.returncode != 0:
        raise _git_error(result, "diff")
    patch = result.text
    if not patch.strip():
        return "No differences"
    files = sum(1 for line in patch.splitlines() if line.startswith("diff --git "))
    summary = _plural(files, "changed file", "changed files")
    if len(summary) + 1 + len(patch) <= cfg.max_chars and not result.capped:
        return summary + "\n" + patch.rstrip("\n")
    stat_result = await run_git(["diff", "--stat=120,80", *flags, *selectors])
    stat = stat_result.text.rstrip("\n") if stat_result.returncode == 0 else ""
    if stat:
        summary = _last_line(stat, summary)
    return compose_truncated_diff(summary, stat, patch, cfg.max_chars, lower_bound=result.capped)


def _folder_table(paths: list[str], budget: int) -> str:
    """Count files per top-level folder and keep as many table lines as the budget allows."""
    counts: dict[str, int] = {}
    for item in paths:
        top = item.split("/", 1)[0] + "/" if "/" in item else "(root)"
        counts[top] = counts.get(top, 0) + 1
    lines = [f"{name} {count}" for name, count in sorted(counts.items())]
    header = "Files per top-level folder:"
    kept: list[str] = []
    used = len(header)
    for index, line in enumerate(lines):
        hidden_line = f"... {len(lines) - index} more folders not shown"
        if used + 1 + len(line) + 1 + len(hidden_line) > budget and len(lines) - index > 1:
            kept.append(hidden_line)
            break
        if used + 1 + len(line) > budget:
            kept.append(f"... {len(lines) - index} more folders not shown")
            break
        kept.append(line)
        used += 1 + len(line)
    return "\n".join([header, *kept])


@server.tool(structured_output=False)
async def git_tracked_files(path: str | None = None) -> str:
    """LOCAL working copy (not the GitHub remote): list files tracked by git.

    Parameter: `path` (optional) is a file or folder relative to the repository root; without
    it the whole repository is listed. The first line is a summary with the file count. A long
    list is truncated with a marker and a per-folder count table; call again with `path`
    to list one folder.
    """
    max_chars = get_config().max_chars
    rel = _normalise_path(path)
    result = await run_git(["ls-files", "--", rel] if rel is not None else ["ls-files"])
    if result.returncode != 0:
        raise _git_error(result, "ls-files")
    paths = [line for line in result.text.split("\n") if line]
    if not paths:
        return f"No tracked files under {rel}" if rel is not None else "No tracked files"
    summary = _plural(len(paths), "tracked file", "tracked files")
    if rel is not None:
        summary += f" under {rel}"
    body = "\n".join(paths)
    if len(summary) + 1 + len(body) <= max_chars:
        return summary + "\n" + body
    table = _folder_table(paths, max_chars // 3)
    marker_len = len(truncation_marker(len(paths), len(paths), unit="files"))
    head = summary + "\n" + table + "\n"
    budget = max_chars - len(head) - marker_len - 1
    shown: list[str] = []
    used = 0
    for item in paths:
        if used + len(item) + 1 > budget:
            break
        shown.append(item)
        used += len(item) + 1
    marker = truncation_marker(len(shown), len(paths), unit="files")
    return head + ("\n".join(shown) + "\n" if shown else "") + marker


@server.tool(structured_output=False)
async def git_recent_commits(
    count: int = 10, path: str | None = None, ref: str | None = None
) -> str:
    """LOCAL working copy (not the GitHub remote): list the most recent commits.

    Parameters: `count` is how many commits to show (default 10, at most 50); `path` limits
    the history to a file or folder relative to the repository root; `ref` is a branch, tag or
    commit hash to start from (default HEAD). The first line is a summary; each following line
    is tab-separated: short hash, date, author, subject. Long output is truncated with a marker.
    """
    max_chars = get_config().max_chars
    limited = count > 50
    count = max(1, min(count, 50))
    rel = _normalise_path(path)
    resolved = await _normalise_ref(ref)
    argv = [
        "log", "-n", str(count), "--no-color", "--no-show-signature", "--date=short",
        "--format=%h%x09%ad%x09%an%x09%s",
    ]  # fmt: skip
    if resolved:
        argv.append(resolved)
    if rel is not None:
        argv += ["--", rel]
    result = await run_git(argv)
    if result.returncode != 0:
        if "does not have any commits yet" in result.stderr:
            return "No commits yet"
        raise _git_error(result, "log")
    lines = [line for line in result.text.split("\n") if line]
    if not lines:
        return "No commits found"
    summary = _plural(len(lines), "commit", "commits")
    if limited:
        summary += " (count limited to 50)"
    return fit_text(summary, "\n".join(lines), max_chars, lower_bound=result.capped)


def pathspec_argv(git: str) -> list[str]:
    """Return the leading git arguments with glob pathspecs enabled."""
    return [part for part in base_argv(git) if part != "--literal-pathspecs"]


async def visible_paths(rel: str | None = None) -> list[str]:
    """List files git considers part of the project: tracked plus untracked, not ignored."""
    args = ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    if rel is not None:
        args += ["--", rel]
    result = await run_git(args)
    if result.returncode != 0:
        raise _git_error(result, "ls-files")
    return [item for item in result.text.split("\0") if item]


def is_binary(data: bytes) -> bool:
    """Return True when the first bytes contain a NUL byte."""
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def decode_text(data: bytes) -> tuple[str, bool]:
    """Decode UTF-8 text with LF line ends and report whether CRLF was present."""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n"), b"\r\n" in data


def split_lines(text: str) -> list[str]:
    """Split on LF only, dropping the empty tail after a final newline."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _has_git_part(rel: str) -> bool:
    """Return True when any path component names the .git directory (including 8.3 aliases)."""
    for part in rel.split("/"):
        bare = part.rstrip(". ")
        if bare.casefold() == ".git" or _SHORT_GIT.match(bare):
            return True
    return False


def _is_env_name(rel: str) -> bool:
    """Return True when the file name starts with .env."""
    return rel.rsplit("/", 1)[-1].casefold().startswith(".env")


def _is_protected_path(rel: str) -> bool:
    """Return True for paths the tools never read or write: .env* files and .git internals."""
    return _has_git_part(rel) or _is_env_name(rel)


def _resolve_listed(item: str, visible: set[str]) -> str | None:
    """Return the real root-relative path of a listed file, or None when it must not be opened."""
    try:
        real = resolve_inside_root(get_config().root, "./" + item)
    except ToolInputError:
        return None
    if _is_protected_path(real):
        return None
    if os.path.normcase(real) != os.path.normcase(item) and os.path.normcase(real) not in visible:
        return None
    return real


async def require_visible_file(rel: str) -> str:
    """Return the absolute path of a visible regular file or raise ToolInputError."""
    if rel == ".":
        raise ToolInputError("path must name a file, for example agent/ws.py")
    absolute = os.path.join(get_config().root, rel)
    if rel not in await visible_paths(rel) or not os.path.isfile(absolute):
        raise ToolInputError(
            f"{rel} is not a readable file: it does not exist, is a folder, or git ignores it; "
            "call git_tracked_files to list files"
        )
    if _is_protected_path(rel):
        raise ToolInputError(
            f"{rel} is a .env file or inside .git; these hold secrets and repository internals "
            "and cannot be read; choose another file"
        )
    return absolute


@server.tool(structured_output=False)
async def read_file(
    path: str, start_line: int | None = None, end_line: int | None = None
) -> str:
    """LOCAL working copy (not the GitHub remote): read one text file with line numbers.

    Parameters: `path` is a file relative to the repository root; `start_line` and `end_line`
    (1-based, inclusive) select a range. By default 300 lines are shown from the start. The
    first line is a summary "path: lines a-b of N"; when more lines exist a marker at the end
    names the `start_line` to call with next. Files that git ignores (.env, databases) and
    binary files cannot be read, and neither can `.env*` files or anything inside `.git`.
    """
    max_chars = get_config().max_chars
    rel = _normalise_path(path)
    if rel is None:
        raise ToolInputError("path is required, for example agent/ws.py")
    absolute = await require_visible_file(rel)
    try:
        if os.path.getsize(absolute) > MAX_READ_BYTES:
            raise ToolInputError(
                f"{rel} is larger than 2 MB; use search_text to find the lines you need"
            )
        with open(absolute, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ToolInputError(f"could not read {rel}: {exc.strerror or exc}") from None
    if is_binary(data):
        raise ToolInputError(f"{rel} is a binary file and cannot be shown as text")
    lines = split_lines(decode_text(data)[0])
    total = len(lines)
    if total == 0:
        return f"Empty file: {rel} (0 lines)"
    start = 1 if start_line is None else start_line
    if start < 1:
        raise ToolInputError("start_line must be 1 or greater")
    if end_line is not None and end_line < start:
        raise ToolInputError("end_line must be greater than or equal to start_line")
    if start > total:
        raise ToolInputError(f"start_line {start} is past the end: {rel} has {total} lines")
    end = min(total, start + READ_WINDOW_LINES - 1 if end_line is None else end_line)
    reserve = len(
        truncation_marker(
            total,
            total,
            unit="lines",
            advice=f"Call again with start_line={total + 1} to see the rest.",
        )
    )
    used = len(f"{rel}: lines {total}-{total} of {total}") + 2 + reserve
    body: list[str] = []
    last = start - 1
    for number in range(start, end + 1):
        row = f"{number:>6}\t{lines[number - 1]}"
        if used + len(row) + 1 > max_chars:
            if body:
                break
            row = row[: max(0, max_chars - used - 1)]
        body.append(row)
        used += len(row) + 1
        last = number
    summary = f"{rel}: lines {start}-{last} of {total}"
    text = summary + "\n" + "\n".join(body)
    if last < total and (last < end or end_line is None):
        text += "\n" + truncation_marker(
            last - start + 1,
            total,
            unit="lines",
            advice=f"Call again with start_line={last + 1} to see the rest.",
        )
    return text[:max_chars]


@server.tool(structured_output=False)
async def search_text(
    pattern: str, regex: bool = False, glob: str | None = None, ignore_case: bool = False
) -> str:
    """LOCAL working copy (not the GitHub remote): search text across project files.

    Parameters: `pattern` is searched as a plain substring by default; with `regex=true` it is
    an extended regular expression. `glob` limits the files, for example `*.py` or `docs/*.md`.
    `ignore_case` ignores letter case. Files git ignores (.env, databases) and binary files
    are skipped, as are `.env*` files, `.git` paths and links that leave the root or point at
    hidden files. The first line is "N matches in M files", then one `path:line: text` per hit.
    """
    cfg = get_config()
    if (
        not pattern
        or len(pattern) > MAX_PATTERN_CHARS
        or any(ch in pattern for ch in "\r\n\x00")
    ):
        raise ToolInputError("pattern must be 1-200 characters on one line")
    glob = glob or None
    if glob is not None and (
        glob[0] in ":-/\\"
        or ".." in glob
        or ":" in glob
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in glob)
    ):
        raise ToolInputError("glob must be a relative pattern such as *.py or docs/*.md")
    argv = pathspec_argv(cfg.git) + [
        "grep", "-n", "-I", "--no-color", "--untracked", "--exclude-standard",
        "-E" if regex else "-F",
    ]  # fmt: skip
    if ignore_case:
        argv.append("-i")
    argv += ["-e", pattern]
    if glob is not None:
        argv += ["--", glob]
    try:
        result = await run_command(
            argv, cwd=cfg.root, env=git_env(), timeout=cfg.timeout, label="git grep"
        )
    except ToolInputError as exc:
        if "timed out" in str(exc):
            raise ToolInputError(
                f"git grep timed out after {cfg.timeout:g}s; {SEARCH_ADVICE}"
            ) from None
        raise
    none_found = (
        f"No matches for '{pattern[:64]}'" + (f" in {glob}" if glob else "")
        + ". Try a shorter pattern, regex=true, or another glob."
    )
    if result.returncode == 1:
        return none_found
    if result.returncode > 1:
        if regex:
            raise ToolInputError(
                f"invalid regular expression: {_last_line(result.stderr, 'rejected by git grep')}; "
                "fix it or call with regex=false"
            )
        raise _git_error(result, "grep")
    hits: list[str] = []
    files: set[str] = set()
    total = 0
    visible: set[str] | None = None
    allowed: dict[str, bool] = {}
    for line in result.text.split("\n"):
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        if parts[0] not in allowed:
            if visible is None:
                visible = {os.path.normcase(p) for p in await visible_paths()}
            allowed[parts[0]] = (
                not _is_protected_path(parts[0])
                and _resolve_listed(parts[0], visible) is not None
            )
        if not allowed[parts[0]]:
            continue
        total += 1
        files.add(parts[0])
        if len(hits) < MAX_SEARCH_HITS:
            hits.append(f"{parts[0]}:{parts[1]}: {parts[2].strip()[:HIT_TEXT_CHARS]}")
    if total == 0:
        return none_found
    summary = f"{_plural(total, 'match', 'matches')} in {_plural(len(files), 'file', 'files')}"
    if regex:
        summary += " for regex"
    body = "\n".join(hits)
    if total > MAX_SEARCH_HITS:
        body += "\n" + truncation_marker(
            MAX_SEARCH_HITS, total, unit="matches", advice=SEARCH_ADVICE
        )
    return fit_text(summary, body, cfg.max_chars, lower_bound=result.capped, advice=SEARCH_ADVICE)


class _UsageVisitor(ast.NodeVisitor):
    """Collect (line, kind) usages of one name in a Python syntax tree."""

    def __init__(self, symbol: str) -> None:
        self.symbol: str = symbol
        self.found: list[tuple[int, str]] = []
        self._skip: set[int] = set()

    def _add(self, node: ast.AST, kind: str) -> None:
        self.found.append((getattr(node, "lineno", 0), kind))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == self.symbol:
            self._add(node, "def")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name == self.symbol:
            self._add(node, "def")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == self.symbol:
            self._add(node, "class")
        self.generic_visit(node)

    def _visit_aliases(self, node: ast.Import | ast.ImportFrom) -> None:
        for alias in node.names:
            if (alias.asname or alias.name.split(".")[-1]) == self.symbol:
                self._add(node, "import")

    def visit_Import(self, node: ast.Import) -> None:
        self._visit_aliases(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._visit_aliases(node)

    def visit_arg(self, node: ast.arg) -> None:
        if node.arg == self.symbol:
            self._add(node, "param")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (isinstance(func, ast.Name) and func.id == self.symbol) or (
            isinstance(func, ast.Attribute) and func.attr == self.symbol
        ):
            self._add(node, "call")
            self._skip.add(id(func))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == self.symbol and id(node) not in self._skip:
            self._add(node, "assign" if isinstance(node.ctx, ast.Store) else "ref")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == self.symbol and id(node) not in self._skip:
            self._add(node, "assign" if isinstance(node.ctx, ast.Store) else "ref")
        self.generic_visit(node)


USAGE_KINDS: tuple[str, ...] = ("def", "class", "import", "call", "ref", "assign", "param")


@server.tool(structured_output=False)
async def find_usages(symbol: str, path: str | None = None) -> str:
    """LOCAL working copy (not the GitHub remote): find where a Python name is used.

    Parameters: `symbol` is a Python function, class, method or variable name; `path` limits
    the scan to a file or folder. Reports definitions (def, class), imports, calls, plain
    references, assignments and parameters through the Python syntax tree, so comments and
    strings never match. The first line counts usages by kind, then one
    `path:line: kind: source line` per usage. Non-Python files need search_text. `.env*` files,
    `.git` paths and links that leave the root or point at hidden files are skipped.
    """
    cfg = get_config()
    name = symbol.strip().split(".")[-1]
    if not name.isidentifier() or len(name) > 100:
        raise ToolInputError("symbol must be a Python name such as cleanup_chat_caches")
    rel = _normalise_path(path)
    py_files = sorted(
        p for p in await visible_paths(rel) if p.endswith(".py") and not _is_protected_path(p)
    )
    visible: set[str] = {os.path.normcase(p) for p in await visible_paths()}
    started = time.monotonic()
    rows: list[tuple[str, int, str, str]] = []
    skipped = 0
    scanned = 0
    for item in py_files:
        if time.monotonic() - started > USAGES_TIME_BUDGET_S:
            break
        scanned += 1
        await asyncio.sleep(0)
        real = _resolve_listed(item, visible)
        if real is None:
            skipped += 1
            continue
        absolute = os.path.join(cfg.root, real)
        try:
            if os.path.getsize(absolute) > MAX_PARSE_BYTES:
                skipped += 1
                continue
            with open(absolute, "rb") as handle:
                text = decode_text(handle.read())[0]
            tree = ast.parse(text, filename=item)
        except (SyntaxError, ValueError, RecursionError, OSError):
            skipped += 1
            continue
        visitor = _UsageVisitor(name)
        visitor.visit(tree)
        source = split_lines(text)
        for line, kind in visitor.found:
            snippet = source[line - 1].strip()[:USAGE_LINE_CHARS] if 0 < line <= len(source) else ""
            rows.append((item, line, kind, snippet))
    if not rows:
        return (
            f"No usages of {name} found in {_plural(scanned, 'Python file', 'Python files')}. "
            "Use search_text for non-Python files."
        )
    rows.sort(key=lambda row: (row[0], row[1]))
    counts: dict[str, int] = {}
    for _, _, kind, _ in rows:
        counts[kind] = counts.get(kind, 0) + 1
    kinds = ", ".join(f"{k} {counts[k]}" for k in USAGE_KINDS if k in counts)
    files = len({row[0] for row in rows})
    summary = (
        f"{_plural(len(rows), 'usage', 'usages')} of {name} in "
        f"{_plural(files, 'file', 'files')} ({kinds})"
    )
    if skipped:
        summary += f"; {skipped} files skipped (syntax errors, too large or unsafe links)"
    narrow = "Call again with path set to a folder to narrow the scan."
    body_lines = [f"{p}:{n}: {k}: {t}" for p, n, k, t in rows[:MAX_USAGE_HITS]]
    if len(rows) > MAX_USAGE_HITS:
        body_lines.append(
            truncation_marker(MAX_USAGE_HITS, len(rows), unit="usages", advice=narrow)
        )
    if scanned < len(py_files):
        body_lines.append(
            truncation_marker(
                scanned,
                len(py_files),
                unit="Python files",
                advice="Call again with path set to a folder to scan less.",
            )
        )
    return fit_text(summary, "\n".join(body_lines), cfg.max_chars, advice=narrow)


def require_write() -> None:
    """Refuse the call unless the server was started with --allow-write."""
    if not get_config().allow_write:
        raise ToolInputError(
            "writing is disabled: this server was started without --allow-write; "
            "use the project-files entry"
        )


async def is_git_ignored(rel: str) -> bool:
    """Return True when git ignores the path; glob pathspecs stay on because check-ignore needs it."""
    cfg = get_config()
    result = await run_command(
        pathspec_argv(cfg.git) + ["check-ignore", "-q", "--", rel],
        cwd=cfg.root,
        env=git_env(),
        timeout=cfg.timeout,
        label="git check-ignore",
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise _git_error(result, "check-ignore")


async def check_write_target(path: str | None) -> tuple[str, str]:
    """Validate a write path and return (root-relative path, absolute path)."""
    rel = _normalise_path(path)
    if rel is None or rel == ".":
        raise ToolInputError(
            "path is required and must name a file, for example docs/adr/0001-decision.md"
        )
    if _has_git_part(rel):
        raise ToolInputError(
            "writing inside .git is not allowed; choose a path in the working tree"
        )
    for part in rel.split("/"):
        if part.split(".")[0].casefold() in RESERVED_NAMES:
            raise ToolInputError(
                f"'{part}' is a reserved Windows device name; choose another file name"
            )
    if _is_env_name(rel):
        raise ToolInputError(".env files hold secrets and cannot be written; choose another path")
    if await is_git_ignored(rel):
        raise ToolInputError(f"git ignores {rel}; write only files git would track (see .gitignore)")
    absolute = os.path.join(get_config().root, rel)
    if os.path.isdir(absolute):
        raise ToolInputError(f"{rel} is a folder; give a file path inside it")
    return rel, absolute


def unified_diff_text(rel: str, old: str | None, new: str) -> tuple[str, int, int]:
    """Build a unified diff and count added and removed lines."""
    lines = list(
        difflib.unified_diff(
            split_lines(old or ""),
            split_lines(new),
            fromfile="/dev/null" if old is None else f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
            n=3,
        )
    )
    body = lines[2:]
    added = sum(1 for line in body if line.startswith("+"))
    removed = sum(1 for line in body if line.startswith("-"))
    return "\n".join(lines), added, removed


def _write_atomic(abs_path: str, rel: str, text: str, crlf: bool) -> None:
    """Write UTF-8 text through a temporary file in the same folder and os.replace."""
    parent = os.path.dirname(abs_path)
    data = (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")
    tmp_name: str | None = None
    try:
        os.makedirs(parent, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=parent, prefix=".pg-write-", delete=False) as tmp:
            tmp_name = tmp.name
            tmp.write(data)
            tmp.flush()
        os.replace(tmp_name, abs_path)
    except OSError as exc:
        if tmp_name is not None and os.path.exists(tmp_name):
            with contextlib.suppress(OSError):
                os.remove(tmp_name)
        raise ToolInputError(f"could not write {rel}: {exc.strerror or exc}") from None


def _check_content(text: str) -> None:
    """Refuse binary-looking or oversized content."""
    if "\x00" in text:
        raise ToolInputError("content contains NUL bytes; binary files cannot be written")
    if len(text.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ToolInputError(
            "content is larger than 200000 bytes; split the file or write a shorter one"
        )


def _read_existing(abs_path: str, rel: str) -> tuple[str, bool] | None:
    """Return (LF text, had CRLF) of an existing text file, or None when it does not exist."""
    try:
        with open(abs_path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ToolInputError(f"could not read {rel}: {exc.strerror or exc}") from None
    if is_binary(raw):
        raise ToolInputError(f"{rel} is a binary file and cannot be changed")
    return decode_text(raw)


def _result(rel: str, verb: str, old: str | None, new: str) -> str:
    """Compose the summary line plus diff for a finished write."""
    diff, added, removed = unified_diff_text(rel, old, new)
    return fit_text(
        f"{verb} {rel} (+{added} -{removed})", diff, get_config().max_chars, advice=WRITE_ADVICE
    )


async def write_file(path: str, content: str) -> str:
    """LOCAL working copy (not the GitHub remote): create a file, or fully rewrite one, inside the repository and save it immediately.

    Parameters: `path` is a file relative to the repository root (missing folders are
    created); `content` is the complete new text of the file. The result is a summary line
    "Created <path> (+A -R)" or "Updated <path> (+A -R)" followed by the unified diff. For a
    partial change of an existing file use `edit_file` instead. Paths inside `.git`, `.env*`
    files and paths that git ignores are refused.
    After all changes are done, list every file you created or changed in your final answer.
    """
    require_write()
    rel, absolute = await check_write_target(path)
    new = content.replace("\r\n", "\n")
    _check_content(new)
    existing = _read_existing(absolute, rel)
    old = existing[0] if existing else None
    if existing is not None and old == new:
        return f"No changes: {rel} already has this content"
    _write_atomic(absolute, rel, new, existing[1] if existing else False)
    return _result(rel, "Updated" if existing is not None else "Created", old, new)


def _not_found_reason(rel: str, text: str, old: str) -> str:
    """Explain a zero-match edit and hint where the first line of old_text appears."""
    first = next((line.strip() for line in old.split("\n") if line.strip()), "")
    if first:
        for number, line in enumerate(text.split("\n"), start=1):
            if first in line:
                return (
                    f"old_text was not found in {rel} (its first line appears at line {number}; "
                    "check indentation and copy the lines exactly from read_file)"
                )
    return f"old_text was not found in {rel} (call read_file and copy the lines exactly)"


async def edit_file(path: str, old_text: str, new_text: str) -> str:
    """LOCAL working copy (not the GitHub remote): replace one exact fragment of an existing text file and save it immediately.

    Call read_file first and copy `old_text` exactly, without the line-number prefix.
    `old_text` must occur exactly once in the file; include surrounding lines to make it
    unique. `new_text` replaces it. The result is a summary line "Updated <path> (+A -R)"
    followed by the unified diff.
    After all changes are done, list every file you created or changed in your final answer.
    """
    require_write()
    rel, absolute = await check_write_target(path)
    existing = _read_existing(absolute, rel)
    if existing is None:
        raise ToolInputError(f"{rel} does not exist; use write_file to create it")
    if not old_text:
        raise ToolInputError(
            "old_text must not be empty; copy the exact lines to replace from read_file"
        )
    old = old_text.replace("\r\n", "\n")
    new = new_text.replace("\r\n", "\n")
    if old == new:
        raise ToolInputError("old_text and new_text are identical; nothing to change")
    text, crlf = existing
    count = text.count(old)
    if count == 0:
        raise ToolInputError(_not_found_reason(rel, text, old))
    if count > 1:
        starts: list[int] = []
        index = text.find(old)
        while index != -1 and len(starts) < 10:
            starts.append(text.count("\n", 0, index) + 1)
            index = text.find(old, index + 1)
        raise ToolInputError(
            f"old_text occurs {count} times in {rel} (lines {', '.join(map(str, starts))}); "
            "add surrounding lines so it is unique"
        )
    result = text.replace(old, new, 1)
    _check_content(result)
    _write_atomic(absolute, rel, result, crlf)
    return _result(rel, "Updated", text, result)


async def _optional_line(args: list[str]) -> str | None:
    """Run a git command and return its trimmed output, or None when it fails or is empty."""
    result = await run_git(args)
    text = result.text.strip()
    return text if result.returncode == 0 and text else None


async def _remote_tags() -> tuple[list[str] | None, str | None]:
    """List tag names on origin; on failure return (None, reason)."""
    cfg = get_config()
    try:
        result = await run_command(
            base_argv(cfg.git) + ["ls-remote", "--refs", "origin", "refs/tags/*"],
            cwd=cfg.root,
            env=git_env(),
            timeout=RELEASE_NETWORK_TIMEOUT,
            label="git ls-remote",
        )
    except ToolInputError as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, _last_line(result.stderr, f"git ls-remote exited with {result.returncode}")
    prefix = "refs/tags/"
    names = [
        line.split("\t", 1)[1][len(prefix):]
        for line in result.text.splitlines()
        if "\t" in line and line.split("\t", 1)[1].startswith(prefix)
    ]
    return names, None


async def release_context() -> str:
    """LOCAL working copy (not the GitHub remote): read what a release needs.

    Takes no parameters. Line 1 names the branch; line 2 is a JSON object with branch, head,
    origin_url, local_tags, merged_tags (tags reachable from HEAD), remote_tags (tags on origin,
    null when origin cannot be reached, with remote_error) and dirty (changed and untracked files).
    """
    branch = await _optional_line(["symbolic-ref", "--short", "-q", "HEAD"])
    head = await run_git(["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0:
        raise _git_error(head, "rev-parse")
    status = await run_git(
        ["-c", "core.quotePath=false", "status", "--porcelain=v1", "--branch",
         "--untracked-files=all"]
    )  # fmt: skip
    if status.returncode != 0:
        raise _git_error(status, "status")
    local = await run_git(["tag", "--list"])
    merged = await run_git(["tag", "--merged", "HEAD"])
    remote_tags, remote_error = await _remote_tags()
    info = {
        "branch": branch,
        "head": head.text.strip(),
        "origin_url": await _optional_line(["remote", "get-url", "origin"]),
        "local_tags": local.text.split(),
        "merged_tags": merged.text.split(),
        "remote_tags": remote_tags,
        "remote_error": remote_error,
        "dirty": [line for line in status.text.splitlines() if line and not line.startswith("##")],
    }
    return f"Release context for {branch or 'detached HEAD'}\n" + json.dumps(
        info, ensure_ascii=False
    )


def _commit_row(record: str) -> dict[str, object]:
    """Turn one git log record (header line plus file paths) into the noise-filter row."""
    head, _, rest = record.partition("\n")
    sha, parents, subject = (head.split("\t", 2) + ["", ""])[:3]
    files = [line for line in rest.split("\n") if line.strip()]
    dirs = sorted({path.split("/", 1)[0] if "/" in path else "." for path in files})
    return {
        "sha": sha.strip(),
        "parents": len(parents.split()),
        "subject": subject,
        "dirs": dirs[:MAX_COMMIT_DIRS],
        "planning_only": bool(files) and all(path.startswith(".planning/") for path in files),
        "files_n": len(files),
    }


async def release_range_commits(range: str, page: int = 1) -> str:  # noqa: A002
    """LOCAL working copy (not the GitHub remote): list the commits of an A..B range in pages of 50.

    Parameters: `range` looks like v4.0..HEAD; `page` starts at 1. Line 1 is a summary, then one
    JSON object per commit (sha, parents, subject, dirs, planning_only, files_n) and a final
    {"next_page", "total"} line; call again with next_page while it is not null.
    """
    if range.count("..") != 1 or "..." in range:
        raise ToolInputError("range must look like v4.0..HEAD")
    if page < 1 or page > MAX_RELEASE_PAGES:
        raise ToolInputError(f"page must be between 1 and {MAX_RELEASE_PAGES}")
    left_raw, _, right_raw = range.partition("..")
    if not left_raw or not right_raw:
        raise ToolInputError("range must look like v4.0..HEAD")
    span = await resolve_ref(left_raw) + ".." + await resolve_ref(right_raw)
    counted = await run_git(["rev-list", "--count", span])
    if counted.returncode != 0 or not counted.text.strip().isdigit():
        raise _git_error(counted, "rev-list")
    total = int(counted.text.strip())
    skip = (page - 1) * RELEASE_PAGE_SIZE
    result = await run_git(
        ["log", "--no-color", "--no-show-signature", "--no-renames", "--name-only",
         "--format=%x1e%H%x09%P%x09%s", f"--skip={skip}", f"--max-count={RELEASE_PAGE_SIZE}", span]
    )  # fmt: skip
    if result.returncode != 0:
        raise _git_error(result, "log")
    rows = [_commit_row(rec) for rec in result.text.split("\x1e") if rec.strip()]
    first = skip + 1 if rows else 0
    summary = f"Commits {first}-{skip + len(rows)} of {total} in {range}"
    more = skip + len(rows) < total
    tail = json.dumps({"next_page": page + 1 if more else None, "total": total})
    text = "\n".join([summary, *(json.dumps(r, ensure_ascii=False) for r in rows), tail])
    if len(text) > get_config().max_chars:
        raise ToolInputError("page is too large; this should not happen with 50 commits")
    return text


def _check_version(version: str) -> None:
    """Require a tag name such as v1.2."""
    if not _VERSION_TAG.fullmatch(version):
        raise ToolInputError("version must look like v1.2 (letter v, major, dot, minor)")


def _insert_section(existing: str, section: str) -> str:
    """Put the new section above the first existing release section, or after the header."""
    block = section.rstrip("\n") + "\n\n"
    lines = existing.split("\n")
    for index, line in enumerate(lines):
        if line.startswith("## ["):
            return "\n".join(lines[:index]) + ("\n" if index else "") + block + "\n".join(lines[index:])
    return existing.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"


async def release_commit_changelog(version: str, section: str) -> str:
    """LOCAL working copy: prepend a release section to CHANGELOG.md and commit only that file.

    Parameters: `version` such as v1.2; `section` is the full Markdown block starting with
    "## [v1.2] - <date>". Refuses when other files are staged or the version is already listed.
    Returns the commit hash as JSON on the second line.
    """
    _check_version(version)
    if not section.startswith(f"## [{version}] - "):
        raise ToolInputError(f'section must start with "## [{version}] - <date>"')
    if "\x00" in section or len(section) > MAX_SECTION_CHARS:
        raise ToolInputError("section contains a NUL byte or is larger than 100000 characters")
    cfg = get_config()
    rel = resolve_inside_root(cfg.root, "CHANGELOG.md")
    path = os.path.join(cfg.root, rel)
    staged = await run_git(["diff", "--cached", "--name-only"])
    if staged.returncode != 0:
        raise _git_error(staged, "diff")
    others = [name for name in staged.text.split() if name != "CHANGELOG.md"]
    if others:
        raise ToolInputError(
            f"other files are staged ({', '.join(others[:5])}); unstage them before the release commit"
        )
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            existing = handle.read().replace("\r\n", "\n")
    except FileNotFoundError:
        existing = CHANGELOG_HEADER
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolInputError(f"could not read CHANGELOG.md: {exc}") from None
    if f"## [{version}]" in existing:
        raise ToolInputError(f"CHANGELOG.md already has a section for {version}")
    updated = _insert_section(existing, section.replace("\r\n", "\n"))
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
    except OSError as exc:
        raise ToolInputError(f"could not write CHANGELOG.md: {exc.strerror or exc}") from None
    added = await run_git(["add", "--", "CHANGELOG.md"])
    if added.returncode != 0:
        raise _git_error(added, "add")
    committed = await run_git(
        ["commit", "-m", f"docs(release): {version} changelog", "--", "CHANGELOG.md"]
    )
    if committed.returncode != 0:
        raise _git_error(committed, "commit")
    head = await run_git(["rev-parse", "HEAD"])
    return f"Committed CHANGELOG.md for {version}\n" + json.dumps({"commit": head.text.strip()})


async def release_create_tag(version: str, message: str, commit: str) -> str:
    """LOCAL working copy: create an annotated tag on a commit; refuses an existing tag.

    Parameters: `version` such as v1.2; `message` is a one-line tag message; `commit` is the hash.
    """
    _check_version(version)
    if not message.strip() or "\r" in message or "\n" in message:
        raise ToolInputError("message must be one non-empty line")
    if len(message) > MAX_TAG_MESSAGE_CHARS:
        raise ToolInputError(f"message must be at most {MAX_TAG_MESSAGE_CHARS} characters")
    sha = await resolve_ref(commit)
    listed = await run_git(["tag", "--list", "--", version])
    if listed.text.strip():
        raise ToolInputError(f"tag {version} already exists; choose a higher version")
    created = await run_git(["tag", "-a", version, "-m", message, sha])
    if created.returncode != 0:
        raise _git_error(created, "tag")
    return f"Created annotated tag {version} at {sha[:7]}\n" + json.dumps(
        {"tag": version, "commit": sha}
    )


async def release_push(branch: str, tag: str) -> str:
    """LOCAL working copy to origin: push one branch and one tag with explicit refspecs.

    Parameters: `branch` is a plain branch name; `tag` is an existing local tag such as v1.2.
    A rejected push (for example non-fast-forward) is reported and never retried.
    """
    check_ref_syntax(branch)
    if (
        branch.startswith(("refs/", "-"))
        or any(ch in branch for ch in ":+*?[~^")
        or ".." in branch
    ):
        raise ToolInputError("branch must be a plain branch name such as main")
    checked = await run_git(["check-ref-format", "--branch", branch])
    if checked.returncode != 0:
        raise ToolInputError(f"'{branch}' is not a valid branch name")
    _check_version(tag)
    listed = await run_git(["tag", "--list", "--", tag])
    if not listed.text.strip():
        raise ToolInputError(f"tag {tag} does not exist locally; create it first")
    cfg = get_config()
    result = await run_command(
        base_argv(cfg.git)
        + [
            "push", "--porcelain", "origin",
            f"refs/heads/{branch}:refs/heads/{branch}",
            f"refs/tags/{tag}:refs/tags/{tag}",
        ],  # fmt: skip
        cwd=cfg.root,
        env=git_env(),
        timeout=RELEASE_NETWORK_TIMEOUT,
        label="git push",
    )
    if result.returncode != 0:
        raise _git_error(result, "push")
    return f"Pushed {branch} and {tag} to origin"


def _github_failure(status: int, owner: str, repo: str, token: str, payload: object) -> ToolInputError:
    """Map a GitHub error status to an English reason without echoing credentials."""
    if status == 401:
        return ToolInputError("GitHub rejected the token (401)")
    if status in (403, 404):
        return ToolInputError(
            f"no write access to {owner}/{repo} ({status}): the token needs Contents: write"
        )
    if status == 422:
        message = payload.get("message") if isinstance(payload, dict) else None
        text = str(message) if message else "unknown"
        return ToolInputError(
            f"GitHub validation failed (422): {text.replace(token, '***')[:300]}"
        )
    return ToolInputError(f"GitHub answered {status}")


def _json_or_none(response: httpx.Response) -> object:
    """Decode a JSON body, or None when it is not JSON."""
    try:
        return response.json()
    except ValueError:
        return None


def _release_answer(verb: str, item: dict[str, object]) -> str:
    """Compose the result text for a draft release."""
    return f"{verb}: {item.get('html_url')}\n" + json.dumps(
        {"html_url": item.get("html_url"), "id": item.get("id"), "draft": True}
    )


async def github_create_draft_release(
    owner: str, repo: str, tag: str, name: str, body: str, commit: str
) -> str:
    """GitHub REMOTE: create a DRAFT release for an existing tag. Never publishes.

    Parameters: `owner` and `repo` name the GitHub repository; `tag` such as v1.2; `name` is the
    one-line title; `body` is the Markdown notes; `commit` is the 40-character hash the tag points
    at. A draft that already exists for the tag is returned instead of a duplicate.
    """
    for label, value in (("owner", owner), ("repo", repo)):
        if not _GITHUB_NAME.fullmatch(value) or value in (".", ".."):
            raise ToolInputError(f"{label} must be a plain GitHub name such as octocat")
    _check_version(tag)
    if not name.strip() or "\r" in name or "\n" in name or len(name) > MAX_RELEASE_NAME_CHARS:
        raise ToolInputError("name must be one non-empty line of at most 200 characters")
    if len(body) > MAX_RELEASE_BODY_CHARS:
        raise ToolInputError("body is larger than 125000 characters; shorten the release notes")
    if not _FULL_SHA.fullmatch(commit):
        raise ToolInputError("commit must be a full 40-character lowercase hash")
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    if not token:
        raise ToolInputError(
            f"{GITHUB_TOKEN_ENV} is not set in this server's environment; "
            "add it in the MCP server form"
        )
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
    url = f"{GITHUB_API}/repos/{owner}/{repo}/releases"
    try:
        async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT) as client:
            listed = await client.get(url, params={"per_page": 100}, headers=headers)
            if listed.status_code in (401, 403, 404):
                raise _github_failure(listed.status_code, owner, repo, token, None)
            if listed.status_code == 200:
                items = _json_or_none(listed)
                for item in items if isinstance(items, list) else []:
                    if isinstance(item, dict) and item.get("tag_name") == tag:
                        return _release_answer("Draft release already exists", item)
            created = await client.post(
                url,
                headers=headers,
                json={
                    "tag_name": tag,
                    "target_commitish": commit,
                    "name": name,
                    "body": body,
                    "draft": True,
                    "prerelease": False,
                },
            )
    except httpx.HTTPError as exc:
        raise ToolInputError(f"GitHub request failed: {type(exc).__name__}") from None
    payload = _json_or_none(created)
    if created.status_code == 201 and isinstance(payload, dict):
        return _release_answer("Draft release created", payload)
    raise _github_failure(created.status_code, owner, repo, token, payload)


def register_release_tools() -> None:
    """Expose the release tools; called only when the server starts with --allow-release."""
    global _release_registered
    if _release_registered:
        return
    for tool in (
        release_context,
        release_range_commits,
        release_commit_changelog,
        release_create_tag,
        release_push,
        github_create_draft_release,
    ):
        server.tool(structured_output=False)(tool)
    _release_registered = True


def check_work_tree(git: str, root: str, timeout: float) -> None:
    """Stop the server unless root is inside a git working tree; never runs git init."""
    try:
        proc = subprocess.run(
            base_argv(git) + ["rev-parse", "--is-inside-work-tree"],
            cwd=root,
            env=git_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail(f"git did not answer within {timeout:g}s while checking {root}")
    except OSError as exc:
        fail(f"git could not be started: {exc}")
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode == 0 and out == "true":
        return
    if proc.returncode == 0 and out == "false":
        fail(f"{root} has no working tree (bare repository)")
    if "not a git repository" in err:
        fail(f"{root} is not a git repository. Run 'git init' in that folder to create one.")
    last = err.splitlines()[-1] if err else f"git exited with code {proc.returncode}"
    fail(f"git refused to use {root}: {last}")


def main() -> None:
    """Validate launch arguments and the repository, then serve over stdio."""
    args = parse_args()
    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        fail(f"repository root does not exist or is not a folder: {root}")
    git = shutil.which("git")
    if git is None:
        fail("git was not found in PATH; install Git for Windows and restart the app")
    check_work_tree(git, root, args.timeout)
    if args.max_chars > APP_RESULT_CAP:
        sys.stderr.write(
            f"warning: --max-chars={args.max_chars} exceeds the app limit of "
            f"{APP_RESULT_CAP} characters; the app will cut tool results at {APP_RESULT_CAP}\n"
        )
        sys.stderr.flush()
    configure(
        root,
        git,
        args.max_chars,
        args.timeout,
        allow_write=args.allow_write,
        allow_release=args.allow_release,
    )
    if args.allow_release:
        register_release_tools()
    if args.allow_write:
        server.add_tool(write_file, structured_output=False)
        server.add_tool(edit_file, structured_output=False)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
