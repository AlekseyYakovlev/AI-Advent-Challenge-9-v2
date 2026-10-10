# Phase 17: Mini-chat with RAG and task memory (Day 25) - Context

**Gathered:** 2026-10-10
**Status:** Ready for planning

<domain>
## Phase Boundary

The existing chat works as the RAG mini-chat. In a chat with RAG on, dialog history is kept, retrieval runs on every new question, and every answer shows its sources (Phase 14-16 machinery). The phase adds a per-chat **task memory** (dialog goal, what the user has clarified, fixed constraints/terms) that code updates after each turn and the UI shows, and makes retrieval **history-aware**: task memory and recent turns feed the query rewrite and the system prompt, so follow-ups like «а за повторное?» retrieve correctly. Two scripted 10-15 message scenarios are run end to end and documented in `Day25_report.md`. Requirements: RCHAT-01..RCHAT-05. Branch: `Day25`.

Out of this phase: a separate mini-chat UI or CLI (REQUIREMENTS §Out of Scope), new retrieval stages or threshold changes (Phase 15 owns them), changes to quote verification or the «не знаю» gate rules (Phase 16 owns them), cross-encoder rerankers.

</domain>

<decisions>
## Implementation Decisions

### Task memory update rules
- **D-01:** Task memory is produced by an LLM extraction call that **code always makes** after every answered turn: one non-streaming, temperature-0 call on the chat's own provider/model (same choice as 15 D-11) that returns JSON with `goal`, clarified points and constraints/terms. Code validates the JSON and merges it. It is not an LLM tool call and the model does not choose whether to save. This is the RCHAT-02 "deterministic" update.
- **D-02:** The update runs **inside the turn, before `done`**: after the answer stream ends, under the per-chat lock, and the fresh memory goes out in the same `done` frame. The next question and the UI never see stale memory. The debounced fire-and-forget pattern of `extract_and_update_facts` is explicitly not used.
- **D-03:** Merge is done by code and the goal is sticky. The model is asked only for what is new in this turn. Code appends unique clarified points and constraints, and replaces the goal only when the model flags an explicit change of goal by the user. Items are never silently dropped by an extraction; lists have a size cap.
- **D-04:** Only **user-stated** content enters task memory. Clarified points and constraints come from what the user said, not from the assistant's answer and not from law text; the answer is passed to the extraction call only as context. Facts established by the assistant's sourced answers are not stored.
- **D-05:** Task memory exists only in chats with **RAG on**. Chats without RAG make no extraction call and show no task-memory UI.
- **D-06:** A gated «не знаю» turn (16 D-10, answer LLM not called) **still updates** task memory from the user's message.
- **D-07:** Extraction is fail-soft: on timeout, unparsable JSON or any error, memory stays as it was, the turn completes normally, and the failure is only logged / marked (same rule as 14 D-04, 15 D-14).

