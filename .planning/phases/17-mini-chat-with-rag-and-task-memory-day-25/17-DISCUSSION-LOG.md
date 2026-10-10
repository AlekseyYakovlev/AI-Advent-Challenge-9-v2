# Phase 17: Mini-chat with RAG and task memory (Day 25) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-10
**Phase:** 17-mini-chat-with-rag-and-task-memory-day-25
**Areas discussed:** Task memory update rules, Task memory in the UI, History-aware retrieval, Scenarios and Day25 report

---

## Task memory update rules

### How should task memory be produced after each turn?

| Option | Description | Selected |
|--------|-------------|----------|
| LLM extraction call (Recommended) | Code always makes one non-streaming temperature-0 call on the chat's model returning JSON {goal, clarified, constraints}; code validates and merges. | ✓ |
| Rules only, no LLM | Heuristics: first question is the goal, «только…» / «не нужно…» become constraints, article numbers become terms. | |
| Hybrid | LLM extraction plus code-collected facts (articles cited so far, sources used). | |

### When does the update run relative to the end of the turn?

| Option | Description | Selected |
|--------|-------------|----------|
| Before done, in the turn (Recommended) | After the stream, under the chat lock; fresh memory in the same `done` frame. | ✓ |
| After done, awaited | Answer finishes first; extraction pushes a separate frame; next message waits under the lock. | |
| Background, debounced | Fire-and-forget like `extract_and_update_facts`. | |

### How should new extraction results be merged into the existing memory?

| Option | Description | Selected |
|--------|-------------|----------|
| Code merges, goal is sticky (Recommended) | Model returns only what is new; code appends unique items; goal replaced only on an explicit user change; lists capped. | ✓ |
| Model rewrites everything | Model returns the full new memory, which replaces the old one. | |
| Append-only, goal frozen | Goal set once on the first turn; lists only grow. | |

### What is allowed into task memory, and when is the update skipped?

| Option | Description | Selected |
|--------|-------------|----------|
| User-stated only, RAG chats (Recommended) | Only what the user said; runs only with RAG on; gated «не знаю» turns still update; failures keep old memory. | ✓ |
| Also facts from answers | Additionally store short facts from the assistant's sourced answers with a chunk reference. | |
| User-stated only, every chat | Same content rule for all chats, with or without RAG. | |

**User's choice:** all four recommended options.
**Notes:** none.

---

## Task memory in the UI

### Where should task memory be shown in the UI?

| Option | Description | Selected |
|--------|-------------|----------|
| Own block in the sidebar panel (Recommended) | «Память задачи» section above «Рабочая (этот чат)» with «Цель», «Уточнено», «Ограничения и термины»; reserved row hidden from the plain list; RAG chats only. | ✓ |
| Raw working-memory row | The `dialog_state` row shown as key + JSON text. | |
| Strip above the chat | Collapsible bar under the chat header. | |

### Should each answer also keep a snapshot of task memory as it was at that turn?

| Option | Description | Selected |
|--------|-------------|----------|
| Snapshot + diff per answer (Recommended) | Stored per assistant message; collapsed block under the answer; new items marked «новое». | ✓ |
| Current state only | Only the sidebar block; report captures per-turn memory itself. | |
| One-line note per answer | Grey line like «Память задачи: +1 уточнение, +1 ограничение». | |

### How much manual control over task memory should the user have?

| Option | Description | Selected |
|--------|-------------|----------|
| Reset button only (Recommended) | «Сбросить» with native confirm; no item editing. | |
| Read-only | No controls. | |
| Reset + delete single items | Reset plus «×» per item and an editable goal; new PUT/DELETE routes and inline edit like Phase 11. | ✓ |

### What should task memory do on regenerate, edit of an earlier message, or branch switch?

| Option | Description | Selected |
|--------|-------------|----------|
| Follow the active branch (Recommended) | Restored from the snapshot of the last assistant message on the active path; manual edits after that snapshot are lost on switch. | ✓ |
| One memory per chat | Single forward-only state; no rollback on regenerate/branch. | |
| You decide | Claude picks during planning. | |

**User's choice:** recommended options, except manual control: "Reset + delete single items".
**Notes:** the user picked the larger manual-control option over the recommended reset-only button.

---

## History-aware retrieval

### When should the history-aware query rewrite run in a RAG chat?

| Option | Description | Selected |
|--------|-------------|----------|
| Always on follow-ups (Recommended) | From the second turn on, regardless of the «Переписывание запроса» switch; first turn as in Phase 15. | ✓ |
| New separate switch | Sixth switch «Учитывать диалог», on by default. | |
| Reuse the existing switch | History fed only when «Переписывание запроса» is on; default flips to on. | |

### What should the condensing call see, besides the new question?

