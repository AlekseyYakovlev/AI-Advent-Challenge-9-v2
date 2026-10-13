"""Control-run driver for the /help command on an isolated copy of the app.

Usage:
    python scripts/help_eval.py check
    python scripts/help_eval.py measure [--allow-draft]
    python scripts/help_eval.py spike [--ids ID,ID,ID]
    python scripts/help_eval.py run [--models local,deepseek]

The driver copies the working tree to a scratch directory, repoints the copy to UI :18000 /
Agent :18001, starts it with a scratch database, uploads README.md and docs/*.md of this
repository as the documentation base, connects the read-only `local-git` MCP server to this
repository, marks the base through PUT /api/v1/help/config and sends `/help` questions to the
HTTP chat endpoint with an API key. The user's app on the default ports and app.db are never
touched, and only processes started here are ever stopped.

The DeepSeek key is read from the repository .env at run time and handed to the child process
through its environment only. It is never written to a file, a log line or the console: every
saved or printed text passes through scrub().

Commands:
    check    preflight only: print which parts can run and which are `not run`
    measure  index the base, search every question, write eval_out/day31/threshold.json
    spike    try the combined quotes-plus-tools prompt on three questions per model
    run      send the control questions to every available model, save raw bodies and answers

A step that cannot run (LM Studio down, a model not loaded, no or refused DeepSeek key) is
recorded as `not run` with the reason; it is never recorded as passed.

Exit codes: 0 done, 1 a step aborted, 2 blocked by preflight or nothing could run.
"""

import argparse
import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCRIPTS_DIR: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import e2e_kb_playwright as kb  # noqa: E402
import httpx  # noqa: E402
import rag_dialog as dialog  # noqa: E402
import rag_eval  # noqa: E402
import rag_judge  # noqa: E402

UI_PORT: int = 18000
AGENT_PORT: int = 18001
DEFAULT_OUT: Path = REPO_ROOT / "eval_out" / "day31"
DEFAULT_FIXTURE: Path = REPO_ROOT / "tests" / "fixtures" / "help" / "control_set.json"
LOCAL_MODEL: str = "qwen/qwen3.5-9b"
EMBEDDING_MODEL: str = "text-embedding-bge-m3"
SERVER_NAME: str = "local-git"
KB_NAME: str = "project-docs"
DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
DEEPSEEK_ENV_NAME: str = "DEEPSEEK_API_KEY"
PREFERRED_DEEPSEEK_MODEL: str = "deepseek-flash"
EXIT_OK, EXIT_ABORTED, EXIT_PREFLIGHT = 0, 1, 2
REQUEST_TIMEOUT_S: float = 300.0
KB_READY_TIMEOUT_S: float = 900.0
SEARCH_TOP_K: int = 5
CHAT_TEMPERATURE: float = 0.0
CHAT_MAX_TOKENS: int = 8192
CHAT_CONTEXT_LENGTH: int = 16384
OWNER_NAME: str = "help_eval"
MODEL_KEYS: tuple[str, ...] = ("local", "deepseek")
EXPECTED_CATEGORIES: dict[str, int] = {"docs": 5, "git": 2, "mixed": 2, "out_of_corpus": 1}
REQUIRED_FIELDS: tuple[str, ...] = ("question", "expected_source", "expected_tool", "key_facts")
GIT_EXPECTED_FROM: frozenset[str] = frozenset({"git_branch", "git_log", "git_status"})
CLEAN_TREE_TEXT: str = "рабочая копия чистая"
SPIKE_CATEGORIES: tuple[str, ...] = ("docs", "git", "mixed")
NOT_RUN: str = "not run"


def report(message: str) -> None:
    """Print one terminal line (scripts are the one place where terminal output is allowed)."""
    print(message, flush=True)


# ---------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------


def validate_control_set(data: dict[str, Any]) -> list[str]:
    """Return the problems of a control set; an empty list means the set is well formed."""
    questions = data.get("questions")
    if not isinstance(questions, list):
        return ["questions: missing or not a list"]
    errors: list[str] = []
    if len(questions) != sum(EXPECTED_CATEGORIES.values()):
        errors.append(f"count: expected {sum(EXPECTED_CATEGORIES.values())} questions, got {len(questions)}")
    counts = dict(Counter(str(item.get("category")) for item in questions))
    if counts != EXPECTED_CATEGORIES:
        errors.append(f"categories: expected {EXPECTED_CATEGORIES}, got {counts}")
    ids = [item.get("id") for item in questions]
    duplicates = sorted({str(i) for i in ids if ids.count(i) > 1})
    if duplicates or any(not i for i in ids):
        errors.append(f"ids: duplicate or empty {duplicates}")
    for item in questions:
        for name in REQUIRED_FIELDS:
            value = item.get(name)
            empty = not value or (
                isinstance(value, list) and not all(isinstance(v, str) and v.strip() for v in value)
            )
            if empty or (isinstance(value, str) and not value.strip()):
                errors.append(f"{item.get('id')}: missing {name}")
    return errors


