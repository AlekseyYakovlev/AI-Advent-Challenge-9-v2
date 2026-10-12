# Phase 18: LAN exposure (Day 30) - Pattern Map

**Mapped:** 2026-10-12
**Files analyzed:** 27 (11 new, 16 modified)
**Analogs found:** 25 / 27 (two partial)

Project rules that govern every excerpt below (CLAUDE.md): single-line module docstring on each file, type hints everywhere, `logger = get_logger(__name__)` plus snake_case event names with key=value pairs, no `print()` except in `run.py` and the pre-logging stderr message in `shared/config.py`, stdlib -> third-party -> local imports, UI strings Russian, never insert HTML without sanitizing (use `textContent`).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `shared/origins.py` (NEW) | utility + middleware | request-response (pure function + ASGI wrapper) | `agent/dependencies.py::require_allowed_origin` + `agent/ws.py::_validate_origin` (logic); Starlette `CORSMiddleware` (wrap target) | role-match |
| `shared/network.py` (NEW) | utility | transform (psutil query) | `run.py::cleanup_port` / `ui/supervisor.py::find_pids_on_port` (psutil + specific exceptions) | partial |
| `shared/config.py` (MOD: `BIND_HOST`, validator, `_load_settings`) | config | transform | `shared/config.py::Settings` itself; `shared/runtime.py::check_python_version` (fail-fast message) | exact |
| `agent/network_api.py` (NEW, or inline in `agent/main.py`) | route | request-response (read-only, auth) | `agent/providers_api.py` router header; `agent/main.py::get_me` (GET + `get_current_user`) | exact |
| `agent/main.py` (MOD: swap middleware, include router, import) | config/route | request-response | itself, lines 445-451 | exact |
| `agent/state.py` (MOD: delete `CORS_ORIGINS`) | store | n/a | itself, lines 7-10 | exact |
| `agent/dependencies.py` (MOD: `require_allowed_origin`) | middleware (dependency) | request-response | itself, lines 19-29 | exact |
| `agent/ws.py` (MOD: `_validate_origin`) | middleware | request-response (WS handshake) | itself, lines 133-163 | exact |
| `agent/events.py` (verify only) | route | pub-sub | imports `_validate_origin` from `agent.ws`; no change | exact |
| `ui/main.py` (MOD: drop local `CORS_ORIGINS`, swap middleware, `host=` kwarg) | config | request-response | itself, lines 19-45 | exact |
| `ui/supervisor.py` (MOD: `host` param) | service | event-driven (subprocess lifecycle) | itself, lines 52-60, 98-109 | exact |
| `run.py` (MOD: banner, `host=`) | utility (entry point) | batch (startup output) | itself, lines 44-66 (bootstrap-admin banner) | exact |
| `ui/static/index.html` (MOD: `#network-section`) | component | request-response (render) | `#llm-providers-section` / `#mcp-section` | exact |
| `ui/static/app.js` (MOD: `loadNetworkInfo`, `copyTextToClipboard`, hook in `openSettingsModal`) | component | request-response | `loadLlmProviders` / `loadMcpServers`; `copyMcpToolName` | exact |
| `tests/test_origins.py` (NEW) | test | transform + request-response | `tests/test_cors.py`, `tests/test_mcp_origin.py` | role-match |
| `tests/test_bind_host.py` (NEW) | test | transform + subprocess | `tests/test_runtime.py`, `tests/test_run_cleanup.py` (subprocess + REPO_ROOT) | role-match |
| `tests/test_network_info.py` (NEW) | test | request-response | `tests/test_cors.py` (client fixture), `tests/test_mcp_origin.py` | role-match |
| `tests/test_run_banner.py` (NEW, or extend `tests/test_run_cleanup.py`) | test | batch (capsys output) | `tests/test_run_cleanup.py` (imports `run.main`, patches) | role-match |
| `tests/test_supervisor.py` / `test_orphan_cleanup.py` (verify; add one `--host` arg test) | test | event-driven | themselves (construct `AgentSupervisor(port, db)`) | exact |
| `tests/test_cors.py`, `test_mcp_origin.py`, `test_memory_api.py:364`, `test_task_memory_api.py:209` (MOD import) | test | request-response | themselves | exact |
| `tests/test_ws_origin_validation.py`, `tests/test_websocket_cors.py` (MOD: null/:8001 become rejections) | test | request-response | themselves | exact |
| `scripts/e2e_kb_playwright.py`, `scripts/e2e_llm_providers_playwright.py` (MOD: delete two `patch("agent/state.py", ...)` lines each) | utility | batch | themselves | exact |
| `.env.example` (MOD) | config | n/a | existing commented blocks (`# RAG task memory`) | exact |
| `docs/USER_GUIDE.md`, `ARCHITECTURE.md`, `API_SPEC.md`, `TESTING_GUIDE.md` (MOD) | docs | n/a | existing sections (see Docs below) | role-match |
| `.planning/REQUIREMENTS.md` (MOD: NET-01/NET-02 wording per D-13) | docs | n/a | itself | exact |

