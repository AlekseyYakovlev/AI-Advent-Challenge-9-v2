# Phase 18: LAN exposure (Day 30) - Research

**Researched:** 2026-10-12
**Domain:** Network binding, Origin/CORS enforcement, startup UX (FastAPI/Starlette, pydantic-settings, psutil, vanilla JS)
**Confidence:** HIGH (all code claims verified by reading the repo and the installed packages in this session)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** An Origin is accepted when its host equals the host of the request's `Host` header and its port equals `UI_PORT`. This covers any LAN IP and machine name (`http://my-pc:8000`) with no lists; a foreign site fails because its host differs.
- **D-02:** The host-match rule applies always, in both loopback and LAN mode (one rule, no mode branch in the Origin check).
- **D-03:** `http://localhost:{UI_PORT}` and `http://127.0.0.1:{UI_PORT}` stay always-allowed in addition to the host-match rule, so existing tests (Origin `http://localhost:8000` with a test-client Host) keep passing unchanged.
- **D-04:** The `:8001` origin variants in `agent/ws.py::_validate_origin` (`allowed_patterns`) are removed - the UI is never served from the Agent port.
- **D-05:** A request or WebSocket handshake with no `Origin` header stays allowed (non-browser clients: curl, scripts, tests; still gated by the session cookie).
- **D-06:** `Origin: null` is rejected on WebSocket in both modes (today it is allowed at `agent/ws.py:150`).
- **D-07:** The loopback list and the check live in one shared place used by all three enforcement points: REST CORS, `require_allowed_origin` (REST mutations) and the WebSocket check (`/ws/chat`, `/ws/events`). The duplicated lists in `agent/state.py`, `ui/main.py` and `agent/ws.py` are consolidated.
- **D-08:** In LAN mode `run.py` prints one URL per non-loopback IPv4 address of an active interface, plus `http://<machine-name>:{UI_PORT}` and the local `http://127.0.0.1:{UI_PORT}`. Several lines on a machine with VPN/WSL/Hyper-V adapters are acceptable.
- **D-09:** The exposure warning is a framed block (`=` rule lines, same style as the bootstrap-admin banner at `run.py:50-55`), in English like the rest of `run.py` output. Content: the service is reachable by everyone on the network, traffic is plain HTTP (no HTTPS), do not enable on public networks, how to go back to loopback.
- **D-10:** Loopback-mode startup output stays byte-for-byte unchanged (NET-05).
- **D-11:** Settings modal gets a read-only "Сеть" section, shown in both modes. Loopback: "Доступен только на этом компьютере" plus a hint about `BIND_HOST`. LAN: "Открыт в локальной сети" plus the same addresses `run.py` prints; clicking an address copies the link.
- **D-12:** The data for that section comes from a new REST endpoint available only to an authenticated user (cookie session). UI text is Russian, matching the rest of the UI.
- **D-13:** Accepted values after trimming surrounding whitespace: `127.0.0.1`, `localhost` (synonym for `127.0.0.1`) and `0.0.0.0`. Unset or empty means loopback. Anything else (`::`, `192.168.x.x`, `*`, ...) refuses to start. **This amends NET-01/NET-02**, which name only two values - update the REQUIREMENTS.md wording when the phase lands.
- **D-14:** Validation lives in `shared/config.py`, so it fires for `python run.py`, for a standalone `uvicorn agent.main:app`, and in tests. The single value drives both the UI and the Agent host.
- **D-15:** On an invalid value `run.py` prints a short English explanation to stderr with no traceback: the value given, the accepted values and what each means, and that a specific LAN IP is not supported ("use 0.0.0.0"). It exits with code 2 before orphan-port cleanup and before bootstrap-admin creation.
- **D-16:** LAN mode is documented as a new section in `docs/USER_GUIDE.md` (English). `BIND_HOST` is added to `.env.example` with a comment, and a short note on the Origin rule goes into `docs/ARCHITECTURE.md`.
- **D-17:** Firewall instructions give a ready `New-NetFirewallRule` PowerShell command (run as administrator) for TCP 8000,8001, short GUI steps via "Windows Defender Firewall with Advanced Security", and the command that removes the rule. The app itself never touches the firewall.
- **D-18:** The documented rule is scoped to the Private profile only (`-Profile Private`), with a note on how to check and change the network category when the home Wi-Fi is marked Public.

### Claude's Discretion
- How the host-match rule is wired into REST CORS (custom middleware, regex, or another approach), as long as D-01..D-07 hold.
- Whether the UI server's own CORS (`ui/main.py`) uses the same shared check or is simplified.
- Path and response shape of the network-info endpoint (D-12) and exact placement of the "Сеть" section inside the Settings modal.
- Exact wording of the warning banner, the error message and the UI strings.
- Case handling for `localhost`, and behaviour when the request has no usable `Host` header (reject is the safe default).
- How the supervisor passes the host to the Agent subprocess; health checks stay on `127.0.0.1`.

