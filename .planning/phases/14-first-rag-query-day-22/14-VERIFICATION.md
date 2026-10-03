---
phase: 14-first-rag-query-day-22
verified: 2026-10-03T00:00:00Z
status: passed
score: 4/4 roadmap success criteria verified (8/8 requirements satisfied)
has_blocking_gaps: false
overrides_applied: 0
re_verification: false
gaps: []
---

# Phase 14: First RAG query (Day 22) Verification Report

**Phase Goal:** users can attach a KB to a chat and get answers grounded in retrieved chunks, with sources visible, and compare answers with/without RAG on a frozen control set.
**Status:** passed (initial verification)

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC1: attach KB, switch "без RAG"/"с RAG", mode and KB visible | VERIFIED | `agent/rag_api.py` GET/PUT `/chats/{id}/rag`; `ui/static/index.html` has `#rag-badge` in header; `tests/test_rag_static.py` (25 pass); 14-08 Playwright E2E (12 PASS) recorded in SUMMARY |
| 2 | SC2: RAG answer built from top-K chunks, sources (file, section, chunk_id, score) under message; stored user message stays raw | VERIFIED | `agent/rag.py` `sources_from_chunks` carries rank/chunk_id/file/section/page/score; `merge_rag_block` mutates only the outbound `llm_messages` copy; `ws.py:796` calls `prepare_rag_turn` after `build_llm_context`, persists `rag_sources=rag_turn.sources_json` (ws.py:993) and sends `done.rag` (ws.py:1046); payload is metadata only (no chunk text); `tests/test_rag_ws.py` passes |
| 3 | SC3: embedder unavailable / KB deleted gives a plain answer with a visible warning; large RAG block never deletes the user message | VERIFIED | `rag.retrieve` maps every failure to `RagFailure` codes (embedder_unavailable, kb_deleted, kb_not_ready, dim_mismatch, index_corrupt, retrieval_failed); `prepare_rag_turn` catches all and returns a warning payload; budget is `min(30% ctx, free space)` computed after the strategy, so RAG cannot trigger the `no_compression` overflow/deletion path; `context_full` degrades to no RAG; unit and WS tests pass |
| 4 | SC4: `scripts/rag_eval.py` runs frozen 10-question set (incl. out-of-corpus); `Day22_report.md` compares no-RAG vs RAG and nomic vs bge-m3 on hit@k | VERIFIED | `scripts/rag_eval.py` exists with build-kbs/run; `tests/fixtures/rag/control_set.json` frozen, 10 questions (Q09, Q10 out_of_corpus), `eval_out/day22/` contains raw/, retrieval.md, answers.md/csv, run_meta.json; `Day22_report.md` (102 lines) lists the 10 questions with expected answer and sources, a no-RAG vs RAG verdict table, hit@k nomic vs bge-m3 (0.75/0.88/0.88 vs 0.12/0.25/0.25) and the giga rationale |

**Score:** 4/4

## Requirements Coverage

| Requirement | Status | Evidence |
|-------------|--------|----------|
| RAG-01 | SATISFIED | ChatRagConfig + REST routes + header controls/badge (14-01, 14-03, 14-06) |
| RAG-02 | SATISFIED | Per-KB embedder search via `search_kb`, last-user-message merge on outbound copy only, no chunk text persisted |
| RAG-03 | SATISFIED | `rag_budget` (30% / free space) applied after strategy; `merge` never touches stored messages |
| RAG-04 | SATISFIED | `RagFailure` mapping + fail-soft `prepare_rag_turn`; warning in payload, UI warning line |
| RAG-05 | SATISFIED | `rag_sources` column on Message, `MessageResponse.rag_sources`, "Источники" block in app.js, snippet route |
| RAG-06 | SATISFIED | Frozen fixture with expected answers/sources, `tests/test_rag_fixture.py` |
| RAG-07 | SATISFIED | `scripts/rag_eval.py`, `tests/test_rag_eval.py`, live outputs in `eval_out/day22` |
| RAG-08 | SATISFIED | `Day22_report.md`, `tests/test_rag_report.py` |

All 8 IDs appear in PLAN frontmatter and ROADMAP and in REQUIREMENTS.md; no orphaned requirements. Note for orchestrator: REQUIREMENTS.md still shows RAG-01..08 unchecked / "Pending" in the traceability table; update on phase completion.

## Behavioral Spot-Checks

Ran `pytest` on tests/test_rag.py, test_rag_turn.py, test_rag_ws.py, test_rag_api.py, test_rag_eval.py, test_rag_fixture.py, test_rag_report.py, test_rag_static.py: **112 passed**. The full suite and live LM Studio runs were not re-executed by the verifier (SUMMARY reports 1469 passed, 1 skipped, and the E2E 12 PASS). `tests/test_supervisor.py::test_agent_restarts_within_5_seconds` was reported failing in a worktree in an early plan; unrelated to this phase and later full runs passed.

## Probe Execution

No `probe-*.sh` declared; SKIPPED.

## Anti-Patterns

No TODO/FIXME/TBD/XXX markers in agent/rag.py, rag_turn.py, rag_api.py, scripts/rag_eval.py, Day22_report.md.

## Advisory Review Warnings (14-REVIEW.md, non-blocking, not gaps)

- WR-01 (app.js chat-switch race in saveChatRag/loadChatRag): confirm-worthy UI robustness issue; does not break SC1 in the single-chat path.
- WR-02 (`_neutralize` not applied to source/section header): real prompt-injection hardening gap in `agent/rag.py:render_rag_block`; recommend fix.
- WR-03 (`merge_rag_block` can silently no-op while sources are recorded): edge case; user message always exists in current flow.
- WR-04 / WR-04b (PUT coerces rag+null KB to off; broad exception in pre-step with shared session): hardening items.
- IN-01..03: minor.
Recommend running `/bm:code-review-fix` for WR-01 to WR-03 before or in Phase 15.

## Deviation Noted

D-12 ordering: budget is computed after `build_llm_context` rather than before the strategy. This is a documented stricter implementation that satisfies RAG-03's intent (rationale in 14-04-SUMMARY and docs/ARCHITECTURE.md).

## Human Verification Required

None outstanding: browser behavior was covered by the Playwright E2E on the isolated copy and verdicts/report were user-approved during plan 14-07.

## Gaps Summary

No blocking gaps. All 4 roadmap success criteria and 8 requirements are supported by code, tests and artifacts.

---

_Verified: 2026-10-03_
_Verifier: Claude (gsd-verifier)_
