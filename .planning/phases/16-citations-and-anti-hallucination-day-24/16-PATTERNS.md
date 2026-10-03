# Phase 16: Citations and anti-hallucination (Day 24) - Pattern Map

**Mapped:** 2026-10-03
**Files analyzed:** 17 (1 new module, 1 new test file, 15 modified)
**Analogs found:** 17 / 17 (one new module has only a partial analog, see "No Analog Found")

All analogs are Phase 14/15 files already in the repo. Line numbers refer to the working tree at mapping time.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `agent/rag_cite.py` (NEW) | utility (pure) | transform | `agent/rag_rank.py` | role-match (pure, no-I/O module) |
| `agent/rag.py` (MOD) | service/helper | transform | itself (`RAG_INSTRUCTION`, `render_rag_block`, `build_rag_payload`) | exact |
| `agent/rag_turn.py` (MOD) | service | request-response | itself (`VERDICT_BELOW_THRESHOLD` branch) | exact |
| `agent/rag_api.py` (MOD) | route/schema | CRUD | itself (`lexical`/`llm_rerank` flags) | exact |
| `agent/ws.py` (MOD) | controller (WS) | streaming | itself (post-stream persist + `done`) | exact |
| `shared/models.py` (MOD) | model | CRUD | `ChatRagConfig` stage flags | exact |
| `shared/database.py` (MOD) | migration | batch | `_CHATRAGCONFIG_RANK_COLUMNS` | exact |
| `ui/static/app.js` (MOD) | component | request-response (render) | `buildRagMeta` / `buildRagSourcesBlock` / `renderRagSearchPopover` | exact |
| `ui/static/index.html` (MOD) | component | - | `#rag-stage-rewrite` switch label | exact |
| `scripts/rag_eval.py` (MOD) | script | batch | `ablate` subcommand (`_ablate_question`, `run_ablation`, `_add_ablate_parser`) | exact |
| `scripts/rag_judge.py` (MOD) | script | batch | itself (`build_judge_messages`, `parse_judge_reply`) | exact |
| `Day24_report.md` (NEW, generated) | doc | batch | `Day23_report.md` via `_render_ablation_outputs` | exact |
| `tests/test_rag_cite.py` (NEW) | test | transform | `tests/test_rag_rank.py` | role-match |
| `tests/test_rag_turn.py` (MOD) | test | request-response | itself | exact |
| `tests/test_rag_ws.py` (MOD) | test | streaming | itself (`test_below_threshold_turn_still_answers_with_note`) | exact |
| `tests/test_rag_api.py`, `test_rag.py`, `test_rag_eval.py`, `test_rag_search_static.py` (MOD) | test | - | themselves | exact |
| `docs/ARCHITECTURE.md`, `API_SPEC.md`, `USER_GUIDE.md` (MOD) | doc | - | existing Phase 15 sections | exact |

## Pattern Assignments

### `agent/rag_cite.py` (NEW; utility, transform)

**Analog:** `agent/rag_rank.py` (pure functions, module-level compiled regexes and constants, `list`/`set` returns, no I/O, no logger needed).

**Module header and constants pattern** (`agent/rag_rank.py` lines 1-31):
```python
"""Pure ranking, validation and calibration helpers for two-stage RAG retrieval."""

import math
import re
import statistics
from typing import Any

STEM_LEN: int = 5
MIN_TOKEN_LEN: int = 3
_WORD_RE: re.Pattern[str] = re.compile(r"[^\W_]+")
```
Typed module constants, `_PRIVATE` compiled patterns, single-line docstring per function, explicit return types.

**Reuse for auto-quote overlap** (`agent/rag_rank.py` lines 34-46):
```python
def tokenize(text: str) -> list[str]: ...
def stems(text: str) -> set[str]:
    """Prefix stems of the content tokens of a text."""
    return {token[:STEM_LEN] for token in tokenize(text)}
```
Import as `from agent.rag_rank import stems, tokenize` (and `_normalise_ws` at line 233 only if needed; it is private, prefer a local helper).

**Neutralization to mirror when verifying** (`agent/rag.py` lines 83, 184-186): the chunk the model saw went through `_neutralize` (`={3,}` -> `==`). Import `from agent.rag import _neutralize` or normalize `=` runs away on both sides.

