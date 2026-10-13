"""Read-only git MCP server for one repository, served over stdio.

Usage:
    python mcp_servers/project_git.py --root=<repository> [--max-chars=18000] [--timeout=10]

This file is self-contained on purpose. It does not use the application logger or
settings modules from the shared package: that logger writes JSON to stdout, and stdout is
the MCP protocol channel here. Diagnostics go to stderr through ``sys.stderr.write``. This
is the documented exception to the project's logging rule, so the file has no print calls.
"""

import argparse
import asyncio
import contextlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import NoReturn

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
_COMMIT_ID = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


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


_config: ServerConfig | None = None

server = FastMCP("local-git", log_level="WARNING")


def configure(root: str, git: str, max_chars: int, timeout: float) -> None:
    """Store the validated launch settings for the tools."""
    global _config
    _config = ServerConfig(root=root, git=git, max_chars=max_chars, timeout=timeout)


def get_config() -> ServerConfig:
    """Return the launch settings or fail when the server is not configured."""
    if _config is None:
        raise RuntimeError("server is not configured")
    return _config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate the launch arguments."""
    parser = argparse.ArgumentParser(
        prog="project_git.py",
        description="Read-only git MCP server over stdio.",
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
    configure(root, git, args.max_chars, args.timeout)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
