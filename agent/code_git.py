"""Read-only git access to a local repository for the code index: fixed commands, no shell."""

import asyncio
import contextlib
import os
import re
import shutil
from dataclasses import dataclass

import psutil

from shared.logger import get_logger

logger = get_logger(__name__)

GIT_TIMEOUT: float = 30.0
FETCH_TIMEOUT: float = 120.0
MAX_REF_LENGTH: int = 255

MSG_PATH_EMPTY = "Путь к репозиторию не задан."
MSG_PATH_MISSING = "Папка не найдена: {path}."
MSG_NOT_A_REPO = "Папка не является git-репозиторием: {path}."
MSG_NO_ORIGIN = "У репозитория нет удалённого origin."
MSG_BAD_REF = "Недопустимое имя ветки."
MSG_FETCH_FAILED = "Не удалось получить ветку {ref} из origin."
MSG_TIMEOUT = "Команда git не ответила вовремя."
MSG_OBJECTS = "Не удалось прочитать объекты git."
MSG_NO_GIT = "Команда git не найдена."

_COMMIT_ID = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
_REF_CHARS = re.compile(r"[A-Za-z0-9._/+@-]+")
_REGULAR_MODES = frozenset({"100644", "100755"})
_URL_SPLIT = re.compile(r"[/:\\]+")

_fetch_locks: dict[str, asyncio.Lock] = {}


class GitError(Exception):
    """A git read failed; `detail` is a readable Russian reason free of URLs and secrets."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class TreeEntry:
    """One regular file of a commit tree."""

    path: str
    blob_id: str
    size: int


@dataclass(frozen=True)
class _Result:
    """Outcome of one git process."""

    returncode: int
    stdout: bytes


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


def base_argv(git: str, top: str | None) -> list[str]:
    """Return the fixed leading arguments for every git command."""
    argv = [git, "--no-pager", "--literal-pathspecs"]
    if top is not None:
        argv += ["-C", top]
    return argv + ["-c", "core.quotepath=off", "-c", "core.fsmonitor=false", "-c", "color.ui=false"]


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


def _find_git() -> str:
    """Locate the git executable."""
    found = shutil.which("git")
    if found is None:
        raise GitError(MSG_NO_GIT)
    return found


async def _run(
    args: list[str],
    *,
    top: str | None,
    timeout: float | None = None,
    stdin: bytes | None = None,
    cwd: str | None = None,
) -> _Result:
    """Run one fixed git command without a shell, bounded in time."""
    argv = base_argv(_find_git(), top) + args
    limit = GIT_TIMEOUT if timeout is None else timeout
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
            env=git_env(),
        )
    except OSError as exc:
        logger.error("code_git_failed", command=args[0], returncode=-1, error=type(exc).__name__)
        raise GitError(MSG_NO_GIT) from exc
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), limit)
    except TimeoutError:
        _kill_tree(proc.pid)
        await proc.wait()
        logger.error("code_git_failed", command=args[0], returncode=-1, error="timeout")
        raise GitError(MSG_TIMEOUT) from None
    except asyncio.CancelledError:
        _kill_tree(proc.pid)
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
    code = proc.returncode if proc.returncode is not None else -1
    return _Result(returncode=code, stdout=out)


def _text(result: _Result) -> str:
    """Decode stdout as UTF-8."""
    return result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")


def _fail(command: str, result: _Result, detail: str) -> GitError:
    """Log a failed command without its output and build the user-facing error."""
    logger.error("code_git_failed", command=command, returncode=result.returncode)
    return GitError(detail)


async def validate_repo_path(path: str) -> str:
    """Return the top-level directory of the work tree at `path`; raise GitError otherwise."""
    if not path or not path.strip():
        raise GitError(MSG_PATH_EMPTY)
    if not os.path.isabs(path) or any(ch in path for ch in "\x00\r\n"):
        raise GitError(MSG_PATH_MISSING.format(path=path.replace("\x00", "")[:200]))
    if not os.path.isdir(path):
        raise GitError(MSG_PATH_MISSING.format(path=path))
    result = await _run(["rev-parse", "--show-toplevel"], top=path)
    top = _text(result).strip()
    if result.returncode != 0 or not top:
        raise GitError(MSG_NOT_A_REPO.format(path=path))
    return top


def _parse_origin(url: str) -> str:
    """Return the lower-case `owner/name` from the tail of a remote URL."""
    cleaned = url.strip().rstrip("/\\")
    if cleaned.lower().endswith(".git"):
        cleaned = cleaned[:-4]
    parts = [part for part in _URL_SPLIT.split(cleaned) if part]
    if len(parts) < 2:
        raise GitError(MSG_NO_ORIGIN)
    return f"{parts[-2]}/{parts[-1]}".lower()


async def repo_key(path: str) -> str:
    """Return the lower-case `owner/name` of the repository's `origin` remote."""
    top = await validate_repo_path(path)
    result = await _run(["remote", "get-url", "origin"], top=top)
    url = _text(result).strip()
    if result.returncode != 0 or not url:
        raise GitError(MSG_NO_ORIGIN)
    return _parse_origin(url)


