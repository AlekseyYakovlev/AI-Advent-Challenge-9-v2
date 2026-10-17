"""Run one goal against a running app over the HTTP chat endpoint and save the evidence.

Usage:
    python scripts/files_task.py --root <git work tree> --goal "<goal>" --out <dir>
        [--agent-url URL] [--api-key-env NAME] [--provider-id N --model ID]
        [--max-tokens N] [--context-length N] [--timeout S] [--title T] [--allow-own-repo]

The goal is sent as an ordinary message to POST /api/v1/chats/{id}/messages of a fresh chat with
temperature 0, so the script path runs the same chat code as the browser. After the turn the
script records run.json (goal, commit, model, settings, MCP servers, every tool call, the answer,
status), run.patch (git diff of the root, new files included) and answer.md in --out.

The API key is read from the environment variable named by --api-key-env (default
FILES_TASK_API_KEY) and is never written anywhere. The absolute root path is replaced by the
folder name in every saved text. Runs are meant for a disposable clone: the assistant may edit
files in --root.

Exit codes: 0 done, 1 failed, 2 blocked (app or model unavailable, busy, rate-limited),
3 refused (root not clean / not a work tree / own repository / missing key).
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

EXIT_DONE: int = 0
EXIT_FAILED: int = 1
EXIT_BLOCKED: int = 2
EXIT_REFUSED: int = 3

DEFAULT_AGENT_URL: str = "http://localhost:18001"
DEFAULT_KEY_ENV: str = "FILES_TASK_API_KEY"
PREFERRED_MODEL: str = "qwen/qwen3.5-9b"
DEFAULT_SETTINGS: dict[str, Any] = {
    "temperature": 0.0,
    "max_tokens": 8192,
    "context_length": 65536,
}
REQUEST_TIMEOUT_S: float = 1200.0
BLOCKING_STATUSES: frozenset[int] = frozenset({409, 429, 503})
RESULT_SUMMARY_CUT: int = 300
GIT_TIMEOUT_S: int = 60


def report(message: str) -> None:
    """Print one terminal line (scripts are the one place where terminal output is allowed)."""
    print(message, flush=True)


def scrub(text: str, secrets_list: list[str]) -> str:
    """Replace every non-empty secret in a text with ***."""
    for secret in secrets_list:
        if secret:
            text = text.replace(secret, "***")
    return text


def scrub_paths(text: str, root: Path) -> str:
    """Replace the absolute root path (both separator forms, any case) with the folder name."""
    resolved = str(root.resolve())
    label = root.resolve().name
    variants = {
        resolved,
        str(root),
        resolved.replace("\\", "/"),
        str(root).replace("\\", "/"),
        resolved.replace("\\", "\\\\"),
        str(root).replace("\\", "\\\\"),
    }
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            text = re.sub(re.escape(variant), lambda _m: label, text, flags=re.IGNORECASE)
    return text


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command in root with list argv (no shell)."""
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT_S,
    )


def _toplevel(path: Path) -> str:
    """Normalised git top-level folder of a path, or an empty string."""
    try:
        result = _git(path, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    top = result.stdout.strip()
    return os.path.normcase(os.path.realpath(top)) if result.returncode == 0 and top else ""


def check_root(root: Path, allow_own_repo: bool) -> str | None:
    """Return the reason a root is refused, or None when it is safe to run in."""
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired):
        return f"root is not a git work tree: {root}"
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return f"root is not a git work tree: {root}"
    own = _toplevel(REPO_ROOT)
    if not allow_own_repo and own and _toplevel(root) == own:
        return "root is this project's own repository; run on a clone (D-13) or pass --allow-own-repo"
    status = _git(root, "status", "--porcelain")
    changes = [line for line in status.stdout.splitlines() if line.strip()]
    if changes:
        return f"root is not clean: commit or discard {len(changes)} changes first"
    return None


