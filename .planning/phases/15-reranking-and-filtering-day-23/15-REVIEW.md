---
phase: 15-reranking-and-filtering-day-23
reviewed: 2026-10-03T00:00:00Z
depth: standard
files_reviewed: 17
files_reviewed_list:
  - agent/kb_search.py
  - agent/rag.py
  - agent/rag_api.py
  - agent/rag_fts.py
  - agent/rag_llm.py
  - agent/rag_pipeline.py
  - agent/rag_rank.py
  - agent/rag_turn.py
  - agent/ws.py
  - scripts/e2e_rag_search_playwright.py
  - scripts/rag_eval.py
  - scripts/rag_judge.py
  - shared/config.py
  - shared/database.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 0
  warning: 5
  info: 4
  total: 9
status: issues_found
---

# Phase 15: Code Review Report

**Reviewed:** 2026-10-03
**Depth:** standard
**Files Reviewed:** 17

## Summary

The two-stage retrieval pipeline is well structured. FTS queries are built only from quoted terms and bound parameters, so there is no SQL or MATCH injection. Untrusted text is neutralised before it goes into the LLM prompts. The frontend builds all DOM through `textContent`, so there is no XSS. Failures in optional stages degrade to a recorded skip. No blockers were found. The warnings are logic defects that change retrieval behaviour on realistic inputs, plus a few robustness gaps.

`rag_eval.py` (about 700 added lines) was only sampled: the calibrate and ablation sections. It was not read line by line.

No structural findings (fallow) were provided.

## Warnings

### WR-01: FTS threshold exemption matches article numbers as raw substrings

**File:** `agent/rag_pipeline.py:229-231`
**Issue:** `_earns_fts_exemption` tests `number in text`, a plain substring check. For a query like "статья 5", `article_numbers` returns `"5"`, and almost any chunk containing the digit 5 (for example "2015", "15.3", "5 дней") earns the exemption and bypasses the cosine threshold. For "ст. 1.1" it also matches "11.12". This weakens the below_threshold behaviour that the D-08 amendment was meant to restore. `rag_rank._contains_number` already implements a correct whole-token match, but it is private and is not used here.
**Fix:** Reuse the boundary-aware helper.
```python
from agent.rag_rank import contains_number  # rename _contains_number to public

for number in article_numbers(question):
    if number in chunk_articles or contains_number(text, number):
        return True
```

### WR-02: Rewrite validator rejects legitimate queries starting with "ответ"

**File:** `agent/rag_rank.py:195, 247`
**Issue:** `CHATTY_PREFIXES` contains `"ответ"` and is applied with `startswith` and no word boundary. Valid search rewrites such as "ответственность за нарушение ...", "ответчик ..." or "ответ на претензию" are discarded as `bad_output`. This is a legal-domain KB, where these words are common. The same applies to `"я не"`, which matches "я неё...", though that case is rare. The result is a silently skipped rewrite stage, and the trace tells the user the model output was bad.
**Fix:** Match whole words or phrases only.
```python
CHATTY_PREFIXES = ("конечно", "вот ", "переписанн", "запрос:", "ответ:", "ответ ", "я не ", "как ии", "как языковая")
```
The `"ответ "` entry still blocks "ответ на ...", so prefer a regex such as `^(ответ\s*:|конечно\b|...)`. The key point is a word boundary.

### WR-03: Judge loop can abort on non-HTTP failures despite the "never aborting" contract

**File:** `scripts/rag_judge.py:156-164`
**Issue:** `_judge_one` catches only `httpx.HTTPError` and `asyncio.TimeoutError`. A malformed 200 reply (`response.json()` raising `ValueError`/`JSONDecodeError`, or `_post_chat_completion` failing on an unexpected body) propagates out of `judge_rows`. The run then dies after partial progress, and the final meta file is never written. `judge_rows` is documented as "never aborting the loop".
**Fix:** Add `ValueError` and `KeyError` to the caught tuple, or catch `Exception` (not bare) and return `JUDGE_ERROR`.

### WR-04: FTS5 migration is unguarded and can stop the agent from starting

**File:** `shared/database.py:177-200` (inside `init_db`, line ~356)
**Issue:** `ensure_kb_chunk_fts` runs unconditionally in `init_db`. If the SQLite build lacks FTS5, `CREATE VIRTUAL TABLE ... USING fts5` raises `OperationalError` and the whole agent fails at startup. The feature is optional (hybrid is a per-chat flag, and `rag_pipeline._hybrid_stage` already handles a missing FTS table via `fts_error`). Optional retrieval should not be able to block the app. The backfill also compares only row counts, so a mirror that is out of sync but has an equal count is never repaired.
**Fix:** Wrap the FTS creation in `try/except OperationalError` and log a warning (`kb_chunk_fts_unavailable`). Consider backfilling by comparing id sets or using `INSERT INTO kb_chunk_fts(kb_chunk_fts) VALUES('rebuild')` where applicable.

### WR-05: No overall time bound on the optional LLM stages

**File:** `agent/rag_pipeline.py:118-167, 275-305`, `agent/rag_llm.py:75-78`
**Issue:** Each stage is individually bounded (embed 30 s, stage 45 s). The worst case is the original embed, then the rewrite call, then the rewritten embed, then the hybrid stage, then the rerank call, which is about 30 + 45 + 30 + 45 s with no streaming activity or progress frame to the client. `retrieve_vectors` for the rewritten query also uses the full `RAG_EMBED_TIMEOUT` again. For a "fail-soft pre-step" this can stall a reply for minutes. The WS flow has no heartbeat during this phase.
**Fix:** Pass a single deadline (for example `RAG_TURN_TIMEOUT`) into `run_retrieval_pipeline` and use `asyncio.timeout` or `wait_for` on the optional stages with the remaining budget. Alternatively, send a "searching" frame.

## Info

### IN-01: Private helpers imported across modules

**File:** `agent/rag_api.py:13`, `agent/rag_turn.py:10`
**Issue:** `_get_owned_kb` and `_unprocessable` (from `agent.kb_api`) and `_message_tokens` (from `agent.context_engine`) are underscore-private but imported from other modules. This is CONVENTION-level only, because the project convention is that `_` names are not exported.
**Fix:** Expose public names, or move the helpers to a shared module.

### IN-02: `assert kb is not None` used for control flow

**File:** `agent/rag_pipeline.py:348`
**Issue:** The assert only narrows the type. It is stripped under `python -O`. It is currently unreachable in practice because `retrieve_vectors` raises `RagFailure` for `kb is None`, but it is fragile.
**Fix:** Replace with `if kb is None: raise RagFailure("kb_deleted", MSG_KB_DELETED)` before the call.

### IN-03: Duplicated constants across scripts

**File:** `scripts/rag_judge.py:43-53`, `scripts/rag_eval.py` (`ANSWER_COLUMNS` near line 1000)
**Issue:** `ANSWER_COLUMNS` is defined twice, and the judge columns are also referenced via `JUDGE_COLUMNS`. A schema change in one will silently break CSV round-tripping in the other.
**Fix:** Define the column tuple once and import it in both scripts.

### IN-04: Frontend renders `NaN` when numeric trace fields are missing

**File:** `ui/static/app.js` (`buildRagThresholdLine`, `buildRagDetailsBlock`)
**Issue:** `Number(undefined).toFixed(2)` yields `"NaN"` if `search.config.threshold` or `best_cosine` is absent or null. This can happen for stored v1 or partial payloads. It is cosmetic only.
**Fix:** Guard with `Number.isFinite(...)` and fall back to `—`.

---

_Reviewed: 2026-10-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
