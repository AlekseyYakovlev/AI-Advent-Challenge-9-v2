"""Control run of the PR review on a frozen set of real Pull Requests.

Commands: prepare-docs, open, wait, collect, rerun, close, status.

Secrets come from the environment only:

    PR_REVIEW_API_KEY             API key of the app (prepare-docs, collect)
    GITHUB_PERSONAL_ACCESS_TOKEN  GitHub REST token (optional; without it the
                                  script prints compare URLs and asks for manual steps)

Exit codes: 0 done, 1 failed, 3 not run (a precondition is missing),
4 a manual step is needed (instructions are printed).

Git work happens in a temporary detached worktree outside the repository, and
only refs under refs/heads/review-test/ are ever pushed. The script never
changes the working tree, never deletes a branch and never merges a PR.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pr_review_ci import BOT_LOGIN, MARKER, SECTION_TITLES  # noqa: E402

NOT_RUN = "не запускалось"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_RUN = 3
EXIT_MANUAL = 4

DEFAULT_MANIFEST = "tests/fixtures/review/control_set.json"
DEFAULT_OUT = "eval_out/day32"
DEFAULT_APP_URL = "http://127.0.0.1:8001"
DEFAULT_GITHUB_API = "https://api.github.com"
PUSH_REF_PREFIX = "refs/heads/review-test/"
POLL_SECONDS = 20.0
KB_READY_TIMEOUT_SECONDS = 900.0
HTTP_TIMEOUT_SECONDS = 60.0
RUNNER_OFFLINE_REASON = "задача не стартовала (раннер не в сети?)"
MSG_APP_DOWN = f"{NOT_RUN}: приложение недоступно"
MSG_NO_API_KEY = f"{NOT_RUN}: не задан PR_REVIEW_API_KEY"
MSG_NO_DOCS_KB = f"{NOT_RUN}: база документации не выбрана"
MSG_NO_TOKEN = f"{NOT_RUN}: не задан GITHUB_PERSONAL_ACCESS_TOKEN"

# Git sub-commands the script may run; anything else is refused.
ALLOWED_GIT = frozenset(
    {"fetch", "worktree", "add", "write-tree", "commit-tree", "rev-parse", "push", "show"}
)
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
STRIPPED_GIT_ENV = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY")

_SECRETS: list[str] = []
_COMMIT_RE = re.compile(r"Коммит: `([0-9a-fA-F]{7,40})`")
_MODEL_RE = re.compile(r"Модель: `([^`]*)`")


class ControlError(Exception):
    """A step cannot continue; `exit_code` says how the script ends."""

    def __init__(self, message: str, exit_code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def make_client() -> httpx.Client:
    """Create an HTTP client without proxies; tests replace this factory."""
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, trust_env=False)


client_factory: Callable[[], httpx.Client] = make_client
sleep: Callable[[float], None] = time.sleep
clock: Callable[[], float] = time.monotonic


def register_secrets() -> None:
    """Remember the secret values so no output line or file can contain them."""
    for name in ("PR_REVIEW_API_KEY", "GITHUB_PERSONAL_ACCESS_TOKEN"):
        value = os.environ.get(name, "")
        if value and value not in _SECRETS:
            _SECRETS.append(value)


def scrub(text: str) -> str:
    """Remove every known secret from a piece of text."""
    for secret in _SECRETS:
        text = text.replace(secret, "[секрет]")
    return text


def say(message: str) -> None:
    """Print one console line without secrets."""
    print(scrub(message), flush=True)


def now_iso() -> str:
    """Current UTC time as an ISO string."""
    return datetime.now(timezone.utc).isoformat()


def write_file(path: Path, text: str) -> None:
    """Write a UTF-8 file with scrubbed content, creating parent folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scrub(text), encoding="utf-8", newline="\n")


