"""Multi-turn RAG dialog scenarios driven through the real WebSocket chat on an isolated copy."""

import argparse
import asyncio
import hashlib
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCRIPTS_DIR: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import e2e_kb_playwright as kb  # noqa: E402
import httpx  # noqa: E402
import rag_eval  # noqa: E402

FORBIDDEN_PORTS: frozenset[int] = frozenset({8000, 8001})
FORBIDDEN_DB_NAMES: frozenset[str] = frozenset({"app.db"})
MODEL_QUOTE_STATES: frozenset[str] = frozenset({"exact", "fuzzy"})
MEMORY_FIELDS: tuple[str, ...] = ("goal", "clarified", "constraints")
RUN_NAMES: tuple[str, ...] = ("main", "baseline")
EXIT_OK, EXIT_ABORTED, EXIT_PREFLIGHT = 0, 1, 2
DEFAULT_OUT: Path = REPO_ROOT / "eval_out" / "day25"
DEFAULT_FIXTURE: Path = REPO_ROOT / "tests" / "fixtures" / "rag" / "dialog_scenarios.json"
DEFAULT_SOURCE_DB: Path = REPO_ROOT / "eval_out" / "day23" / "eval.db"
DEFAULT_SOURCE_KB: Path = REPO_ROOT / "eval_out" / "day23" / "eval_kb"
OWNER_USER_ID: int = 1
CHAT_TEMPERATURE: float = 0.0


def report(message: str) -> None:
    """Print one terminal line (scripts are the one place where terminal output is allowed)."""
    print(message, flush=True)


def load_scenarios(path: Path, require_frozen: bool = True) -> list[dict[str, Any]]:
    """Load the dialog fixture and return its scenarios; a draft fixture is refused."""
    data: dict[str, Any] = rag_eval.load_fixture(path, require_frozen)
    scenarios: list[dict[str, Any]] = data["scenarios"]
    return scenarios