**Imports for this module (derived):** stdlib only (`difflib`, `re`, `unicodedata`, `dataclasses`), then `from agent.rag_rank import stems`. No `shared.logger` unless it logs; if it does, `logger = get_logger(__name__)` and log counts/ids only, never quote text (CLAUDE.md).

**Core pattern to follow:** RESEARCH.md Patterns 2-5 give normalize/window_ratio/tail grammar/auto-quote/`build_idk_reply` skeletons. Verification must be fail-soft: wrap per-quote in `try/except Exception` and degrade to `unverified` (14 D-04 pattern; see `rag_turn.py` fail-soft below). Public surface that both `ws.py` and `scripts/rag_eval.py` import: `STRICT_INSTRUCTION`, `process_answer`, `build_idk_reply`, `QuoteResult`.

---

### `agent/rag.py` (MOD; helper, transform)

**Analog:** itself.

**Instruction selection** (lines 59-63, 189-199): keep `RAG_INSTRUCTION` byte-identical (Pitfall 7); add a `strict: bool = False` parameter to the renderer and swap the last line.
```python
def render_rag_block(chunks: list[dict[str, Any]]) -> str:
    """Render numbered fragments between delimiters, followed by the instruction."""
    lines: list[str] = [BLOCK_OPEN]
    for index, chunk in enumerate(chunks, 1):
        label = " ".join(str(chunk.get("section") or chunk.get("title") or "").split())
        source = " ".join(str(chunk["source"]).split())
        lines.append(_neutralize(f"[{index}] {source} — {label}"))
        lines.append(_neutralize(chunk["text"]))
    lines.append(BLOCK_CLOSE)
    lines.append(RAG_INSTRUCTION)          # -> STRICT_INSTRUCTION if strict
    return "\n".join(lines)
```
`build_rag_block` (lines 202-214) calls `render_rag_block([*kept, chunk])` for budget fitting, so thread `strict` through it too (budget must count the longer strict instruction). `scripts/rag_eval.py` also calls `build_rag_block`; it needs the same parameter.

**Payload builder** (lines 255-281): add new keyword args with defaults so existing callers/tests stay valid, bump `PAYLOAD_VERSION = 2` (line 65) to 3; verdict is already a free string (add `VERDICT_MODEL_IDK = "model_idk"` beside `VERDICT_OFF`/`VERDICT_KB_UNAVAILABLE`, lines 66-67).
```python
def build_rag_payload(*, mode, kb_id, kb_name, top_k, sources, dropped, context_tokens,
                      warning, verdict: str = "ok", search: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"v": PAYLOAD_VERSION, "mode": mode, ..., "verdict": verdict, "search": search}
```
Add `strict`, `quotes`, `cited_ranks`, `invalid_refs`, `answer_supported` (names per RESEARCH "Payload v3 additions"). Keep sources text-free (`sources_from_chunks`, lines 240-252, is the metadata-only builder; quotes go in a separate list). Check `tests/test_rag.py` for `v == 2` assertions (A5).

---

### `agent/rag_turn.py` (MOD; service, request-response)

**Analog:** itself. Edit points:

**`RagTurn` dataclass** (lines 40-55): add fields, excluded from `done_payload`/`sources_json` (chunk text must never be serialized):
```python
@dataclass(frozen=True)
class RagTurn:
    mode: str
    payload: dict[str, Any]
    # new: kept_chunks: list[dict[str, Any]] = field(default_factory=list)  (has text, in-memory only)
    # new: strict: bool = False
    # new: reply_text: str | None = None   (set only for the gate short-circuit)
```

**Gate branch to modify** (lines 136-143):
```python
verdict = trace.pop("verdict")
if verdict == VERDICT_BELOW_THRESHOLD:
    merge_no_fragments_note(llm_messages)          # strict: skip this, build reply instead
    turn = RagTurn(
        MODE_RAG,
        _payload(MODE_RAG, kb_id, kb_name, top_k, verdict=verdict, search=trace),
    )
    _log(chat_id, turn)
    return turn
```
Strict path: `if config.strict:` -> `reply = build_idk_reply(trace)` and `RagTurn(..., reply_text=reply)` with `verdict=VERDICT_BELOW_THRESHOLD` and `search=trace` preserved (grey line + "Детали поиска" keep working). Non-strict: unchanged. Per A3, also gate zero-candidate `ok` results in strict mode. Read `config.strict` from the `ChatRagConfig` row loaded at line 117 (`config`), alongside `config_from_row(config, kb)` at line 131.

