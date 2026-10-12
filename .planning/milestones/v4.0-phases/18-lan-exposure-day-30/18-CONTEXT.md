# Phase 18: LAN exposure (Day 30) - Context

**Gathered:** 2026-10-12
**Status:** Ready for planning

<domain>
## Phase Boundary

One `.env` setting (`BIND_HOST`) exposes the whole app (UI on 8000, Agent on 8001) on the home network. A user on another LAN machine opens the UI in a browser, logs in and gets a streamed reply over WebSocket, with no IP lists configured. Requests and WebSocket connections carrying a foreign site's Origin stay rejected. `run.py` prints the reachable addresses and an exposure warning in LAN mode, and the docs explain LAN mode and Windows Firewall.

Covers NET-01..NET-06. API keys, HTTP chat, rate/login limits, `/debug/routes` auth and the verification script belong to Phases 19-20.

Locked by REQUIREMENTS.md / PROJECT.md (not re-discussed): no IP filters or allowlists, no HTTPS, no binding to a specific LAN IP, no new dependencies, cookie session unchanged, branch `Day30`.

</domain>

<decisions>
## Implementation Decisions

### Origin check rule
- **D-01:** An Origin is accepted when its host equals the host of the request's `Host` header and its port equals `UI_PORT`. This covers any LAN IP and machine name (`http://my-pc:8000`) with no lists; a foreign site fails because its host differs.
- **D-02:** The host-match rule applies always, in both loopback and LAN mode (one rule, no mode branch in the Origin check).
- **D-03:** `http://localhost:{UI_PORT}` and `http://127.0.0.1:{UI_PORT}` stay always-allowed in addition to the host-match rule, so existing tests (Origin `http://localhost:8000` with a test-client Host) keep passing unchanged.
- **D-04:** The `:8001` origin variants in `agent/ws.py::_validate_origin` (`allowed_patterns`) are removed — the UI is never served from the Agent port.
- **D-05:** A request or WebSocket handshake with no `Origin` header stays allowed (non-browser clients: curl, scripts, tests; still gated by the session cookie).
- **D-06:** `Origin: null` is rejected on WebSocket in both modes (today it is allowed at `agent/ws.py:150`).
- **D-07:** The loopback list and the check live in one shared place used by all three enforcement points: REST CORS, `require_allowed_origin` (REST mutations) and the WebSocket check (`/ws/chat`, `/ws/events`). The duplicated lists in `agent/state.py`, `ui/main.py` and `agent/ws.py` are consolidated.

### Startup output and LAN visibility
- **D-08:** In LAN mode `run.py` prints one URL per non-loopback IPv4 address of an active interface, plus `http://<machine-name>:{UI_PORT}` and the local `http://127.0.0.1:{UI_PORT}`. Several lines on a machine with VPN/WSL/Hyper-V adapters are acceptable.
- **D-09:** The exposure warning is a framed block (`=` rule lines, same style as the bootstrap-admin banner at `run.py:50-55`), in English like the rest of `run.py` output. Content: the service is reachable by everyone on the network, traffic is plain HTTP (no HTTPS), do not enable on public networks, how to go back to loopback.
- **D-10:** Loopback-mode startup output stays byte-for-byte unchanged (NET-05).
- **D-11:** Settings modal gets a read-only "Сеть" section, shown in both modes. Loopback: "Доступен только на этом компьютере" plus a hint about `BIND_HOST`. LAN: "Открыт в локальной сети" plus the same addresses `run.py` prints; clicking an address copies the link.
- **D-12:** The data for that section comes from a new REST endpoint available only to an authenticated user (cookie session). UI text is Russian, matching the rest of the UI.

### BIND_HOST values and validation
- **D-13:** Accepted values after trimming surrounding whitespace: `127.0.0.1`, `localhost` (synonym for `127.0.0.1`) and `0.0.0.0`. Unset or empty means loopback. Anything else (`::`, `192.168.x.x`, `*`, ...) refuses to start. **This amends NET-01/NET-02**, which name only two values — update the REQUIREMENTS.md wording when the phase lands.
- **D-14:** Validation lives in `shared/config.py`, so it fires for `python run.py`, for a standalone `uvicorn agent.main:app`, and in tests. The single value drives both the UI and the Agent host.
- **D-15:** On an invalid value `run.py` prints a short English explanation to stderr with no traceback: the value given, the accepted values and what each means, and that a specific LAN IP is not supported ("use 0.0.0.0"). It exits with code 2 before orphan-port cleanup and before bootstrap-admin creation.

### Documentation and Windows Firewall
- **D-16:** LAN mode is documented as a new section in `docs/USER_GUIDE.md` (English, like the rest of that file). `BIND_HOST` is added to `.env.example` with a comment, and a short note on the Origin rule goes into `docs/ARCHITECTURE.md`.
- **D-17:** Firewall instructions give a ready `New-NetFirewallRule` PowerShell command (run as administrator) for TCP 8000,8001, short GUI steps via "Windows Defender Firewall with Advanced Security", and the command that removes the rule. The app itself never touches the firewall.
- **D-18:** The documented rule is scoped to the Private profile only (`-Profile Private`), with a note on how to check and change the network category when the home Wi-Fi is marked Public.