def normalise(text: str | None) -> str:
    """Lowercase and collapse whitespace for comparison."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def turn_verdict(frame_type: str, rag: dict[str, Any] | None) -> str:
    """Classify a turn as ok, gated, model_idk, no_rag or error."""
    if frame_type != "done":
        return "error"
    if not rag:
        return "no_rag"
    if rag.get("gated"):
        return "gated"
    verdict = rag.get("verdict")
    if verdict == "model_idk":
        return "model_idk"
    if verdict == "ok":
        return "ok"
    return "no_rag"


def _item_texts(items: Any) -> list[str]:
    """Normalised texts of a snapshot list whose items are id/text dicts or plain strings."""
    texts: list[str] = []
    for item in items or []:
        raw = item.get("text") if isinstance(item, dict) else item
        texts.append(normalise(str(raw or "")))
    return texts


def memory_check(
    expect_memory: dict[str, list[str]] | None, snapshot: dict[str, Any] | None
) -> tuple[int, int]:
    """Count expected memory entries and how many of them the snapshot holds (substring match)."""
    expected = 0
    found = 0
    for field in MEMORY_FIELDS:
        wanted = [normalise(entry) for entry in (expect_memory or {}).get(field, [])]
        expected += len(wanted)
        if not snapshot:
            continue
        if field == "goal":
            haystacks = [normalise(snapshot.get("goal"))]
        else:
            haystacks = _item_texts(snapshot.get(field))
        found += sum(1 for entry in wanted if any(entry in text for text in haystacks))
    return expected, found


def article_in_sources(article: str, sources: list[dict[str, Any]] | None) -> bool:
    """True when a source section names exactly this article."""
    return any(
        rag_eval.parse_article(source.get("section")) == article for source in sources or []
    )


def _quote_counts(rag: dict[str, Any] | None) -> tuple[int, int, int]:
    """Model-verified, auto and unverified quote counts of a payload."""
    model = auto = unverified = 0
    for quote in (rag or {}).get("quotes") or []:
        if quote.get("auto"):
            auto += 1
        elif quote.get("state") in MODEL_QUOTE_STATES:
            model += 1
        else:
            unverified += 1
    return model, auto, unverified


def _goal_checks(
    turn_spec: dict[str, Any], answer: str, snapshot: dict[str, Any] | None, first_goal: str | None
) -> dict[str, Any]:
    """Goal-text, keyword and goal-kept fields; all None without a task-memory snapshot."""
    result: dict[str, Any] = {
        "goal_text": None,
        "goal_unchanged": None,
        "keywords_ok": None,
        "goal_kept": None,
    }
    if snapshot is None:
        return result
    goal = snapshot.get("goal")
    result["goal_text"] = goal or None
    if first_goal is not None:
        result["goal_unchanged"] = normalise(goal) == normalise(first_goal)
    keywords = turn_spec.get("expect_keywords") or []
    if keywords:
        lowered = normalise(answer)
        result["keywords_ok"] = all(normalise(word) in lowered for word in keywords)
    result["goal_kept"] = result["goal_unchanged"] is not False and result["keywords_ok"] is not False
    return result


def compute_checks(
    turn_spec: dict[str, Any],
    frame_type: str,
    answer: str,
    rag: dict[str, Any] | None,
    first_goal: str | None,
) -> dict[str, Any]:
    """Automatic per-turn checks; a gated, refused or failed turn is recorded, never raised."""
    rag_data = rag if frame_type == "done" else None
    snapshot = (rag_data or {}).get("task_memory")
    search = (rag_data or {}).get("search") or {}
    model, auto, unverified = _quote_counts(rag_data)
    expected, found = memory_check(turn_spec.get("expect_memory"), snapshot)
    article = turn_spec.get("expect_article")
    sources = (rag_data or {}).get("sources") or []
    checks: dict[str, Any] = {
        "sources_present": bool(sources),
        "quotes_model": model,
        "quotes_auto": auto,
        "quotes_unverified": unverified,
        "verdict": turn_verdict(frame_type, rag_data),
        "memory_expected": expected,
        "memory_found": found,
        "memory_ok": (found == expected) if expected else None,
        "article_expected": article,
        "article_ok": article_in_sources(article, sources) if article else None,
        "condensed": bool(search.get("condensed")),
        "condensed_query": search.get("rewritten"),
        "memory_failed": snapshot.get("failed") if snapshot else None,
    }
    checks.update(_goal_checks(turn_spec, answer, snapshot, first_goal))
    return checks


def assert_isolated(db_path: Path, ui_port: int, agent_port: int) -> None:
    """Refuse the user's ports, the live database and any database inside the repository."""
    busy = sorted({ui_port, agent_port} & FORBIDDEN_PORTS)
    if busy:
        raise RuntimeError(f"refusing to use the live app port(s) {busy}")
    resolved = Path(db_path).resolve()
    if resolved.name in FORBIDDEN_DB_NAMES:
        raise RuntimeError(f"refusing to use the live database {resolved.name}")
    if resolved.is_relative_to(REPO_ROOT.resolve()):
        raise RuntimeError(f"refusing a database inside the repository: {resolved}")


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a UTF-8 JSON file, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rag_eval._write_json(path, data)


def parse_runs(text: str) -> list[str]:
    """Validate a comma list of run names."""
    runs = [part.strip() for part in text.split(",") if part.strip()]
    unknown = [run for run in runs if run not in RUN_NAMES]
    if not runs or unknown:
        raise ValueError(f"unknown run(s) {unknown or text!r}; choose from {', '.join(RUN_NAMES)}")
    return runs


def parse_scenarios(text: str | None, available: list[str]) -> list[str]:
    """Validate a comma list of scenario ids against the fixture; None selects all."""
    if not text:
        return list(available)
    chosen = [part.strip() for part in text.split(",") if part.strip()]
    unknown = [item for item in chosen if item not in available]
    if not chosen or unknown:
        raise ValueError(f"unknown scenario(s) {unknown or text!r}; fixture has {available}")
    return chosen


def run_config(run_name: str, args: argparse.Namespace, scratch: Path | None = None) -> dict[str, str]:
    """Environment overrides of the isolated app; the baseline run switches task memory off."""
    if run_name not in RUN_NAMES:
        raise ValueError(f"unknown run {run_name!r}")
    base = scratch or Path(tempfile.gettempdir()) / f"rag_dialog_{run_name}"
    return {
        "DB_PATH": str(base / "dialog.db"),
        "KB_STORAGE_DIR": str(base / "kb"),
        "UI_PORT": str(kb.UI_PORT),
        "AGENT_PORT": str(kb.AGENT_PORT),
        "TASK_MEMORY_ENABLED": "true" if run_name == "main" else "false",
    }