| Option | Description | Selected |
|--------|-------------|----------|
| Last 3 turns + task memory (Recommended) | Last 3 user/assistant pairs (answers trimmed, no quote blocks) plus goal, clarified points, constraints. | ✓ (with change) |
| Task memory + last user turns only | No assistant text. | |
| Same context as the answer | Whatever the compression strategy sends, plus task memory. | |

**User's choice:** free text — "1, but there should be a setting to alter number of turns".

### Where should the number-of-history-turns setting live, and what range should it allow?

| Option | Description | Selected |
|--------|-------------|----------|
| «Поиск ⚙» popover, 0-10 (Recommended) | Per-chat «Ходов истории», default 3, on `ChatRagConfig`; 0 = task memory only. | ✓ |
| «Поиск ⚙» popover, 1-10 | At least one previous turn always included. | |
| Global Settings modal | One value for all chats. | |

### On a follow-up, which queries are searched, and what decides the strict «не знаю» gate?

| Option | Description | Selected |
|--------|-------------|----------|
| Both, merged; gate on merged (Recommended) | Same as 15 D-12; gate fires only if nothing survives from either query. | ✓ |
| Condensed query only | Raw question only as fallback for a bad rewrite. | |
| Both; soften the gate on follow-ups | Still call the LLM with history when nothing survives. | |

### How should the strict RAG chat treat turns about the dialog itself?

| Option | Description | Selected |
|--------|-------------|----------|
| Retrieve anyway, no special case (Recommended) | Retrieval every turn; if the gate fires, the templated «не знаю» is shown and recorded. | ✓ |
| Reuse the previous turn's fragments | Re-send the last answered turn's fragments, marked «из предыдущего хода». | |
| Keep such turns out of the scenarios | No product change; scenarios avoid them. | |

**Notes:** the configurable number of turns was added by the user; a follow-up question fixed its place and range.

---

## Scenarios and Day25 report

### What should the two scripted scenarios be about?

| Option | Description | Selected |
|--------|-------------|----------|
| Two different tasks, Claude drafts (Recommended) | A: concrete driver case with fixed facts/constraints; B: study task on ФЗ-196 with a term fixed early; user approves, fixture frozen. | ✓ |
| One goal-kept, one goal-changed | Scenario B has the user change the goal mid-dialog. | |
| I will write them | User provides the message lists. | |

### How should the scenarios be executed for the report?

| Option | Description | Selected |
|--------|-------------|----------|
| Real chat over WebSocket, scripted (Recommended) | Multi-turn mode in `scripts/rag_eval.py` drives a real chat on the isolated copy via WS; one Playwright pass for the UI. | ✓ |
| Headless pipeline in the eval script | Calls pipeline and memory functions directly. | |
| Playwright through the UI only | Both scenarios typed into the browser. | |

### How should the per-turn checks in Day25_report.md be decided?

| Option | Description | Selected |
|--------|-------------|----------|
| Auto checks + manual «goal kept» (Recommended) | Automatic checks plus manual verdicts reviewed at a checkpoint. | |
| Fully automatic | «Goal kept» by code: goal text unchanged and expected keywords in the answer. | ✓ (with addition) |
| Auto + manual + DeepSeek judge | Recommended option plus a judge column. | |

**User's choice:** free text — "2 + DeepSeek judge": fully automatic checks plus a DeepSeek judge column, no manual verdict.

### Which runs should the report contain?

| Option | Description | Selected |
|--------|-------------|----------|
| Main run + «no memory» baseline (Recommended) | Both scenarios twice; baseline disables task memory and history-aware rewrite via an eval-only flag. | ✓ |
| Main run only | Both scenarios once. | |
| Main run + baseline + DeepSeek as answering model | Also repeat the main run with DeepSeek answering. | |

---

## Claude's Discretion

- Storage of task memory and of the per-message snapshot; protection of the reserved key from the `save_working_memory` tool.
- Extraction and condensing prompt wording, JSON schema, goal-change flag, dedup, caps, timeouts.
- How labelled task memory sits in `build_system_prompt` next to the generic working-memory line.
- What «Детали поиска» shows for the condensed query; how first-turn rewrite and follow-up condensing share code.
- Route shapes for reset / delete / edit, `done` field names, manual edit vs latest snapshot.
- The eval-only «no memory» flag mechanism, fixture and raw-output locations, judge rubric, report layout.

## Deferred Ideas

- Rules-only or hybrid extraction; facts from assistant answers in task memory; task memory in non-RAG chats.
- A task-memory strip above the chat.
- A UI switch for history-aware rewrite.
- Re-sending the previous turn's fragments on a gated follow-up; softening the gate on follow-ups.
- A goal-change scenario; DeepSeek as the answering model; manual per-turn verdicts.