### Deferred Ideas (OUT OF SCOPE)
None. API keys, HTTP chat, rate/login limits, `/debug/routes` auth and the verification script belong to Phases 19-20. No IP filters/allowlists, no HTTPS, no binding to a specific LAN IP, no new dependencies.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| NET-01 | `BIND_HOST` in `.env`, single value for UI and Agent | Settings field + validator (Pattern 2); supervisor `host` param (Pattern 6) |
| NET-02 | Invalid value refuses to start with explanation | Import-time catch in `shared/config.py`, exit code 2 (Pattern 2) |
| NET-03 | LAN browser can log in and chat; Origin of the opened host accepted, no IP lists | `shared/origins.py` host-match + `HostMatchCORSMiddleware` (Patterns 1, 3); cookie/frontend verified (Section "Verified facts") |
| NET-04 | Foreign Origin rejected by REST CORS and WS | Same shared check at all three points; tests listed in "Tests affected" |
| NET-05 | `run.py` prints addresses + warning in LAN mode, loopback unchanged | `shared/network.py` + banner (Pattern 4) |
| NET-06 | Docs: LAN mode and Windows Firewall | Docs section (Pattern 8); command text in Code Examples |
</phase_requirements>

## Summary

The phase is small and entirely buildable with what is installed. Three things carry the risk: (1) the host-match Origin rule must be implemented once and reused by REST CORS, `require_allowed_origin` and the WebSocket check; (2) `BIND_HOST` validation must fire at `shared.config` import time and exit with code 2 and no traceback; (3) several existing tests and three Playwright e2e scripts encode the old behaviour (`Origin: null` accepted, `:8001` origins accepted, literal `"http://localhost:8000"` in `agent/state.py` patched by the e2e scripts) and must be updated.

Starlette's `CORSMiddleware` cannot express the rule (static list or one compiled regex, no access to the request `Host`). Recommended: a ~15-line pure-ASGI wrapper that computes `allowed` per request with the shared function and delegates to a stock `CORSMiddleware` built with `allow_origins=[origin] if allowed else []`. This keeps all Starlette header/preflight/credentials/Vary logic and makes disallowed origins behave exactly as today.

The cookie and the frontend already work on a LAN host (verified): the session cookie is host-only without `Domain`, `SameSite=Lax`, no `Secure`, and `fetch(..., credentials: 'include')` is used; browsers treat `192.168.x.x:8000` -> `192.168.x.x:8001` as same-site. One extra finding: the Windows machine here has VPN and link-local adapters, so the address list needs the `isup` filter (and a link-local filter) to be useful.

**Primary recommendation:** create `shared/origins.py` (rule + `HostMatchCORSMiddleware`) and `shared/network.py` (address list), validate `BIND_HOST` in `shared/config.py` with import-time `SystemExit(2)`, keep `CORS_ORIGINS` out of `agent/state.py`, and fix the listed tests and e2e scripts in the same plan wave as the behaviour change.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Bind host selection | Process launcher (`run.py`, `ui/supervisor.py`) | `shared/config.py` | One setting drives both uvicorn invocations |
| BIND_HOST validation | `shared/config.py` | - | Runs for every entry point incl. tests (D-14) |
| Origin rule | `shared/origins.py` | Agent API, WS | Pure function, no I/O, shared by all enforcement points |
| REST CORS headers | Agent API (middleware) | UI server (middleware) | Browser calls the Agent cross-origin (UI :8000 -> Agent :8001) |
| Network address discovery | `shared/network.py` (psutil) | `run.py` and Agent endpoint | One source of truth for banner and Settings section |
| Network-info endpoint | Agent API (`/api/v1/network`) | - | Frontend only talks to the Agent for data; needs cookie auth |
| "Сеть" section + copy | Browser | - | Read-only render from endpoint; clipboard fallback in vanilla JS |
| Firewall | Operator / docs | - | App never touches the firewall (D-17) |

## Standard Stack

No new packages. Everything is already in `requirements.txt` and installed.

### Core (installed, verified in this environment)
| Library | Installed | Purpose | Use |
|---------|-----------|---------|-----|
| Starlette | 1.6.0 [VERIFIED: python import] | `CORSMiddleware`, `Headers` | Wrapped by the custom middleware |
| FastAPI | 0.141.1 [VERIFIED: python import] | App/Depends | Unchanged |
| pydantic-settings | 2.15.0 [VERIFIED: python import] | `Settings` | Add `BIND_HOST` + `field_validator` |
| psutil | 7.2.2 [VERIFIED: python import] | `net_if_addrs`, `net_if_stats` | Interface IPv4 discovery |
| Python | 3.13.15 [VERIFIED: python --version] | stdlib `ipaddress`, `socket`, `urllib.parse` | Parsing and filtering |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Wrapper around stock `CORSMiddleware` | Subclass overriding `is_allowed_origin` | Subclass method has no access to the request/Host; would need per-request instance state (race under concurrency). Rejected. |
| Wrapper | `allow_origin_regex` | Regex is static; Host is per request. A regex permitting "any private IP" would re-introduce IP-range lists and also admit other LAN machines' origins. Rejected. |
| Wrapper | Re-implement CORS logic from scratch | Duplicates ~80 lines of preflight/Vary/credentials logic and diverges on Starlette upgrades. Rejected. |

