"""Scenario driver for the project files assistant: four fixed goals on an isolated copy.

Usage:
    python scripts/files_eval.py check
    python scripts/files_eval.py run [--scenarios usages,adr,changelog,readme]
    python scripts/files_eval.py summary

Commands:
    check    preflight only: print which parts can run and which are `not run`
    run      copy the working tree to a scratch directory, repoint the copy to UI :18000 and
             Agent :18001 with a scratch database, make a fresh `git clone --local --no-hardlinks`
             of this repository as the write root, register only the `project-files` MCP server
             with that clone and --allow-write, create an API key and send each scenario goal
             through files_task.run_goal. Before every scenario the clone is reset to the
             recorded commit. Each scenario saves eval_out/day34/<slug>/run.json, run.patch and
             answer.md; run.json carries a `checks` block of automatic, model-independent checks.
    summary  write eval_out/day34/summary.md from the saved run.json files; no app is contacted

Isolation: the user's app on 8000/8001, app.db and the working copy are never touched; only
processes started here are stopped. Only the local LM Studio model is used (no judge, no second
model). When LM Studio is down or has no loaded chat model, every selected scenario is recorded
as `not run` with the reason; it is never recorded as passed.

Exit codes: 0 done, 1 a scenario aborted on an unexpected error, 2 blocked or not run.
"""

import argparse
import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCRIPTS_DIR: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import e2e_kb_playwright as kb  # noqa: E402
import files_task  # noqa: E402
import httpx  # noqa: E402
import rag_dialog as dialog  # noqa: E402

UI_PORT: int = 18000
AGENT_PORT: int = 18001
OUT_DIR: Path = REPO_ROOT / "eval_out" / "day34"
SERVER_NAME: str = "project-files"
CLONE_NAME: str = "files-root"
OWNER_NAME: str = "files_eval"
NOT_RUN: str = "not run"
USAGE_SYMBOL: str = "cleanup_chat_caches"
CHANGELOG_MIN_REFS: int = 3
GIT_LOG_COUNT: int = 50
GIT_TIMEOUT_S: int = 300
EXIT_OK, EXIT_ABORTED, EXIT_PREFLIGHT = 0, 1, 2
PREFERRED_MODEL: str = files_task.PREFERRED_MODEL

SCENARIOS: dict[str, str] = {
    "usages": (
        "Найди все места в проекте, где используется функция cleanup_chat_caches, "
        "и объясни роль каждого места. Файлы не меняй."
    ),
    "adr": (
        "Напиши ADR о разделении приложения на два процесса (UI и Agent): контекст, решение, "
        "последствия. Опирайся на код и документацию проекта. Сохрани ADR новым файлом в папке "
        "docs/adr/ и в конце перечисли созданные или изменённые файлы."
    ),
    "changelog": (
        "Составь CHANGELOG.md в корне проекта по истории git: возьми последние 50 коммитов и "
        "сгруппируй изменения по фазам и дням курса. В конце перечисли созданные или "
        "изменённые файлы."
    ),
    "readme": (
        "Напиши README.md для папки mcp_servers/ по коду серверов в ней: какие серверы есть, "
        "как их запускать, какие у них инструменты и ограничения. В конце перечисли созданные "
        "или изменённые файлы."
    ),
}


def report(message: str) -> None:
    """Print one terminal line (scripts are the one place where terminal output is allowed)."""
    print(message, flush=True)


# ---------------------------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command with list argv (no shell)."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_git_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT_S,
        stdin=subprocess.DEVNULL,
    )


def make_clone(scratch: Path) -> Path:
    """Fresh local clone of this repository inside the scratch folder: the write root."""
    clone = scratch / CLONE_NAME
    result = _run_git(
        scratch, "clone", "--local", "--no-hardlinks", str(REPO_ROOT), str(clone)
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr.strip()[:300]}")
    return clone


def reset_clone(clone: Path, commit: str) -> None:
    """Hard-reset the scratch clone to a commit; refuses any other path."""
    resolved = clone.resolve()
    own = REPO_ROOT.resolve()
    if resolved.name != CLONE_NAME:
        raise ValueError(f"refusing to reset {resolved.name}: not the {CLONE_NAME} clone")
    if resolved == own or own in resolved.parents:
        raise ValueError("refusing to reset this project's own repository")
    for args in (("reset", "--hard", commit), ("clean", "-fd")):
        result = _run_git(resolved, *args)
        if result.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[:300]}")