### Task memory in the UI
- **D-08:** Task memory has its own block «Память задачи» in the existing sidebar memory panel, above «Рабочая (этот чат)», with three labelled parts: «Цель», «Уточнено», «Ограничения и термины». If it is stored as a reserved working-memory row, that row is hidden from the plain working-memory list. The block is shown only in RAG chats and refreshes from the `done` frame.
- **D-09:** Every assistant message stores a **snapshot** of task memory after its turn. Under the answer, a collapsed «Память задачи» block shows that snapshot, with items added in this turn marked «новое». History therefore shows how memory grew, and the report's per-turn memory column reads the same data.
- **D-10:** The user has manual control: a «Сбросить» button that clears the chat's task memory (native `confirm()`, as in Phase 11), an «×» on each clarified point and each constraint, and an editable goal. This needs new user-scoped routes and inline editing in the style of Phase 11 (Origin + JSON content-type checks).
- **D-11:** Task memory **follows the active branch** of the message tree. On regenerate, edit of an earlier message or branch switch, the current memory is restored from the snapshot of the last assistant message on the active path (the parent's snapshot when regenerating). A regenerated turn starts from the memory as it was before that turn. Manual edits made after that snapshot are lost on a switch; this was accepted.

### History-aware retrieval
- **D-12:** From the second turn of a RAG chat on, **every turn** condenses the new question with recent history and task memory into a standalone search query. This is always on and does not depend on the «Переписывание запроса» switch. The first turn of a chat behaves exactly as in Phase 15 (the switch still controls the single-turn rewrite there), so the single-turn Day 22-24 eval runs stay reproducible. There is no UI switch to turn history-aware rewrite off.
- **D-13:** The condensing call sees the last **N user/assistant pairs** plus task memory (goal, clarified points, constraints/terms). Assistant answers are trimmed to a few hundred characters and carry no quote blocks. N is a per-chat setting «Ходов истории» in the «Поиск ⚙» popover, next to «Кандидатов» and «Порог»: range **0-10, default 3**, stored on `ChatRagConfig`. 0 means the call sees task memory only, no dialog turns.
- **D-14:** On a follow-up, **both** the raw question and the condensed query are embedded and searched, and the candidates are merged by best cosine per chunk (same rule as 15 D-12). The strict «не знаю» gate (16 D-10/D-12) is decided on the merged result: it fires only when nothing survives from either query. A bad condensed output (empty, too long, multi-line, chatty) falls back to the raw question only. The gate is not softened for follow-ups.
- **D-15:** Turns about the dialog itself («подытожь, что мы выяснили», «объясни проще», «спасибо») get **no special case**. Retrieval runs on every turn (RCHAT-01); the condensed query carries the goal and terms, so such turns normally pull the current topic's fragments. If the gate still fires, the templated «не знаю» is shown and the report records it as it happened.
- **D-16:** Task memory is also rendered into the answering LLM's system prompt as labelled lines (goal / clarified / constraints), so constraints persist across the dialog (RCHAT-03).

### Scenarios and Day25 report
- **D-17:** Claude drafts both scenarios; the user approves the draft and it is frozen as a fixture before the first run (same checkpoint pattern as 14 D-13, 15 D-15). The two scenarios are **different tasks** on the ФЗ-196 + КоАП corpus:
  - Scenario A: a concrete driver case with facts and constraints fixed by the user (for example speeding, a repeat offence, «только по КоАП»).
  - Scenario B: a study/overview task on ФЗ-196 with a term fixed early in the dialog.
  - Each is 10-15 messages and contains follow-ups with pronouns/ellipsis, one out-of-corpus turn and one goal check near the end. The fixture carries per-turn expectations (expected memory items, expected article in sources for marked follow-ups, expected keywords).
- **D-18:** Scenarios run through the **real chat over WebSocket**: a new multi-turn mode in `scripts/rag_eval.py` drives a real chat on the isolated copy (ports 18000/18001, never 8000/8001) through `WS /ws/chat/{chat_id}`, message by message. Per turn it records the answer, `done.rag` sources, quotes and their states, the verdict, the condensed query and the task-memory snapshot. One Playwright pass on the isolated copy provides UI screenshots / the UAT of the new blocks.
- **D-19:** Per-turn checks are **fully automatic**; there is no manual verdict column in this report. Computed per turn: sources present, quotes (model verified / auto), verdict (`ok` / gated / `model_idk`), expected task-memory items present, expected article in sources on marked follow-ups, and «goal kept» as a code proxy (the goal text in memory is unchanged and the fixture's expected keywords appear in the answer).
- **D-20:** In addition, `scripts/rag_judge.py` gets a goal-adherence rubric and adds a **DeepSeek judge** column. It needs the real `DEEPSEEK_API_KEY` and uses the pause-for-key checkpoint from 15 D-18.
- **D-21:** The report contains two runs of both scenarios, on the Day 24 configuration (bge-m3 KB, calibrated threshold 0.67, strict mode on, local `qwen/qwen3.5-9b` at temperature 0): the **main run**, and a **«no memory» baseline** in which the script disables task memory and history-aware rewrite through an eval-only flag (not a UI switch). The report shows side by side how the follow-ups retrieve with and without them.
- **D-22:** `Day25_report.md` lives at the repo root next to `Day22_report.md`..`Day24_report.md`, in Russian like them, and contains both scenario transcripts with the per-turn checks and task-memory contents (RCHAT-05). Report honesty carries over: gated turns, false refusals and extraction failures are shown, not hidden.

### Claude's Discretion
- Where task memory is stored (the research proposal is one reserved `dialog_state` `WorkingMemory` row holding JSON; a dedicated table is also acceptable) and where the per-message snapshot goes (next `rag_sources` payload version or a new `Message` column with an idempotent `ALTER TABLE`). If the reserved row is used, the `save_working_memory` tool must not be able to overwrite it.
- Extraction prompt wording, the JSON schema, how "explicit change of goal" is flagged, dedup/normalization of items, the list caps, the extraction timeout and `max_tokens`.
- How labelled task memory replaces or sits next to the existing generic "Working memory" JSON line in `build_system_prompt`.
- Condensing prompt wording, how long trimmed assistant answers are, how `validate_rewrite` limits change for a condensed query, and what «Детали поиска» shows for it (the trace already has an original/rewritten query pair).
- How the first-turn single-turn rewrite switch and the always-on follow-up condensing share code and trace fields.
- Route shapes for reset / delete item / edit goal, `done` frame field names, how a manual edit interacts with the latest snapshot.
- The mechanism of the eval-only «no memory» flag, fixture and raw-output locations, the judge rubric text, report layout.
- How the extraction call interacts with tool-call rounds and with turns that end in an error.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and scope
- `.planning/REQUIREMENTS.md` §Mini-chat with RAG and task memory (Day 25) — RCHAT-01..RCHAT-05; §Out of Scope (no separate mini-chat UI)
- `.planning/ROADMAP.md` §Phase 17 — goal, success criteria, research flag (confirm `dialog_state` rendering in the memory panel)
- `.planning/PROJECT.md` — core value (distinct, inspectable kinds of state), constraints

### Research
- `.planning/research/SUMMARY.md` — Phase 17 outline; conflict resolution row 4 (single `dialog_state` row, deterministic post-turn extraction)
- `.planning/research/ARCHITECTURE.md` §D9 (Day 25 task memory on existing working memory), data-flow line "Chat turn with RAG (Days 22-25)"
- `.planning/research/PITFALLS.md` — Pitfall 8 (RAG block is ephemeral), Pitfall 18 (query rewrite drift), Pitfall 21 (multi-turn retrieval on the last message only, memory bloat, law text stored as "facts")
- `.planning/research/FEATURES.md` §Day 25 — condense-question rewrite with last 2-4 turns, state injection, anti-feature: second mini-chat

### Prior phases
- `.planning/phases/16-citations-and-anti-hallucination-day-24/16-CONTEXT.md` — D-02 clean stored answer (no quote tail in history), D-10..D-13 gate and `model_idk`, D-15 strict mode default on, D-16..D-18 report and judge pattern
- `.planning/phases/15-reranking-and-filtering-day-23/15-CONTEXT.md` — D-01 «Поиск ⚙» popover, D-03 defaults, D-06 trace storage, D-11/D-12 rewrite on the chat's model with both queries searched, D-14 fail-soft stages, D-18 DeepSeek key checkpoint
- `.planning/phases/14-first-rag-query-day-22/14-CONTEXT.md` — D-08 `done.rag`, D-09/D-10 deterministic pre-step and injection into the outbound copy only, D-12 RAG budget, D-13 frozen-fixture checkpoint
- `.planning/phases/11-edit-and-delete-long-term-memory-entries-via-ui-day-21/` — inline edit / delete pattern and route checks reused by D-10

### Eval inputs and earlier reports
- `Day24_report.md`, `Day23_report.md`, `Day22_report.md` — configuration for D-21, report style
- `tests/fixtures/rag/control_set.json` — fixture format to follow for the scenario fixture

### Project conventions
- `CLAUDE.md` — hard constraints, code conventions
- `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/TESTING_GUIDE.md`, `docs/USER_GUIDE.md` — to be synced after the phase
- `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/TESTING.md`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shared/models.py::WorkingMemory` — chat-scoped key/value rows (`key` ≤ 200, `value` ≤ 50 000), cascade on chat delete; `agent/memory.py::list_working_memory` / `save_working_memory`.
- `agent/context_engine.py::build_system_prompt` — already appends a "Working memory" JSON line, long-term memory and open tasks; the labelled task-memory lines go here.
- `agent/rag_llm.py::rewrite_query` + `agent/rag_rank.py` (`build_rewrite_messages`, `validate_rewrite`, `REWRITE_SYSTEM_PROMPT`, `clean_llm_text`) — the single-turn rewrite call to extend with history and task memory; `agent/rag_pipeline.py::_rewrite_stage` already searches both queries and merges.
- `agent/rag_turn.py::prepare_rag_turn` — the single entry `ws.py` calls with the raw user text; it needs the history and memory inputs.
- `agent/rag_api.py` (`RagConfigIn` / `RagConfigOut`, `_apply_search_settings`) and `shared/database.py` idempotent column list — pattern for the new «Ходов истории» field on `ChatRagConfig`.
- `ui/static/app.js` — `renderMemoryPanel` / `renderMemoryEntries` (long-term rows already have inline edit and delete), `renderRagSearchPopover`, `buildRagMeta` and the `<details>` builders for «Цитаты», «Источники», «Детали поиска».
- `scripts/rag_eval.py` (sub-commands `calibrate`, `ablate`, `cite`), `scripts/rag_judge.py` (rubrics), `scripts/e2e_rag_*_playwright.py`.

### Established Patterns
- STATE decision: memory writes are synchronous and per-chat-locked, never fire-and-forget. This phase keeps both; the trigger is code after the turn rather than an LLM tool call, which is what RCHAT-02 requires.
- RAG is fail-soft: a failed stage is skipped and marked, it never breaks the turn.
- Retrieved text never enters the message tree; messages store metadata and references only. The stored assistant answer has no quote tail, so replayed history is clean.
- New columns on existing tables need an idempotent `ALTER TABLE`; flags and numbers are validated in Pydantic.
- All new data is scoped by `user_id`; new routes check Origin and JSON content type.
- All UI text via `textContent`; vanilla JS, no new CDN libraries.
- Frozen fixtures go through a user checkpoint; E2E and scripted runs use the isolated copy at ports 18000/18001.

### Integration Points
- `agent/ws.py::_handle_chat_message` — `prepare_rag_turn` call site (pass history and memory), the post-stream step before `done` (extraction, merge, snapshot, `done` payload), and `_complete_gated_turn` (memory still updates on a gated turn).
- Regenerate / edit / `POST /api/v1/chats/{id}/branch` paths in `agent/main.py` — restore task memory from the active path's snapshot (D-11).
- `GET /api/v1/chats/{chat_id}/memory` in `agent/main.py` (`ChatMemoryResponse`) — expose task memory separately from plain working rows; add the reset / delete / edit routes.
- `shared/models.py::ChatRagConfig` + `GET/PUT /api/v1/chats/{id}/rag` — «Ходов истории».
- `ui/static/index.html` `#memory-panel` and the «Поиск ⚙» popover.
- `agent/state.py::cleanup_chat_caches` — if any per-chat in-memory state is added.

</code_context>

<specifics>
## Specific Ideas

- UI wording chosen in discussion: «Память задачи», «Цель», «Уточнено», «Ограничения и термины», «новое», «Сбросить», «Ходов истории».
- The canonical follow-up to prove is «а за повторное?» after a speeding question: it must retrieve the repeat-offence part through the condensed query.
- The user wants the number of history turns to be adjustable rather than fixed at 3, including 0 (task memory only) to show the difference.
- The «no memory» baseline exists to give before/after evidence in the report, in the same spirit as the Phase 15 ablation and the Phase 16 strict-off row.
- The user chose item-level manual editing over a reset-only button, and a fully automatic check plus a DeepSeek judge over manual verdicts.

</specifics>

<deferred>
## Deferred Ideas

- Rules-only (no LLM) extraction, and a hybrid with code-collected facts such as "articles cited so far" — considered, not chosen (D-01).
- Storing facts established by the assistant's sourced answers in task memory — considered, not chosen (D-04).
- Task memory in chats without RAG — not chosen (D-05).
- A collapsible task-memory strip above the chat — not chosen (D-08).
- A UI switch to turn history-aware rewrite off («Учитывать диалог») — not chosen; only the eval-only baseline flag exists (D-12, D-21).
- Re-sending the previous turn's fragments when a follow-up is gated, and softening the gate on follow-ups — considered, not chosen (D-14, D-15).
- A scenario where the user deliberately changes the goal mid-dialog — not chosen for the two scenarios (D-17); the sticky-goal replace rule is still implemented and should be covered by a test.
- Repeating the main run with DeepSeek as the answering model — not in this phase; only the judge column uses DeepSeek (D-20).
- Manual per-turn verdicts in the Day 25 report — not chosen (D-19).

</deferred>

---

*Phase: 17-mini-chat-with-rag-and-task-memory-day-25*
*Context gathered: 2026-10-10*