**Package Legitimacy Audit:** not applicable - this phase installs no external packages (psutil/pydantic-settings/starlette already in `requirements.txt`). Packages removed: none. Packages flagged: none.

## Architecture Patterns

### System Architecture Diagram

```
.env BIND_HOST --> shared/config.py (import time)
                      |-- invalid --> stderr message, exit(2)   (before run.py main, cleanup_port, bootstrap)
                      v valid: 127.0.0.1 | 0.0.0.0
run.py main --> [LAN? banner + shared/network.reachable_urls] --> uvicorn.run(UI, host=BIND_HOST, :8000)
   |
   +--> ui.main lifespan --> AgentSupervisor(host=BIND_HOST) --> uvicorn agent.main:app --host BIND_HOST :8001
                               health/debug-routes probes stay on 127.0.0.1

Browser (any LAN machine) --http--> UI :8000  (static pages)
   |  fetch+cookie, Origin=http://H:8000, Host=H:8001
   v
Agent :8001
   HostMatchCORSMiddleware --(shared/origins.is_allowed_origin(Origin, Host))--> stock CORSMiddleware(allow_origins=[origin] | [])
   REST mutation --> require_allowed_origin --(same function)--> 403 or pass
   WS /ws/chat, /ws/events --> _validate_origin --(same function; missing Origin ok; "null" rejected)--> cookie auth --> stream
   GET /api/v1/network (cookie auth) --> shared/network.reachable_urls --> Settings "Сеть" section --> click = copy
```

### Recommended Project Structure
```
shared/origins.py     # LOOPBACK_ORIGINS, is_allowed_origin(), HostMatchCORSMiddleware
shared/network.py     # lan_ipv4_addresses(), reachable_urls(port)
shared/config.py      # BIND_HOST field, validator, import-time catch, settings.is_lan_mode
run.py                # banner, host=settings.BIND_HOST
ui/supervisor.py      # host kwarg -> --host
agent/network_api.py  # (or inline in agent/main.py) GET /api/v1/network
ui/static/index.html, app.js  # "Сеть" section, copyTextToClipboard() helper
```

### Pattern 1: Shared origin rule (`shared/origins.py`)
**What:** one pure function. **Rules:** origin must be `http` scheme with no path; host (lowercased) equals the `Host` header hostname (port stripped, IPv6 brackets handled) and explicit port == `UI_PORT`; OR origin is in `LOOPBACK_ORIGINS` (D-03). Missing/unparseable Host => only the loopback list can match (reject by default). `"null"` never matches. Callers handle the "no Origin" case (D-05) themselves.
```python
# shared/origins.py
from urllib.parse import urlsplit
from shared.config import settings

def _loopback_origins(port: int) -> tuple[str, ...]:
    return (f"http://localhost:{port}", f"http://127.0.0.1:{port}")

LOOPBACK_ORIGINS: tuple[str, ...] = _loopback_origins(settings.UI_PORT)  # no 8000 literals

def _host_of(host_header: str | None) -> str | None:
    if not host_header:
        return None
    try:
        return urlsplit(f"//{host_header}").hostname   # lowercases; strips :port; handles [::1]
    except ValueError:
        return None

def is_allowed_origin(origin: str, host_header: str | None) -> bool:
    if origin in LOOPBACK_ORIGINS:
        return True
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "http" or parts.path or parts.query or parts.fragment or parts.hostname is None:
        return False
    host = _host_of(host_header)
    return host is not None and parts.hostname == host and port == settings.UI_PORT
```
Both `urlsplit(...).hostname` values are lowercased, so `DESKTOP-PC` vs `desktop-pc` matches. Origin without explicit port yields `port=None` -> rejected (correct: `UI_PORT` is explicit). [VERIFIED: urllib semantics, repo read]

