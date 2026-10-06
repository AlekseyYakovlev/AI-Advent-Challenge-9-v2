---
phase: quick-261006-lrs
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - shared/models.py
  - shared/database.py
  - agent/schemas.py
  - agent/main.py
  - agent/mcp_tools.py
  - ui/static/app.js
  - tests/test_mcp_auto_connect.py
  - tests/test_mcp_api.py
  - .planning/ROADMAP.md
  - .planning/phases/999.12-mcp-disable-toggle-ineffective/
autonomous: true
requirements: [BACKLOG-999.12]

must_haves:
  truths:
    - "After the user clicks «Отключить» on a connected MCP server, the next chat turn does NOT reconnect it and sends none of its tool schemas to the LLM"
    - "The «Отключить» state survives an Agent restart (persisted in the DB, not only in memory)"
    - "Clicking «Подключить» on such a server connects it again and re-enables lazy auto-connect for it"
    - "Enabled servers that were never manually disconnected still lazy auto-connect on the next chat turn (quick 260924-2n8 behaviour unchanged)"
    - "The edit-form «enabled» checkbox keeps its current meaning (disabled server cannot be connected, POST /connect returns 409)"
  artifacts:
    - path: "shared/models.py"
      provides: "McpServerConfig.auto_connect persistent flag"
      contains: "auto_connect: bool"
    - path: "shared/database.py"
      provides: "Idempotent ALTER TABLE migration adding mcpserverconfig.auto_connect"
      contains: "ADD COLUMN auto_connect"
    - path: "agent/mcp_tools.py"
      provides: "_auto_connect_missing skips rows with auto_connect False"
      contains: "row.auto_connect"
    - path: "tests/test_mcp_auto_connect.py"
      provides: "Regression tests for disconnect-sticks / connect-restores"
  key_links:
    - from: "agent/main.py::disconnect_mcp_server"
      to: "McpServerConfig.auto_connect"
      via: "persist False + session.commit()"
      pattern: "auto_connect = False"
    - from: "agent/main.py::connect_mcp_server"
      to: "McpServerConfig.auto_connect"
      via: "persist True + session.commit()"
      pattern: "auto_connect = True"
    - from: "agent/mcp_tools.py::_auto_connect_missing"
      to: "McpServerConfig.auto_connect"
      via: "candidate filter"
      pattern: "row\\.auto_connect"
---

<objective>
Fix backlog 999.12: «Отключить» in Settings → MCP серверы does not stick. The disconnect route
(`agent/main.py::disconnect_mcp_server`) only closes the live session; on the next chat turn
`agent/mcp_tools.py::build_mcp_toolset` → `_auto_connect_missing` sees an `enabled` row with no
live session and no recorded failure and reconnects it, so ~11k tokens of tool schemas
(filesystem 17 + GitLab 20) are sent again and starve RAG context
(see .planning/debug/rag-k15-context-full.md).

Root cause (confirmed by reading code): there is no persistent "user disconnected this server"
state; `enabled` is the only flag and the disconnect route does not touch it.

Design decision (planner discretion): add a separate persistent boolean `auto_connect`
(default True) to `McpServerConfig` instead of flipping `enabled`. Reason: `enabled` is the
edit-form checkbox and a disabled server is explicitly rejected by POST /connect with 409
(covered by `tests/test_mcp_api.py::test_disable_connected_server_disconnects_and_connect_rejected`)
and the UI greys out «Подключить» for it — reusing `enabled` would make «Отключить» a one-way
trap. With `auto_connect`: «Отключить» → False (persisted), «Подключить» → True (persisted),
lazy auto-connect only considers `enabled and auto_connect` rows. Tool schemas come only from
live sessions, so a manually disconnected server contributes none.

Out of scope (explicitly, per "optional/only if cheap"): showing tool-schema tokens in the
usage meter — `compute_chat_stats` has no access to the per-turn toolset, so it would need new
plumbing through ws.py/headless.py; not cheap. Not implemented here.

Output: model field + migration, route changes, auto-connect filter, small UI hint, tests,
backlog cleanup.
</objective>

<execution_context>
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/workflows/execute-plan.md
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/STATE.md
@.planning/debug/rag-k15-context-full.md

<interfaces>
From shared/models.py (line ~556):
  class McpServerConfig(SQLModel, table=True): id, user_id, name, command, args_json, env_json,
  cwd, enabled: bool = Field(default=True), created_at, updated_at

From shared/database.py: migrations are `async def migrate_add_*(conn)` functions that check
`PRAGMA table_info(<table>)` (and first that the table exists via sqlite_master, see
`migrate_add_task_transition_rejection_columns` / `migrate_add_scheduledtask_provider_id`) then
`ALTER TABLE ... ADD COLUMN`. They are called from `init_db()` BEFORE
`SQLModel.metadata.create_all`. Table name is `mcpserverconfig`.