def load_control_set(path: Path, require_frozen: bool = True) -> list[dict[str, Any]]:
    """Load the control questions; a draft set is refused unless a draft is allowed."""
    data = rag_eval.load_fixture(path, require_frozen)
    errors = validate_control_set(data)
    if errors:
        raise ValueError("; ".join(errors))
    questions: list[dict[str, Any]] = data["questions"]
    return questions


def read_env_value(env_path: Path, name: str) -> str | None:
    """Value of `name` in a dotenv-style file (quotes stripped, comments ignored), else None."""
    try:
        text = Path(env_path).read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if value[:1] in ("'", '"') and value.endswith(value[:1]) and len(value) >= 2:
            return value[1:-1] or None
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        return value or None
    return None


def scrub(text: str, secrets_list: list[str]) -> str:
    """Replace every non-empty secret in a text with ***."""
    for secret in secrets_list:
        if secret:
            text = text.replace(secret, "***")
    return text


def _git(repo: Path, *args: str) -> str:
    """Run one read-only git command with list argv and return its stdout (no shell)."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout


def git_snapshot(repo: Path) -> dict[str, Any]:
    """Branch, short head, `git status --short` lines and the last five commit subjects."""
    return {
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        "head": _git(repo, "rev-parse", "--short", "HEAD").strip(),
        "status_short": [line for line in _git(repo, "status", "--short").split("\n") if line],
        "recent_commits": [
            line for line in _git(repo, "log", "-5", "--format=%s").split("\n") if line.strip()
        ],
    }


def _changed_paths(status_short: list[str]) -> list[str]:
    """Paths named by `git status --short` lines."""
    return [line[3:].strip().strip('"') for line in status_short if len(line) > 3]


def expected_answer(item: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """Reference answer: the recorded key facts, or text built from the live git state."""
    facts = "\n".join(str(fact) for fact in item.get("key_facts") or [])
    source = item.get("expected_from")
    if source not in GIT_EXPECTED_FROM:
        return facts
    if source == "git_branch":
        git_text = f"Текущая ветка: {snapshot.get('branch')}"
    elif source == "git_log":
        git_text = "Последние коммиты: " + "; ".join(snapshot.get("recent_commits", [])[:3])
    else:
        paths = _changed_paths(snapshot.get("status_short", []))
        git_text = "Изменены: " + ", ".join(paths) if paths else CLEAN_TREE_TEXT
    return f"{git_text}\n{facts}" if item.get("category") == "mixed" and facts else git_text


def _tool_summary(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool name (the server's own tool name when known) and success flag per call."""
    return [
        {"name": frame.get("tool") or frame.get("name"), "ok": bool(frame.get("ok"))}
        for frame in frames or []
    ]


def _source_files(rag: dict[str, Any] | None) -> list[str]:
    """Distinct source file names of a payload, in rank order."""
    files: list[str] = []
    for source in (rag or {}).get("sources") or []:
        name = str(source.get("file") or source.get("source") or "")
        if name and name not in files:
            files.append(name)
    return files


def question_record(
    item: dict[str, Any],
    body: dict[str, Any] | None,
    elapsed: float,
    error: str | None = None,
) -> dict[str, Any]:
    """One answer record from an HTTP body: answer, sources, quotes, tool calls and warnings."""
    body = body if isinstance(body, dict) else {}
    help_part = body.get("help") or {}
    notice = body.get("stored") is False
    rag = body.get("rag")
    model_quotes, auto_quotes, unverified = dialog._quote_counts(rag)
    scores = [s.get("score") for s in (rag or {}).get("sources") or [] if s.get("score") is not None]
    warnings = [str(w.get("text") or w) if isinstance(w, dict) else str(w)
                for w in help_part.get("warnings") or []]
    return {
        "id": item["id"],
        "category": item.get("category"),
        "question": item["question"],
        "answer": help_part.get("text") if notice else body.get("content"),
        "kind": help_part.get("kind") if notice else None,
        "stored": False if notice else (True if body.get("content") is not None else None),
        "sources": _source_files(rag),
        "best_score": max(scores) if scores else None,
        "quotes": model_quotes,
        "quotes_auto": auto_quotes,
        "quotes_unverified": unverified,
        "tool_calls": _tool_summary(body.get("tool_calls") or []),
        "warnings": warnings,
        "verdict": (rag or {}).get("verdict"),
        "kb_name": help_part.get("kb_name"),
        "error": error,
        "elapsed_s": round(elapsed, 2),
    }