def rag_settings(args: argparse.Namespace) -> dict[str, Any]:
    """Per-chat RAG configuration of the Day 24 setup plus the history window."""
    return {
        "mode": "rag",
        "kb_id": args.kb_id,
        "top_k": 5,
        "candidate_k": 20,
        "threshold": None,
        "strict": True,
        "lexical": False,
        "llm_rerank": False,
        "hybrid": False,
        "rewrite": False,
        "history_turns": args.history_turns,
    }


async def drain_turn(ws: Any, timeout: float) -> tuple[str, str, dict[str, Any] | None, str | None]:
    """Read frames until done or error; returns frame type, answer, last frame and an error text."""
    tokens: list[str] = []
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except TimeoutError:
            return "error", "".join(tokens), None, f"timeout: no frame within {timeout}s"
        except Exception as exc:  # a closed or broken socket must be recorded, not raised
            if not type(exc).__name__.startswith("ConnectionClosed"):
                raise
            return "error", "".join(tokens), None, f"closed: {type(exc).__name__}"
        try:
            frame: dict[str, Any] = json.loads(raw)
        except (TypeError, ValueError):
            continue
        kind = frame.get("type")
        if kind == "token":
            tokens.append(str(frame.get("content", "")))
        elif kind == "done":
            return "done", "".join(tokens), frame, None
        elif kind == "error":
            code = frame.get("code") or "error"
            return "error", "".join(tokens), frame, f"{code}: {frame.get('detail', '')}"


def turn_record(
    spec: dict[str, Any],
    outcome: tuple[str, str, dict[str, Any] | None, str | None],
    elapsed: float,
    first_goal: str | None,
) -> dict[str, Any]:
    """One raw turn record with its automatic checks."""
    frame_type, answer, frame, error = outcome
    rag = (frame or {}).get("rag") if frame_type == "done" else None
    return {
        "id": spec["id"],
        "kind": spec["kind"],
        "question": spec["text"],
        "answer": answer,
        "frame_type": frame_type,
        "error": error,
        "elapsed_s": round(elapsed, 2),
        "rag": rag,
        "task_memory": (rag or {}).get("task_memory"),
        "checks": compute_checks(spec, frame_type, answer, rag, first_goal),
    }


def raw_path(out: Path, run_name: str, scenario_id: str) -> Path:
    """Raw file of one run and scenario."""
    return out / "raw" / f"{run_name}_{scenario_id}.json"


def scenario_counts(turns: list[dict[str, Any]]) -> dict[str, int]:
    """Turn, done, error and gated counts of one scenario."""
    return {
        "turns": len(turns),
        "done": sum(1 for t in turns if t["frame_type"] == "done"),
        "errors": sum(1 for t in turns if t["frame_type"] == "error"),
        "gated": sum(1 for t in turns if t["checks"]["verdict"] == "gated"),
    }


@dataclass
class RunEnv:
    """Scratch directory, app copy and environment of one isolated app instance."""

    scratch: Path
    copy_dir: Path
    db_path: Path
    kb_dir: Path
    env: dict[str, str]
    username: str
    password: str


def preflight_dialog(args: argparse.Namespace) -> int | None:
    """Return an exit code when the run must not start, else None; touches no running app."""
    try:
        load_scenarios(args.fixture, require_frozen=True)
    except SystemExit:
        return EXIT_PREFLIGHT
    try:
        assert_isolated(Path(tempfile.gettempdir()) / "dialog.db", kb.UI_PORT, kb.AGENT_PORT)
    except RuntimeError as exc:
        report(f"BLOCKED: {exc}")
        return EXIT_PREFLIGHT
    if args.source_db.name in FORBIDDEN_DB_NAMES:
        report("BLOCKED: the source database must not be app.db")
        return EXIT_PREFLIGHT
    for label, path in (("source database", args.source_db), ("source KB directory", args.source_kb)):
        if not Path(path).exists():
            report(f"BLOCKED: {label} not found: {path}")
            return EXIT_PREFLIGHT
    busy = [p for p in (kb.UI_PORT, kb.AGENT_PORT) if not kb.port_is_free(p)]
    if busy:
        report(f"BLOCKED: port(s) {busy} are in use; the isolated copy needs them free.")
        return EXIT_PREFLIGHT
    try:
        httpx.get(f"{kb.LM_URL}/v1/models", timeout=5.0).raise_for_status()
    except httpx.HTTPError as exc:
        report(f"BLOCKED: LM Studio not available at {kb.LM_URL} ({type(exc).__name__})")
        return EXIT_PREFLIGHT
    return None