From agent/schemas.py: class McpServerResponse(BaseModel): id, name, command, args, env_keys,
cwd, enabled: bool, created_at, updated_at, connection: McpConnectResult

From agent/main.py:
  _mcp_server_to_response(row, connection) -> McpServerResponse   (line ~202)
  connect_mcp_server(server_id, session, current_user)   (line ~1192; 409 if not row.enabled)
  disconnect_mcp_server(server_id, session, current_user) (line ~1236; calls mcp_client.disconnect_server)
  update_mcp_server also calls mcp_client.disconnect_server after edit — leave auto_connect untouched there.

From agent/mcp_tools.py:
  async def _auto_connect_missing(user_id: int, rows: list[McpServerConfig]) -> None
    candidates = rows where row.enabled and row.id is not None and get_live_tools(...) is None
                 and not has_recorded_failure(...)
  async def build_mcp_toolset(session, user_id, reserved) -> McpToolset  (only live tools of enabled rows)

From ui/static/app.js:
  renderMcpServerRow(server) (~line 1981): «Отключить» when conn.status === 'connected', else
  «Подключить» (disabled when !server.enabled). disconnectMcpServer(server) (~line 2172) POSTs
  /disconnect and calls replaceMcpServer(updated).

From tests/test_mcp_auto_connect.py: helpers `_add(user_id, name, mode, enabled, command) -> int`,
`_build(user_id) -> McpToolset`, `_count_spawns(monkeypatch)`, constant ECHO = "mcp__fixture__echo",
fixture server tests/fixtures/mcp_stdio_server.py; `_create_user` from tests.conftest.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Persistent auto_connect flag — model, migration, routes, auto-connect filter</name>
  <files>shared/models.py, shared/database.py, agent/schemas.py, agent/main.py, agent/mcp_tools.py, tests/test_mcp_auto_connect.py, tests/test_mcp_api.py</files>
  <behavior>
    - test_mcp_auto_connect: server connected (via _build auto-connect), then POST-equivalent disconnect (set auto_connect False through the API or mcp_config + mcp_client.disconnect_server) → next _build returns no ECHO binding and _count_spawns shows zero new spawns.
    - test_mcp_auto_connect: row with auto_connect=False and no live session is never auto-connected even with MCP_AUTO_CONNECT on; a sibling enabled row with auto_connect=True in the same user IS auto-connected (lazy auto-connect preserved).
    - test_mcp_api: POST /disconnect returns auto_connect false and GET /api/v1/mcp/servers reflects auto_connect false (persisted); subsequent POST /connect returns auto_connect true; new servers default to auto_connect true.
    - Existing test_disable_connected_server_disconnects_and_connect_rejected (409 for enabled=False) still passes unchanged.
  </behavior>
  <action>
    1. shared/models.py: add `auto_connect: bool = Field(default=True)` to McpServerConfig right after `enabled`, with a short comment: False = user pressed «Отключить»; lazy auto-connect must skip it (backlog 999.12).
    2. shared/database.py: add `async def migrate_add_mcpserverconfig_auto_connect(conn: Any) -> None` following the existing pattern (return early if the table does not exist in sqlite_master; read PRAGMA table_info(mcpserverconfig); if `auto_connect` missing, log `migrating_mcpserverconfig_add_auto_connect` and run `ALTER TABLE mcpserverconfig ADD COLUMN auto_connect BOOLEAN DEFAULT 1`). Call it in init_db() alongside the other migrate_add_* calls before create_all. Idempotent.
    3. agent/schemas.py: add `auto_connect: bool = True` to McpServerResponse. agent/main.py `_mcp_server_to_response`: pass `auto_connect=row.auto_connect`.
    4. agent/main.py `disconnect_mcp_server`: after `mcp_client.disconnect_server(...)`, set `row.auto_connect = False`, `row.updated_at = datetime.now(timezone.utc)`, `session.add(row)`, `await session.commit()`, `await session.refresh(row)`; wrap the DB write in try/except Exception → `await session.rollback()` + re-raise (project commit/rollback convention). Log `mcp_auto_connect_disabled` with user_id/server_id.
    5. agent/main.py `connect_mcp_server`: after the 409 enabled-guard and before connecting, set `row.auto_connect = True` and persist with the same commit/rollback pattern (only if it was False, to avoid needless writes). Manual connect expresses intent regardless of connect outcome; a failed connect is still guarded by has_recorded_failure in auto-connect.
    6. agent/mcp_tools.py `_auto_connect_missing`: add `and row.auto_connect` to the candidate filter; update the docstrings of `_auto_connect_missing` and `build_mcp_toolset` to say "enabled servers with auto_connect on". Do NOT add an auto_connect filter to the live-tools loop in build_mcp_toolset (a server the user connected manually is live and auto_connect is True anyway; a disconnected one has no live tools).
    7. Do not touch update_mcp_server/create_mcp_server semantics (create uses the model default True). Use `datetime.now(timezone.utc)` (check main.py imports), structlog only, type hints.
    8. Write the tests from <behavior> first (RED), then implement (GREEN). In test_mcp_auto_connect use the existing `_add`/`_build`/`_count_spawns` helpers; to simulate the API disconnect at unit level, update the row via `mcp_config.update_server`-style direct session write (set auto_connect False, commit) plus `await mcp_client.disconnect_server(user_id, sid)`; also add an API-level test in test_mcp_api using `authenticated_client` and the fixture server mirroring `test_disconnect_endpoint`. Clean up live sessions at test end the same way existing tests in those files do.
  </action>
  <verify>
    <automated>pytest tests/test_mcp_auto_connect.py tests/test_mcp_api.py tests/test_mcp_tools.py tests/test_mcp_chat_ws.py -q</automated>
  </verify>
  <done>New tests pass; manually disconnected servers are neither auto-connected nor present in the toolset on the next build; connect restores auto_connect=True; existing MCP tests green; migration adds the column on an existing app.db without data loss.</done>
