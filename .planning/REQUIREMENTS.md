# Requirements: AiAdventAgentV2 — Week 6: Local LLM as a Private Service

**Defined:** 2026-10-12
**Core Value:** The agent must demonstrably separate and manage distinct kinds of state — short-term dialog, working task data, long-term profile/knowledge, and task lifecycle — making explicit, inspectable decisions about what goes where, rather than dumping everything into one undifferentiated context window.

**Milestone goal:** The app runs as a private AI service on the home network: the local model is reachable from any LAN machine, both through the browser UI and through a plain HTTP chat endpoint (Day 30 assignment).

## v4.0 Requirements

Requirements for milestone v4.0. Each maps to exactly one roadmap phase.

### Network Access

- [x] **NET-01**: Operator can set `BIND_HOST` in `.env` to `127.0.0.1` (default; `localhost` is an accepted synonym and an unset or empty value means the same) or `0.0.0.0`; surrounding whitespace is ignored and the single value applies to both the UI and the Agent process.
- [x] **NET-02**: With any other `BIND_HOST` value (for example `::`, a specific LAN IP such as `192.168.1.5`, or `*`) the app refuses to start, prints a clear explanation and exits with code 2.
- [x] **NET-03**: With `BIND_HOST=0.0.0.0`, user can open the UI, log in and chat with the model from a browser on another LAN machine; REST (CORS) and WebSocket accept the Origin of the host the UI was opened from, with no IP lists.
- [x] **NET-04**: Requests from a foreign Origin (another site) are still rejected by REST CORS and by the WebSocket Origin check.
- [x] **NET-05**: In LAN mode `run.py` prints the addresses the service is reachable at on the network and a warning that it is exposed; in loopback mode the startup output is unchanged.
- [x] **NET-06**: Documentation describes how to enable LAN mode and how to open ports 8000/8001 in Windows Firewall.

### API Keys

- [x] **KEY-01**: User can create a named API key in Settings; the full key is shown exactly once.
- [x] **KEY-02**: Only a hash of the key is stored; the key list shows name, key prefix, creation date and last-used date.
- [x] **KEY-03**: User can revoke a key, and a revoked key stops working immediately.
- [x] **KEY-04**: `Authorization: Bearer <key>` authenticates REST routes as the key's owner, with the same ownership checks as the session cookie. Cookie-only exceptions, which answer 403 to a key: API key management (`/api/v1/api-keys`), adding a user (`POST /api/v1/auth/users`) and logout (`POST /api/v1/auth/logout`). WebSocket endpoints stay cookie-only.
- [x] **KEY-05**: An invalid or revoked key gets HTTP 401.

### HTTP Chat

- [x] **HCHAT-01**: User can `POST` a message (text and model) to an existing chat and receive the whole reply in one JSON body together with token statistics.
- [x] **HCHAT-02**: Messages sent over HTTP are written to the chat's message tree and are visible in the UI; the same settings, memory, context compression and RAG apply as for a WebSocket turn.
- [x] **HCHAT-03**: Failures are returned as clear HTTP statuses: context overflow with the `CONTEXT_OVERFLOW` code, LM Studio not running, and chat busy with another request.
- [x] **HCHAT-04**: `docs/API_SPEC.md` documents the endpoint and a full `curl` scenario: create key, create chat, send message.

### Limits

- [ ] **LIM-01**: The per-chat messages-per-minute limit is configurable in `.env` and shared by WebSocket and HTTP chat.
- [ ] **LIM-02**: When the limit is exceeded, HTTP chat responds with 429 and a `Retry-After` header.
- [ ] **LIM-03**: After several consecutive wrong passwords, login is temporarily blocked (threshold and block time configurable).
- [ ] **LIM-04**: `/debug/routes` is available only to an authenticated user.
- [ ] **LIM-05**: The number of concurrent generations is capped by a setting; excess requests wait in a queue and complete successfully.

### Verification and Report

- [ ] **VER-01**: A script given the server address and an API key checks reachability and authentication over the network.
- [ ] **VER-02**: The script sends N parallel requests to a real model and reports successes, errors and response times.
- [ ] **VER-03**: The script demonstrates the rate limit and the context-overflow behaviour.
- [x] **VER-04**: `Day30_report.md` holds the results of a live run from a second LAN machine.
- [x] **VER-05**: Manual check: chatting in the UI from another device on the same network. (Approved by the user on 2026-10-12.)
- [ ] **VER-06**: pytest covers bind host, Origin, API keys, HTTP chat and limits; `docs/TESTING_GUIDE.md` has matching sections.

## Future Requirements

Deferred. Tracked but not in the current roadmap.

### HTTP Chat

- **HCHAT-F1**: Streaming (SSE) reply for HTTP chat via a `stream` parameter.
- **HCHAT-F2**: Stateless HTTP chat where the client sends the full `messages` list and nothing is stored.

### API Keys

- **KEY-F1**: API-key authentication for WebSocket endpoints.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| IP filters / LAN IP allowlists | User decision: every machine on the local network must have access |
| HTTPS / TLS termination | Home LAN only; the session cookie works over plain HTTP |
| Binding to a specific LAN IP | `0.0.0.0` covers the use case; one more value adds validation without benefit |
| Docker / reverse proxy deployment | Hard project constraint: no Docker, two-process `asyncio.create_subprocess_exec` model |
| Exposure to the public internet | Assignment asks for a private service on a home server |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| NET-01 | Phase 18 | Complete |
| NET-02 | Phase 18 | Complete |
| NET-03 | Phase 18 | Complete |
| NET-04 | Phase 18 | Complete |
| NET-05 | Phase 18 | Complete |
| NET-06 | Phase 18 | Complete |
| KEY-01 | Phase 19 | Complete |
| KEY-02 | Phase 19 | Complete |
| KEY-03 | Phase 19 | Complete |
| KEY-04 | Phase 19 | Complete |
| KEY-05 | Phase 19 | Complete |
| HCHAT-01 | Phase 19 | Complete |
| HCHAT-02 | Phase 19 | Complete |
| HCHAT-03 | Phase 19 | Complete |
| HCHAT-04 | Phase 19 | Complete |
| LIM-01 | Phase 20 | Pending |
| LIM-02 | Phase 20 | Pending |
| LIM-03 | Phase 20 | Pending |
| LIM-04 | Phase 20 | Pending |
| LIM-05 | Phase 20 | Pending |
| VER-01 | Phase 20 | Pending |
| VER-02 | Phase 20 | Pending |
| VER-03 | Phase 20 | Pending |
| VER-04 | Phase 20 | Complete |
| VER-05 | Phase 20 | Complete (user-approved 2026-10-12) |
| VER-06 | Phase 20 | Pending |

**Coverage:**
- v4.0 requirements: 26 total
- Mapped to phases: 26
- Unmapped: 0 ✓

---
*Requirements defined: 2026-10-12*
*Last updated: 2026-10-12 after Phase 18 planning (NET-01/NET-02 amended by decision D-13)*
