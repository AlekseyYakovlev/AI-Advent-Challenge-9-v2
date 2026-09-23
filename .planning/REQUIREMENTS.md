# Requirements: AiAdventAgentV2 — Week 4: MCP Integration

**Defined:** 2026-09-23
**Core Value:** The agent must demonstrably separate and manage distinct kinds of state, making explicit, inspectable decisions — extended this milestone to external tools reached over the Model Context Protocol.

## v2.0 Requirements

### MCP Connection (Day 16)

- [ ] **MCP-01**: User can add, edit and delete MCP server configs (name, command, args) in the Settings UI; configs are stored in SQLite scoped by `user_id`
- [ ] **MCP-02**: User can press "Connect" for a server in Settings; the Agent opens a stdio MCP session (initialize handshake) and the UI shows connection status plus serverInfo (name, version, protocol version)
- [ ] **MCP-03**: After a successful connection, the UI lists the server's tools (name, description, parameters derived from inputSchema)
- [ ] **MCP-04**: Connection failures (nonexistent command path, server exits/crashes, handshake timeout) are reported to the user as clear error messages; the Agent process keeps running
- [ ] **MCP-05**: The `mcp` SDK is pinned in `requirements.txt`, and pytest covers connect + list_tools (success and failure paths)
- [ ] **MCP-06**: A standalone CLI script `scripts/mcp_list_tools.py <command> [args…]` connects to an MCP server and prints serverInfo and the tool list to the console

## Future Requirements

Later Week 4 days (not yet announced) — likely candidates:

- **MCP-F1**: LLM can call MCP tools during a chat turn via the existing tool-call dispatcher
- **MCP-F2**: Persistent (long-lived) MCP sessions reused across chat turns
- **MCP-F3**: Non-stdio transports (Streamable HTTP / SSE)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Node/npx-based MCP servers (e.g. `@modelcontextprotocol/server-filesystem`) | Project hard constraint: no Node.js/npm; the Go filesystem server is used instead |
| LLM invoking MCP tools in chat | Day 16 only requires connect + list tools; deferred to a later day |
| MCP resources/prompts listing | Assignment asks for tools only |
| MCP server auth/OAuth | Local stdio servers need no auth |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| MCP-01 | Phase 7 | Pending |
| MCP-02 | Phase 7 | Pending |
| MCP-03 | Phase 7 | Pending |
| MCP-04 | Phase 7 | Pending |
| MCP-05 | Phase 7 | Pending |
| MCP-06 | Phase 7 | Pending |

**Coverage:**
- v2.0 requirements: 6 total
- Mapped to phases: 6
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-23*
*Last updated: 2026-09-23 after initial definition*