### Pattern 2: BIND_HOST validation at import time (`shared/config.py`)
`settings = Settings()` runs at import, and `run.py` imports `shared.config` on line 9, i.e. before `main()`. So the catch must be at module level in `config.py`; that also covers `uvicorn agent.main:app` and tests (D-14) and trivially precedes `cleanup_port`/bootstrap (D-15). Pydantic wraps a validator `ValueError` in `ValidationError`; extract the message from `err["ctx"]["error"]`.
```python
BIND_LOOPBACK, BIND_ALL = "127.0.0.1", "0.0.0.0"

class Settings(BaseSettings):
    BIND_HOST: str = BIND_LOOPBACK

    @field_validator("BIND_HOST", mode="before")
    @classmethod
    def _check_bind_host(cls, value: object) -> str:
        raw = "" if value is None else str(value).strip()
        key = raw.lower()
        if key in ("", "localhost", BIND_LOOPBACK):
            return BIND_LOOPBACK
        if key == BIND_ALL:
            return BIND_ALL
        raise ValueError(bind_host_error_message(raw))

    @property
    def is_lan_mode(self) -> bool:
        return self.BIND_HOST == BIND_ALL

def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        errs = exc.errors()
        if any(e["loc"] == ("BIND_HOST",) for e in errs):
            msg = next(str(e["ctx"]["error"]) for e in errs if e["loc"] == ("BIND_HOST",))
            print(msg, file=sys.stderr)
            raise SystemExit(2) from None
        raise

settings = _load_settings()
```
Notes: (a) `raise SystemExit("text")` exits with code 1, not 2 - print to stderr then `SystemExit(2)` (note `check_python_version` uses the string form, code 1; do not copy it literally). (b) `from None` suppresses the chained traceback; the interpreter prints nothing for `SystemExit`. (c) Keep the message ASCII (cp1252 consoles). (d) Define `bind_host_error_message` above `Settings` (module level function) - it states the value given, accepted values (`127.0.0.1`/`localhost` = this PC only, `0.0.0.0` = whole LAN), that a specific LAN IP is not supported, "use 0.0.0.0". (e) Pydantic-settings precedence: process env var beats `.env`; an empty `BIND_HOST=` becomes `""` -> loopback. (f) Tests construct `Settings(BIND_HOST="x")` to assert `ValidationError`, and use `subprocess.run([sys.executable, "-c", "import shared.config"], env={..., "BIND_HOST": "192.168.1.5"})` to assert `returncode == 2`, "Traceback" not in stderr. Run it with cwd = repo root; scrub `.env` influence by relying on the env var taking precedence.

### Pattern 3: `HostMatchCORSMiddleware` (recommended wiring for REST CORS)
```python
# shared/origins.py
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

class HostMatchCORSMiddleware:
    """CORS whose allowed origin depends on the request Host (D-01), credentials enabled."""
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send); return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is None:
            await self.app(scope, receive, send); return
        allowed = is_allowed_origin(origin, headers.get("host"))
        inner = CORSMiddleware(
            self.app, allow_origins=[origin] if allowed else [],
            allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
        )
        await inner(scope, receive, send)
```
`app.add_middleware(HostMatchCORSMiddleware)` replaces the `CORSMiddleware` registrations in `agent/main.py:445` and `ui/main.py:39`. Constructing `CORSMiddleware` per request is a few dict/sorted operations, negligible here. A disallowed origin gets no `Access-Control-Allow-Origin` (and a 400 on preflight), identical to today. CORS only controls what the browser may read; the server-side block for mutations is `require_allowed_origin` (unchanged semantics), and cookie auth + `SameSite=Lax` still apply. [VERIFIED: read Starlette 1.6.0 `starlette/middleware/cors.py` source]

For `ui/main.py`: the UI server only serves static files and the browser never calls it cross-origin, so its CORS is effectively unused. Recommendation: swap in the same `HostMatchCORSMiddleware` (one-line change, removes the third copy of the list, no behaviour risk). Removing it is also valid but changes more.

### Pattern 4: Address list, one source of truth (`shared/network.py`)
`psutil.net_if_addrs()` returns `{name: [snicaddr(family, address, netmask, ...)]}` and has no up flag; `psutil.net_if_stats()[name].isup` gives it. Observed on this Windows 11 machine [VERIFIED: ran it]: 7 adapters, 5 down but still carrying APIPA `169.254.x.x` addresses, one Wi-Fi `192.168.88.224/24` up, a VPN `10.8.1.1/32` up, loopback `127.0.0.1` up, adapter names in Cyrillic (non-ASCII - never print names, JSON is fine), hostname `DESKTOP-CR8NVEL`. So filter: `family == AF_INET`, `stats.get(name)` exists and `isup`, not loopback (`ipaddress.ip_address(a).is_loopback`), and skip link-local `169.254.0.0/16` (`is_link_local`) - an up interface with APIPA is unreachable for peers. (Link-local skip is a small refinement of D-08; flag it to the user via the plan, harmless.) VPN `/32` entries stay in per D-08.
```python
def lan_ipv4_addresses() -> list[str]:
    stats = psutil.net_if_stats()
    found: list[str] = []
    for name, addrs in psutil.net_if_addrs().items():
        st = stats.get(name)
        if st is None or not st.isup:
            continue
        for a in addrs:
            if a.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(a.address)
            if ip.is_loopback or ip.is_link_local:
                continue
            if a.address not in found:
                found.append(a.address)
    return found

def reachable_urls(port: int) -> list[str]:
    urls = [f"http://{ip}:{port}" for ip in lan_ipv4_addresses()]
    urls.append(f"http://{socket.gethostname()}:{port}")
    urls.append(f"http://127.0.0.1:{port}")
    return urls
```
Wrap psutil calls so an `OSError`/`psutil.Error` degrades to an empty IP list (never crash startup or the endpoint). The Agent endpoint lives in a different process than `run.py` but both are on the same host, so calling the same function yields the same list; no IPC needed (the earlier concern is moot). Hostname URL: `socket.gethostname()` (Windows NetBIOS/DNS resolves it for most LAN peers; the Origin rule is case-insensitive so any capitalization the browser uses works).

