# Roadmap: AiAdventAgentV2

## Milestones

- ✅ **v1.0 Week 3: Agent Memory & Task State** — Phases 1-6 (shipped 2026-09-23)
- 🚧 **v2.0 Week 4: MCP Integration** — Phase 7+ (in progress)

## Phases

<details>
<summary>✅ v1.0 Week 3: Agent Memory & Task State (Phases 1-6) — SHIPPED 2026-09-23</summary>

- [x] Phase 1: Auth Foundation (5/5 plans) — completed 2026-09-20
- [x] Phase 2: Memory (Day 11) (5/5 plans) — completed 2026-09-20
- [x] Phase 3: Personalization (Day 12) (3/3 plans) — completed 2026-09-20
- [x] Phase 4: Task State Machine (Day 13) (4/4 plans) — completed 2026-09-20
- [x] Phase 5: Invariants (Day 14) (4/4 plans) — completed 2026-09-20
- [x] Phase 6: Controlled Transitions (Day 15) (4/4 plans) — completed 2026-09-21

Full details: [milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)

</details>

### 🚧 v2.0 Week 4: MCP Integration (In Progress)

- [ ] **Phase 7: MCP Connection (Day 16)** — The agent connects to an MCP server configured in Settings and shows the server's tool list

## Phase Details

### Phase 7: MCP Connection (Day 16)

**Goal**: A user can configure an MCP server in the Settings UI, connect to it, and see the list of tools the server exposes — proven against the locally installed Go filesystem MCP server
**Branch**: `Day16`
**Depends on**: Phase 1 (Auth) — server configs are scoped by `user_id`
**Requirements**: MCP-01, MCP-02, MCP-03, MCP-04, MCP-05, MCP-06
**Success Criteria** (what must be TRUE):
1. User adds a server in Settings with command `C:\Users\Aleksey\go\bin\filesystem.exe` and an allowed-directory arg; the config survives an app restart and is invisible to other users
2. Pressing "Connect" shows status "connected" with serverInfo `filesystem-mcp-server` / version / protocol, and lists all 17 tools with descriptions and parameters
3. A bad command path or a server that exits immediately yields a readable error in the UI, and the Agent's `/health` stays OK
4. `python scripts/mcp_list_tools.py C:\Users\Aleksey\go\bin\filesystem.exe <dir>` prints serverInfo and the tool list
5. `pytest tests/ -v` passes, including new MCP connect/list_tools tests (success + failure)
**Plans**: 6 plans
**UI hint**: yes

Plans:
- [ ] 07-01-PLAN.md — MCP stdio client core: pin mcp, timeout config, result schemas, fixture server, owner-task registry + error classification (wave 1)
- [ ] 07-02-PLAN.md — McpServerConfig table + user-scoped CRUD service with env masking/merge (wave 1)
- [ ] 07-03-PLAN.md — REST endpoints /api/v1/mcp/servers (CRUD, connect/disconnect/status) + lifespan cleanup + API tests (wave 2)
- [ ] 07-04-PLAN.md — CLI scripts/mcp_list_tools.py reusing connect_once_and_list + tests (wave 2)
- [ ] 07-05-PLAN.md — "MCP серверы" section in the Settings modal (wave 3)
- [ ] 07-06-PLAN.md — End-to-end acceptance vs filesystem.exe + human UI walkthrough (wave 4, checkpoint)

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Auth Foundation | v1.0 | 5/5 | Complete | 2026-09-20 |
| 2. Memory (Day 11) | v1.0 | 5/5 | Complete | 2026-09-20 |
| 3. Personalization (Day 12) | v1.0 | 3/3 | Complete | 2026-09-20 |
| 4. Task State Machine (Day 13) | v1.0 | 4/4 | Complete | 2026-09-20 |
| 5. Invariants (Day 14) | v1.0 | 4/4 | Complete | 2026-09-20 |
| 6. Controlled Transitions (Day 15) | v1.0 | 4/4 | Complete | 2026-09-21 |
| 7. MCP Connection (Day 16) | v2.0 | 0/? | Not started | - |

---
*Roadmap created: 2026-09-19*
*Last updated: 2026-09-23 — v1.0 milestone archived*