**Kept chunks and strict block** (lines 153-175): `build_rag_block(chunks, budget)` -> pass `strict=config.strict`; store `kept` on the `RagTurn`.

**Fail-soft error pattern to keep** (lines 176-208): `except asyncio.CancelledError: raise` / `except RagFailure` / `except Exception` building a `VERDICT_KB_UNAVAILABLE` payload; any new code in this function must stay inside this try.

**Logging pattern** (lines 211-229): `_log` emits `rag_turn_prepared` with counts only; add `strict=` and `gated=` key=value pairs.

---

### `agent/rag_api.py` (MOD; route/schema, CRUD)

**Analog:** itself, the `lexical`/`llm_rerank`/`hybrid`/`rewrite` flags. Four touch points:

```python
# RagConfigIn (lines 36-39)
    rewrite: StrictBool | None = None
    strict: StrictBool | None = None          # NEW
# RagConfigOut (line 59)
    rewrite: bool
    strict: bool                              # NEW
# _config_out default (lines 79-85): row is None -> add strict=True
# _config_out row path (line 108):  rewrite=bool(row.rewrite), strict=bool(row.strict),
# _apply_search_settings (line 119)
    for name in ("lexical", "llm_rerank", "hybrid", "rewrite", "strict"):
        value = getattr(body, name)
        if value is not None:
            setattr(row, name, value)
```
Also check where a new `ChatRagConfig` row is constructed (around line 180, `lexical=row.lexical` appears in a response/audit; grep for it) so `strict` is carried. Auth: `_get_owned_chat` (lines 62-71, 404 not 403) already wraps the endpoint.

---

### `agent/ws.py` (MOD; controller, streaming)

**Analog:** itself. Two integration points.

**1. Short-circuit after `prepare_rag_turn`** (lines 796-813). Everything between `assistant_text = ""` (808) and `_persist_assistant_message` (989) must be bypassed when `rag_turn.reply_text is not None`; set `assistant_text = rag_turn.reply_text`, send one token frame, then reuse the persist + stats + `done` tail unchanged:
```python
rag_turn: RagTurn = await prepare_rag_turn(session, chat, payload.content, llm_messages,
    effective.context_length, max_tokens, schema_tokens, client=client, model=payload.model)

assistant_text = ""
pending_tool_calls: list[dict[str, Any]] = []
stream_task = asyncio.current_task()
active_streams[chat_id] = stream_task      # skip registering for the gated path (Pitfall 5)
```
Token frame format (A1 confirmed at lines 270, 278, 696):
```python
await websocket.send_json({"type": "token", "content": safe})
```
Line 696 (`await websocket.send_json({"type": "token", "content": summary})`) is the existing example of sending a whole templated string as one frame.

**2. Post-stream processing before persist** (lines 989-996). Insert `process_answer` just before this call (use `asyncio.to_thread` for CPU work), persist the clean text, and put the updated payload into `rag_sources`/`done.rag`:
```python
assistant_msg = await _persist_assistant_message(
    session, chat, user_msg.id, assistant_text,      # -> cleaned answer text
    tool_trace=tool_trace,
    rag_sources=rag_turn.sources_json,               # -> serialize updated v3 payload
)
...
await websocket.send_json({"type": "done", "message_id": assistant_msg.id, "stats": stats,
    "memory_writes": memory_writes, "task_writes": task_writes,
    "invariant_conflict": conflict_payload, "rag": rag_turn.done_payload})   # lines 1040-1050
```
Because `RagTurn` is a frozen dataclass, produce a new payload dict (or `dataclasses.replace`) and use it for both lines 995 and 1048. Invariant justification text is appended to `assistant_text` at lines 977-987, so the "Цитаты:" tail may not be last; parse the last marker (Pitfall 5). `schedule_title_generation` (lines 1031-1039) must still run on the gated path. `_persist_assistant_message` signature at lines 208-225 takes `rag_sources: str | None`.

---

