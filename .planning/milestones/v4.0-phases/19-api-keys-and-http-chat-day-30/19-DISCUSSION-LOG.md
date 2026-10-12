# Phase 19: API keys and HTTP chat (Day 30) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-12
**Phase:** 19-API keys and HTTP chat (Day 30)
**Areas discussed:** API key permissions, HTTP chat request and response, Errors and busy chat, "API ключи" section in UI

---

## API key permissions

### Can a key manage keys (create, list, revoke)?

| Option | Description | Selected |
|--------|-------------|----------|
| Cookie only | Key management only from a browser session; a leaked key cannot mint a replacement | ✓ |
| Key can do everything | Literal KEY-04, including key management | |
| Key: list and revoke only | Key may list and revoke, creation only from the browser | |

**User's choice:** Cookie only

### Add-user and logout under key auth

| Option | Description | Selected |
|--------|-------------|----------|
| Also cookie only | 403 with a clear message for a key; everything else open | ✓ |
| Key can do everything else | Only key management is closed | |
| You decide | Planner's call | |

**User's choice:** Also cookie only

### Expiry and count limit

| Option | Description | Selected |
|--------|-------------|----------|
| No expiry, count limit | Lives until revoked; about 20 active keys per user | ✓ |
| No expiry, no limit | No restrictions | |
| Optional expiry at creation | 30/90 days or none; expired key gives 401 | |

**User's choice:** No expiry, count limit

### Bearer header and session cookie both present

| Option | Description | Selected |
|--------|-------------|----------|
| Bearer wins, no fallback | Invalid or revoked key gives 401 even with a valid cookie | ✓ |
| Bearer, else cookie | Falls back to the cookie when the key is bad | |
| Cookie wins | Key is looked at only without a cookie | |

**User's choice:** Bearer wins, no fallback

---

## HTTP chat request and response

### Provider

| Option | Description | Selected |
|--------|-------------|----------|
| Same as WS: any provider | content, model, optional provider_id | ✓ |
| LM Studio only | content and model only | |

**User's choice:** Same as WS

### Response contents

| Option | Description | Selected |
|--------|-------------|----------|
| Full, like the done frame | Text, ids, stats, tool calls, memory/task writes, invariant conflict, RAG | ✓ |
| Minimal | Text, ids, stats | |
| Text, stats and RAG sources | Middle option | |

**User's choice:** Full, like the done frame

### Client disconnects mid-generation

| Option | Description | Selected |
|--------|-------------|----------|
| Turn runs to completion | Reply saved; read later via the tree or UI | ✓ |
| Turn is cancelled | Generation stopped, user message removed | |
| You decide | | |

**User's choice:** Turn runs to completion

### Tools available in an HTTP turn

| Option | Description | Selected |
|--------|-------------|----------|
| All, same as WS | Built-in and MCP | ✓ |
| Built-in, no MCP | | |
| No tools | | |

**User's choice:** All, same as WS

### Attachment point in the tree

| Option | Description | Selected |
|--------|-------------|----------|
| Always the current leaf | Other branch via existing POST .../branch | ✓ |
| Optional parent_id | Branching in one request | |

**User's choice:** Always the current leaf

### Model not loaded in LM Studio

| Option | Description | Selected |
|--------|-------------|----------|
| Does not load | Client calls existing lm-studio routes; documented | ✓ |
| Loads automatically | May unload a model someone is using in the UI | |
| You decide | | |

**User's choice:** Does not load

### Mark API-origin messages in the UI

| Option | Description | Selected |
|--------|-------------|----------|
| No distinction | No schema change | ✓ |
| "API" badge | New Message column, migration, render changes | |

**User's choice:** No distinction

### Background actions (auto-title, fact extraction)

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, same as WS | | ✓ |
| None | One request, one model call | |
| Fact extraction only | | |

**User's choice:** Yes, same as WS

### MCP and RAG on/off (raised by the user)

**User's request (free text):** it must be possible to get the list of connected MCP servers and switch a given one on or off with PUT; it must be possible to get the list of knowledge bases, and the request body must allow choosing which one is used on the current turn. Default when not given: same as the previous turn; on the first turn RAG is not used.

| Option | Description | Selected |
|--------|-------------|----------|
| As restated | MCP via existing list + PUT enabled (per user); RAG field in the body, given value saved to the chat config, null turns off, omitted uses saved | ✓ |
| Do not persist RAG | Field affects one turn only | |
| Per-chat MCP switch | New per-chat toggle, also in the UI | |

**User's choice:** As restated
**Notes:** Both MCP routes and the KB list route already exist; they only need key auth, a test and a place in the curl scenario.

---

## Errors and busy chat

### Chat busy with another turn

| Option | Description | Selected |
|--------|-------------|----------|
| Immediate 409 | CHAT_BUSY, nothing stored | ✓ |
| Short wait, then 409 | Configurable wait for the lock | |

**User's choice:** Immediate 409

### HTTP statuses

| Option | Description | Selected |
|--------|-------------|----------|
| 413 / 503 / 502 | Overflow 413, LM Studio down or provider unavailable 503, other LLM error 502, busy 409, missing chat 404 | ✓ |
| 422 / 409 / 503 | All model problems as 503, distinguished by code | |
| You decide | | |

**User's choice:** 413 / 503 / 502

### User message after a model error

| Option | Description | Selected |
|--------|-------------|----------|
| Same as WS | Tree left exactly as a WS turn leaves it; body says whether the message was kept | ✓ |
| Always roll back | Chat returns to the pre-request state | |

**User's choice:** Same as WS

### Visibility in an open UI chat

| Option | Description | Selected |
|--------|-------------|----------|
| Immediately, via /ws/events | Event after the turn; open chat re-reads the tree | ✓ |
| After reopening the chat | No live delivery | |
| Immediately with in-progress indicator | Event at start too, input locked | |

**User's choice:** Immediately, via /ws/events

---

## "API ключи" section in UI

### Showing the new key

| Option | Description | Selected |
|--------|-------------|----------|
| Separate modal | Full key, copy button, warning; closes only via × | ✓ |
| Inline banner in the section | Disappears on close | |

**User's choice:** Separate modal

### Row after revocation

| Option | Description | Selected |
|--------|-------------|----------|
| Row is deleted | Only active keys listed; cap counts them | ✓ |
| Stays marked "revoked" | History kept, list grows | |

**User's choice:** Row is deleted

### Key format

| Option | Description | Selected |
|--------|-------------|----------|
| Recognizable prefix | `aia_<random>`, list shows `aia_3fK9…` | ✓ |
| Plain random string | | |
| You decide | | |

**User's choice:** Recognizable prefix

### Usage hint

| Option | Description | Selected |
|--------|-------------|----------|
| Ready curl in the key modal | Server address and key substituted, copyable; one line in the section | ✓ |
| One explanatory line only | Examples only in docs | |
| No hints | | |

**User's choice:** Ready curl in the key modal

---

## Claude's Discretion

- Endpoint paths and field names, name of the KB field, RAG mode when a KB is given while the saved mode is `off`
- Hashing, key length, table layout, `last_used` update policy
- Wiring of Bearer auth and of cookie-only routes
- How the HTTP turn reuses the WebSocket turn and survives client disconnect
- Exact 503 codes and all wording
- `/ws/events` frame name and frontend refresh behaviour
- Server-side timeout for very long generations

## Deferred Ideas

- Per-chat or per-request MCP switch
- "API" badge on HTTP-origin messages
- Key expiry dates
- In-progress indicator in the UI for HTTP turns