### Pattern 5: Network-info endpoint
`GET /api/v1/network` in the Agent, `Depends(get_current_user)` (no Origin dependency - GET). Recommended response: `{"lan_mode": bool, "bind_host": "0.0.0.0", "ui_port": 8000, "urls": [..]}` where `urls == []` in loopback mode (the frontend shows the static loopback text; no need to leak addresses). Compute with `await asyncio.to_thread(reachable_urls, settings.UI_PORT)` (project rule: all I/O async). Standalone `uvicorn agent.main:app --host X` ignores `BIND_HOST` for binding, so `lan_mode` reflects the setting, not the actual socket - acceptable, note in API_SPEC. Document in `docs/API_SPEC.md`.

### Pattern 6: Supervisor host passing
`AgentSupervisor.__init__(self, agent_port, db_path, host: str = "127.0.0.1")` (default keeps `tests/test_supervisor.py` and `tests/test_orphan_cleanup.py`, which construct it with two positional args, unchanged). `_launch_agent` uses `self.host` in place of the literal at `ui/supervisor.py:103`; `_ping_health` (line 141) and `_has_websocket_route` (line 151) stay on `http://127.0.0.1:` - a `0.0.0.0` bind accepts loopback connections. `ui/main.py:28` passes `host=settings.BIND_HOST`. The subprocess also re-reads `BIND_HOST` itself (env inherited + same `.env`), so validation runs there too. `kill_processes_on_port` and `cleanup_port` match by port regardless of address - no change.

### Pattern 7: Startup output (`run.py`)
Loopback branch must remain byte-identical (existing lines 57-59 incl. `Starting UI on http://127.0.0.1:{port}`; add a test with `capsys`). In LAN mode: print the framed warning block (`"=" * 60`, same width as the admin banner), then one line per URL from `reachable_urls`, then the usual "Agent will be auto-started" and "Press Ctrl+C" lines; `host=settings.BIND_HOST` in `uvicorn.run`. Keep the order: config import (validation) -> `check_python_version` -> `cleanup_port` -> bootstrap -> banner -> uvicorn. Banner text must be ASCII-safe for Windows consoles.

### Pattern 8: Clipboard fallback over plain HTTP
`navigator.clipboard` is undefined in non-secure contexts (LAN IP over HTTP). `ui/static/app.js:2137-2155` (`copyMcpToolName`) already has a `textarea` + `document.execCommand('copy')` fallback inline. Extract a helper `async function copyTextToClipboard(text) -> boolean` (try `navigator.clipboard?.writeText`, else the textarea fallback with `readOnly`, `focus()` and `select()` before `execCommand('copy')`, remove the element in `finally`), have `copyMcpToolName` use it, and use it for the address click. `execCommand('copy')` still works over HTTP inside a user-gesture handler (click). Toast text in Russian via existing `showToast`. Render addresses with `textContent` (no innerHTML, per DOMPurify rule). [CITED: MDN Clipboard API "secure contexts only"; execCommand fallback is the existing in-repo pattern]

### Anti-Patterns to Avoid
- Putting `if lan_mode` into the Origin check (violates D-02).
- Computing the allow-list once from `socket`/psutil addresses (that is an IP list; breaks hostnames and DHCP changes).
- Keeping `CORS_ORIGINS` literals in `agent/state.py` "for the e2e scripts".
- Checking `Origin` substring/`startswith` instead of parsing (`http://localhost:8000.evil.com` must fail; the parse-based rule handles this).
- `raise SystemExit("msg")` when the code must be 2.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CORS preflight/credentials/Vary | Own CORS response logic | Stock `CORSMiddleware` built per request | Edge cases (mirrored headers, Vary: Origin) already solved |
| Host/Origin parsing | String splitting | `urllib.parse.urlsplit` | Handles IPv6 brackets, case, bad ports |
| Interface enumeration | `socket.gethostbyname_ex`, `ipconfig` parsing | `psutil.net_if_addrs` + `net_if_stats` | Includes all adapters, no subprocess |
| IP classification | Regexes for 127/169.254 | `ipaddress.ip_address(...).is_loopback / .is_link_local` | Correct and readable |
| Settings validation | Manual env parsing | pydantic `field_validator` | Same error path for env and `.env` |

## Runtime State Inventory

Not a rename/migration phase. Nothing stored under the old behaviour. The only "state" touched: `agent/state.py::CORS_ORIGINS` (code, not runtime) and the e2e scripts that textually patch it (below). Stored data, live service config, OS-registered state, secrets, build artifacts: none affected. Windows Firewall rules are created by the operator, not the app.

## Common Pitfalls