def threshold_decision(
    doc_scores: list[float], outside_scores: list[float], current: float
) -> tuple[float, str]:
    """Written rule for the default threshold: keep it, move it to the midpoint, or keep it."""
    if not doc_scores:
        return current, "no documentation scores"
    if all(s >= current for s in doc_scores) and all(s < current for s in outside_scores):
        return current, "kept: every documentation score is at or above it, outside is below"
    top_outside = max(outside_scores) if outside_scores else 0.0
    if min(doc_scores) > top_outside:
        value = round((min(doc_scores) + top_outside) / 2, 2)
        return value, "midpoint of the lowest documentation score and the highest outside score"
    return current, "not separable"


def pick_deepseek_model(model_ids: list[str]) -> str | None:
    """The DeepSeek chat model to use: the preferred flash model, any flash model, else a plain one."""
    if not model_ids:
        return None
    if PREFERRED_DEEPSEEK_MODEL in model_ids:
        return PREFERRED_DEEPSEEK_MODEL
    flash = [m for m in model_ids if "flash" in m.lower()]
    if flash:
        return flash[0]
    plain = [m for m in model_ids if "reasoner" not in m.lower()]
    return (plain or model_ids)[0]


def pick_spike_ids(items: list[dict[str, Any]], requested: str | None) -> list[str]:
    """Spike question ids: the requested ones, else the first docs, git and mixed items."""
    available = [str(item["id"]) for item in items]
    if requested:
        chosen = [part.strip() for part in requested.split(",") if part.strip()]
        unknown = [c for c in chosen if c not in available]
        if not chosen or unknown:
            raise ValueError(f"unknown question id(s) {unknown or requested!r}; have {available}")
        return chosen
    picked: list[str] = []
    for category in SPIKE_CATEGORIES:
        first = next((i for i in items if i.get("category") == category), None)
        if first is not None:
            picked.append(str(first["id"]))
    return picked


def tool_called(record: dict[str, Any], expected_tool: str) -> bool | None:
    """True when the expected tool was called; None when the question expects no tool."""
    if expected_tool in ("", "none"):
        return None
    return any(
        str(call.get("name") or "").endswith(expected_tool) for call in record.get("tool_calls", [])
    )