def expected_usage_files(clone: Path, symbol: str) -> list[str]:
    """Tracked Python files that mention a symbol as a whole word, sorted."""
    result = _run_git(clone, "grep", "-l", "-w", "-F", "-e", symbol, "--", "*.py")
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def git_refs(clone: Path, count: int = GIT_LOG_COUNT) -> list[str]:
    """Subjects and short hashes of the last commits (the strings a changelog may cite)."""
    result = _run_git(clone, "log", f"-n{count}", "--format=%h%x09%s")
    refs: list[str] = []
    for line in result.stdout.splitlines():
        short, _, subject = line.partition("\t")
        refs.extend(item for item in (subject.strip(), short.strip()) if item)
    return refs


# ---------------------------------------------------------------------------------------------
# Scenario checks
# ---------------------------------------------------------------------------------------------


def _added_text(patch: str, path: str) -> str:
    """Added lines of one file of a unified diff, joined by newlines."""
    lines: list[str] = []
    current = ""
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.*) b/(.*)$", line)
            current = match.group(2) if match else ""
        elif current == path and line.startswith("+") and not line.startswith("+++ "):
            lines.append(line[1:])
    return "\n".join(lines)


def _new_paths(patch: str) -> list[str]:
    return [item["path"] for item in files_task.patch_stats(patch)["files"] if item["new"]]


def _answer_lists_changed(patch: str, answer: str) -> bool:
    paths = [item["path"] for item in files_task.patch_stats(patch)["files"]]
    return bool(paths) and all(path in answer for path in paths)


def scenario_checks(slug: str, record: dict[str, Any], patch: str, clone: Path) -> dict[str, bool]:
    """Automatic, model-independent checks of one scenario."""
    answer = str(record.get("answer") or "")
    new_paths = _new_paths(patch)
    if slug == "usages":
        expected = expected_usage_files(clone, USAGE_SYMBOL)
        return {
            "no_files_changed": not patch.strip(),
            "mentions_usage_files": bool(expected) and all(name in answer for name in expected),
        }
    if slug == "adr":
        return {
            "new_file_in_docs_adr": any(p.startswith("docs/adr/") for p in new_paths),
            "answer_lists_changed_files": _answer_lists_changed(patch, answer),
        }
    if slug == "changelog":
        added = _added_text(patch, "CHANGELOG.md")
        cited = {ref for ref in git_refs(clone) if ref in added}
        return {
            "changelog_created": "CHANGELOG.md" in new_paths,
            "cites_git_history": len(cited) >= CHANGELOG_MIN_REFS,
            "answer_lists_changed_files": _answer_lists_changed(patch, answer),
        }
    if slug == "readme":
        added = _added_text(patch, "mcp_servers/README.md")
        return {
            "readme_created": "mcp_servers/README.md" in new_paths,
            "names_server_files": "project_git.py" in added,
            "answer_lists_changed_files": _answer_lists_changed(patch, answer),
        }
    raise ValueError(f"unknown scenario: {slug}")


# ---------------------------------------------------------------------------------------------
# Records and summary
# ---------------------------------------------------------------------------------------------


def not_run_record(slug: str, goal: str, reason: str) -> dict[str, Any]:
    """run.json content of a scenario that did not run."""
    return {
        "schema": "files-task/1",
        "scenario": slug,
        "goal": goal,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": NOT_RUN,
        "reason": reason,
        "tool_call_count": 0,
        "patch": {"files": [], "total_added": 0, "total_removed": 0},
        "checks": {},
    }


def _scrub_value(value: Any, scratch: Path) -> Any:
    """Replace the scratch path with its folder name in every string of a JSON value."""
    if isinstance(value, str):
        return files_task.scrub_paths(value, scratch)
    if isinstance(value, list):
        return [_scrub_value(item, scratch) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_value(item, scratch) for key, item in value.items()}
    return value


def save_not_run(out_dir: Path, record: dict[str, Any], scratch: Path) -> None:
    """Save a not-run scenario with a plain answer line and no absolute scratch path."""
    cleaned = _scrub_value(record, scratch)
    answer = f"Не запускалось: {cleaned['reason']}"
    files_task.save_artifacts(out_dir, cleaned, "", answer, [], root=scratch)


