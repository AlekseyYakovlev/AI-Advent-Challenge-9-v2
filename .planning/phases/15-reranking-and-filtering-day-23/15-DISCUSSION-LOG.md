# Phase 15: Reranking and filtering (Day 23) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-03
**Phase:** 15-reranking-and-filtering-day-23
**Areas discussed:** Search controls and Детали поиска, Threshold semantics, Rerankers and rewrite behavior, Calibration and Day23 report

---

## Search controls and Детали поиска

### Placement of the new controls

| Option | Description | Selected |
|--------|-------------|----------|
| Popover from header | "Поиск ⚙" button next to K opens a panel with candidate-K, threshold and 4 switches | ✓ |
| Inline in the header | Everything in the header row next to K (as 14 D-02 anticipated) | |
| Section in chat Settings | "Поиск (RAG)" section in the per-chat settings panel | |

**User's choice:** Popover from header.

### Defaults when RAG is on

| Option | Description | Selected |
|--------|-------------|----------|
| Threshold only | candidate-K 20, final K 5, calibrated threshold, all optional stages off | ✓ |
| Everything off (Day 22 behavior) | Plain top-K, threshold 0 | |
| Best-found config on | Defaults follow the Day23 report winner | |

**User's choice:** Threshold only.

### Layout of Детали поиска

| Option | Description | Selected |
|--------|-------------|----------|
| One candidate table | Header lines + one table with rank before→after, scores, status chip | ✓ |
| Stage-by-stage sections | Separate collapsible sub-sections per stage | |
| You decide | Leave to UI-SPEC | |

**User's choice:** One candidate table.

### Persistence

| Option | Description | Selected |
|--------|-------------|----------|
| Store on the message | Full trace (metadata only) in `Message.rag_sources` and `done.rag` | ✓ |
| Live only | Only in `done.rag` for the current session | |
| Store, capped at top-10 candidates | Persist 10 best plus counts | |

**User's choice:** Store on the message.

---

## Threshold semantics

### Which score the threshold applies to

| Option | Description | Selected |
|--------|-------------|----------|
| Raw cosine, before rerank | Only the calibrated cosine is thresholded; rerankers reorder survivors | ✓ |
| Final score, after rerank | Threshold on the last stage's score | |
| Cosine + relative cut | Absolute threshold plus `top1 − delta` | |

**User's choice:** Raw cosine, before rerank.

### FTS5-only hits under hybrid

| Option | Description | Selected |
|--------|-------------|----------|
| Exempt keyword hits | FTS5 hits pass the cut, marked «прошло по FTS» | ✓ |
| Same threshold for everyone | FTS-only hits cut by cosine like the rest | |
| Exempt only top-3 FTS hits | Only the 3 best keyword matches bypass | |

**User's choice:** Exempt keyword hits.

### Zero chunks left

| Option | Description | Selected |
|--------|-------------|----------|
| Answer without fragments + notice | LLM answers with a "nothing found" instruction; grey line with scores; verdict `below_threshold` | ✓ |
| Keep the single best chunk | Always pass top-1 | |
| Templated reply, skip the LLM | Fixed message, no model call (pulls Phase 16 forward) | |

**User's choice:** Answer without fragments + notice.

### Where the calibrated value lives

| Option | Description | Selected |
|--------|-------------|----------|
| Per-model constant + chat override | Code constant per embedder; `ChatRagConfig.threshold` NULL = calibrated | ✓ |
| Stored on the KB row | `KnowledgeBase.threshold`, editable per KB | |
| Chat value only | Number prefilled when RAG is first enabled | |

**User's choice:** Per-model constant + chat override.

---

## Rerankers and rewrite behavior

### Model for rewrite and LLM rerank

| Option | Description | Selected |
|--------|-------------|----------|
| The chat's own model | Same provider/model, non-streaming, temperature 0 | ✓ |
| Separate "helper model" setting | Model select in the popover | |
| Chat model, eval can override | `--helper-model` only in the eval script | |

**User's choice:** The chat's own model.

### What is searched with rewrite on

| Option | Description | Selected |
|--------|-------------|----------|
| Original + rewrite, merged | Both searched, candidates merged by best cosine | ✓ |
| Rewritten query only | Rewrite replaces the question | |
| You decide after the spike | Pick from drift-spike numbers | |

**User's choice:** Original + rewrite, merged.

### Both rerankers on

| Option | Description | Selected |
|--------|-------------|----------|
| Chain: lexical first, LLM on the top 10 | Lexical reorders all survivors, LLM scores the 10 best in one prompt | ✓ |
| LLM overrides lexical | Lexical ignored when LLM is on | |
| Mutually exclusive in the UI | Radio: без реранка / лексический / LLM | |

**User's choice:** Chain: lexical first, LLM on the top 10.

### Stage failure

| Option | Description | Selected |
|--------|-------------|----------|
| Skip the stage, note it in Детали | Continue with previous order / original query; no warning or toast | ✓ |
| Skip + visible yellow warning | Same fallback plus the RAG-04 style warning | |
| Fall back to plain RAG entirely | Any failure drops to Day 22 behavior | |

**User's choice:** Skip the stage, note it in Детали.

---

## Calibration and Day23 report

### Calibration data

| Option | Description | Selected |
|--------|-------------|----------|
| Separate calibration set | ~20 extra questions (12 answerable, 8 out-of-corpus), approved and frozen | ✓ |
| Calibrate on the 10 control questions | Literal RANK-07, limitation stated | |
| Control set + extra out-of-corpus only | 10 control + ~8 new negatives | |

**User's choice:** Separate calibration set.

### Comparison matrix

| Option | Description | Selected |
|--------|-------------|----------|
| Ablation: one stage at a time + all-on | 7 runs with answers and verdicts | ✓ |
| Minimal: 4 runs | baseline, +threshold, +lexical, +rewrite | |
| Ablation with split cost | Retrieval metrics for all 7, answers for 3 | |

**User's choice:** Ablation: one stage at a time + all-on.

### Embedders

| Option | Description | Selected |
|--------|-------------|----------|
| Full matrix on the Day 22 winner, calibrate both | Thresholds for nomic and bge-m3; matrix on the winner | ✓ |
| Everything on both embedders | ~140 answers | |
| Winner only | Other model keeps threshold 0 | |

**User's choice:** Full matrix on the Day 22 winner, calibrate both.

### LLM judge with a placeholder DeepSeek key

| Option | Description | Selected |
|--------|-------------|----------|
| Build the script, column filled only with a real key | "не запускалось" recorded if no key | |
| Fall back to the local model as judge | qwen3.5-9b judges, labelled | |
| I'll provide a DeepSeek key before the eval | Checkpoint pauses for a real key, then DeepSeek fills the column | ✓ |

**User's choice:** I'll provide a DeepSeek key before the eval.

---

## Claude's Discretion

- Popover styling, input ranges/steps, "reset to calibrated" affordance
- Lexical formula, article-number boost weight, RRF constant, FTS5 tokenizer/escaping
- FTS5 backfill for existing KBs and cleanup on delete
- Cosine for FTS-only hits
- Rewrite/rerank prompts, timeouts, bad-output rules
- `rag_sources` v2 shape, `done.rag` fields, `ChatRagConfig` migration
- Whether the test-search modal gains the new stages
- Fixture/raw-output locations, judge rubric and script name

## Deferred Ideas

- Code-enforced "не знаю" on zero survivors — Phase 16
- History-aware rewrite — Phase 17
- Separate helper model for rewrite/rerank
- Relative cut-off (`top1 − delta`)
- Per-KB editable threshold
- Cross-encoder reranker (Future Requirements)