### `shared/models.py` (MOD; model, CRUD)

**Analog:** `ChatRagConfig` stage flags (lines 740-743):
```python
    lexical: bool = Field(default=False)
    llm_rerank: bool = Field(default=False)
    hybrid: bool = Field(default=False)
    rewrite: bool = Field(default=False)
    strict: bool = Field(default=True)        # NEW (D-15: on by default)
```
Update the class docstring (lines 718-723) to mention the strict switch. `shared/models.py` has uncommitted changes at mapping time; re-read before editing.

### `shared/database.py` (MOD; migration, batch)

**Analog:** `_CHATRAGCONFIG_RANK_COLUMNS` (lines 143-173):
```python
_CHATRAGCONFIG_RANK_COLUMNS: tuple[tuple[str, str], ...] = (
    ("candidate_k", "INTEGER DEFAULT 20"),
    ...
    ("rewrite", "BOOLEAN DEFAULT 0"),
    ("strict", "BOOLEAN DEFAULT 1"),          # NEW
)
...
    for name, definition in _CHATRAGCONFIG_RANK_COLUMNS:
        if name in existing:
            continue
        logger.info("migrating_chatragconfig_add_column", column=name)
        await conn.execute(text(f"ALTER TABLE chatragconfig ADD COLUMN {name} {definition}"))
```
No new function needed; the PRAGMA-checked loop is idempotent. Uncommitted changes exist in this file; re-read first.

---

### `ui/static/index.html` + `ui/static/app.js` (MOD; component)

**Analog (switch):** the `#rag-stage-rewrite` label in the popover (index.html line 199). Copy the whole `<label class="flex items-center justify-between ..."><span>…</span><span class="relative inline-block"><input id="rag-stage-rewrite" type="checkbox" role="switch" class="sr-only peer">…</span></label>` and change the id to `rag-strict`, text to `Строгий режим (цитаты + «не знаю»)`, plus a `title`.

**Popover render** (`app.js` lines 3957-3966): add the flag to the map. Note `anyOn` highlights the button; strict is default-on, so exclude it from `anyOn` or the button is always highlighted (decide in plan/UI-SPEC).
```js
const flags = {
    'rag-stage-lexical': cfg.lexical,
    'rag-stage-llm': cfg.llm_rerank,
    'rag-stage-hybrid': cfg.hybrid,
    'rag-stage-rewrite': cfg.rewrite,
};
Object.entries(flags).forEach(([id, value]) => { $(id).checked = Boolean(value); });
```
Bind the change handler next to the other stage switches in `bindRagSearchUi` (line 3976+), saving through `saveRagSearchSetting({ strict: checked })` (line 3969).

**Meta builder** (`buildRagMeta`, lines 4048-4072): the integration point for the amber line, grey "не знаю" styling, invalid-ref note, and the «Цитаты (N)» block. Element helper is `mcpEl(tag, className, text)` (all text via `textContent`). Existing line/notice pattern to copy:
```js
const line = mcpEl('div', 'mt-2 text-xs text-slate-500', `Фрагменты не прошли порог (лучший ${best} < ${threshold})`);
line.setAttribute('role', 'status');
line.title = 'Снизьте порог или переформулируйте вопрос. Подробности в «Детали поиска»';
```
and the amber warning container (lines 4054-4059) `'mt-2 rounded-lg border border-yellow-700 bg-yellow-900/30 px-3 py-2 text-xs text-yellow-400'` for «ответ не подтверждён фрагментами». Line 4066 special-cases `rag.verdict !== 'below_threshold'` for the empty message; add `model_idk` there. `buildRagThresholdLine` (4074-4082) is the grey below-threshold analog (it reads `rag.search.best_cosine`; it must tolerate the zero-candidates gate where `best_cosine` is null).

**Quotes block + chips:** model on `buildRagSourcesBlock` (lines 4256-4275, a `<details>` with a `summary` and body of rows) but with the `open` attribute set by default, and on `buildRagSourceRow` (4277-4303) for row construction and the "показать полностью" toggle (line 4293-4300: `line-clamp-4` toggle plus `aria-expanded`). Quote rows must use `textContent` only (model output). "цитируется" marks go in `buildRagSourceRow`; sort cited sources first in `buildRagSourcesBlock`. See `16-UI-SPEC.md` for exact copy and colors.