def render_summary(records: list[dict[str, Any]]) -> str:
    """Markdown table of the saved scenario runs."""
    lines = [
        "# Day 34: сценарии ассистента по файлам проекта",
        "",
        "| Сценарий | Статус | Вызовов инструментов | Изменённые файлы | Проверки |",
        "| --- | --- | --- | --- | --- |",
    ]
    for record in records:
        files = [item.get("path", "") for item in (record.get("patch") or {}).get("files", [])]
        checks = record.get("checks") or {}
        marks = ", ".join(f"{name} {'✓' if ok else '✗'}" for name, ok in checks.items()) or "—"
        status = str(record.get("status"))
        if status == NOT_RUN and record.get("reason"):
            status = f"{NOT_RUN}: {record['reason']}"
        lines.append(
            f"| {record.get('scenario', '?')} | {status} | {record.get('tool_call_count', 0)} "
            f"| {', '.join(files) or '—'} | {marks} |"
        )
    return "\n".join(lines) + "\n"


def load_records(out_dir: Path) -> list[dict[str, Any]]:
    """Saved run.json files in scenario table order."""
    records: list[dict[str, Any]] = []
    for slug in SCENARIOS:
        path = out_dir / slug / "run.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("scenario", slug)
            records.append(data)
    return records


def summary_command(out_dir: Path = OUT_DIR) -> int:
    """Write summary.md from the saved run.json files."""
    records = load_records(out_dir)
    if not records:
        report(f"no saved runs in {out_dir}")
        return EXIT_PREFLIGHT
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.md").write_text(render_summary(records), encoding="utf-8")
    report(f"summary written: {len(records)} scenarios")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------------------------


def pick_chat_model() -> str | None:
    """Preferred loaded chat model of LM Studio, else the first loaded non-embedding one."""
    data = httpx.get(f"{kb.LM_URL}/api/v0/models", timeout=5.0).json().get("data", [])
    loaded = [m["id"] for m in data if m.get("state") == "loaded" and m.get("type") != "embeddings"]
    if PREFERRED_MODEL in loaded:
        return PREFERRED_MODEL
    return loaded[0] if loaded else None


def preflight() -> str | None:
    """Return the reason the run cannot start, else None."""
    busy = [p for p in (UI_PORT, AGENT_PORT) if not kb.port_is_free(p)]
    if busy:
        return f"port(s) {busy} are in use; the isolated copy needs them free"
    if shutil.which("git") is None:
        return "git is not installed"
    try:
        model = pick_chat_model()
    except (httpx.HTTPError, ValueError) as exc:
        return f"LM Studio not available ({type(exc).__name__})"
    if model is None:
        return "no loaded chat model in LM Studio"
    return None


def check_command() -> int:
    """Print what can run; exit 2 when nothing can."""
    reason = preflight()
    report(f"isolated run: {'ok' if reason is None else f'{NOT_RUN}: {reason}'}")
    return EXIT_OK if reason is None else EXIT_PREFLIGHT


# ---------------------------------------------------------------------------------------------
# Isolated app
# ---------------------------------------------------------------------------------------------


def isolated_overrides(
    scratch: Path,
    ui_port: int = UI_PORT,
    agent_port: int = AGENT_PORT,
    db_name: str = "files_eval.db",
) -> dict[str, str]:
    """Environment overrides of the isolated app; refuses the live ports and app.db."""
    db_path = Path(scratch) / db_name
    dialog.assert_isolated(db_path, ui_port, agent_port)
    return {
        "DB_PATH": str(db_path),
        "KB_STORAGE_DIR": str(Path(scratch) / "kb"),
        "UI_PORT": str(ui_port),
        "AGENT_PORT": str(agent_port),
    }


async def build_run_env(scratch: Path) -> dialog.RunEnv:
    """Copy the tree, seed one scratch user and build the child environment (no cloud key)."""
    overrides = isolated_overrides(scratch)
    db_path = Path(overrides["DB_PATH"])
    copy_dir = kb.prepare_copy(scratch)
    password = secrets.token_urlsafe(16)
    await kb.seed_scratch_users(db_path, {OWNER_NAME: password})
    env: dict[str, str] = {
        **os.environ,
        **overrides,
        "LM_STUDIO_BASE_URL": kb.LM_URL,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join([str(copy_dir), *filter(None, [os.environ.get("PYTHONPATH")])]),
    }
    return dialog.RunEnv(
        scratch, copy_dir, db_path, Path(overrides["KB_STORAGE_DIR"]), env, OWNER_NAME, password
    )