def spike_entry(item: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """The spike result of one question on one model."""
    return {
        "id": item["id"],
        "category": item.get("category"),
        "answer": record["answer"],
        "sources": record["sources"],
        "quotes_verified": record["quotes"],
        "tool_calls": [c["name"] for c in record["tool_calls"]],
        "has_sources": bool(record["sources"]),
        "has_verified_quote": record["quotes"] > 0,
        "called_expected_tool": tool_called(record, str(item.get("expected_tool"))),
        "expected_tool": item.get("expected_tool"),
        "warnings": record["warnings"],
        "error": record["error"],
    }


def default_help_threshold() -> float:
    """Read DEFAULT_HELP_THRESHOLD from the source without importing application modules."""
    text = (REPO_ROOT / "agent" / "help_command.py").read_text(encoding="utf-8")
    match = re.search(r"^DEFAULT_HELP_THRESHOLD\s*(?::\s*float)?\s*=\s*([0-9.]+)", text, re.M)
    if match is None:
        raise RuntimeError("DEFAULT_HELP_THRESHOLD not found in agent/help_command.py")
    return float(match.group(1))


def isolated_overrides(
    scratch: Path,
    ui_port: int = UI_PORT,
    agent_port: int = AGENT_PORT,
    db_name: str = "help_eval.db",
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


def save_json(path: Path, data: Any, secrets_list: list[str]) -> None:
    """Write a scrubbed UTF-8 JSON file, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = scrub(json.dumps(data, ensure_ascii=False, indent=2), secrets_list)
    path.write_text(text, encoding="utf-8")


def deepseek_env_name(app_db: Path) -> str:
    """Env-variable name the user's DeepSeek provider declares (database opened read-only)."""
    try:
        conn = sqlite3.connect(f"file:{Path(app_db).resolve().as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return DEEPSEEK_ENV_NAME
    try:
        row = conn.execute(
            "SELECT api_key_env FROM llmprovider WHERE base_url LIKE '%deepseek%' "
            "AND api_key_env IS NOT NULL LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return DEEPSEEK_ENV_NAME
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else DEEPSEEK_ENV_NAME


# ---------------------------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------------------------


@dataclass
class Capabilities:
    """What can run: None means ok, a text is the `not run` reason."""

    blocked: str | None = None
    embedding: str | None = None
    local: str | None = None
    deepseek: str | None = None
    deepseek_model: str | None = None
    deepseek_key: str | None = field(default=None, repr=False)

    def model_status(self, key: str) -> str | None:
        """Reason a model cannot run, or None when it can."""
        return self.local if key == "local" else self.deepseek

    def hard_block(self) -> str | None:
        """Reason that blocks every command (no ports, no git, no embedding model)."""
        if self.blocked:
            return self.blocked
        if self.embedding:
            return f"embedding: {self.embedding}"
        return None


async def _check_lm_studio(caps: Capabilities) -> None:
    """Fill the embedding and local model status from the LM Studio control API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{kb.LM_URL}/api/v0/models")
            response.raise_for_status()
            entries = response.json().get("data", [])
    except (httpx.HTTPError, ValueError) as exc:
        reason = f"LM Studio not available at {kb.LM_URL} ({type(exc).__name__})"
        caps.embedding = reason
        caps.local = reason
        return
    by_id = {str(e.get("id")): e for e in entries if isinstance(e, dict)}
    embedding = by_id.get(EMBEDDING_MODEL)
    if embedding is None or embedding.get("type") != "embeddings":
        caps.embedding = f"embedding model {EMBEDDING_MODEL} is not listed"
    chat = by_id.get(LOCAL_MODEL)
    if chat is None:
        caps.local = f"model {LOCAL_MODEL} is not listed"
    elif chat.get("state") != "loaded":
        caps.local = f"model {LOCAL_MODEL} is not loaded"


async def _check_deepseek(caps: Capabilities) -> None:
    """Prove that the key from the repository .env is accepted; only status codes are reported."""
    name = deepseek_env_name(REPO_ROOT / "app.db")
    key = read_env_value(REPO_ROOT / ".env", name)
    if not key:
        caps.deepseek = f"{name} is not set in .env"
        return
    caps.deepseek_key = key
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{DEEPSEEK_BASE_URL}/v1/models", headers={"Authorization": f"Bearer {key}"}
            )
    except httpx.HTTPError as exc:
        caps.deepseek = f"DeepSeek unreachable ({type(exc).__name__})"
        return
    if response.status_code in (401, 403):
        caps.deepseek = f"key rejected (HTTP {response.status_code})"
        return
    if response.status_code != 200:
        caps.deepseek = f"DeepSeek answered HTTP {response.status_code}"
        return
    ids = [str(e["id"]) for e in response.json().get("data", []) if isinstance(e, dict) and "id" in e]
    model = pick_deepseek_model(ids)
    if model is None:
        caps.deepseek = "DeepSeek lists no models"
        return
    code, message = await rag_judge.check_access(DEEPSEEK_BASE_URL, key, model)
    if code != 0:
        caps.deepseek = scrub(message, [key])
        return
    caps.deepseek_model = model


async def preflight() -> Capabilities:
    """Decide what can run; touches no running app."""
    caps = Capabilities()
    busy = [p for p in (UI_PORT, AGENT_PORT) if not kb.port_is_free(p)]
    if busy:
        caps.blocked = f"port(s) {busy} are in use; the isolated copy needs them free"
    elif shutil.which("git") is None:
        caps.blocked = "git is not installed"
    await _check_lm_studio(caps)
    await _check_deepseek(caps)
    return caps


def print_capabilities(caps: Capabilities) -> None:
    """One line per capability: ok or `not run: <reason>`."""
    if caps.blocked:
        report(f"BLOCKED: {caps.blocked}")
    for label, reason in (
        ("embedding", caps.embedding),
        ("local model", caps.local),
        ("deepseek", caps.deepseek),
    ):
        report(f"{label}: {'ok' if reason is None else f'{NOT_RUN}: {reason}'}")


# ---------------------------------------------------------------------------------------------
# Isolated app
# ---------------------------------------------------------------------------------------------


async def build_run_env(scratch: Path, deepseek_key: str | None) -> dialog.RunEnv:
    """Copy the tree, seed one scratch user and build the child environment."""
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
    if deepseek_key:
        env[DEEPSEEK_ENV_NAME] = deepseek_key
    return dialog.RunEnv(
        scratch, copy_dir, db_path, Path(overrides["KB_STORAGE_DIR"]), env, OWNER_NAME, password
    )


@contextlib.asynccontextmanager
async def isolated_app(
    scratch: Path, deepseek_key: str | None
) -> AsyncIterator[tuple[dialog.RunEnv, httpx.AsyncClient]]:
    """Start the copy, log in with a cookie client, and always stop what was started."""
    run_env = await build_run_env(scratch, deepseek_key)
    proc, log_file = await dialog.start_app(run_env)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
            await dialog.login(client, run_env)
            yield run_env, client
    finally:
        await kb.teardown(proc)
        log_file.close()


def documentation_files() -> list[Path]:
    """README.md and every docs/*.md of the real repository."""
    return [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]


async def wait_kb_ready(client: httpx.AsyncClient, kb_id: int) -> dict[str, Any]:
    """Poll the base until it is ready; a failed base or a timeout raises with the cause."""
    deadline = time.monotonic() + KB_READY_TIMEOUT_S
    last_report = 0.0
    while True:
        response = await client.get(f"{kb.AGENT_URL}/api/v1/kb/{kb_id}")
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        if data.get("status") == "ready":
            return data
        if data.get("status") == "failed":
            raise RuntimeError(f"indexing failed: {data.get('error')}")
        if time.monotonic() > deadline:
            raise RuntimeError("indexing did not finish in time")
        if time.monotonic() - last_report > 60:
            report(f"indexing: {data.get('done')}/{data.get('total')} ({data.get('status')})")
            last_report = time.monotonic()
        await asyncio.sleep(3)


async def upload_base(client: httpx.AsyncClient) -> tuple[dict[str, Any], list[str]]:
    """Upload the real documentation files under relative names and wait for the index."""
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    names: list[str] = []
    for path in documentation_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        names.append(relative)
        files.append(("files", (relative, path.read_bytes(), "text/markdown")))
    response = await client.post(
        f"{kb.AGENT_URL}/api/v1/kb",
        data={"name": KB_NAME, "strategy": "structural", "embedding_model": EMBEDDING_MODEL},
        files=files,
        headers=dialog._json_headers(),
    )
    if response.status_code != 202:
        raise RuntimeError(f"base upload refused: HTTP {response.status_code} {response.text[:300]}")
    created = response.json()
    report(f"base {KB_NAME}: uploaded {len(names)} files, indexing")
    return await wait_kb_ready(client, int(created["id"])), names


async def connect_git_server(client: httpx.AsyncClient, run_env: dialog.RunEnv) -> dict[str, Any]:
    """Create and connect the read-only local-git server aimed at the real repository."""
    script = run_env.copy_dir / "mcp_servers" / "project_git.py"
    created = await client.post(
        f"{kb.AGENT_URL}/api/v1/mcp/servers",
        json={
            "name": SERVER_NAME,
            "command": sys.executable,
            "args": [str(script), f"--root={REPO_ROOT}"],
            "cwd": str(REPO_ROOT),
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
    status = str(connection.get("status"))
    report(f"mcp server {SERVER_NAME}: {status}")
    return {"id": server_id, "status": status}


async def setup_project(client: httpx.AsyncClient, run_env: dialog.RunEnv) -> dict[str, Any]:
    """Index the documentation, connect local-git, mark the base and create an API key."""
    base, names = await upload_base(client)
    mcp = await connect_git_server(client, run_env)
    marked = await client.put(
        f"{kb.AGENT_URL}/api/v1/help/config",
        json={"kb_id": int(base["id"])},
        headers=dialog._json_headers(),
    )
    marked.raise_for_status()
    key_response = await client.post(
        f"{kb.AGENT_URL}/api/v1/api-keys", json={"name": "help-eval"}, headers=dialog._json_headers()
    )
    key_response.raise_for_status()
    return {
        "kb": base,
        "files": names,
        "mcp": mcp,
        "help_config": marked.json(),
        "api_key": key_response.json()["key"],
    }


# ---------------------------------------------------------------------------------------------
# Chat over HTTP
# ---------------------------------------------------------------------------------------------


async def provider_models(client: httpx.AsyncClient, caps: Capabilities) -> dict[str, dict[str, Any]]:
    """Provider id and model id per model key, resolved from the running app."""
    response = await client.get(f"{kb.AGENT_URL}/api/v1/llm-providers/models")
    response.raise_for_status()
    found: dict[str, dict[str, Any]] = {}
    for group in response.json():
        ids = [str(m["id"]) for m in group.get("models") or []]
        if group.get("kind") == "lm_studio" and "local" not in found:
            if LOCAL_MODEL in ids:
                found["local"] = {"provider_id": group["provider_id"], "model": LOCAL_MODEL}
        elif "deepseek" in str(group.get("name", "")).lower() and "deepseek" not in found:
            model = pick_deepseek_model(ids)
            if model:
                found["deepseek"] = {"provider_id": group["provider_id"], "model": model}
    return found


async def create_chat(client: httpx.AsyncClient, title: str) -> int:
    """Create a fresh chat with fixed generation settings."""
    created = await client.post(
        f"{kb.AGENT_URL}/api/v1/chats", json={"title": title}, headers=dialog._json_headers()
    )
    created.raise_for_status()
    chat_id = int(created.json()["id"])
    settings_resp = await client.put(
        f"{kb.AGENT_URL}/api/v1/settings",
        json={
            "chat_id": chat_id,
            "temperature": CHAT_TEMPERATURE,
            "max_tokens": CHAT_MAX_TOKENS,
            "context_length": CHAT_CONTEXT_LENGTH,
        },
        headers=dialog._json_headers(),
    )
    settings_resp.raise_for_status()
    return chat_id


async def send_help(
    chat_id: int, question: str, target: dict[str, Any], api_key: str
) -> tuple[dict[str, Any] | None, float, str | None, int | None]:
    """POST one /help message with the API key; returns body, elapsed, error text and status."""
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as http:
            response = await http.post(
                f"{kb.AGENT_URL}/api/v1/chats/{chat_id}/messages",
                json={
                    "content": f"/help {question}",
                    "model": target["model"],
                    "provider_id": target["provider_id"],
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        return None, time.monotonic() - started, f"{type(exc).__name__}: {exc}", None
    elapsed = time.monotonic() - started
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200:
        detail = body.get("detail") if isinstance(body, dict) else response.text[:300]
        return body if isinstance(body, dict) else None, elapsed, f"HTTP {response.status_code}: {detail}", response.status_code
    return body, elapsed, None, 200


async def ask_all(
    client: httpx.AsyncClient,
    items: list[dict[str, Any]],
    target: dict[str, Any],
    api_key: str,
    label: str,
    secrets_list: list[str],
    raw_dir: Path | None,
) -> list[dict[str, Any]]:
    """Send the items one by one in a fresh chat; every failure is recorded, never skipped."""
    chat_id = await create_chat(client, f"help eval {label}")
    sent: list[float] = []
    records: list[dict[str, Any]] = []
    for item in items:
        delay = dialog.send_delay(sent, time.monotonic())
        if delay > 0:
            await asyncio.sleep(delay)
        sent.append(time.monotonic())
        body, elapsed, error, status = await send_help(chat_id, item["question"], target, api_key)
        record = question_record(item, body, elapsed, error)
        record["status"] = status
        records.append(record)
        if raw_dir is not None:
            save_json(raw_dir / f"{item['id']}.json", {"status": status, "error": error, "body": body}, secrets_list)
        report(f"{label} {item['id']}: {'error ' + str(error)[:80] if error else 'ok'} ({record['elapsed_s']}s)")
    return records


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------


def _now() -> str:
    """Current UTC time as ISO text."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def measure_command(args: argparse.Namespace, caps: Capabilities, scratch: Path) -> int:
    """Index the real documentation, search every question and write threshold.json."""
    items = load_control_set(args.fixture, require_frozen=not args.allow_draft)
    snapshot = git_snapshot(REPO_ROOT)
    async with isolated_app(scratch, None) as (run_env, client):
        base, names = await upload_base(client)
        rows: list[dict[str, Any]] = []
        for item in items:
            response = await client.post(
                f"{kb.AGENT_URL}/api/v1/kb/{base['id']}/search",
                json={"query": item["question"], "top_k": SEARCH_TOP_K},
                headers=dialog._json_headers(),
            )
            response.raise_for_status()
            results = response.json().get("results") or []
            best = results[0] if results else {}
            rows.append({
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "expected_source": item["expected_source"],
                "best_score": best.get("score"),
                "best_source": best.get("source"),
                "top": [{"source": r.get("source"), "score": r.get("score")} for r in results],
            })
            report(f"search {item['id']}: {best.get('score')} {best.get('source')}")
    current = default_help_threshold()
    doc = [float(r["best_score"]) for r in rows if r["category"] == "docs" and r["best_score"] is not None]
    outside = [float(r["best_score"]) for r in rows if r["category"] == "out_of_corpus" and r["best_score"] is not None]
    value, reason = threshold_decision(doc, outside, current)
    changed = {line[3:].strip().strip('"') for line in snapshot["status_short"] if len(line) > 3}
    save_json(args.out / "threshold.json", {
        "date": _now(),
        "embedding_model": base.get("embedding_model"),
        "chunking": {
            "strategy": base.get("strategy"),
            "chunk_size": base.get("chunk_size"),
            "chunk_overlap": base.get("chunk_overlap"),
        },
        "chunk_count": base.get("chunk_count"),
        "git": snapshot,
        "corpus": {
            "commit": snapshot["head"],
            "files": names,
            "dirty_files": [n for n in names if n in changed],
        },
        "questions": rows,
        "doc_scores": doc,
        "outside_scores": outside,
        "current_threshold": current,
        "decision": {"value": value, "reason": reason},
    }, [])
    report(f"threshold: current {current}, decision {value} ({reason})")
    return EXIT_OK


def available_models(caps: Capabilities, wanted: list[str]) -> tuple[list[str], dict[str, str]]:
    """Models that can run and the `not run` reason of the others."""
    runnable: list[str] = []
    skipped: dict[str, str] = {}
    for key in wanted:
        reason = caps.model_status(key)
        if reason is None:
            runnable.append(key)
        else:
            skipped[key] = reason
            report(f"{key}: {NOT_RUN}: {reason}")
    return runnable, skipped


async def spike_command(args: argparse.Namespace, caps: Capabilities, scratch: Path) -> int:
    """Try three questions per available model and write spike.json."""
    items = load_control_set(args.fixture, require_frozen=True)
    by_id = {str(i["id"]): i for i in items}
    ids = pick_spike_ids(items, args.ids)
    runnable, skipped = available_models(caps, list(MODEL_KEYS))
    if not runnable:
        save_json(args.out / "spike.json", {"date": _now(), "models": {k: {"status": NOT_RUN, "reason": r} for k, r in skipped.items()}}, [])
        return EXIT_PREFLIGHT
    key = caps.deepseek_key if "deepseek" in runnable else None
    secret_list = [key or ""]
    result: dict[str, Any] = {"date": _now(), "questions": ids, "models": {}}
    async with isolated_app(scratch, key) as (run_env, client):
        project = await setup_project(client, run_env)
        secret_list.append(project["api_key"])
        targets = await provider_models(client, caps)
        result["mcp"] = project["mcp"]
        for model_key in runnable:
            target = targets.get(model_key)
            if target is None:
                result["models"][model_key] = {"status": NOT_RUN, "reason": "model not offered by the app"}
                continue
            records = await ask_all(
                client, [by_id[i] for i in ids], target, project["api_key"], f"spike {model_key}", secret_list, None
            )
            result["models"][model_key] = {
                "status": "run",
                "model": target["model"],
                "results": [spike_entry(by_id[r["id"]], r) for r in records],
            }
    for model_key, reason in skipped.items():
        result["models"][model_key] = {"status": NOT_RUN, "reason": reason}
    save_json(args.out / "spike.json", result, secret_list)
    return EXIT_OK


async def run_command(args: argparse.Namespace, caps: Capabilities, scratch: Path) -> int:
    """Send the control questions to every available model and save raw bodies and answers."""
    items = load_control_set(args.fixture, require_frozen=True)
    wanted = [part.strip() for part in args.models.split(",") if part.strip()]
    unknown = [w for w in wanted if w not in MODEL_KEYS]
    if not wanted or unknown:
        report(f"preflight: unknown model(s) {unknown or args.models!r}; choose from {MODEL_KEYS}")
        return EXIT_PREFLIGHT
    runnable, skipped = available_models(caps, wanted)
    statuses: dict[str, Any] = {k: {"status": NOT_RUN, "reason": r} for k, r in skipped.items()}
    snapshot = git_snapshot(REPO_ROOT)
    if not runnable:
        _write_run_meta(args, snapshot, statuses, None, None)
        return EXIT_PREFLIGHT
    key = caps.deepseek_key if "deepseek" in runnable else None
    secret_list = [key or ""]
    config_used: dict[str, Any] | None = None
    mcp_info: dict[str, Any] | None = None
    async with isolated_app(scratch, key) as (run_env, client):
        project = await setup_project(client, run_env)
        secret_list.append(project["api_key"])
        mcp_info = project["mcp"]
        config = await client.get(f"{kb.AGENT_URL}/api/v1/help/config")
        config.raise_for_status()
        config_used = config.json()
        targets = await provider_models(client, caps)
        for model_key in runnable:
            target = targets.get(model_key)
            if target is None:
                statuses[model_key] = {"status": NOT_RUN, "reason": "model not offered by the app"}
                continue
            records = await ask_all(
                client, items, target, project["api_key"], model_key, secret_list,
                args.out / "raw" / model_key,
            )
            for record, item in zip(records, items):
                record["expected_answer"] = expected_answer(item, snapshot)
                record["expected_source"] = item["expected_source"]
                record["expected_tool"] = item["expected_tool"]
            save_json(args.out / f"answers_{model_key}.json", records, secret_list)
            statuses[model_key] = {
                "status": "run",
                "model": target["model"],
                "questions": len(records),
                "failed": sum(1 for r in records if r["error"]),
            }
    _write_run_meta(args, snapshot, statuses, config_used, mcp_info)
    if not any(s["status"] == "run" for s in statuses.values()):
        return EXIT_PREFLIGHT
    return EXIT_OK


def _write_run_meta(
    args: argparse.Namespace,
    snapshot: dict[str, Any],
    statuses: dict[str, Any],
    config_used: dict[str, Any] | None,
    mcp_info: dict[str, Any] | None,
) -> None:
    """Write run_meta.json: date, git state, fixture hash, models, settings and statuses."""
    save_json(args.out / "run_meta.json", {
        "date": _now(),
        "git": snapshot,
        "fixture": str(args.fixture.relative_to(REPO_ROOT).as_posix()) if args.fixture.is_relative_to(REPO_ROOT) else str(args.fixture),
        **dialog._fixture_hashes(args.fixture),
        "embedding_model": EMBEDDING_MODEL,
        "help_config": config_used,
        "mcp": mcp_info,
        "models": statuses,
    }, [])


async def check_command(caps: Capabilities) -> int:
    """Print what can run; exit 2 when nothing can."""
    print_capabilities(caps)
    return EXIT_PREFLIGHT if caps.hard_block() else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Command-line parser."""
    parser = argparse.ArgumentParser(description="Control run of /help on an isolated copy.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="preflight only")
    measure = sub.add_parser("measure", help="measure the /help threshold on the real base")
    measure.add_argument("--allow-draft", action="store_true", help="accept a draft control set")
    spike = sub.add_parser("spike", help="try the combined prompt on three questions")
    spike.add_argument("--ids", default=None, help="comma list of question ids")
    run = sub.add_parser("run", help="run the control questions")
    run.add_argument("--models", default=",".join(MODEL_KEYS), help="comma list: local,deepseek")
    return parser


async def amain(args: argparse.Namespace, scratch: Path) -> int:
    """Run the chosen command after preflight."""
    caps = await preflight()
    if args.command == "check":
        return await check_command(caps)
    print_capabilities(caps)
    blocked = caps.hard_block()
    if blocked:
        report(f"BLOCKED: {blocked}")
        if args.command == "run":
            _write_run_meta(args, git_snapshot(REPO_ROOT), {k: {"status": NOT_RUN, "reason": blocked} for k in MODEL_KEYS}, None, None)
        return EXIT_PREFLIGHT
    try:
        if args.command == "measure":
            return await measure_command(args, caps, scratch)
        if args.command == "spike":
            return await spike_command(args, caps, scratch)
        return await run_command(args, caps, scratch)
    except (RuntimeError, ValueError, httpx.HTTPError, OSError) as exc:
        report(scrub(f"ABORTED: {type(exc).__name__}: {exc}", [caps.deepseek_key or ""]))
        return EXIT_ABORTED


def main() -> int:
    """Entry point; the whole process works on a scratch database, never app.db."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    args = build_parser().parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="help_eval_"))
    os.environ["DB_PATH"] = str(scratch / "help_eval.db")
    try:
        return asyncio.run(amain(args, scratch))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