def _copy_database(source: Path, target: Path) -> None:
    """Copy a database through a read-only connection, so the source is never written."""
    src = sqlite3.connect(f"file:{Path(source).resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _set_owner_password(db_path: Path, user_id: int, password: str) -> str:
    """Give the KB owner in the scratch copy a known password; returns the username."""
    from shared.auth import hash_password

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT username FROM user WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"owner user {user_id} not found in the copied database")
        conn.execute(
            "UPDATE user SET password_hash = ? WHERE id = ?", (hash_password(password), user_id)
        )
        conn.commit()
    finally:
        conn.close()
    return str(row[0])


def prepare_run(run_name: str, args: argparse.Namespace, scratch: Path) -> RunEnv:
    """Copy the app tree, the source database and the KB into the scratch directory."""
    overrides = run_config(run_name, args, scratch)
    db_path = Path(overrides["DB_PATH"])
    assert_isolated(db_path, int(overrides["UI_PORT"]), int(overrides["AGENT_PORT"]))
    copy_dir = kb.prepare_copy(scratch)
    kb_dir = Path(overrides["KB_STORAGE_DIR"])
    _copy_database(args.source_db, db_path)
    shutil.copytree(args.source_kb, kb_dir)
    password = secrets.token_urlsafe(16)
    username = _set_owner_password(db_path, OWNER_USER_ID, password)
    env = {
        **os.environ,
        **overrides,
        "LM_STUDIO_BASE_URL": kb.LM_URL,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join([str(copy_dir), *filter(None, [os.environ.get("PYTHONPATH")])]),
    }
    return RunEnv(scratch, copy_dir, db_path, kb_dir, env, username, password)


async def start_app(run_env: RunEnv) -> tuple[asyncio.subprocess.Process, Any]:
    """Start the copy's supervisor with asyncio and wait until UI and Agent answer."""
    log_file = open(run_env.scratch / "app.log", "wb")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(run_env.copy_dir / "run.py"),
        cwd=str(run_env.copy_dir), env=run_env.env, stdout=log_file, stderr=log_file,
    )
    async with httpx.AsyncClient(timeout=3.0) as client:
        ready = await kb.wait_for_app(client)
    if not ready:
        await kb.teardown(proc)
        log_file.close()
        raise RuntimeError("isolated app did not become healthy")
    return proc, log_file


def _json_headers() -> dict[str, str]:
    """Origin header the app's mutating routes require."""
    return {"Origin": kb.UI_URL}


async def login(client: httpx.AsyncClient, run_env: RunEnv) -> str:
    """Log in as the KB owner and return the session cookie value."""
    from shared.auth import SESSION_COOKIE_NAME

    response = await client.post(
        f"{kb.AGENT_URL}/api/v1/auth/login",
        json={"username": run_env.username, "password": run_env.password},
        headers=_json_headers(),
    )
    if response.status_code != 200:
        raise RuntimeError(f"login failed with status {response.status_code}")
    token = client.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise RuntimeError("login set no session cookie")
    return token


async def lm_provider_id(client: httpx.AsyncClient) -> int:
    """Id of the seeded LM Studio provider."""
    response = await client.get(f"{kb.AGENT_URL}/api/v1/llm-providers")
    response.raise_for_status()
    for provider in response.json():
        if provider.get("kind") == "lm_studio":
            return int(provider["id"])
    raise RuntimeError("no LM Studio provider found")


async def verify_kb(client: httpx.AsyncClient, kb_id: int) -> None:
    """The knowledge base must be visible to the logged-in owner and ready."""
    response = await client.get(f"{kb.AGENT_URL}/api/v1/kb/{kb_id}")
    if response.status_code != 200:
        raise RuntimeError(f"KB {kb_id} is not visible to the logged-in user ({response.status_code})")
    if response.json().get("status") != "ready":
        raise RuntimeError(f"KB {kb_id} is not ready: {response.json().get('status')}")