@contextlib.asynccontextmanager
async def isolated_app(scratch: Path) -> AsyncIterator[tuple[dialog.RunEnv, httpx.AsyncClient]]:
    """Start the copy, log in with a cookie client, and always stop what was started."""
    run_env = await build_run_env(scratch)
    proc, log_file = await dialog.start_app(run_env)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            await dialog.login(client, run_env)
            yield run_env, client
    finally:
        await kb.teardown(proc)
        log_file.close()


async def register_server(
    client: httpx.AsyncClient, run_env: dialog.RunEnv, clone: Path
) -> dict[str, Any]:
    """Create and connect the project-files server with write tools aimed at the clone."""
    script = run_env.copy_dir / "mcp_servers" / "project_git.py"
    created = await client.post(
        f"{kb.AGENT_URL}/api/v1/mcp/servers",
        json={
            "name": SERVER_NAME,
            "command": sys.executable,
            "args": [str(script), f"--root={clone}", "--allow-write"],
            "cwd": str(clone),
            "enabled": True,
        },
        headers=dialog._json_headers(),
    )
    created.raise_for_status()
    server_id = int(created.json()["id"])
    connected = await client.post(
        f"{kb.AGENT_URL}/api/v1/mcp/servers/{server_id}/connect", headers=dialog._json_headers()
    )
    connected.raise_for_status()
    connection = connected.json().get("connection") or {}
    tools = [str(tool.get("name")) for tool in connection.get("tools", [])]
    status = str(connection.get("status"))
    report(f"mcp server {SERVER_NAME}: {status}, {len(tools)} tools")
    return {"id": server_id, "status": status, "tools": tools}


async def create_api_key(client: httpx.AsyncClient) -> str:
    """Create an API key for the scratch user."""
    response = await client.post(
        f"{kb.AGENT_URL}/api/v1/api-keys", json={"name": "files-eval"}, headers=dialog._json_headers()
    )
    response.raise_for_status()
    return str(response.json()["key"])


# ---------------------------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------------------------


def select_scenarios(raw: str | None) -> list[str]:
    """Scenario slugs in table order; an unknown name is an error."""
    if not raw:
        return list(SCENARIOS)
    wanted = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [item for item in wanted if item not in SCENARIOS]
    if unknown:
        raise ValueError(f"unknown scenarios: {', '.join(unknown)}")
    return [slug for slug in SCENARIOS if slug in wanted]


def save_all_not_run(slugs: list[str], reason: str, scratch: Path) -> None:
    """Record every selected scenario as not run."""
    for slug in slugs:
        save_not_run(OUT_DIR / slug, not_run_record(slug, SCENARIOS[slug], reason), scratch)
        report(f"{slug}: {NOT_RUN} ({reason})")


async def run_one(
    slug: str,
    *,
    client: httpx.AsyncClient,
    clone: Path,
    commit: str,
    api_key: str,
    password: str,
    target: dict[str, Any],
    scratch: Path,
) -> str:
    """Reset the clone, run one scenario, save its artifacts, return the run status."""
    goal = SCENARIOS[slug]
    reset_clone(clone, commit)
    refusal = files_task.check_root(clone, allow_own_repo=False)
    if refusal:
        raise RuntimeError(refusal)
    started_at = datetime.now(timezone.utc)
    head = files_task.git_head(clone)
    agent_url = kb.AGENT_URL
    servers, warnings = await files_task.list_mcp_servers(client, agent_url, api_key)
    outcome = await files_task.run_goal(
        client, agent_url=agent_url, api_key=api_key, goal=goal, title=f"day34-{slug}",
        target=target, settings=files_task.DEFAULT_SETTINGS,
    )  # fmt: skip
    patch = files_task.collect_patch(clone)
    record = files_task.build_run_record(
        goal=goal, root=clone, head=head, agent_url=agent_url, target=target,
        settings=files_task.DEFAULT_SETTINGS, servers=servers, warnings=warnings,
        outcome=outcome, patch=patch, started_at=started_at,
    )  # fmt: skip
    record["scenario"] = slug
    record["checks"] = scenario_checks(slug, record, patch, clone)
    cleaned = _scrub_value(record, scratch)
    files_task.save_artifacts(
        OUT_DIR / slug, cleaned, _scrub_value(patch, scratch), cleaned["answer"],
        [api_key, password], root=clone,
    )  # fmt: skip
    report(
        f"{slug}: {outcome.status} tool_calls={record['tool_call_count']} "
        f"files={len(record['patch']['files'])} checks={record['checks']}"
    )
    return outcome.status


