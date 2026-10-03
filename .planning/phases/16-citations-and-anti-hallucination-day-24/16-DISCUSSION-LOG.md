# Phase 16: Citations and anti-hallucination (Day 24) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-03
**Phase:** 16-citations-and-anti-hallucination-day-24
**Areas discussed:** Where quotes come from, Unverified quotes and bad refs, "Не знаю" gate behavior, Strict mode and Day 24 check

---

## Where quotes come from

### How should the quotes for an answer be produced?

| Option | Description | Selected |
|--------|-------------|----------|
| Model, inline (Recommended) | Model ends its streamed answer with a fixed "Цитаты:" section (`[N] «…»`); server parses and verifies after the stream. One LLM call. | ✓ |
| Second call, JSON | Non-streaming temperature-0 call returns `[{n, quote}]` after the answer. +1 call per turn. | |
| Code picks sentences | Code selects best-overlap sentences from cited chunks; always verified, not the model's evidence. | |

### What should the user see of the raw "Цитаты:" section?

| Option | Description | Selected |
|--------|-------------|----------|
| Strip, render as block (Recommended) | Server cuts the tail from stored text; UI shows a structured block from `done.rag`; tail visible briefly while streaming. | ✓ |
| Keep text, add block | Answer text unchanged plus the structured block; quotes appear twice. | |
| Hide while streaming too | Streaming filter withholds the tail in all stream sites. | |

### How long may a quote be, and how many per answer?

| Option | Description | Selected |
|--------|-------------|----------|
| Short, 1-3 per answer (Recommended) | At most ~300 characters each; longer ones verified but truncated in the UI. | ✓ |
| One per cited fragment | Every `[N]` used must have its own quote. | |
| You decide | Claude picks limits after measuring the local model. | |

### Fallback if verbatim-quote compliance is poor?

| Option | Description | Selected |
|--------|-------------|----------|
| Code fills the gap (Recommended) | Zero verified quotes → code picks best-overlap sentence per cited/top chunk, marked «подобрана автоматически». | ✓ |
| One retry call | Extra non-streaming call for quotes as JSON, then a warning. | |
| No fallback, just report | Show what the model gave and record the rate. | |

### Which normalization should the substring check use?

| Option | Description | Selected |
|--------|-------------|----------|
| Normalized exact (Recommended) | Casefold, whitespace, quotes/dashes, ё→е, hyphen breaks, "…" split into ordered parts. | |
| Plus fuzzy tolerance | Same, and if exact fails accept at difflib similarity ≥ 0.9, shown as «почти дословно». | ✓ |
| You decide | Claude settles the rules. | |

**User's choice:** Model inline · strip and render as block · 1-3 short quotes · code fills the gap · normalized + fuzzy tolerance
**Notes:** The user chose fuzzy tolerance over the recommended exact-only match; this adds a third quote state.

---

## Unverified quotes and bad refs

### A quote fails both checks against its cited chunk. What happens?

| Option | Description | Selected |
|--------|-------------|----------|
| Show with red chip (Recommended) | Stays in the block with «не подтверждена». | |
| Search other chunks first | Try the other fragments of the turn; re-attach with «источник исправлен» on a match, else red chip. | ✓ |
| Drop silently | Removed from the block, counted only in payload/report. | |

### The model references a fragment that does not exist. How is that handled?

| Option | Description | Selected |
|--------|-------------|----------|
| Reject + count (Recommended) | Quote line tried against all fragments, else «не подтверждена (нет такого источника)»; text markers left as written; count stored and shown as a grey note. | ✓ |
| Also strip from text | Additionally remove invalid `[N]` markers from the stored answer. | |
| You decide | Claude settles this in planning. | |

### Where do the quotes sit under the answer?

| Option | Description | Selected |
|--------|-------------|----------|
| Own open block (Recommended) | «Цитаты (N)» under the answer, expanded by default; other blocks collapsed below. | ✓ |
| Inside source rows | Quotes shown inside «Источники» rows. | |
| Collapsed block | «Цитаты (N)» collapsed with a summary. | |

### Sources list: all sent or only cited?

| Option | Description | Selected |
|--------|-------------|----------|
| All sent, cited marked (Recommended) | Every fragment listed; cited ones marked «цитируется» and listed first. | ✓ |
| Only cited | Only fragments the answer cites. | |
| You decide | Claude settles this in planning/UI-SPEC. | |