def git_head(root: Path) -> dict[str, str]:
    """Commit hash and branch name of the root."""
    commit = _git(root, "rev-parse", "HEAD").stdout.strip()
    branch = _git(root, "branch", "--show-current").stdout.strip()
    return {"commit": commit, "branch": branch or "(detached)"}


def _auth(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def resolve_target(
    client: httpx.AsyncClient,
    agent_url: str,
    api_key: str,
    provider_id: int | None,
    model: str | None,
) -> dict[str, Any] | None:
    """Pick the LM Studio provider and model: explicit ones, else the preferred or the first."""
    if provider_id is not None and model:
        return {"provider_id": provider_id, "model": model}
    try:
        response = await client.get(
            f"{agent_url}/api/v1/llm-providers/models", headers=_auth(api_key)
        )
        groups = response.json() if response.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return None
    fallback: dict[str, Any] | None = None
    for group in groups if isinstance(groups, list) else []:
        if group.get("kind") != "lm_studio":
            continue
        ids = [str(item.get("id")) for item in group.get("models", []) if item.get("id")]
        if PREFERRED_MODEL in ids:
            return {"provider_id": group["provider_id"], "model": PREFERRED_MODEL}
        if fallback is None and ids:
            fallback = {"provider_id": group["provider_id"], "model": ids[0]}
    return fallback


async def list_mcp_servers(
    client: httpx.AsyncClient, agent_url: str, api_key: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Summarise the MCP servers of the user and warn about duplicated project_git servers."""
    try:
        response = await client.get(f"{agent_url}/api/v1/mcp/servers", headers=_auth(api_key))
    except httpx.HTTPError as exc:
        return [], [f"MCP server list unavailable: {type(exc).__name__}: {exc}"]
    if response.status_code != 200:
        return [], [f"MCP server list unavailable: HTTP {response.status_code}"]
    servers: list[dict[str, Any]] = []
    git_servers: list[str] = []
    for item in response.json():
        connection = item.get("connection") or {}
        servers.append(
            {
                "name": item.get("name"),
                "enabled": bool(item.get("enabled")),
                "status": connection.get("status"),
                "tools": [tool.get("name") for tool in connection.get("tools", [])],
            }
        )
        args = [str(arg) for arg in item.get("args") or []]
        if item.get("enabled") and any(arg.endswith("project_git.py") for arg in args):
            git_servers.append(str(item.get("name")))
    warnings: list[str] = []
    if len(git_servers) > 1:
        warnings.append(
            f"several enabled servers run project_git.py ({', '.join(git_servers)}); "
            "the model sees duplicate tools"
        )
    return servers, warnings


@dataclass
class RunOutcome:
    """Result of one goal turn."""

    status: str
    http_status: int | None
    error: str | None
    body: dict[str, Any] | None
    elapsed_s: float
    chat_id: int | None


def _status_for(code: int) -> str:
    return "blocked" if code in BLOCKING_STATUSES else "failed"


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])[:300]
    return response.text[:300]


async def run_goal(
    client: httpx.AsyncClient,
    *,
    agent_url: str,
    api_key: str,
    goal: str,
    title: str,
    target: dict[str, Any],
    settings: dict[str, Any],
    timeout_s: float = REQUEST_TIMEOUT_S,
) -> RunOutcome:
    """Create a chat, fix its settings and send the goal as one message."""
    started = time.monotonic()
    chat_id: int | None = None
    headers = _auth(api_key)

    def outcome(
        status: str, code: int | None, error: str | None, body: dict[str, Any] | None
    ) -> RunOutcome:
        return RunOutcome(status, code, error, body, time.monotonic() - started, chat_id)

    try:
        created = await client.post(f"{agent_url}/api/v1/chats", json={"title": title}, headers=headers)
        if created.status_code not in (200, 201):
            return outcome(
                _status_for(created.status_code), created.status_code,
                f"HTTP {created.status_code}: {_detail(created)}", None,
            )  # fmt: skip
        chat_id = int(created.json()["id"])
        applied = await client.put(
            f"{agent_url}/api/v1/settings", json={"chat_id": chat_id, **settings}, headers=headers
        )
        if applied.status_code != 200:
            return outcome(
                _status_for(applied.status_code), applied.status_code,
                f"HTTP {applied.status_code}: {_detail(applied)}", None,
            )  # fmt: skip
        response = await client.post(
            f"{agent_url}/api/v1/chats/{chat_id}/messages",
            json={
                "content": goal,
                "model": target["model"],
                "provider_id": target["provider_id"],
            },
            headers=headers,
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        return outcome("blocked", None, f"{type(exc).__name__}: {exc}", None)
    if response.status_code != 200:
        return outcome(
            _status_for(response.status_code), response.status_code,
            f"HTTP {response.status_code}: {_detail(response)}", None,
        )  # fmt: skip
    try:
        body = response.json()
    except ValueError:
        return outcome("failed", 200, "response is not JSON", None)
    if not isinstance(body, dict) or not str(body.get("content") or "").strip():
        return outcome("failed", 200, "empty assistant answer", body if isinstance(body, dict) else None)
    return outcome("done", 200, None, body)


def collect_patch(root: Path) -> str:
    """Return the full diff of the root, new files included, as text."""
    # Intent-to-add makes untracked files visible to `git diff`; without it a created file is lost.
    _git(root, "add", "-N", ".")
    result = subprocess.run(
        ["git", "diff", "--binary"], cwd=root, capture_output=True, timeout=GIT_TIMEOUT_S
    )
    return result.stdout.decode("utf-8", errors="replace")


def patch_stats(patch: str) -> dict[str, Any]:
    """Per-file added/removed line counts and new-file flags of a unified diff."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.*) b/(.*)$", line)
            path = match.group(2) if match else line[len("diff --git "):]
            current = {"path": path, "added": 0, "removed": 0, "new": False}
            files.append(current)
        elif current is None:
            continue
        elif line.startswith("new file mode"):
            current["new"] = True
        elif line.startswith("+") and not line.startswith("+++ "):
            current["added"] += 1
        elif line.startswith("-") and not line.startswith("--- "):
            current["removed"] += 1
    return {
        "files": files,
        "total_added": sum(item["added"] for item in files),
        "total_removed": sum(item["removed"] for item in files),
    }


def summarize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact record of each tool call: arguments as received, first line of the result."""
    summary: list[dict[str, Any]] = []
    for call in tool_calls:
        result = str(call.get("result") or "")
        first_line = result.splitlines()[0] if result.splitlines() else ""
        summary.append(
            {
                "name": call.get("name"),
                "server": call.get("server"),
                "tool": call.get("tool"),
                "ok": call.get("ok"),
                "truncated": call.get("truncated"),
                "arguments": call.get("arguments"),
                "result_summary": first_line[:RESULT_SUMMARY_CUT],
                "result_chars": len(result),
            }
        )
    return summary


def build_run_record(
    *,
    goal: str,
    root: Path,
    head: dict[str, str],
    agent_url: str,
    target: dict[str, Any] | None,
    settings: dict[str, Any],
    servers: list[dict[str, Any]],
    warnings: list[str],
    outcome: RunOutcome,
    patch: str,
    started_at: datetime,
) -> dict[str, Any]:
    """Assemble the run.json content (the root is recorded as a label only)."""
    body = outcome.body or {}
    tool_calls = summarize_tool_calls(body.get("tool_calls") or [])
    return {
        "schema": "files-task/1",
        "goal": goal,
        "started_at": started_at.isoformat(),
        "elapsed_s": round(outcome.elapsed_s, 2),
        "root": root.resolve().name,
        "commit": head["commit"],
        "branch": head["branch"],
        "agent_url": agent_url,
        "provider_id": (target or {}).get("provider_id"),
        "model": (target or {}).get("model"),
        "settings": settings,
        "mcp_servers": servers,
        "warnings": warnings,
        "status": outcome.status,
        "http_status": outcome.http_status,
        "error": outcome.error,
        "chat_id": outcome.chat_id,
        "assistant_message_id": body.get("assistant_message_id"),
        "tool_call_count": len(tool_calls),
        "tool_calls": tool_calls,
        "answer": str(body.get("content") or ""),
        "patch": patch_stats(patch),
    }


def save_artifacts(
    out_dir: Path,
    record: dict[str, Any],
    patch: str,
    answer: str,
    secrets_list: list[str],
    root: Path | None = None,
) -> None:
    """Write run.json, run.patch and answer.md after scrubbing secrets and the root path."""

    def clean(text: str) -> str:
        text = scrub(text, secrets_list)
        return scrub_paths(text, root) if root is not None else text

    out_dir.mkdir(parents=True, exist_ok=True)
    run_json = json.dumps(record, ensure_ascii=False, indent=2)
    if not answer:
        answer = f"Ответа нет: {record.get('status')} — {record.get('error')}"
    (out_dir / "run.json").write_bytes(clean(run_json).encode("utf-8"))
    (out_dir / "run.patch").write_bytes(clean(patch).encode("utf-8"))
    (out_dir / "answer.md").write_bytes(clean(answer).encode("utf-8"))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one goal against the app and save evidence.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--agent-url", default=DEFAULT_AGENT_URL)
    parser.add_argument("--api-key-env", default=DEFAULT_KEY_ENV)
    parser.add_argument("--provider-id", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_SETTINGS["max_tokens"])
    parser.add_argument("--context-length", type=int, default=DEFAULT_SETTINGS["context_length"])
    parser.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT_S)
    parser.add_argument("--title", default="files-task run")
    parser.add_argument("--allow-own-repo", action="store_true")
    return parser.parse_args(argv)


_EXIT_BY_STATUS: dict[str, int] = {
    "done": EXIT_DONE,
    "failed": EXIT_FAILED,
    "blocked": EXIT_BLOCKED,
}


async def amain(argv: list[str] | None = None) -> int:
    """Check the root, run the goal, collect the patch and save the artifacts."""
    args = _parse_args(argv)
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        report(f"REFUSED: environment variable {args.api_key_env} is not set")
        return EXIT_REFUSED
    root: Path = args.root
    reason = check_root(root, args.allow_own_repo)
    if reason:
        report(f"REFUSED: {reason}")
        return EXIT_REFUSED
    head = git_head(root)
    settings = {
        **DEFAULT_SETTINGS,
        "max_tokens": args.max_tokens,
        "context_length": args.context_length,
    }
    agent_url = args.agent_url.rstrip("/")
    started_at = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=30.0) as client:
        target = await resolve_target(client, agent_url, api_key, args.provider_id, args.model)
        servers: list[dict[str, Any]] = []
        warnings: list[str] = []
        if target is None:
            outcome = RunOutcome("blocked", None, "no local model available", None, 0.0, None)
        else:
            servers, warnings = await list_mcp_servers(client, agent_url, api_key)
            outcome = await run_goal(
                client, agent_url=agent_url, api_key=api_key, goal=args.goal, title=args.title,
                target=target, settings=settings, timeout_s=args.timeout,
            )  # fmt: skip
    patch = collect_patch(root)
    record = build_run_record(
        goal=args.goal, root=root, head=head, agent_url=agent_url, target=target,
        settings=settings, servers=servers, warnings=warnings, outcome=outcome, patch=patch,
        started_at=started_at,
    )  # fmt: skip
    save_artifacts(args.out, record, patch, record["answer"], [api_key], root=root)
    report(
        f"RESULT: {outcome.status} tool_calls={record['tool_call_count']} "
        f"files={len(record['patch']['files'])}"
    )
    return _EXIT_BY_STATUS.get(outcome.status, EXIT_FAILED)


def main() -> None:
    """Entry point."""
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