### Claude's Discretion
- How the host-match rule is wired into REST CORS (Starlette `CORSMiddleware` takes a static list or a regex — custom middleware, regex, or another approach is the planner's call), as long as D-01..D-07 hold.
- Whether the UI server's own CORS (`ui/main.py`) uses the same shared check or is simplified.
- Path and response shape of the network-info endpoint (D-12) and exact placement of the "Сеть" section inside the Settings modal.
- Exact wording of the warning banner, the error message and the UI strings.
- Case handling for `localhost`, and behaviour when the request has no usable `Host` header (reject is the safe default).
- How the supervisor passes the host to the Agent subprocess; health checks stay on `127.0.0.1`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and scope
- `.planning/REQUIREMENTS.md` — NET-01..NET-06 and the Out of Scope table (no IP filters, no HTTPS, no specific-IP binding)
- `.planning/ROADMAP.md` §Phase 18 — goal and the five success criteria
- `.planning/PROJECT.md` — milestone v4.0 goal, hard constraints (two-process model, vanilla JS, no new dependencies)

### Docs to update
- `docs/USER_GUIDE.md` — gets the LAN mode + Firewall section (D-16..D-18)
- `docs/ARCHITECTURE.md` — gets the Origin rule note (D-16)
- `docs/API_SPEC.md` — document the new network-info endpoint (D-12)
- `docs/TESTING_GUIDE.md` — required scenario coverage per test file; bind host and Origin tests are written in this phase (VER-06 is tracked in Phase 20)

### Codebase maps
- `.planning/codebase/ARCHITECTURE.md` — process split, supervisor, entry points
- `.planning/codebase/CONVENTIONS.md` — naming, logging, error-handling rules
- `.planning/codebase/TESTING.md` — test fixtures and isolation

No external specs or ADRs — the rest is captured in the decisions above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shared/config.py::Settings` — pydantic-settings class; `BIND_HOST` goes here with a validator (D-13, D-14).
- `shared/runtime.py::check_python_version` — existing "fail fast with a clear message" startup check called first in `run.py::main`; model for the BIND_HOST exit path.
- `run.py:50-55` — framed bootstrap-admin banner; style reference for the LAN warning (D-09).
- `psutil` (already a dependency, used in `run.py::cleanup_port`) — `net_if_addrs` / `net_if_stats` give interface IPv4 addresses without new packages (D-08).
- `agent/dependencies.py::require_allowed_origin` — REST mutation Origin guard; already lets a missing Origin through.
- `ui/static/app.js:2137-2139` — existing clipboard copy with a note that `navigator.clipboard` is unavailable outside secure contexts (i.e. when opened via a LAN IP over HTTP). The address copy in D-11 needs a fallback that works over plain HTTP.

### Established Patterns
- Host is hardcoded in two places: `run.py:65` (`uvicorn.run(app, host="127.0.0.1", ...)`) and `ui/supervisor.py:103` (`--host 127.0.0.1` for the Agent subprocess).
- Origin lists are static and duplicated: `agent/state.py:7` (`CORS_ORIGINS`), `ui/main.py:19`, `agent/ws.py:154` (`allowed_patterns`).
- Origin is enforced at three points: `CORSMiddleware` (`agent/main.py:445`, `ui/main.py:39`), `require_allowed_origin`, and `agent/ws.py::_validate_origin` (also imported by `agent/events.py:16` for `/ws/events`).
- The frontend already derives the Agent address from `window.location.hostname` (`ui/static/app.js:3-5`, `ui/static/login.html:41-42`) — no change needed for a LAN host. `AGENT_PORT` is hardcoded to 8001 there.
- Session cookie: `httponly`, `samesite="lax"`, no `secure` flag (`agent/main.py:511-517`) — works over plain HTTP and across ports on the same host.
- Tests send `Origin: http://localhost:8000` (`WS_ORIGIN` in `tests/test_concurrent_ws.py`, `test_invariants_ws.py`, `test_memory_ws.py`; `tests/test_cors.py` parametrizes over `CORS_ORIGINS`; foreign-origin tests in `tests/test_kb_api.py:186` and `tests/test_llm_providers_api.py:298`).

### Integration Points
- `run.py::main` — validation exit, LAN banner, `host=` argument.
- `ui/supervisor.py::_start_agent` — `--host` for the Agent; `_ping_health` / `_has_websocket_route` keep using `127.0.0.1`.
- `agent/state.py`, `agent/dependencies.py`, `agent/ws.py`, `agent/main.py`, `ui/main.py` — shared Origin check.
- `ui/static/index.html` (`#settings-modal`, existing sections "MCP серверы" and "Провайдеры LLM") and `ui/static/app.js` — the "Сеть" section.
- `.env.example` — `BIND_HOST` entry.

</code_context>

<specifics>
## Specific Ideas

- The Settings "Сеть" section and the console banner must show the same address list — one source of truth for "where is the service reachable".
- The invalid-value message should say outright that a specific LAN IP is not supported and point to `0.0.0.0`.
- The firewall instructions include the removal command, not only the creation one.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 18-LAN exposure (Day 30)*
*Context gathered: 2026-10-12*