def check_base_ref(ref: str) -> None:
    """Reject a branch name that could be read as an option or is not a valid branch."""
    bad = (
        not ref
        or len(ref) > MAX_REF_LENGTH
        or ref.startswith(("-", "/", "."))
        or ref.endswith(("/", ".", ".lock"))
        or ".." in ref
        or "//" in ref
        or "/." in ref
        or ref == "@"
        or _REF_CHARS.fullmatch(ref) is None
    )
    if bad:
        raise GitError(MSG_BAD_REF)


async def fetch_base_commit(path: str, base_ref: str) -> str:
    """Fetch the branch from origin and return its tip commit (40 or 64 hex characters)."""
    check_base_ref(base_ref)
    top = await validate_repo_path(path)
    key = os.path.normcase(top)
    lock = _fetch_locks.setdefault(key, asyncio.Lock())
    # FETCH_HEAD is one file per repository, so fetch and rev-parse stay together.
    async with lock:
        fetched = await _run(
            ["fetch", "--no-tags", "--quiet", "origin", f"refs/heads/{base_ref}"],
            top=top,
            timeout=FETCH_TIMEOUT,
        )
        if fetched.returncode != 0:
            raise _fail("fetch", fetched, MSG_FETCH_FAILED.format(ref=base_ref))
        parsed = await _run(
            ["rev-parse", "--verify", "--end-of-options", "FETCH_HEAD^{commit}"], top=top
        )
    commit = _text(parsed).strip()
    if parsed.returncode != 0 or not _COMMIT_ID.fullmatch(commit):
        raise _fail("rev-parse", parsed, MSG_FETCH_FAILED.format(ref=base_ref))
    return commit


async def current_branch(path: str) -> str | None:
    """Return the checked-out branch name, or None on a detached head."""
    top = await validate_repo_path(path)
    result = await _run(["symbolic-ref", "--short", "-q", "HEAD"], top=top)
    name = _text(result).strip()
    if result.returncode != 0 or not name:
        return None
    return name


async def list_tree(path: str, commit: str) -> list[TreeEntry]:
    """List the regular files of a commit with blob ids and sizes."""
    if not _COMMIT_ID.fullmatch(commit):
        raise GitError(MSG_OBJECTS)
    top = await validate_repo_path(path)
    result = await _run(["ls-tree", "-r", "-z", "-l", commit], top=top)
    if result.returncode != 0:
        raise _fail("ls-tree", result, MSG_OBJECTS)
    entries: list[TreeEntry] = []
    for raw in result.stdout.split(b"\x00"):
        if not raw:
            continue
        meta, _, name = raw.decode("utf-8", errors="replace").partition("\t")
        fields = meta.split()
        if len(fields) != 4 or not name:
            continue
        mode, kind, blob_id, size = fields
        if kind != "blob" or mode not in _REGULAR_MODES or not size.isdigit():
            continue
        entries.append(TreeEntry(path=name, blob_id=blob_id, size=int(size)))
    return entries


async def read_blobs(path: str, blob_ids: list[str]) -> dict[str, bytes]:
    """Return the exact bytes of each requested blob; an unknown id raises GitError."""
    unique = list(dict.fromkeys(blob_ids))
    if not unique:
        return {}
    if not all(_COMMIT_ID.fullmatch(blob_id) for blob_id in unique):
        raise GitError(MSG_OBJECTS)
    top = await validate_repo_path(path)
    payload = ("\n".join(unique) + "\n").encode("ascii")
    result = await _run(["cat-file", "--batch"], top=top, stdin=payload)
    if result.returncode != 0:
        raise _fail("cat-file", result, MSG_OBJECTS)
    return _parse_batch(result.stdout, unique)


def _parse_batch(data: bytes, wanted: list[str]) -> dict[str, bytes]:
    """Parse `cat-file --batch` output: header line, `size` bytes, newline, per object."""
    found: dict[str, bytes] = {}
    position = 0
    for blob_id in wanted:
        end = data.find(b"\n", position)
        if end < 0:
            raise GitError(MSG_OBJECTS)
        fields = data[position:end].decode("ascii", errors="replace").split()
        position = end + 1
        if len(fields) != 3 or fields[1] != "blob" or not fields[2].isdigit():
            logger.error("code_git_failed", command="cat-file", returncode=0)
            raise GitError(MSG_OBJECTS)
        size = int(fields[2])
        if position + size > len(data):
            raise GitError(MSG_OBJECTS)
        found[blob_id] = data[position : position + size]
        position += size + 1
    return found
