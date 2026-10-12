"""Tests for scripts/verify_service.py against a local stub HTTP server."""

import ast
import hashlib
import importlib.util
import json
import pathlib
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_service.py"
KEY = "aia_" + "k" * 30

_spec = importlib.util.spec_from_file_location("verify_service", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
verify_service = importlib.util.module_from_spec(_spec)
sys.modules["verify_service"] = verify_service
_spec.loader.exec_module(verify_service)

DEFAULT_MODELS: list[dict[str, Any]] = [
    {"provider_id": 7, "name": "Local", "kind": "lm_studio", "error": None,
     "models": [{"id": "m1", "loaded": False}, {"id": "m2", "loaded": True}]},
]


class Stub:
    """Shared state and knobs of the stub server."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.key = KEY
        self.chats: dict[int, str] = {1000: "foreign chat"}
        self.settings: dict[int, dict[str, Any]] = {}
        self.counters: dict[int, int] = {}
        self.attempts: dict[int, int] = {}
        self.turn_bodies: list[dict[str, Any]] = []
        self.deleted: list[int] = []
        self.requests: list[tuple[str, str, str | None, str | None]] = []
        self.next_id = 1
        self.in_flight = 0
        self.max_in_flight = 0
        self.rate_limit: int | None = None
        self.turn_delay = 0.0
        self.health_status = 200
        self.models_payload: list[dict[str, Any]] = DEFAULT_MODELS
        self.fail_chat_numbers: set[int] = set()
        self.overflow_status = 413
        self.url = ""

    def chat_id_with(self, fragment: str) -> int | None:
        """Return the id of the first created chat whose title ends with the fragment."""
        for chat_id, title in self.chats.items():
            if chat_id != 1000 and title.endswith(fragment):
                return chat_id
        return None


def make_handler(stub: Stub) -> type[BaseHTTPRequestHandler]:
    """Build the request handler class bound to a stub."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def reply(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
            data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            if data:
                self.send_header("Content-Type", "application/json")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if data:
                self.wfile.write(data)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        def handle_any(self, method: str) -> None:
            auth = self.headers.get("Authorization")
            with stub.lock:
                stub.requests.append((method, self.path, auth, self.headers.get("Origin")))
            if self.path == "/health":
                self.reply(stub.health_status, {"status": "ok"})
                return
            if auth != f"Bearer {stub.key}":
                self.reply(401, {"detail": "unauthorized", "code": "UNAUTHORIZED"})
                return
            body = self.read_json() if method in ("POST", "PUT") else {}
            if method == "GET" and self.path == "/api/v1/auth/me":
                self.reply(200, {"id": 1, "username": "tester"})
            elif method == "GET" and self.path == "/api/v1/llm-providers/models":
                self.reply(200, stub.models_payload)
            elif method == "POST" and self.path == "/api/v1/chats":
                with stub.lock:
                    chat_id = stub.next_id
                    stub.next_id += 1
                    stub.chats[chat_id] = body["title"]
                self.reply(201, {"id": chat_id, "title": body["title"]})
            elif method == "PUT" and self.path == "/api/v1/settings":
                with stub.lock:
                    stub.settings.setdefault(body["chat_id"], {}).update(body)
                self.reply(200, body)
            elif method == "DELETE" and self.path.startswith("/api/v1/chats/"):
                with stub.lock:
                    stub.deleted.append(int(self.path.rsplit("/", 1)[1]))
                self.reply(204)
            elif method == "POST" and re.fullmatch(r"/api/v1/chats/\d+/messages", self.path):
                self.turn(int(self.path.split("/")[4]), body)
            else:
                self.reply(404, {"detail": "not found"})

        def turn(self, chat_id: int, body: dict[str, Any]) -> None:
            with stub.lock:
                stub.turn_bodies.append({"chat_id": chat_id, **body})
                stub.attempts[chat_id] = stub.attempts.get(chat_id, 0) + 1
                count = stub.counters.get(chat_id, 0)
                if stub.rate_limit is not None and count >= stub.rate_limit:
                    limited = True
                else:
                    limited = False
                    stub.counters[chat_id] = count + 1
                title = stub.chats.get(chat_id, "")
                settings = stub.settings.get(chat_id, {})
            if limited:
                self.reply(429, {"detail": "rate limited", "code": "RATE_LIMITED", "retry_after": 42},
                           {"Retry-After": "42"})
                return
            if (settings.get("strategy") == "no_compression" and settings.get("context_length") == 512
                    and len(body["content"]) > 2000):
                self.reply(stub.overflow_status, {"detail": "context overflow", "code": "CONTEXT_OVERFLOW",
                                                  "suggested_strategy": "sliding"})
                return
            match = re.search(r"parallel #(\d+)$", title)
            if match and int(match.group(1)) in stub.fail_chat_numbers:
                self.reply(502, {"detail": "model failed", "code": "LLM_ERROR"})
                return
            with stub.lock:
                stub.in_flight += 1
                stub.max_in_flight = max(stub.max_in_flight, stub.in_flight)
                position = stub.in_flight
            time.sleep(stub.turn_delay)
            with stub.lock:
                stub.in_flight -= 1
            wait = 0.0 if position <= 2 else 0.5
            self.reply(200, {"content": "Париж.", "stats": {"queue_wait_seconds": wait}})

        def do_GET(self) -> None:
            self.handle_any("GET")

        def do_POST(self) -> None:
            self.handle_any("POST")

        def do_PUT(self) -> None:
            self.handle_any("PUT")

        def do_DELETE(self) -> None:
            self.handle_any("DELETE")

    return Handler


@pytest.fixture
def stub() -> Any:
    """Run a stub server on a free loopback port."""
    state = Stub()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    server.daemon_threads = True
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()


@dataclass
class Run:
    """Outcome of one script run."""

    code: int
    data: dict[str, Any]
    md: str
    console: str
    out_dir: pathlib.Path


@pytest.fixture
def run(stub: Stub, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> Any:
    """Return a function that runs the script's main against the stub."""

    def go(*extra: str, url: str | None = None, key: str | None = KEY, n: str | None = "3") -> Run:
        out_dir = tmp_path / f"out{len(list(tmp_path.iterdir()))}"
        argv = ["verify_service.py", "--url", url or stub.url, "--out", str(out_dir)]
        if key is not None:
            argv += ["--key", key]
        if n is not None:
            argv += ["--n", n]
        monkeypatch.setattr(sys, "argv", argv + list(extra))
        code = verify_service.main()
        captured = capsys.readouterr()
        json_file = out_dir / "verify_results.json"
        md_file = out_dir / "verify_results.md"
        data = json.loads(json_file.read_text(encoding="utf-8")) if json_file.exists() else {}
        md = md_file.read_text(encoding="utf-8") if md_file.exists() else ""
        return Run(code, data, md, captured.out + captured.err, out_dir)

    return go


def closed_port_url() -> str:
    """Return a loopback URL on a port nobody listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def test_happy_path_writes_both_files(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run("--rate-mode", "both", n="4")
    assert result.code == 0, result.console
    assert (result.out_dir / "verify_results.md").exists()
    assert (result.out_dir / "verify_results.json").exists()
    for section in ("reachability", "authentication", "parallel", "overflow", "rate_limit", "cleanup"):
        assert result.data[section]["ran"] is True


def test_reachability_and_auth(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run()
    assert result.data["reachability"]["status"] == 200
    health = [r for r in stub.requests if r[1] == "/health"]
    assert health and all(r[2] is None for r in health)
    assert result.data["authentication"]["good_status"] == 200
    assert result.data["authentication"]["bad_status"] == 401


def test_unreachable_server_exits_2_and_marks_later_steps(run: Any) -> None:
    result = run(url=closed_port_url())
    assert result.code == 2
    assert "unreachable" in result.console
    assert (result.out_dir / "verify_results.md").exists()
    assert "не запускалось" in result.md


def test_refused_key_exits_2(stub: Stub, run: Any) -> None:
    result = run(key="aia_" + "z" * 30)
    assert result.code == 2
    assert "refused" in result.console


def test_no_usable_model_exits_2(stub: Stub, run: Any) -> None:
    stub.models_payload = [{"provider_id": 1, "name": "Bad", "kind": "x", "models": [], "error": "down"}]
    assert run().code == 2


def test_explicit_model_skips_models_route(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run("--model", "custom", "--provider-id", "9")
    assert result.code == 0, result.console
    assert not [r for r in stub.requests if r[1] == "/api/v1/llm-providers/models"]
    assert {b["model"] for b in stub.turn_bodies} == {"custom"}
    assert {b["provider_id"] for b in stub.turn_bodies} == {9}


def test_model_choice_prefers_loaded(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    run()
    assert {b["model"] for b in stub.turn_bodies} == {"m2"}


def test_model_choice_falls_back_to_first_healthy(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    stub.models_payload = [
        {"provider_id": 1, "name": "Down", "kind": "x", "models": [], "error": "down"},
        {"provider_id": 2, "name": "Cloud", "kind": "x", "error": None,
         "models": [{"id": "c1", "loaded": None}, {"id": "c2", "loaded": None}]},
    ]
    run()
    assert {b["model"] for b in stub.turn_bodies} == {"c1"}
    assert {b["provider_id"] for b in stub.turn_bodies} == {2}


def test_parallel_run(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    stub.turn_delay = 0.2
    result = run(n="4")
    titles = [t for i, t in stub.chats.items() if "parallel" in t]
    assert len(titles) == 4 and len(set(titles)) == 4
    assert all("verify-service" in t for t in titles)
    parallel_ids = [i for i, t in stub.chats.items() if "parallel" in t]
    assert all(stub.attempts[i] == 1 for i in parallel_ids)
    assert stub.max_in_flight >= 2
    records = result.data["parallel"]["records"]
    assert len(records) == 4
    for record in records:
        assert record["status"] == 200
        assert record["elapsed"] > 0
        assert record["queue_wait_seconds"] is not None
        assert record["reply_chars"] > 0
    section = result.md.split("## Параллельные запросы")[1].split("## ")[0]
    assert len(re.findall(r"^\| \d+ \|", section, flags=re.M)) == 4
    assert "Успешно: 4, ошибок: 0" in section


def test_parallel_failure_exits_1(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    stub.fail_chat_numbers = {2}
    result = run()
    assert result.code == 1
    records = {r["number"]: r for r in result.data["parallel"]["records"]}
    assert records[2]["code"] == "LLM_ERROR"
    assert records[1]["status"] == 200 and records[3]["status"] == 200


def test_overflow_demonstration(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run()
    chat_id = result.data["overflow"]["chat_id"]
    assert stub.settings[chat_id]["strategy"] == "no_compression"
    assert stub.settings[chat_id]["context_length"] == 512
    assert stub.settings[chat_id]["max_tokens"] == 256
    long_bodies = [b for b in stub.turn_bodies if b["chat_id"] == chat_id]
    assert len(long_bodies[0]["content"]) >= 6000
    assert result.data["overflow"]["status"] == 413
    assert result.data["overflow"]["code"] == "CONTEXT_OVERFLOW"


def test_overflow_not_413_exits_1(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    stub.overflow_status = 200
    assert run().code == 1


def test_rate_limit_default_mode(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run()
    rate_chat = stub.chat_id_with(" rate")
    assert rate_chat is not None
    sequence = result.data["rate_limit"]["sequences"][0]
    assert sequence["mode"] == "chat"
    assert sequence["limit_request"] == 6
    assert sequence["retry_after_header"] == "42"
    assert stub.attempts[rate_chat] == 6
    assert "Retry-After" in result.console
    assert "429 on request 6" in result.console


def test_rate_limit_not_reached_exits_1(stub: Stub, run: Any) -> None:
    result = run("--rate-max-attempts", "3", n="2")
    rate_chat = stub.chat_id_with(" rate")
    assert stub.attempts[rate_chat] == 3
    assert "limit not reached" in result.console
    assert result.data["rate_limit"]["sequences"][0]["reached"] is False
    assert result.code == 1


def test_rate_limit_fast_mode(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run("--rate-mode", "fast")
    assert result.code == 0, result.console
    assert stub.chat_id_with(" rate") is None
    sequence = result.data["rate_limit"]["sequences"][0]
    assert sequence["mode"] == "fast"
    assert sequence["limit_request"] == 6
    assert sequence["chat_id"] == result.data["overflow"]["chat_id"]
    assert [r["status"] for r in sequence["records"][:-1]] == [413] * 5
    assert sequence["records"][0]["number"] == 1


def test_expected_limit_note(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run("--server-rate-limit", "5")
    assert result.data["server_rate_limit"] == 5
    notes = result.data["rate_limit"]["sequences"][0]["notes"]
    assert any("request 6 as expected" in note for note in notes)


def test_cleanup_deletes_only_own_chats(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    run()
    own = {i for i in stub.chats if i != 1000}
    assert set(stub.deleted) == own
    assert 1000 not in stub.deleted


def test_cleanup_after_failed_run(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    stub.fail_chat_numbers = {1}
    result = run()
    assert result.code == 1
    assert set(stub.deleted) == {i for i in stub.chats if i != 1000}


def test_keep_leaves_chats(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run("--keep")
    assert not [r for r in stub.requests if r[0] == "DELETE"]
    chats = result.data["cleanup"]["chats"]
    assert len(chats) >= 5
    for chat in chats:
        assert f"chat {chat['id']}: {chat['title']}" in result.console


def test_key_hygiene(stub: Stub, run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    stub.rate_limit = 5
    monkeypatch.setenv("AIA_API_KEY", KEY)
    result = run(key=None)
    assert result.code == 0, result.console
    text = (result.out_dir / "verify_results.md").read_text(encoding="utf-8")
    text += (result.out_dir / "verify_results.json").read_text(encoding="utf-8")
    assert KEY not in text and KEY not in result.console
    assert result.data["key_prefix"] == KEY[:8]
    assert "key" not in result.data["arguments"]


def test_no_origin_and_bearer_everywhere(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    run()
    assert all(r[3] is None for r in stub.requests)
    assert all(r[2] and r[2].startswith("Bearer ") for r in stub.requests if r[1] != "/health")


def test_proxies_are_bypassed(stub: Stub, run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    stub.rate_limit = 5
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    assert run().code == 0


def test_json_metadata(stub: Stub, run: Any) -> None:
    stub.rate_limit = 5
    result = run()
    for field in ("python_version", "platform", "started_at", "finished_at"):
        assert result.data[field]
    assert result.data["url"] == stub.url
    assert result.data["arguments"]["n"] == 3
    expected = hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest()
    assert result.data["script_sha256"] == expected


def test_stdlib_only() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not sorted(m for m in modules if m not in sys.stdlib_module_names)
    for forbidden in ("import httpx", "import requests", "from shared", "from agent", "from ui"):
        assert forbidden not in source


def test_python_311_floor() -> None:
    ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), feature_version=(3, 11))


def test_file_level_rules() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "utcnow(" not in source
    tree = ast.parse(source)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is None]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        writes = (isinstance(func, ast.Attribute) and func.attr == "write_text") or (
            isinstance(func, ast.Name) and func.id == "open")
        if writes:
            encodings = [k.value.value for k in node.keywords
                         if k.arg == "encoding" and isinstance(k.value, ast.Constant)]
            assert encodings == ["utf-8"]