### Pitfall 1: Tests and e2e scripts that encode the old behaviour
**What goes wrong:** removing `CORS_ORIGINS`/changing rules breaks tests and scripts.
**Affected (verified by grep):**
- `tests/test_ws_origin_validation.py`: `test_ws_accepts_null_origin` (lines 24-33) must become a rejection test (D-06); `test_ws_accepts_localhost_8001` and `test_ws_accepts_127_8001` (lines 60-81) must become rejections (D-04). `test_ws_accepts_none_origin`, `localhost:8000`, `127.0.0.1:8000` stay.
- `tests/test_websocket_cors.py::test_websocket_connect_with_null_origin` (lines 37-47) -> rejection. Other cases there stay (the `access-control-allow-origin == "http://localhost:8000"` assertion at line 57 still holds via D-03).
- Importers of `agent.state.CORS_ORIGINS`: `tests/test_cors.py`, `tests/test_mcp_origin.py`, `tests/test_memory_api.py:364`, `tests/test_task_memory_api.py:209` -> import `LOOPBACK_ORIGINS` from `shared.origins` (tuple; `CORS_ORIGINS[0]` indexing still works). `agent/dependencies.py`, `agent/main.py:56`, `agent/ws.py:33` are the code importers.
- Tests with `Origin: http://localhost:8000` plus httpx `ASGITransport` (Host `test`) or Starlette `TestClient` (Host `testserver`) keep passing via D-03. Foreign-origin tests (`http://evil.example(.com)`: `test_ws_security.py:42`, `test_kb_api.py:190`, `test_llm_providers_api.py` FOREIGN_ORIGIN, `test_mcp_origin.py`, `test_memory_api.py`, `test_scheduler_api.py`, `test_scheduler_events.py`, `test_rag_api.py`, `test_task_memory_api.py`, `test_cors.py:28`) all use port-less origins -> still rejected.
- `scripts/e2e_kb_playwright.py:129-130` and `scripts/e2e_llm_providers_playwright.py:183-184` call `patch("agent/state.py", '"http://localhost:8000"', ...)` and raise `RuntimeError("cannot patch ...")` when the literal is absent; their leftover scan also rejects any `8000/8001` literal in the copy except `shared/config.py` and `agent/ws.py`. Because `LOOPBACK_ORIGINS` derives from `settings.UI_PORT` and these scripts already pass `UI_PORT` in the subprocess env, delete those two patch lines in both scripts; keep new code free of `8000`/`8001` literals (defaults live only in `shared/config.py`). `scripts/e2e_mcp_chat_playwright.py` uses real ports and is unaffected.
**Warning signs:** `RuntimeError: cannot patch agent/state.py`.

### Pitfall 2: Import-time side effects and circular imports
`shared/origins.py` imports `settings` from `shared.config`; keep `shared.config` free of imports from `origins`/`network`. `shared/network.py` imports psutil only.

### Pitfall 3: SystemExit at import in the test session
A bad `BIND_HOST` in a developer's `.env` aborts `pytest` collection with exit code 2 and the message. This is intended (D-14); document it in `docs/TESTING_GUIDE.md`.