## Pattern Assignments

### `shared/config.py` (config, transform) - BIND_HOST field, validator, import-time exit

**Analog:** `shared/config.py` (field style) and `shared/runtime.py` (fail-fast message).

**Current field style** (`shared/config.py` lines 18-33): plain annotated fields with defaults, a one-line `#` comment above groups. Add next to the ports:
```python
    UI_PORT: int = 8000
    AGENT_PORT: int = 8001
    DB_PATH: str = "app.db"
```
Add `BIND_HOST: str = BIND_LOOPBACK` (comment: "Single bind address for UI and Agent: 127.0.0.1 (default) or 0.0.0.0 (LAN)"), a `@field_validator("BIND_HOST", mode="before")` and an `is_lan_mode` property as in RESEARCH Pattern 2.

**Import and instantiation site** (lines 1-8, 55): current imports are `os, re`, `dotenv_values`, `pydantic_settings`. Add `import sys`, `from pydantic import ValidationError, field_validator`. Replace the single line `settings = Settings()` (line 55) with `settings = _load_settings()` (RESEARCH Pattern 2). Keep this module free of imports from `shared.origins` / `shared.network` (circular-import guard).

**Fail-fast analog** (`shared/runtime.py` lines 10-18) - copy the message tone, NOT the exit form:
```python
    if tuple(version[:2]) < MIN_PYTHON:
        raise SystemExit(
            "AiAdventAgentV2 requires Python 3.11+ "
            f"(asyncio.timeout, BaseExceptionGroup); running {version[0]}.{version[1]}."
        )
```
`raise SystemExit("text")` exits with code 1. D-15 needs code 2: `print(msg, file=sys.stderr); raise SystemExit(2) from None`. Message is ASCII, English, names the value given, accepted values, "a specific LAN IP is not supported, use 0.0.0.0".

---

### `shared/origins.py` (utility + ASGI middleware, request-response)

**Analog (rule):** the three duplicated checks being consolidated.

`agent/dependencies.py` lines 19-29 (REST mutation guard; missing Origin passes, logs `origin_rejected`):
```python
async def require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None or origin in CORS_ORIGINS:
        return
    logger.warning("origin_rejected", origin=origin, path=request.url.path)
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cross-origin request rejected")
```
`ui/main.py` lines 19-22 (the port-derived list that `LOOPBACK_ORIGINS` must reproduce, no `8000` literal):
```python
CORS_ORIGINS = [
    f"http://localhost:{settings.UI_PORT}",
    f"http://127.0.0.1:{settings.UI_PORT}",
]
```
**Copy:** the port-derived form from `ui/main.py` (NOT the literal list in `agent/state.py`, which the e2e scripts patch). Body of `is_allowed_origin` and `HostMatchCORSMiddleware`: take verbatim from RESEARCH Patterns 1 and 3. Module docstring one line, e.g. `"""Single Origin rule and host-aware CORS middleware shared by REST and WebSocket checks."""`.