---

### `scripts/rag_eval.py` (MOD; script, batch)

**Analog:** the `ablate` subcommand: `ablation_config` (918), `_ablate_question` (1091-1156), `run_ablation` (1159), `_render_ablation_outputs` (1038), `_load_existing_verdicts` (1025), `ablate_command` (1225), `_add_ablate_parser` (1294), registered in `build_parser` (1318).

**Core per-question pattern to copy** (lines 1119-1136): the script builds messages itself and does not go through `prepare_rag_turn`, so strict handling must be duplicated through the shared pure module:
```python
verdict = trace.pop("verdict")
messages = [{"role": "system", "content": EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": question["question"]}]
kept: list[dict[str, Any]] = []
if verdict == VERDICT_BELOW_THRESHOLD:
    merge_no_fragments_note(messages)      # strict: no LLM call, reply = build_idk_reply(trace)
else:
    budget = rag_budget(opts.context_length, _message_tokens(messages), opts.max_tokens)
    block, kept, dropped = build_rag_block(chunks, budget)   # + strict=True
    if block:
        merge_rag_block(messages, block)
    mark_over_budget(trace, len(kept))
reply = await _ask(client, opts, messages)
```
Then call `rag_cite.process_answer(reply["answer"], kept, ...)` and add per-quote state counts plus `finish_reason`/empty-answer category to the raw record (existing record keys at 1137-1156: `verdict`, `fragments`, `cited_sources`, `**reply`). Reads winning config from `eval_out/day23/run_meta.json` (`winner`); use `--max-tokens 8192`. Manual verdict columns survive re-runs through `_load_existing_verdicts` (line 1025). Report renderers `render_ablation_md`/`render_ablation_answers_md` (968-1015) are the shape for the Day 24 table; escape cells with `_cell` (185).

### `scripts/rag_judge.py` (MOD; script, batch)

**Analog:** itself: `build_judge_messages` (line 79), `parse_judge_reply` (98), `judge_rows` (114), `_judge_one` (142), key checkpoint in `check_access` (231) and `judge_command` (283). Add a faithfulness rubric as an additional message builder and an optional column; do not alter the existing relevance rubric. Note `_neutralise` (74) for wrapping untrusted text in the judge prompt.

---

### Tests

**`tests/test_rag_cite.py` (NEW):** analog `tests/test_rag_rank.py` (pure, fast function tests with no fixtures). Cases listed in RESEARCH "Test and Verification Hints".

**`tests/test_rag_ws.py` (MOD):** copy `test_below_threshold_turn_still_answers_with_note` (lines 305-323):
```python
@respx.mock
def test_below_threshold_turn_still_answers_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    _recording_route(captured)
    with TestClient(app) as client:
        chat_id = _open_rag_chat(client, monkeypatch, threshold=0.99)
        with client.websocket_connect(f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN}) as ws:
            frames = _send_and_drain(ws, QUESTION)
        done = frames[-1]
        assert done["rag"]["verdict"] == "below_threshold"
        outbound = _last_user_content(captured[0])
        assert NO_FRAGMENTS_INSTRUCTION in outbound
```
This test now needs `strict=False` on its config (strict defaults on, Pitfall 7/A5). New strict twin asserts `captured == []` (zero LLM requests) and the template text in a `token` frame. `_open_rag_chat` (helper) needs a `strict` parameter.

**`tests/test_rag_turn.py` (MOD):** `_chat(...)` helper (lines 37-60) builds `ChatRagConfig(...)`; add a `strict` param and tests for the gate (`turn.reply_text` set, `merge_no_fragments_note` not applied, `NO_FRAGMENTS_INSTRUCTION` import at line 14 stays for strict-off). Uses `install_fake_embedder`, `seed_kb`, `vector_for` from `kb_helpers`.

**`tests/test_rag_api.py`, `test_rag.py`, `test_rag_eval.py`, `test_rag_search_static.py`:** extend with the strict round trip (default True), payload v3 assertions (replace `v == 2`), Day 24 renderer, and static JS anchors (new ids `rag-strict`, quote block builder).

## Shared Patterns