**User's choice:** Search other chunks first · reject + count · own open block · all sent, cited marked
**Notes:** The user chose re-attachment over the recommended plain red chip.

---

## "Не знаю" gate behavior

### How is the "не знаю" reply produced on below_threshold?

| Option | Description | Selected |
|--------|-------------|----------|
| Template, no LLM (Recommended) | Answer LLM not called; fixed reply + code-built clarifying question. | ✓ |
| Template + LLM question | Fixed sentence, one small LLM call for the question. | |
| LLM under strict prompt | LLM still called; code replaces the reply if it does not comply. | |

### What should the clarifying question be based on?

| Option | Description | Selected |
|--------|-------------|----------|
| Nearest sections (Recommended) | List the 2-3 closest sections under the threshold. | ✓ |
| Generic question | Fixed line plus the KB name. | |
| Sections only if close | Sections only for near misses, generic otherwise. | |

### Does the gate fire when the only survivors are FTS-exempt?

| Option | Description | Selected |
|--------|-------------|----------|
| No, they count (Recommended) | Gate fires only on zero survivors (existing verdict). | ✓ |
| Yes, cosine decides | Gate looks at best raw cosine among survivors. | |
| You decide | Decide after Day 23 hybrid results. | |

### Fragments passed, but the model finds no answer or cites nothing. What then?

| Option | Description | Selected |
|--------|-------------|----------|
| Prompt rule + mark (Recommended) | Model told to say "Не знаю"; code stores `model_idk`; unsupported answers get an amber line. | ✓ |
| Replace unsupported answers | Additionally replace uncited answers with the template. | |
| Gate only | Nothing beyond the retrieval gate. | |

**User's choice:** All recommended options
**Notes:** None.

---

## Strict mode and Day 24 check

### Always on, or switchable per chat?

| Option | Description | Selected |
|--------|-------------|----------|
| Per-chat switch, on by default (Recommended) | «Строгий режим» in the "Поиск ⚙" popover, boolean on `ChatRagConfig`; off = Phase 15 behavior. | ✓ |
| Always on | No switch. | |
| Switch, off by default | Same switch, off until enabled. | |

### Where does the Day 24 check live?

| Option | Description | Selected |
|--------|-------------|----------|
| Day24_report.md (Recommended) | Separate file at the repo root, generated by `scripts/rag_eval.py`. | ✓ |
| Section in Day23_report.md | Appended "Day 24" section. | |
| Section in docs | A section in `docs/`. | |

### Which configuration and model does the run use?

| Option | Description | Selected |
|--------|-------------|----------|
| Best Day 23 config, local model (Recommended) | Winning Day 23 config on qwen3.5-9b, strict on, plus one strict-off comparison row. | ✓ |
| Default pipeline only | Threshold-only default, strict on. | |
| Local + DeepSeek | Recommended run plus the same on DeepSeek. | |

### Who judges "meaning matches quotes"?

| Option | Description | Selected |
|--------|-------------|----------|
| Claude fills, you review (Recommended) | Manual verdict by Claude, reviewed at a checkpoint. | |
| Plus LLM-judge column | Manual verdict stays primary; `scripts/rag_judge.py` adds an optional DeepSeek judge column. | ✓ |
| Automatic only | Derived from code only. | |

**User's choice:** Per-chat switch on by default · `Day24_report.md` · best Day 23 config on the local model · manual verdict plus LLM-judge column
**Notes:** The judge column needs the real `DEEPSEEK_API_KEY` (same checkpoint as 15 D-18).

---

## Claude's Discretion

- Strict-prompt wording, tail marker rules, `model_idk` marker detection
- Fuzzy matcher details beyond the 0.9 bar; auto-quote sentence splitting and overlap score
- Payload v3 shape, `done.rag` field names, `ChatRagConfig` migration
- WebSocket delivery of the templated reply and its exact wording
- Strict mode vs tool-call rounds and the per-answer mode label
- Styling, truncation, fixture and raw-output locations, judge rubric text

## Deferred Ideas

- Second LLM call / retry call for quotes — not chosen
- Streaming filter hiding the quote tail — not chosen
- Replacing unsupported answers after generation — not chosen
- LLM-written clarifying question — not chosen
- Day 24 answering run on DeepSeek — not in this phase
- Click-through from a source to the full chunk with neighbours — backlog candidate