### Pitfall 4: DNS rebinding with a pure host-match rule
D-01 accepts `Origin: http://evil.com:8000` when `Host: evil.com:8001` (attacker domain rebound to the victim's LAN IP and serving on port 8000). Impact is limited: the session cookie is host-only for the real IP/hostname, so the victim's session is not sent to `evil.com`; the attacker only reaches unauthenticated endpoints (login, `/health`, `/debug/routes`). Accept as a known residual risk of the locked design; brute-force/login limits are Phase 19-20. Optional cheap hardening if the user wants it (not required): additionally require the Host hostname to be an IP literal, `localhost`, or the machine's `socket.gethostname()`. Record in docs/ARCHITECTURE.md note. [ASSUMED: analysis, not verified against an attack harness]

### Pitfall 5: Missing/odd Host header
HTTP/1.0 or crafted requests without `Host` -> `_host_of` returns `None` -> only loopback origins match (safe default, as CONTEXT suggests).

### Pitfall 6: Windows first-listen firewall prompt
First bind of `python.exe` on a non-loopback address may pop the Windows "allow access" dialog, which can create rules for the Public profile too (contradicting D-18's Private-only intent). Mention in docs and recommend declining Public there. [ASSUMED: known Windows behaviour, not reproduced here]

### Pitfall 7: Sensitive endpoints become LAN-visible
In LAN mode `/debug/routes` and `/health` are reachable unauthenticated, and login is unlimited. These belong to Phases 19-20; do not fix here, but the warning banner/docs should not claim the exposure is "safe".

## Code Examples

### Origin checks at the three points
```python
# agent/dependencies.py
async def require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None or is_allowed_origin(origin, request.headers.get("host")):
        return
    logger.warning("origin_rejected", origin=origin, path=request.url.path)
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cross-origin request rejected")

# agent/ws.py (agent/events.py keeps importing _validate_origin from here)
def _validate_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:                       # D-05
        return True
    if is_allowed_origin(origin, websocket.headers.get("host")):   # "null" falls through to False (D-06)
        return True
    logger.warning("ws_origin_rejected", origin=origin)
    return False
```

### Supervisor and run.py
```python
# ui/main.py
supervisor = AgentSupervisor(settings.AGENT_PORT, settings.DB_PATH, host=settings.BIND_HOST)
# run.py
uvicorn.run(app, host=settings.BIND_HOST, port=settings.UI_PORT, reload=False)
```

### Firewall docs (D-17/D-18; admin PowerShell)
```powershell
New-NetFirewallRule -DisplayName "AiAdventAgentV2 LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000,8001 -Profile Private
Remove-NetFirewallRule -DisplayName "AiAdventAgentV2 LAN"
Get-NetConnectionProfile                                   # check NetworkCategory
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private
```
[ASSUMED: standard NetSecurity/NetConnection cmdlets from training; the planner should have the executor confirm syntax with `Get-Help New-NetFirewallRule` on the dev machine. GUI path: Windows Defender Firewall with Advanced Security -> Inbound Rules -> New Rule -> Port -> TCP 8000,8001 -> Allow -> Private only.]

### Test sketch for the Host-dependent rule
```python
# httpx ASGITransport: Host comes from base_url or an explicit header
resp = await client.options("/health", headers={
    "Host": "192.168.1.5:8001", "Origin": "http://192.168.1.5:8000",
    "Access-Control-Request-Method": "GET"})
assert resp.headers["access-control-allow-origin"] == "http://192.168.1.5:8000"
# foreign: Origin http://evil.example:8000 with Host 192.168.1.5:8001 -> no ACAO / 403 on mutation
# WS: TestClient.websocket_connect(url, headers={"Host": "192.168.1.5:8001", "Origin": "http://192.168.1.5:8000"})
```
Pure unit tests of `is_allowed_origin` (table: LAN IP, hostname case, wrong port, https scheme, `null`, port-less, `localhost:8000.evil`, IPv6 host, missing Host) are the cheapest high-value coverage.

## Verified facts about CONTEXT.md claims

| Claim | Status |
|-------|--------|
| Origin lists at `agent/state.py:7`, `ui/main.py:19`, `agent/ws.py:154` | Confirmed (exact lines) |
| `CORSMiddleware` at `agent/main.py:445`, `ui/main.py:39` | Confirmed (445/446-451, 39/40-45) |
| `require_allowed_origin` passes missing Origin, uses `CORS_ORIGINS` | Confirmed (`agent/dependencies.py:19-29`) |
| `agent/events.py` imports `_validate_origin` (line 16) and uses it at 124 | Confirmed |
| `ws_chat` calls `_validate_origin` at `agent/ws.py:1215`; `Origin: null` allowed at `ws.py:150` | Confirmed |
| Host hardcoded at `run.py:65` and `ui/supervisor.py:103` | Confirmed; health probes at `supervisor.py:141,151` |
| Session cookie httponly, samesite=lax, no secure (`agent/main.py:511-517`) | Confirmed; host-only (no `Domain`), so it is sent to both ports of the same host |
| Frontend derives Agent host from `window.location.hostname` | Confirmed (`app.js:3-5`, `login.html:41-42`); `AGENT_PORT = 8001` literal; fetches use `credentials: 'include'` (`app.js:83`, `login.html:61`); no `localhost`/`127.0.0.1` literals in `ui/static` |
| Clipboard fallback exists at `app.js:2137-2139` | Confirmed (function `copyMcpToolName`, lines 2135-2155) |
| Settings modal sections | `#settings-modal` at `index.html:272`; `#mcp-section` ~341, `#llm-providers-section` ~386; add `#network-section` as a sibling `<section class="border-t border-slate-700 p-4 space-y-4">` |

## State of the Art

| Old | Current | Impact |
|-----|---------|--------|
| Static CORS list | Request-aware CORS wrapper (this phase) | Works for any LAN IP/hostname without lists |
| `Origin: null` allowed on WS | Rejected (D-06) | `null` comes from sandboxed iframes/`file://`, now blocked |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | DNS-rebinding impact is limited to unauthenticated endpoints because the cookie is host-only | Pitfall 4 | Low; planner may add optional Host hardening |
| A2 | Windows first-listen prompt can create Public-profile rules | Pitfall 6 | Low; wording in docs only |
| A3 | PowerShell cmdlet syntax in the firewall commands | Code Examples | Docs wrong; verify with `Get-Help` on the dev machine |
| A4 | Skipping link-local 169.254.x.x addresses is acceptable vs. D-08 wording | Pattern 4 | Low; user may prefer to list them |
| A5 | `execCommand('copy')` works over HTTP in current Chrome/Edge inside a click handler (in-repo code already relies on it) | Pattern 8 | Copy fails -> toast error shown; no functional break |

## Open Questions

1. **Hardening Host to IP/known hostname (Pitfall 4)?**
   - Known: D-01 is locked as plain host-match; residual risk is small.
   - Recommendation: ship D-01 as is; mention the risk in ARCHITECTURE note; do not add hardening unless the user asks.
2. **Should the banner replace or follow `Starting UI on http://127.0.0.1:8000` in LAN mode?**
   - Recommendation: keep that line (loopback URL is also listed per D-08) and print the framed block and URL list before it; planner's call, loopback output stays unchanged either way.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | yes | 3.13.15 | - |
| Starlette / FastAPI | middleware | yes | 1.6.0 / 0.141.1 | - |
| psutil | address discovery | yes | 7.2.2 | - |
| pydantic-settings | BIND_HOST | yes | 2.15.0 | - |
| Second LAN machine / admin PowerShell | manual UAT of NET-03 and firewall rule | not checkable here | - | Verify NET-03 by a real-LAN browser (or phone) in UAT; automated tests use Host/Origin header overrides |

Network on this machine: Wi-Fi `192.168.88.224`, VPN `10.8.1.1`, hostname `DESKTOP-CR8NVEL` [VERIFIED: psutil, socket].

## Validation Architecture

Skipped: `workflow.nyquist_validation` is `false` in `.planning/config.json`. Test guidance is in Pitfall 1 and the test sketch. Test runner: `pytest tests/ -v` (asyncio_mode=auto). New tests: `tests/test_origins.py` (rule + middleware), `tests/test_bind_host.py` (validator + subprocess exit 2), `tests/test_network_info.py` (monkeypatched psutil + endpoint auth 401/200), `run.py` output tests with `capsys`, supervisor `--host` argument test. Add entries to `docs/TESTING_GUIDE.md`.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | unchanged | Existing cookie session; no change |
| V3 Session Management | yes (exposure) | HTTP-only, SameSite=Lax cookie, host-only; no Secure flag because HTTP is a locked decision |
| V4 Access Control | yes | `/api/v1/network` requires `get_current_user` |
| V5 Input Validation | yes | Strict parsed Origin/Host rule; BIND_HOST whitelist |
| V9 Communications | accepted risk | Plain HTTP on LAN is a locked decision; warn in banner/docs |

### Known Threat Patterns

| Pattern | STRIDE | Mitigation |
|---------|--------|------------|
| Cross-site request / CSWSH from a foreign site | Tampering / Spoofing | Parsed Origin check at REST CORS, `require_allowed_origin`, WS handshake; `null` rejected |
| Substring/prefix Origin bypass (`localhost:8000.evil.com`) | Spoofing | Exact parse (`urlsplit`), scheme+host+port equality |
| DNS rebinding | Spoofing | Cookie host-scoped; residual unauthenticated surface (Pitfall 4) |
| Information disclosure of network layout | Info disclosure | Endpoint behind auth; returns no addresses in loopback mode |
| Sniffing on LAN | Info disclosure | Accepted (no HTTPS by decision); warning text |
| Unauthenticated `/debug/routes`, unlimited login | Info disclosure / brute force | Out of scope, Phases 19-20 |

## Project Constraints (from CLAUDE.md)

- No Docker/npm/Node/Redis; no `multiprocessing`/`os.fork`; supervisor stays `asyncio.create_subprocess_exec`; IPC REST+WS only.
- Frontend vanilla JS + CDN only; never insert HTML without `DOMPurify.sanitize()` (use `textContent`).
- Type hints everywhere, `async`/`await` for I/O, `structlog` (`get_logger(__name__)`, snake_case event names, never `print()` - exceptions: `run.py` banner output, and the pre-logging `shared/config.py` stderr message), `datetime.now(timezone.utc)`.
- Specific exceptions only, no bare `except`; `await session.commit()`/rollback pattern; `.is_(None)`.
- Single-line module docstrings on every new file; UI text Russian; planning docs in English; branch stays `Day30`; push+merge handled per project memory after phase completion; no Claude co-author line in commits (user's global CLAUDE.md overrides the harness attribution reminder).
- `docs/TESTING_GUIDE.md` must be checked/updated for new tests; no linter is configured.

## Sources

### Primary (HIGH confidence)
- Repository files read this session: `agent/state.py`, `agent/dependencies.py`, `agent/ws.py`, `agent/events.py`, `agent/main.py`, `ui/main.py`, `ui/supervisor.py`, `run.py`, `shared/config.py`, `shared/runtime.py`, `ui/static/app.js`, `ui/static/login.html`, `ui/static/index.html`, `.env.example`, `tests/*` (grep), `scripts/e2e_*_playwright.py`.
- Installed Starlette 1.6.0 `starlette/middleware/cors.py` source (read in full).
- Live `psutil.net_if_addrs/net_if_stats`, `socket.gethostname()` output on this machine.

### Secondary (MEDIUM/LOW)
- PowerShell NetSecurity cmdlets, Windows firewall prompt behaviour, DNS-rebinding analysis: training knowledge, tagged [ASSUMED].

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new packages; versions read from the environment
- Architecture: HIGH - based on the Starlette source and the repo code
- Pitfalls: HIGH for test/script breakage (grep-verified), MEDIUM for Windows firewall/rebinding notes

**Research date:** 2026-10-12
**Valid until:** 2026-11-12 (stable; Starlette CORS internals are the only moving part)
