# Phase 8: Scheduler (Day 18) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-26
**Phase:** 08-scheduler-day-18
**Areas discussed:** What a job does when it fires, Schedule kinds & reliability, Creating jobs from chat (LLM tools), UI panel & result display

---

## What a job does when it fires

### Job payload
| Option | Description | Selected |
|--------|-------------|----------|
| LLM prompt | Headless LLM turn with the user's tools | ✓ |
| Direct MCP tool call | Server + tool + args, no LLM | |
| Both, chosen per job | `kind` field: prompt / tool_call | |

**User's choice:** LLM prompt

### Run output
| Option | Description | Selected |
|--------|-------------|----------|
| TaskRun only | Result + trace on the TaskRun row | ✓ |
| Post into the originating chat | Assistant message in the source chat | |
| Dedicated chat per job | Chat row per job | |

**User's choice:** TaskRun only

### Model
| Option | Description | Selected |
|--------|-------------|----------|
| Store model on the job | Model id chosen at creation | ✓ |
| Always the global default model | | |
| DeepSeek only | | |

**User's choice:** Store model on the job

### Tools & limits inside a run
First phrasing (MCP only, no scheduler tools, no memory/task-FSM tools; ~120 s timeout) was **rejected** — the user asked whether task tools are really chat-bound. Verified in code: `Task.chat_id` is NOT NULL and working memory is keyed by `chat_id`, but `save_long_term_memory` is user-scoped only. Question reformulated.

| Option | Description | Selected |
|--------|-------------|----------|
| MCP + long-term memory | MCP tools + `save_long_term_memory`; no chat-bound tools, no scheduler tools; ~120 s timeout + MAX_TOOL_ROUNDS | ✓ |
| Only MCP | No built-ins | |
| Everything, bound to the job's chat | Nullable chat_id on the job; chat-bound tools when created from chat | |

**User's choice:** MCP + long-term memory

---

## Schedule kinds & reliability

### Kinds
**User's choice:** One-shot + interval + cron (over one-shot+interval, one-shot+cron)

### Timezone
**User's choice:** Machine local TZ for cron, UTC in the DB (over per-job timezone field, all-UTC)

### Missed runs
**User's choice:** Catch-up once (over skip-missed, catch-up-all)

### Overlap / retries / limits
**User's choice:** Skip overlap, no retry, optional `max_runs` (over no max_runs, retry with backoff)

---

## Creating jobs from chat (LLM tools)

### schedule_task args
**User's choice:** `schedule_type` + per-type fields (over a single free-form schedule string)

### Origin chat
**User's choice:** `origin_chat_id` nullable, `ON DELETE SET NULL` (over no chat stored, CASCADE)

### list / cancel
**User's choice:** All user's jobs, cancel by explicit id, soft cancel, **plus a gate** requiring an explicit user request to cancel (over per-chat-only scope, ungated cancel)

---

## UI panel & result display

### Placement
**User's choice:** Sidebar panel + modal for a run's full result (over all-inline, Settings modal section)

### Live updates
**User's choice:** WS push (over the recommended polling-only and manual refresh). Follow-ups: first answer "WS live + polling fallback" left the channel unspecified; then chose a **new user-level `/ws/events`** (over tagging the existing chat WS). Noted: `active_connections` is ownerless and `broadcast_model_event` goes to all sockets, so events must be user-keyed.

### UI actions
**User's choice:** All four — create via form, Pause/Resume, Run now, Cancel/Delete

---

## Claude's Discretion

- Cron parser (in-house vs `croniter`), tick interval, timeout value, names/enums, REST + WS frame shapes, headless-runner structure, cancel-gate mechanism, markup/wording.

## Deferred Ideas

- Direct MCP tool-call jobs; results posted into chats; per-job timezone; retries; chat-bound tools in runs; backlog 999.1–999.3 stay deferred to Day 20.