async def create_chat(client: httpx.AsyncClient, args: argparse.Namespace, title: str) -> int:
    """Create a fresh chat with the fixed generation settings and the RAG configuration."""
    created = await client.post(
        f"{kb.AGENT_URL}/api/v1/chats", json={"title": title}, headers=_json_headers()
    )
    created.raise_for_status()
    chat_id = int(created.json()["id"])
    payload = {
        "chat_id": chat_id,
        "temperature": CHAT_TEMPERATURE,
        "max_tokens": args.max_tokens,
        "context_length": args.context_length,
    }
    settings_resp = await client.put(
        f"{kb.AGENT_URL}/api/v1/settings", json=payload, headers=_json_headers()
    )
    settings_resp.raise_for_status()
    rag_resp = await client.put(
        f"{kb.AGENT_URL}/api/v1/chats/{chat_id}/rag", json=rag_settings(args), headers=_json_headers()
    )
    rag_resp.raise_for_status()
    return chat_id


async def _connect(chat_id: int, token: str) -> Any:
    """Open the chat WebSocket with the session cookie and the allowed Origin."""
    import websockets

    from shared.auth import SESSION_COOKIE_NAME

    return await websockets.connect(
        f"ws://localhost:{kb.AGENT_PORT}/ws/chat/{chat_id}",
        additional_headers={"Cookie": f"{SESSION_COOKIE_NAME}={token}", "Origin": kb.UI_URL},
        max_size=None,
    )


def _app_alive(proc: asyncio.subprocess.Process) -> None:
    """Abort the scenario when the isolated app process is gone."""
    if proc.returncode is not None:
        raise RuntimeError(f"isolated app exited with code {proc.returncode}")


async def run_scenario(
    run_name: str,
    scenario: dict[str, Any],
    args: argparse.Namespace,
    client: httpx.AsyncClient,
    token: str,
    provider_id: int,
    proc: asyncio.subprocess.Process,
) -> dict[str, Any]:
    """Drive one scenario message by message; failed turns are recorded and the dialog goes on."""
    started = datetime.now(timezone.utc).isoformat()
    chat_id = await create_chat(client, args, f"dialog {run_name} {scenario['id']}")
    turns: list[dict[str, Any]] = []
    first_goal: str | None = None
    ws: Any = None
    try:
        for spec in scenario["turns"]:
            _app_alive(proc)
            if ws is None:
                ws = await _connect(chat_id, token)
            began = time.monotonic()
            await ws.send(json.dumps(
                {"content": spec["text"], "model": args.model, "provider_id": provider_id}
            ))
            outcome = await drain_turn(ws, args.turn_timeout)
            record = turn_record(spec, outcome, time.monotonic() - began, first_goal)
            first_goal = first_goal or record["checks"]["goal_text"]
            turns.append(record)
            report(
                f"{run_name} {spec['id']}: {record['frame_type']} {record['checks']['verdict']}"
                f" ({record['elapsed_s']}s)"
            )
            if outcome[0] == "error" and (outcome[3] or "").startswith(("closed", "timeout")):
                await ws.close()
                ws = None
    finally:
        if ws is not None:
            await ws.close()
    return {
        "run": run_name,
        "scenario": scenario["id"],
        "config": {
            "rag": rag_settings(args),
            "model": args.model,
            "temperature": CHAT_TEMPERATURE,
            "max_tokens": args.max_tokens,
            "context_length": args.context_length,
            "task_memory_enabled": run_name == "main",
        },
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "turns": turns,
    }