### Fail-soft (a failed check marks, never breaks the turn)
**Source:** `agent/rag_turn.py` lines 176-208 (`except asyncio.CancelledError: raise`, then specific `RagFailure`, then broad `Exception` logged with `type(exc).__name__`).
**Apply to:** `rag_cite.process_answer` and its call site in `ws.py`; any exception in verification yields `unverified`, never a failed turn (D-06). No bare `except:`.

### Metadata-only storage
**Source:** `agent/rag.py::sources_from_chunks` (240-252) and `agent/rag_pipeline.py::_serialise` (309-326, "never carries chunk text").
**Apply to:** payload v3. Chunk text lives only on in-memory `RagTurn.kept_chunks`; stored quote entries carry capped quote text plus rank/chunk_id/file/section copied from metadata.

### Logging
**Source:** `agent/rag_turn.py::_log` (211-229).
**Apply to:** all new Python: `logger = get_logger(__name__)`, `snake_case_action` key plus `key=value` counts and ids, never quote or chunk text.

### Idempotent column migration + optional StrictBool flag
**Source:** `shared/database.py` 143-173; `agent/rag_api.py` 36-39, 119-122.
**Apply to:** the `strict` flag across model, migration, API in/out, popover.

### Safe rendering
**Source:** `ui/static/app.js::mcpEl` use in `buildRagMeta`/`buildRagSourceRow` (textContent only).
**Apply to:** every quote, section, and file string; the server-built clarifying question passes through Marked + DOMPurify as normal assistant text, so escape markdown in section names server-side.

### CPU work off the loop
**Source:** project convention (`asyncio.to_thread`), RESEARCH benchmark: 75 ms per quote/chunk pair.
**Apply to:** `process_answer` call in `ws.py`.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `agent/rag_cite.py` internals (tail parser, fuzzy window matcher, sentence splitter, clarifying-question builder) | utility | transform | `rag_rank.py` gives the module shape only; no existing quote/text-verification code. Use RESEARCH.md Patterns 2-5 skeletons. |
| Templated no-LLM reply delivery in `ws.py` | controller | streaming | No existing turn skips the LLM; closest is the one-frame summary send at `ws.py:696` and the persist/done tail at 989-1050. |
| `Day24_report.md` quote-state columns | doc | batch | Day 22/23 tables have no per-quote state; copy only the renderer structure (`render_ablation_md`, `_cell`). |

## Conventions

Derivation via `gsd-tools verify conventions --derive` was skipped (convention derivation skipped: `gsd-tools.cjs` not found at the plugin root in this environment). Conventions below are taken from CLAUDE.md and the analog files read.

| Axis | Dominant | Share | Entropy | Status |
|------|----------|-------|---------|--------|
| File-name casing | snake_case (`rag_turn.py`, `test_rag_ws.py`) | ~100% (Python) | low | named contract |
| Identifier casing | snake_case functions, PascalCase classes, UPPER_CASE constants | high | low | named contract |
| Export style | plain module-level defs, explicit imports (`from agent.rag import ...`), no `__all__` | high | low | named contract |
| Import style | stdlib -> third-party -> local, absolute from project root, no aliases | high | low | named contract |

Contested hotspots (author's choice): the CJS<->SDK dual resolver (`bin/lib/**` is CJS `module.exports`/`require`; `sdk/src/**` is ESM `export`/`import`) is the prototype intentional-contested split; not relevant to this Python repo, so match each directory's local style. Within this phase, `ui/static/app.js` is a single vanilla-JS script with top-level functions and no modules; match it.

## Metadata

**Analog search scope:** `agent/`, `shared/`, `ui/static/`, `scripts/`, `tests/`
**Files read:** rag.py, rag_turn.py, rag_api.py (28-130), rag_rank.py (1-48), rag_pipeline.py (309-430), ws.py (205-225, 785-845, 960-1050), models.py (717-744), database.py (143-173), app.js (3933-4107, 4256-4315), index.html (line 199), rag_eval.py (1091-1156), tests (excerpts)
**Pattern extraction date:** 2026-10-03
**Caveat:** `shared/database.py`, `shared/models.py`, `agent/context_engine.py`, `agent/schemas.py`, `ui/static/app.js`, `ui/static/index.html` had uncommitted changes at mapping time (unrelated to Phase 16 as far as observed); re-read before editing.