**Keep `LOOPBACK_ORIGINS` a tuple.** Tests index it as `CORS_ORIGINS[0]` (still valid) and `@pytest.mark.parametrize("origin", ...)` accepts it.

**Imports pattern** (stdlib -> third-party -> local, per CLAUDE.md):
```python
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.config import settings
```
No logger needed here; callers log (`origin_rejected`, `ws_origin_rejected`).

---

### `shared/network.py` (utility, transform)

**Analog:** `run.py::cleanup_port` (lines 16-36) and `ui/supervisor.py::find_pids_on_port` (lines 26-35) - psutil usage with specific exception tuples, never bare `except`.
```python
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
```
**Apply:** wrap `psutil.net_if_addrs()` / `net_if_stats()` in `try/except (OSError, psutil.Error)` returning `[]`; log `logger.warning("network_discovery_failed", error=str(exc))` (use `get_logger(__name__)`, never print). Function bodies from RESEARCH Pattern 4 (`lan_ipv4_addresses`, `reachable_urls`). Filter: `AF_INET`, interface `isup`, skip loopback and link-local. Never print adapter names (Cyrillic on this machine).

Both `run.py` (banner) and the endpoint must call `reachable_urls(settings.UI_PORT)` - one source of truth (CONTEXT specifics).

---

### `agent/network_api.py` (NEW route, request-response) - `GET /api/v1/network`

**Analog:** `agent/providers_api.py` lines 1-25 (router file header) and `agent/main.py` lines 571-574 (`get_me`, the simplest authenticated GET).

Router header to copy (`agent/providers_api.py`):
```python
"""REST routes for user-scoped LLM providers (/api/v1/llm-providers)."""

from fastapi import APIRouter, Depends
...
from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from shared.logger import get_logger
from shared.models import LlmProvider, User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/llm-providers", tags=["llm-providers"])
```
Authenticated GET (`agent/main.py` 571-574):
```python
@app.get("/api/v1/auth/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the currently authenticated user."""
    return UserResponse(id=current_user.id, username=current_user.username)
```
**Apply:** `router = APIRouter(prefix="/api/v1/network", tags=["network"])`; GET only, `Depends(get_current_user)` (no `require_allowed_origin`, it is a GET); define a `NetworkInfoOut(BaseModel)` (in `agent/schemas.py` or locally; `lan_mode: bool`, `bind_host: str`, `ui_port: int`, `urls: list[str]`); `urls=[]` in loopback mode; compute with `await asyncio.to_thread(reachable_urls, settings.UI_PORT)`. Register in `agent/main.py` next to the other `app.include_router(...)` lines (445-460 region: `scheduler_router`, `providers_router`, `kb_router`, `rag_router`). Add 401 and 200 tests.

---

### `agent/main.py`, `ui/main.py` (config) - swap CORS middleware

**Analog:** `agent/main.py` lines 445-451 (identical block in `ui/main.py` 39-45):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
**Replace with** `app.add_middleware(HostMatchCORSMiddleware)`; drop the `CORSMiddleware` and `CORS_ORIGINS` imports (`agent/main.py:56` imports from `agent.state`; `ui/main.py` has a local list at 19-22 and `fastapi.middleware.cors` import at line 7). In `ui/main.py` line 28 change to `AgentSupervisor(settings.AGENT_PORT, settings.DB_PATH, host=settings.BIND_HOST)`.

---

### `agent/state.py` (store) - remove duplicate list

Delete lines 7-10 (`CORS_ORIGINS = [...]`). Nothing else in the file references it. Do not leave a re-export "for the e2e scripts" (RESEARCH anti-pattern).

---

### `agent/dependencies.py` (middleware/dependency)

Replace the import `from agent.state import CORS_ORIGINS` (line 10) with `from shared.origins import is_allowed_origin`. New body of `require_allowed_origin` (keep docstring and the `origin_rejected` log line and 403 detail exactly as they are):
```python
    origin = request.headers.get("origin")
    if origin is None or is_allowed_origin(origin, request.headers.get("host")):
        return
```