def _git_commit() -> str | None:
    """Commit hash of the working tree, or None when git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _fixture_hashes(path: Path) -> dict[str, str]:
    """SHA-256 of the fixture bytes and of its LF-normalised bytes."""
    data = path.read_bytes()
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha256_lf": hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
    }


def write_run_meta(
    args: argparse.Namespace,
    runs: list[str],
    started: str,
    counts: dict[str, dict[str, int]],
) -> None:
    """Write run_meta.json: fixture, hashes, configuration, flags, timestamps and counts."""
    meta_path = args.out / "run_meta.json"
    if meta_path.exists():
        try:
            previous = json.loads(meta_path.read_text(encoding="utf-8")).get("scenario_counts", {})
        except (OSError, ValueError):
            previous = {}
        counts = {**previous, **counts}
    meta = {
        "fixture": str(args.fixture),
        **_fixture_hashes(args.fixture),
        "rag": rag_settings(args),
        "model": args.model,
        "runs": {name: {"TASK_MEMORY_ENABLED": name == "main"} for name in runs},
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scenario_counts": counts,
        "git_commit": _git_commit(),
    }
    write_json(meta_path, meta)


async def _execute_run(
    run_name: str,
    ids: list[str],
    scenarios: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    counts: dict[str, dict[str, int]],
) -> bool:
    """Start the app for one run, play its scenarios and always tear the app down."""
    scratch = Path(tempfile.mkdtemp(prefix=f"rag_dialog_{run_name}_"))
    proc: asyncio.subprocess.Process | None = None
    log_file: Any = None
    ok = True
    try:
        run_env = prepare_run(run_name, args, scratch)
        proc, log_file = await start_app(run_env)
        async with httpx.AsyncClient(timeout=30.0) as client:
            token = await login(client, run_env)
            await verify_kb(client, args.kb_id)
            provider_id = await lm_provider_id(client)
            for scenario_id in ids:
                try:
                    data = await run_scenario(
                        run_name, scenarios[scenario_id], args, client, token, provider_id, proc
                    )
                except (RuntimeError, httpx.HTTPError, OSError) as exc:
                    report(f"ABORTED {run_name} {scenario_id}: {type(exc).__name__}: {exc}")
                    ok = False
                    break
                write_json(raw_path(args.out, run_name, scenario_id), data)
                counts[f"{run_name}_{scenario_id}"] = scenario_counts(data["turns"])
    except (RuntimeError, httpx.HTTPError, OSError) as exc:
        report(f"ABORTED {run_name}: {type(exc).__name__}: {exc}")
        ok = False
    finally:
        await kb.teardown(proc)
        if log_file is not None:
            log_file.close()
        shutil.rmtree(scratch, ignore_errors=True)
    return ok


async def _check_only(args: argparse.Namespace) -> int:
    """Start the app once, log in, verify the KB, tear down; no chat message is sent."""
    scratch = Path(tempfile.mkdtemp(prefix="rag_dialog_check_"))
    proc: asyncio.subprocess.Process | None = None
    log_file: Any = None
    try:
        run_env = prepare_run("main", args, scratch)
        proc, log_file = await start_app(run_env)
        async with httpx.AsyncClient(timeout=30.0) as client:
            await login(client, run_env)
            await verify_kb(client, args.kb_id)
            await lm_provider_id(client)
        report("check passed: app starts, owner logs in, KB is ready")
        return EXIT_OK
    except (RuntimeError, httpx.HTTPError, OSError) as exc:
        report(f"BLOCKED: {type(exc).__name__}: {exc}")
        return EXIT_PREFLIGHT
    finally:
        await kb.teardown(proc)
        if log_file is not None:
            log_file.close()
        shutil.rmtree(scratch, ignore_errors=True)


async def dialog_command(args: argparse.Namespace) -> int:
    """Entry point of the dialog sub-command; returns the process exit code."""
    try:
        runs = parse_runs(args.runs)
        scenarios = load_scenarios(args.fixture, require_frozen=True)
        by_id = {s["id"]: s for s in scenarios}
        chosen = parse_scenarios(args.scenarios, list(by_id))
    except ValueError as exc:
        report(f"preflight: {exc}")
        return EXIT_PREFLIGHT
    except SystemExit:
        return EXIT_PREFLIGHT
    blocked = preflight_dialog(args)
    if blocked is not None:
        return blocked
    if args.check:
        return await _check_only(args)
    started = datetime.now(timezone.utc).isoformat()
    counts: dict[str, dict[str, int]] = {}
    ok = True
    for run_name in runs:
        todo = [
            sid for sid in chosen if args.force or not raw_path(args.out, run_name, sid).exists()
        ]
        for sid in sorted(set(chosen) - set(todo)):
            report(f"skip {run_name} {sid}: raw file exists (use --force)")
        if todo:
            ok = await _execute_run(run_name, todo, by_id, args, counts) and ok
    write_run_meta(args, runs, started, counts)
    return EXIT_OK if ok else EXIT_ABORTED