</task>

<task type="auto">
  <name>Task 2: UI hint for manually disconnected servers + full suite + backlog cleanup</name>
  <files>ui/static/app.js, .planning/ROADMAP.md, .planning/phases/999.12-mcp-disable-toggle-ineffective/</files>
  <action>
    1. ui/static/app.js `renderMcpServerRow`: when `server.enabled && server.auto_connect === false && conn.status !== 'connected'`, append a muted note (e.g. `mcpEl('p', 'text-xs text-slate-500', 'Отключён вручную — не подключается автоматически')`) to the card after the header. Use the existing `mcpEl` helper (textContent, no innerHTML, so no DOMPurify needed). `disconnectMcpServer` already calls `replaceMcpServer(updated)` with the response that now carries auto_connect — no other JS change needed. Vanilla JS only.
    2. Run full suite `pytest tests/ -q`; fix any regressions caused by this change (e.g. tests asserting exact McpServerResponse key sets).
    3. Docs cleanup (last step): remove the whole `### Phase 999.12: MCP server disable toggle has no effect ...` section (heading through its `- [ ] TBD ...` line and trailing blank line, stopping before `### Phase 999.17`) from .planning/ROADMAP.md Backlog; delete directory `.planning/phases/999.12-mcp-disable-toggle-ineffective/` (`git rm -r` if tracked, else rm -r). Commit with no Co-Authored-By line (user rule).
  </action>
  <verify>
    <automated>pytest tests/ -q && ! grep -q "Phase 999.12" .planning/ROADMAP.md && ! test -d .planning/phases/999.12-mcp-disable-toggle-ineffective</automated>
  </verify>
  <done>Full test suite passes; disconnected server card shows the hint; 999.12 removed from ROADMAP backlog and its phase dir deleted.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| browser→Agent REST | /api/v1/mcp/servers/{id}/connect and /disconnect mutate a user-scoped row |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-lrs-01 | Elevation/Tampering | connect/disconnect routes | mitigate | Keep `_get_mcp_server_or_404(session, current_user.id, server_id)` as the only row lookup so another user's server can't be toggled (existing 404 test at test_mcp_api.py:175 must stay green) |
| T-lrs-02 | Tampering | disconnect/connect DB write | mitigate | commit/rollback pattern; on failure the row is unchanged and the error propagates |
| T-lrs-03 | Information disclosure | logs | accept | Logs carry only user_id/server_id, no args/env |
</threat_model>

<verification>
- `pytest tests/ -q` green.
- Manually (optional, isolated copy on 18000/18001 per user memory): connect filesystem server, «Отключить», send a chat message → agent log has no `mcp_auto_connect` for that server_id and the tool trace has no mcp__ tools from it; restart app → still not connected; «Подключить» → connected.
</verification>

<success_criteria>
- «Отключить» persists (auto_connect=False in DB) and is honoured by lazy auto-connect and the toolset.
- «Подключить» restores auto_connect=True.
- Never-disconnected enabled servers still lazy auto-connect.
- Backlog 999.12 entry and phase dir removed.
</success_criteria>

<output>
Create `.planning/quick/261006-lrs-fix-backlog-999-12-mcp-disable-toggle-in/261006-lrs-SUMMARY.md` when done
</output>