---

### `agent/ws.py::_validate_origin` (middleware, WS handshake)

**Analog:** itself (lines 133-163, quoted above in this phase's research). Current structure: log `ws_origin_check`; `None` -> allow; `in CORS_ORIGINS` -> allow; `== "null"` -> allow (REMOVE, D-06); `allowed_patterns` list including `:8001` (REMOVE, D-04); else `ws_origin_rejected` warning + `False`.

**Keep:** the signature `_validate_origin(websocket: WebSocket) -> bool` (also imported by `agent/events.py:16`, used at `events.py:124` and `ws.py:1215`), the `ws_origin_check` info log and `ws_origin_allowed` / `ws_origin_rejected` event names. Replace the `CORS_ORIGINS` check with `is_allowed_origin(origin, websocket.headers.get("host"))` and use `reason="host_match"` in the allowed log. Update the import at `agent/ws.py:33`.

---

### `ui/supervisor.py` (service, event-driven)

**Analog:** itself.
`__init__` (lines 55-60): add a defaulted keyword so the existing two-positional-arg tests (`tests/test_supervisor.py:47`, `tests/test_orphan_cleanup.py:75`) still work:
```python
    def __init__(self, agent_port: int, db_path: str, host: str = "127.0.0.1") -> None:
        self.agent_port = agent_port
        self.db_path = db_path
        self.host = host
```
`_launch_agent` (lines ~98-106): replace the `"127.0.0.1"` element after `"--host"` with `self.host`. Leave `_ping_health` (`url = f"http://127.0.0.1:{self.agent_port}/health"`) and `_has_websocket_route` (`.../debug/routes`) on `127.0.0.1` (a `0.0.0.0` bind still accepts loopback). Note `tests/test_orphan_cleanup.py:30` has its own dummy-server `--host 127.0.0.1`; that is test scaffolding, leave it.

---

### `run.py` (entry point, batch output)

**Analog:** itself, bootstrap-admin banner (lines 44-59):
```python
        print("=" * 60)
        print("Bootstrap admin account created:")
        ...
        print("=" * 60)

    print(f"Starting UI on http://127.0.0.1:{settings.UI_PORT}")
    print(f"Agent will be auto-started on port {settings.AGENT_PORT}")
    print("Press Ctrl+C to stop.")
    ...
        uvicorn.run(app, host="127.0.0.1", port=settings.UI_PORT, reload=False)
```
**Apply:** keep order `config import (validation exits 2) -> check_python_version -> cleanup_port x2 -> bootstrap -> banner -> uvicorn`. Guard the new output with `if settings.is_lan_mode:` so the loopback branch keeps the three existing `print` lines byte-identical (add a `capsys` test that compares to the literal text). LAN branch: `"=" * 60` framed warning (English, ASCII only), then one `print(f"    {url}")` per `reachable_urls(settings.UI_PORT)`; then the existing three lines. `uvicorn.run(app, host=settings.BIND_HOST, ...)`. Import `reachable_urls` from `shared.network` at top with the other `shared.*` imports. `main()` is what `tests/test_run_cleanup.py` patches/imports, so keep its name/signature.

---

### `ui/static/index.html` - `#network-section`

**Analog:** `#llm-providers-section` (index.html ~386) and `#mcp-section` (~341); siblings inside `#settings-modal`:
```html
            <section id="llm-providers-section" class="border-t border-slate-700 p-4 space-y-4">
                <div class="flex items-center justify-between">
                    <h3 class="text-lg font-semibold">Провайдеры LLM</h3>
                    ...
```
Empty-state style used in both: `<div id="mcp-empty" class="hidden text-center text-slate-500 text-sm py-4"><p class="font-semibold">...</p><p>...</p></div>`; hint text style `<p class="text-xs text-slate-500 mt-1">`. Add `<section id="network-section" class="border-t border-slate-700 p-4 space-y-4">` with `<h3 class="text-lg font-semibold">Сеть</h3>`, a status `<p id="network-status">`, a hint `<p id="network-hint" class="text-xs text-slate-500">`, and `<div id="network-url-list" class="space-y-2"></div>`. Follow exact classes/placement from `18-UI-SPEC.md`; that contract wins on visual details.

---

### `ui/static/app.js` - network section loader and clipboard helper

**Analog 1 (loader + hook):** `openSettingsModal` (lines 3474-3498) kicks off section loaders non-blocking:
```javascript
    $('settings-modal').classList.remove('hidden');
    loadMcpServers().catch((err) => showToast(err.message, 'error'));
    loadLlmProviders().catch((err) => showToast(err.message, 'error'));
```
Add `loadNetworkInfo().catch((err) => showToast(err.message, 'error'));` as a third line. `loadNetworkInfo` calls `apiFetch('/api/v1/network')` (lines 80-97: sends `credentials: 'include'`, redirects on 401, throws `Error(detail)` otherwise) and renders with `textContent` only. Element creation helper already used in this file: `mcpEl('span', 'classes', 'text')` (see line ~2133); reuse or mirror it, no `innerHTML`.

**Analog 2 (clipboard fallback)** `copyMcpToolName` (lines 2135-2155):
```javascript
async function copyMcpToolName(name) {
    try {
        await navigator.clipboard.writeText(name);
    } catch (err) {
        // navigator.clipboard is unavailable outside secure contexts (e.g. opened via a LAN IP).
        const area = document.createElement('textarea');
        area.value = name;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        const copied = document.execCommand('copy');
        area.remove();
        if (!copied) {
            showToast('Не удалось скопировать в буфер обмена', 'error');
            return;
        }
    }
    showToast(`Скопировано: ${name}`, 'success');
}
```
Extract `async function copyTextToClipboard(text) -> boolean` (try `navigator.clipboard?.writeText`, else the textarea branch with `focus()`, `select()`, `remove()` in `finally`), make `copyMcpToolName` call it, and use it from the address click with `showToast(..., 'success')`. Note: `navigator.clipboard` is `undefined` (not a rejected promise) over HTTP, so guard with `?.` or check, otherwise the `try` catches a TypeError (still works, but be explicit).

Agent host is already `window.location.hostname` (`app.js:3-5`) - no change needed for LAN. Do not introduce `localhost` literals.

---

### Tests

**`tests/test_origins.py` (NEW)** - analog `tests/test_cors.py`:
```python
"""CORS middleware configuration tests."""
@pytest.mark.asyncio
@pytest.mark.parametrize("origin", CORS_ORIGINS)
async def test_cors_allows_ui_origins(client: AsyncClient, origin: str) -> None:
    resp = await client.options("/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert resp.headers.get("access-control-allow-origin") == origin
...
    resp = await client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert resp.headers.get("access-control-allow-origin") is None
```
Copy the `client: AsyncClient` fixture usage (from `tests/conftest.py`: `ASGITransport` client, Host `test`), `@pytest.mark.asyncio`, type-hinted tests, one-line docstrings. Pure unit table for `is_allowed_origin` (LAN IP, hostname case, wrong port, https, `null`, port-less, `http://localhost:8000.evil.com`, IPv6, missing Host) plus Host-override middleware tests (`headers={"Host": "192.168.1.5:8001", "Origin": "http://192.168.1.5:8000"}`). For `require_allowed_origin` cases copy the `FOREIGN_ORIGIN` / parametrized `ROUTES` style from `tests/test_mcp_origin.py`. WS cases: `TestClient.websocket_connect(url, headers={...})` as in `tests/test_ws_origin_validation.py` (uses `login_test_client(client)` and `_create_chat(client)` helpers).

**`tests/test_bind_host.py` (NEW)** - analogs `tests/test_runtime.py` (SystemExit assertion style, `ROOT: Path = Path(__file__).parent.parent`) and `tests/test_run_cleanup.py` (`REPO_ROOT = Path(__file__).resolve().parents[1]`, `subprocess` with `sys.executable`). Parametrize accepted values (`""`, `" localhost "`, `127.0.0.1`, `0.0.0.0` -> normalised) via `Settings(BIND_HOST=...)`; rejected values (`::`, `192.168.1.5`, `*`) via `pytest.raises(ValidationError)`; one subprocess test: `subprocess.run([sys.executable, "-c", "import shared.config"], cwd=REPO_ROOT, env={**os.environ, "BIND_HOST": "192.168.1.5"}, capture_output=True, text=True)` asserting `returncode == 2`, `"Traceback" not in stderr`, `"0.0.0.0" in stderr`. Add a case proving `run.py` exits before `cleanup_port`/bootstrap (import-time exit).

**`tests/test_network_info.py` (NEW)** - analog `tests/test_mcp_origin.py` for authenticated client fixtures; monkeypatch `psutil.net_if_addrs`/`net_if_stats` with fake `snicaddr`/`snicstats` namedtuples; assert 401 without cookie, 200 with, `urls == []` in loopback, filtering of down / loopback / link-local interfaces.

**`tests/test_run_banner.py` (NEW or extend `tests/test_run_cleanup.py`)** - `tests/test_run_cleanup.py` already does `from run import cleanup_port, main` and uses `unittest.mock` (`patch`, `AsyncMock`, `MagicMock`). Patch `run.uvicorn.run`, `run.cleanup_port`, `run.bootstrap_admin_if_needed`, set `settings.BIND_HOST` via `monkeypatch.setattr`, and compare `capsys.readouterr().out`. Loopback test asserts the exact three lines.

**Existing tests to edit:**
- `tests/test_ws_origin_validation.py`: `test_ws_accepts_null_origin` (lines 24-33) becomes a rejection test; `:8001` accept tests (lines ~60-81) become rejections; keep none/localhost:8000/127.0.0.1:8000.
- `tests/test_websocket_cors.py::test_websocket_connect_with_null_origin` (lines 37-47) becomes a rejection.
- `tests/test_cors.py`, `tests/test_mcp_origin.py`, `tests/test_memory_api.py:364`, `tests/test_task_memory_api.py:209`: change `from agent.state import CORS_ORIGINS` to `from shared.origins import LOOPBACK_ORIGINS` (rename uses; tuple indexing still works).

### `scripts/e2e_kb_playwright.py:129-130`, `scripts/e2e_llm_providers_playwright.py:183-184`
Delete the two `patch("agent/state.py", '"http://localhost:8000"', ...)` / `'"http://127.0.0.1:8000"'` lines in each (the literal no longer exists, `patch` would raise `RuntimeError("cannot patch ...")`). `LOOPBACK_ORIGINS` follows `UI_PORT`, which these scripts already pass via the subprocess env. Do not add `8000`/`8001` literals to new code (their leftover scan rejects them outside `shared/config.py` and `agent/ws.py`). `scripts/e2e_mcp_chat_playwright.py` unaffected.

### `.env.example`
Analog: the commented, explained blocks, e.g.:
```
# MCP tool calls from chat
# Enabled MCP servers connect automatically at the first chat turn (false = manual connect only)
MCP_AUTO_CONNECT=true
```
Add under "Process ports" a `# Bind address ...` comment block and `BIND_HOST=127.0.0.1` (explain `0.0.0.0` = whole LAN, plain HTTP, see docs/USER_GUIDE.md; `localhost` is a synonym; specific IPs are rejected).

### Docs
- `docs/USER_GUIDE.md` (English; sections are `## Getting Started`, `## Settings`, ... `## Tips`, `## Troubleshooting`): add `## LAN mode` before `## Tips`, with `BIND_HOST=0.0.0.0` steps, the warning, firewall PowerShell (`New-NetFirewallRule ... -Profile Private`, removal `Remove-NetFirewallRule`, `Get-NetConnectionProfile` / `Set-NetConnectionProfile`), GUI steps, and a Troubleshooting entry. Have the executor confirm cmdlet syntax with `Get-Help` (research assumption A3).
- `docs/ARCHITECTURE.md`: short "Origin rule" note (host-match + loopback list, `null` rejected on WS, missing Origin allowed, residual DNS-rebinding note from RESEARCH Pitfall 4).
- `docs/API_SPEC.md` (section style: `## LLM providers (Day 21)` -> `### Routes`, `### Request and response shapes`): add `## Network` with `GET /api/v1/network` response shape.
- `docs/TESTING_GUIDE.md`: add entries for `test_origins.py`, `test_bind_host.py`, `test_network_info.py`, run banner test; note a bad `BIND_HOST` in `.env` aborts collection (RESEARCH Pitfall 3).

## Shared Patterns

### One Origin rule, three enforcement points
**Source:** `shared/origins.py::is_allowed_origin` (new).
**Apply to:** `HostMatchCORSMiddleware` (agent + ui apps), `agent/dependencies.py::require_allowed_origin`, `agent/ws.py::_validate_origin`. Callers own the "no Origin header -> allow" branch (D-05); `"null"` and port-less/https Origins never match.

### Authenticated read endpoint
**Source:** `agent/main.py` lines 571-574 (`Depends(get_current_user)`), `agent/providers_api.py` router header.
**Apply to:** `agent/network_api.py`.

### Logging
**Source:** `agent/dependencies.py` lines 16-27.
```python
logger = get_logger(__name__)
...
logger.warning("origin_rejected", origin=origin, path=request.url.path)
```
**Apply to:** `shared/network.py` (failure warning), `agent/ws.py` events. Never log secrets; never `print()` outside `run.py` and the `shared/config.py` stderr exit message.

### Specific exception handling
**Source:** `run.py` line ~35 / `ui/supervisor.py` line ~34: `except (psutil.NoSuchProcess, psutil.AccessDenied, ...)`.
**Apply to:** `shared/network.py` (`OSError`, `psutil.Error`), `_host_of` / `is_allowed_origin` (`ValueError` from `urlsplit`).

### Port literals
**Apply to:** all new code - derive from `settings.UI_PORT` / `settings.AGENT_PORT`; the only default literals live in `shared/config.py` (e2e leftover scan). Frontend `AGENT_PORT = 8001` stays as is.

## Conventions

Derived from CLAUDE.md and the files read (the `gsd-tools verify conventions --derive` run was not executed; findings are manual and scoped to `shared/`, `agent/`, `ui/`, `tests/`).

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | snake_case `.py` (`llm_client.py`, `kb_api.py`, `test_*.py`) | ~100% | low | named contract |
| Identifier casing | `snake_case` functions/vars, `PascalCase` classes, `UPPER_CASE` constants, `_private` helpers | ~95% | low | named contract |
| Export style | plain module-level definitions, direct imports (no `__all__` / barrels) | ~95% | low | named contract |
| Import style | absolute from repo root, stdlib -> third-party -> local, no aliases | ~95% | low | named contract |

Contested hotspots (author's choice): the CJS<->SDK dual resolver (`bin/lib/**` CJS `module.exports`/`require` vs `sdk/src/**` ESM `export`/`import`) is the prototype intentional split; it is not present in this Python repo, so nothing here is contested. Within this phase the only mixed zone is `ui/static/app.js` (plain script, global functions, no modules): match that local style. Reviewers/planners should match the directory's local style.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `shared/origins.py::HostMatchCORSMiddleware` | middleware | request-response | No custom ASGI middleware exists in the repo (only stock `CORSMiddleware`); use RESEARCH Pattern 3 and Starlette 1.6.0 `starlette/middleware/cors.py` as the reference. |
| `shared/network.py::lan_ipv4_addresses` | utility | transform | psutil is used only for process/port queries; no interface enumeration exists. Use RESEARCH Pattern 4. |

## Metadata

**Analog search scope:** `shared/`, `agent/`, `ui/`, `ui/static/`, `run.py`, `tests/`, `scripts/`, `.env.example`, `docs/`.
**Files scanned:** about 30 read in whole or in part, plus greps across `tests/` and `scripts/`.
**Pattern extraction date:** 2026-10-12