def write_json(path: Path, data: Any) -> None:
    """Write a JSON file."""
    write_file(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any) -> Any:
    """Read a JSON file, or return the default when it does not exist."""
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# --- git -------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    """Environment for git: no repository overrides, no prompts, an identity if none is set."""
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_GIT_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    configured = subprocess.run(
        ["git", "config", "user.name"], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    if not configured.stdout.strip():
        env.setdefault("GIT_AUTHOR_NAME", "review-control")
        env.setdefault("GIT_AUTHOR_EMAIL", "review-control@localhost")
        env.setdefault("GIT_COMMITTER_NAME", "review-control")
        env.setdefault("GIT_COMMITTER_EMAIL", "review-control@localhost")
    return env


def git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run an allowed git command with a fixed argument list and no shell."""
    if not args or args[0] not in ALLOWED_GIT:
        raise ControlError(f"запрещённая команда git: {args[:1]}")
    if args[0] == "push":
        refspec = args[-1]
        if ":" not in refspec or not refspec.rsplit(":", 1)[1].startswith(PUSH_REF_PREFIX):
            raise ControlError("push разрешён только в refs/heads/review-test/")
        if any(a.startswith("-") and a != "--quiet" for a in args[1:-1]):
            raise ControlError("опции push не разрешены")
    done = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=cwd,
        env=_git_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
    )
    if check and done.returncode != 0:
        raise ControlError(scrub(f"git {args[0]}: {done.stderr.strip()[:300]}"))
    return done


def build_pr_tree(entry: dict[str, Any], read_base_file: Callable[[str], str]) -> dict[str, str]:
    """Return path -> text of a PR: fixture files as they are, copies with edits applied."""
    tree: dict[str, str] = {}
    for item in entry["files"]:
        tree[item["path"]] = Path(item["source"]).read_text(encoding="utf-8")
    for copy in entry["copies"]:
        text = read_base_file(copy["from"])
        for edit in copy["edits"]:
            if text.count(edit["find"]) != 1:
                raise ControlError(
                    f"правка {copy['path']}: строка найдена {text.count(edit['find'])} раз"
                )
            text = text.replace(edit["find"], edit["replace"])
        tree[copy["path"]] = text
    return tree


def _write_tree(root: Path, tree: dict[str, str]) -> None:
    """Write the PR files into a worktree."""
    for path, text in tree.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))


def _remote_branch_head(temp: Path, remote: str, branch: str) -> str | None:
    """Return the head commit of a branch on the remote, or None when it does not exist."""
    done = git(["fetch", "--quiet", remote, f"refs/heads/{branch}"], temp, check=False)
    if done.returncode != 0:
        return None
    return git(["rev-parse", "FETCH_HEAD"], temp).stdout.strip()


def push_branch(repo: Path, remote: str, base: str, entry: dict[str, Any], branch: str) -> str:
    """Create or update a test branch from the base tip; return its head commit."""
    git(["fetch", "--quiet", remote, base], repo)
    base_sha = git(["rev-parse", f"{remote}/{base}"], repo).stdout.strip()

    def read_base(path: str) -> str:
        shown = git(["show", f"{base_sha}:{path}"], repo)
        return shown.stdout.replace("\r\n", "\n")

    tree = build_pr_tree(entry, read_base)
    parent_dir = Path(tempfile.mkdtemp(prefix="review-control-"))
    temp = parent_dir / "wt"
    git(["worktree", "add", "--detach", str(temp), base_sha], repo)
    try:
        _write_tree(temp, tree)
        git(["add", "-A"], temp)
        tree_sha = git(["write-tree"], temp).stdout.strip()
        existing = _remote_branch_head(temp, remote, branch)
        if existing is not None:
            if git(["rev-parse", f"{existing}^{{tree}}"], temp).stdout.strip() == tree_sha:
                return existing
            parent = existing
        else:
            parent = base_sha
        commit = git(
            ["commit-tree", tree_sha, "-p", parent, "-m", entry["title"]], temp
        ).stdout.strip()
        git(["push", "--quiet", remote, f"{commit}:{PUSH_REF_PREFIX}{entry['id']}"], temp)
        return commit
    finally:
        git(["worktree", "remove", "--force", str(temp)], repo, check=False)
        if parent_dir.exists() and not any(parent_dir.iterdir()):
            parent_dir.rmdir()


def push_rerun(repo: Path, remote: str, entry_id: str) -> str:
    """Push an empty re-trigger commit (same tree, previous head as parent); return its sha."""
    ref = f"{PUSH_REF_PREFIX}{entry_id}"
    git(["fetch", "--quiet", remote, ref], repo)
    head = git(["rev-parse", "FETCH_HEAD"], repo).stdout.strip()
    tree = git(["rev-parse", f"{head}^{{tree}}"], repo).stdout.strip()
    commit = git(["commit-tree", tree, "-p", head, "-m", "review-test: re-run"], repo).stdout.strip()
    git(["push", "--quiet", remote, f"{commit}:{ref}"], repo)
    return commit


# --- GitHub ----------------------------------------------------------------


def github_call(
    client: httpx.Client, method: str, path: str, token: str, body: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """Call the GitHub REST API; raise ControlError on an error status."""
    base = os.environ.get("GITHUB_API_URL", DEFAULT_GITHUB_API).rstrip("/")
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
    try:
        reply = client.request(method, f"{base}{path}", headers=headers, json=body, params=params)
    except httpx.HTTPError as exc:
        raise ControlError(scrub(f"GitHub недоступен: {type(exc).__name__}")) from exc
    if reply.status_code >= 400:
        raise ControlError(scrub(f"GitHub {method} {path}: статус {reply.status_code}"))
    return reply.json() if reply.content else None


def parse_review_comment(body: str) -> dict[str, Any] | None:
    """Parse the comment of the Action: commit, model and the three sections."""
    if MARKER not in body:
        return None
    commit = _COMMIT_RE.search(body)
    model = _MODEL_RE.search(body)
    sections: dict[str, str] = {}
    for index, (key, title) in enumerate(SECTION_TITLES):
        start = body.find(f"### {title}")
        if start < 0:
            sections[key] = ""
            continue
        start = body.find("\n", start) + 1
        ends = [p for p in (body.find("\n### ", start), body.find("<details>", start)) if p >= 0]
        sections[key] = body[start : min(ends) if ends else len(body)].strip()
    return {
        "commit": commit.group(1)[:7] if commit else None,
        "provider_model": model.group(1) if model else None,
        "sections": sections,
    }


def evaluate_pr(
    client: httpx.Client, manifest: dict[str, Any], token: str, info: dict[str, Any]
) -> dict[str, Any]:
    """Look at a PR once and classify the state of its review."""
    repo, number, head = manifest["repo"], info["number"], info["head_sha"]
    comments = github_call(
        client, "GET", f"/repos/{repo}/issues/{number}/comments", token, params={"per_page": 100}
    )
    for comment in comments or []:
        if (comment.get("user") or {}).get("login") != BOT_LOGIN:
            continue
        parsed = parse_review_comment(comment.get("body") or "")
        if parsed and parsed["commit"] == head[:7]:
            return {"state": "ok", "comment": comment, "parsed": parsed}
    runs = github_call(
        client, "GET", f"/repos/{repo}/actions/runs", token, params={"head_sha": head}
    ) or {}
    workflow_runs = runs.get("workflow_runs") or []
    if not workflow_runs:
        return {"state": "no_run"}
    run = workflow_runs[0]
    if run.get("status") == "completed":
        return {"state": "failed", "run": run}
    if run.get("status") == "queued":
        return {"state": "queued", "run": run}
    return {"state": "running", "run": run}


def final_outcome(evaluation: dict[str, Any], timed_out: bool = True) -> tuple[str, str]:
    """Map an evaluation to (status, reason) once no more waiting is possible."""
    state = evaluation["state"]
    if state == "ok":
        return "ok", ""
    if state in ("no_run", "queued"):
        return NOT_RUN, RUNNER_OFFLINE_REASON
    if state == "failed":
        conclusion = evaluation["run"].get("conclusion") or "неизвестно"
        return "failed", f"задача завершилась без комментария ревью (итог: {conclusion})"
    reason = "время ожидания истекло, задача ещё выполняется"
    return "failed", reason if timed_out else "задача ещё выполняется"


def wait_for_pr(
    client: httpx.Client, manifest: dict[str, Any], token: str, info: dict[str, Any],
    timeout_minutes: float,
) -> dict[str, Any]:
    """Poll one PR until its review appears, the run ends, or the timeout passes."""
    deadline = clock() + timeout_minutes * 60
    while True:
        evaluation = evaluate_pr(client, manifest, token, info)
        if evaluation["state"] in ("ok", "failed"):
            return evaluation
        if clock() >= deadline:
            return evaluation
        sleep(POLL_SECONDS)


# --- app -------------------------------------------------------------------


def app_call(
    client: httpx.Client, method: str, app_url: str, path: str, key: str, **kwargs: Any
) -> httpx.Response:
    """Call the app with the API key; an unreachable app is a not-run outcome."""
    try:
        return client.request(
            method, f"{app_url.rstrip('/')}{path}",
            headers={"Authorization": f"Bearer {key}"}, **kwargs,
        )
    except httpx.HTTPError as exc:
        raise ControlError(MSG_APP_DOWN, EXIT_NOT_RUN) from exc


def require_api_key() -> str:
    """Return the app API key or end as not run."""
    key = os.environ.get("PR_REVIEW_API_KEY", "")
    if not key:
        raise ControlError(MSG_NO_API_KEY, EXIT_NOT_RUN)
    return key


def read_review_config(client: httpx.Client, app_url: str, key: str) -> dict[str, Any]:
    """Read the review settings of the app owner."""
    reply = app_call(client, "GET", app_url, "/api/v1/reviews/config", key)
    if reply.status_code != 200:
        raise ControlError(f"{NOT_RUN}: настройки ревью недоступны (статус {reply.status_code})",
                           EXIT_NOT_RUN)
    return reply.json()


def docs_candidates(repo: Path) -> list[Path]:
    """Documentation files that the review should see."""
    names = [repo / "CLAUDE.md", repo / "README.md"]
    names += sorted((repo / "docs").glob("*.md")) if (repo / "docs").is_dir() else []
    return [path for path in names if path.is_file()]


def cmd_prepare_docs(args: argparse.Namespace) -> int:
    """Add the project documentation to the documentation base used by the review."""
    key = require_api_key()
    repo = Path(args.repo_dir).resolve()
    out = Path(args.out)
    with client_factory() as client:
        config = read_review_config(client, args.app_url, key)
        kb_id = (config.get("effective") or {}).get("docs_kb_id")
        if kb_id is None:
            raise ControlError(MSG_NO_DOCS_KB, EXIT_NOT_RUN)
        files = [
            ("files", (path.relative_to(repo).as_posix(), path.read_bytes(), "text/markdown"))
            for path in docs_candidates(repo)
        ]
        say(f"Отправляю файлов документации: {len(files)}")
        reply = app_call(client, "POST", args.app_url, f"/api/v1/kb/{kb_id}/files", key, files=files)
        if reply.status_code not in (200, 202):
            raise ControlError(f"добавление файлов: статус {reply.status_code}")
        deadline = clock() + KB_READY_TIMEOUT_SECONDS
        while True:
            state = app_call(client, "GET", args.app_url, f"/api/v1/kb/{kb_id}", key).json()
            if state.get("status") == "ready" and state.get("phase") is None:
                break
            if state.get("status") == "failed" or clock() >= deadline:
                raise ControlError(f"база документации не стала готовой: {state.get('status')}")
            sleep(2.0)
        listing = app_call(client, "GET", args.app_url, f"/api/v1/kb/{kb_id}/files", key).json()
    write_json(out / "docs_kb_files.json", listing)
    say(f"Файлов в базе документации: {len(listing)}")
    return EXIT_OK


# --- commands --------------------------------------------------------------


def load_manifest(args: argparse.Namespace) -> dict[str, Any]:
    """Load the manifest of the control set."""
    return json.loads(Path(args.manifest).read_text(encoding="utf-8"))


def selected(manifest: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    """PR entries chosen by --only, or every control entry."""
    if args.only:
        return [e for e in manifest["prs"] if e["id"] in args.only]
    return [e for e in manifest["prs"] if e["control"]]


def compare_url(manifest: dict[str, Any], branch: str) -> str:
    """URL where a PR can be opened by hand."""
    return f"https://github.com/{manifest['repo']}/compare/{manifest['base']}...{branch}?expand=1"


def cmd_open(args: argparse.Namespace) -> int:
    """Push the test branches and open the Pull Requests."""
    manifest = load_manifest(args)
    repo = Path(args.repo_dir).resolve()
    out = Path(args.out)
    prs: dict[str, Any] = read_json(out / "prs.json", {})
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    entries = selected(manifest, args)
    for entry in entries:
        branch = f"{manifest['branch_prefix']}{entry['id']}"
        head = push_branch(repo, args.remote, manifest["base"], entry, branch)
        record = prs.get(entry["id"], {})
        record.update({"number": record.get("number"), "branch": branch, "head_sha": head,
                       "url": record.get("url")})
        prs[entry["id"]] = record
        say(f"Ветка {branch}: {head[:7]}")
    write_json(out / "prs.json", prs)
    if not token:
        say(MSG_NO_TOKEN)
        for entry in entries:
            branch = prs[entry["id"]]["branch"]
            say(f"{entry['id']}: {entry['title']}\n  {compare_url(manifest, branch)}")
        say("Откройте Pull Request по ссылкам вручную и запустите снова с токеном для учёта.")
        return EXIT_MANUAL
    owner = manifest["repo"].split("/", 1)[0]
    with client_factory() as client:
        for entry in entries:
            record = prs[entry["id"]]
            found = github_call(
                client, "GET", f"/repos/{manifest['repo']}/pulls", token,
                params={"head": f"{owner}:{record['branch']}", "state": "open"},
            )
            if found:
                pull = found[0]
            else:
                pull = github_call(
                    client, "POST", f"/repos/{manifest['repo']}/pulls", token,
                    body={"title": entry["title"], "body": entry["body"],
                          "head": record["branch"], "base": manifest["base"]},
                )
            record["number"] = pull["number"]
            record["url"] = pull.get("html_url")
            say(f"{entry['id']}: Pull Request #{pull['number']}")
    write_json(out / "prs.json", prs)
    return EXIT_OK


def _opened(args: argparse.Namespace, manifest: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return prs.json and the ids to work on (only those that were opened)."""
    prs = read_json(Path(args.out) / "prs.json", {})
    ids = [e["id"] for e in selected(manifest, args) if e["id"] in prs]
    return prs, ids


def cmd_wait(args: argparse.Namespace) -> int:
    """Wait for the Action to comment on every PR."""
    manifest = load_manifest(args)
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not token:
        raise ControlError(MSG_NO_TOKEN, EXIT_NOT_RUN)
    prs, ids = _opened(args, manifest)
    if not ids:
        raise ControlError(f"{NOT_RUN}: нет открытых Pull Request (prs.json пуст)", EXIT_NOT_RUN)
    worst = EXIT_OK
    with client_factory() as client:
        for entry_id in ids:
            evaluation = wait_for_pr(client, manifest, token, prs[entry_id], args.timeout_minutes)
            status, reason = final_outcome(evaluation)
            say(f"{entry_id}: {status}" + (f" ({reason})" if reason else ""))
            if status == NOT_RUN:
                worst = max(worst, EXIT_NOT_RUN)
            elif status != "ok":
                worst = EXIT_FAILED
    return worst


def _seconds(evaluation: dict[str, Any]) -> float | None:
    """Seconds between the start of the run and the update of the comment."""
    run, comment = evaluation.get("run"), evaluation.get("comment")
    if not run or not comment:
        return None
    try:
        started = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
        ended = datetime.fromisoformat(comment["updated_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None
    return (ended - started).total_seconds()


def cmd_collect(args: argparse.Namespace) -> int:
    """Save the review text of every PR and the effective model of the run."""
    manifest = load_manifest(args)
    key = require_api_key()
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    prs, ids = _opened(args, manifest)
    run_dir = Path(args.out) / args.run
    started = now_iso()
    with client_factory() as client:
        config = read_review_config(client, args.app_url, key)
        effective = config.get("effective") or {}
        worst = EXIT_OK
        for entry_id in ids:
            info = prs[entry_id]
            record: dict[str, Any] = {
                "id": entry_id, "pr_number": info.get("number"), "head_sha": info["head_sha"],
                "status": NOT_RUN, "reason": "", "provider_model": None, "comment_id": None,
                "updated_at": None, "workflow_conclusion": None, "seconds": None,
            }
            body = ""
            if not token or info.get("number") is None:
                record["reason"] = "Pull Request не открыт или нет токена GitHub"
            else:
                evaluation = evaluate_pr(client, manifest, token, info)
                status, reason = final_outcome(evaluation, timed_out=False)
                record.update(status=status, reason=reason)
                run = evaluation.get("run")
                if run:
                    record["workflow_conclusion"] = run.get("conclusion")
                if evaluation["state"] == "ok":
                    comment = evaluation["comment"]
                    body = comment.get("body") or ""
                    record.update(
                        provider_model=evaluation["parsed"]["provider_model"],
                        comment_id=comment.get("id"), updated_at=comment.get("updated_at"),
                        seconds=_seconds(evaluation),
                    )
            if record["status"] == NOT_RUN:
                worst = max(worst, EXIT_NOT_RUN)
            elif record["status"] != "ok":
                worst = EXIT_FAILED
            write_file(run_dir / f"{entry_id}.md", body)
            write_json(run_dir / f"{entry_id}.json", record)
            say(f"{entry_id}: {record['status']}" + (f" ({record['reason']})" if record["reason"] else ""))
    write_json(run_dir / "run.json", {
        "run": args.run, "provider": effective.get("provider_name"), "model": effective.get("model"),
        "started_at": started, "finished_at": now_iso(),
    })
    return worst


def cmd_rerun(args: argparse.Namespace) -> int:
    """Re-trigger the Action on every PR with an empty commit."""
    manifest = load_manifest(args)
    out = Path(args.out)
    prs, ids = _opened(args, manifest)
    if not ids:
        raise ControlError(f"{NOT_RUN}: нет открытых Pull Request (prs.json пуст)", EXIT_NOT_RUN)
    if not args.after_run:
        raise ControlError(f"{NOT_RUN}: укажите --after-run с уже собранным прогоном", EXIT_NOT_RUN)
    missing = [i for i in ids if not (out / args.after_run / f"{i}.json").is_file()]
    if missing:
        raise ControlError(
            f"{NOT_RUN}: прогон {args.after_run} не собран для {', '.join(missing)}; "
            "комментарий Action будет заменён, сначала выполните collect",
            EXIT_NOT_RUN,
        )
    repo = Path(args.repo_dir).resolve()
    for entry_id in ids:
        prs[entry_id]["head_sha"] = push_rerun(repo, args.remote, entry_id)
        say(f"{entry_id}: новый коммит {prs[entry_id]['head_sha'][:7]}")
    write_json(out / "prs.json", prs)
    return EXIT_OK


def cmd_close(args: argparse.Namespace) -> int:
    """Close the PRs without merging; branches are kept."""
    manifest = load_manifest(args)
    prs = read_json(Path(args.out) / "prs.json", {})
    targets = {i: p for i, p in prs.items() if p.get("number") and (not args.only or i in args.only)}
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not token:
        say(MSG_NO_TOKEN)
        for entry_id, info in targets.items():
            say(f"Закройте без слияния: {info.get('url') or entry_id}")
        return EXIT_MANUAL
    with client_factory() as client:
        for entry_id, info in targets.items():
            github_call(client, "PATCH", f"/repos/{manifest['repo']}/pulls/{info['number']}",
                        token, body={"state": "closed"})
            say(f"{entry_id}: #{info['number']} закрыт без слияния")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    """Print a table of the known PRs and their last collected result."""
    out = Path(args.out)
    prs = read_json(out / "prs.json", {})
    if not prs:
        say(f"{NOT_RUN}: prs.json нет, Pull Request ещё не открывались")
        return EXIT_NOT_RUN
    say(f"{'id':<8}{'PR':<8}{'head':<10}результат")
    for entry_id, info in prs.items():
        last = "нет данных"
        for run_dir in sorted((p for p in out.iterdir() if p.is_dir()), reverse=True):
            data = read_json(run_dir / f"{entry_id}.json", None)
            if data:
                last = f"{run_dir.name}: {data['status']}"
                break
        say(f"{entry_id:<8}{str(info.get('number') or '-'):<8}{info['head_sha'][:7]:<10}{last}")
    return EXIT_OK


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "prepare-docs": cmd_prepare_docs,
    "open": cmd_open,
    "wait": cmd_wait,
    "collect": cmd_collect,
    "rerun": cmd_rerun,
    "close": cmd_close,
    "status": cmd_status,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(description="Control run of the PR review.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--only", action="append", help="id of a test PR (repeatable)")
    parser.add_argument("--run", default="local", help="name of the run folder for collect")
    parser.add_argument("--after-run", default=None, help="collected run required before rerun")
    parser.add_argument("--app-url", default=DEFAULT_APP_URL)
    parser.add_argument("--timeout-minutes", type=float, default=45.0)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--repo-dir", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the exit code."""
    args = build_parser().parse_args(argv)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    register_secrets()
    try:
        return COMMANDS[args.command](args)
    except ControlError as exc:
        say(str(exc))
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