def save_failed(slug: str, error: str, scratch: Path, secrets_list: list[str]) -> None:
    """Record a scenario aborted by an unexpected error."""
    record = not_run_record(slug, SCENARIOS[slug], error)
    record["status"] = "failed"
    record["error"] = error
    del record["reason"]
    cleaned = _scrub_value(record, scratch)
    files_task.save_artifacts(
        OUT_DIR / slug, cleaned, "", f"Сценарий прерван: {cleaned['error']}", secrets_list,
        root=scratch,
    )  # fmt: skip


async def run_scenarios(slugs: list[str], scratch: Path) -> int:
    """Start the isolated app and run the selected scenarios one after another."""
    clone = make_clone(scratch)
    commit = files_task.git_head(clone)["commit"]
    statuses: list[str] = []
    aborted = False
    async with isolated_app(scratch) as (run_env, client):
        server = await register_server(client, run_env, clone)
        if server["status"] != "connected" or "write_file" not in server["tools"]:
            save_all_not_run(slugs, "project-files server did not connect with write tools", scratch)
            return EXIT_PREFLIGHT
        api_key = await create_api_key(client)
        target = await files_task.resolve_target(client, kb.AGENT_URL, api_key, None, None)
        if target is None:
            save_all_not_run(slugs, "no local model available in the app", scratch)
            return EXIT_PREFLIGHT
        report(f"model: {target['model']}, commit: {commit[:10]}")
        for slug in slugs:
            try:
                statuses.append(
                    await run_one(
                        slug, client=client, clone=clone, commit=commit, api_key=api_key,
                        password=run_env.password, target=target, scratch=scratch,
                    )  # fmt: skip
                )
            except (RuntimeError, ValueError, httpx.HTTPError, OSError, subprocess.SubprocessError) as exc:
                aborted = True
                error = f"{type(exc).__name__}: {exc}"
                report(f"{slug}: failed ({files_task.scrub_paths(error, scratch)})")
                save_failed(slug, error, scratch, [api_key, run_env.password])
    if aborted:
        return EXIT_ABORTED
    return EXIT_PREFLIGHT if "blocked" in statuses else EXIT_OK


async def run_command(args: argparse.Namespace) -> int:
    """Preflight, then run the selected scenarios on an isolated copy."""
    slugs = select_scenarios(args.scenarios)
    scratch = Path(tempfile.mkdtemp(prefix="files_eval_"))
    os.environ["DB_PATH"] = str(scratch / "files_eval.db")
    reason = preflight()
    if reason:
        report(f"BLOCKED: {reason}")
        save_all_not_run(slugs, reason, scratch)
        shutil.rmtree(scratch, ignore_errors=True)
        summary_command()
        return EXIT_PREFLIGHT
    code = EXIT_ABORTED
    try:
        code = await run_scenarios(slugs, scratch)
    except (RuntimeError, ValueError, httpx.HTTPError, OSError) as exc:
        report(f"ABORTED: {files_task.scrub_paths(f'{type(exc).__name__}: {exc}', scratch)}")
    if code == EXIT_OK:
        shutil.rmtree(scratch, ignore_errors=True)
    else:
        report(f"scratch kept: {scratch}")
    summary_command()
    return code


def build_parser() -> argparse.ArgumentParser:
    """Command-line parser."""
    parser = argparse.ArgumentParser(description="Day 34 scenarios on an isolated copy.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="preflight only")
    run = sub.add_parser("run", help="run the scenarios")
    run.add_argument("--scenarios", default=None, help="comma list: usages,adr,changelog,readme")
    sub.add_parser("summary", help="write summary.md from saved files")
    return parser


def main() -> int:
    """Entry point."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    args = build_parser().parse_args()
    if args.command == "summary":
        return summary_command()
    if args.command == "check":
        return check_command()
    return asyncio.run(run_command(args))


if __name__ == "__main__":
    sys.exit(main())
