---
phase: 12-llm-providers-section-in-settings-day-21
verified: 2026-10-02T00:00:00Z
status: human_needed
score: 6/7 must-haves verified (PROV-07 live check unverifiable_runtime)
has_blocking_gaps: false
overrides_applied: 0
human_verification:
  - test: "RUN_LIVE_DEEPSEEK=1 pytest tests/test_live_deepseek_title.py -q -rs, with a real DEEPSEEK_API_KEY in .env"
    expected: "HTTP 200 from api.deepseek.com, finish_reason stop, non-empty short title, via the new provider routing"
    why_human: "Paid external call; .env holds only a placeholder key. The test was SKIPPED. REQUIREMENTS.md and the ROADMAP already mark PROV-07 complete, which is premature."
---

# Phase 12: LLM providers section in Settings: Verification Report

**Phase Goal:** a "Провайдеры LLM" section in Settings (like "MCP серверы"), with a "+ Добавить провайдера" button and the added providers listed below. After save, a connection check shows a success or error indicator. Provider LLMs appear in the picker, prefixed with the provider name.
**Status:** human_needed
**Re-verification:** No, initial verification

The ROADMAP defines no Success Criteria list, so truths were derived from the goal and PROV-01..07.

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | PROV-01 CRUD of providers, user-scoped, unique names, key value never stored | VERIFIED | `LlmProvider` in shared/models.py has `UniqueConstraint("user_id","name")`. `providers_router` is included in agent/main.py:417. tests/test_llm_providers_api.py and test_llm_providers_config.py pass (cascade on user delete included). |
| 2 | PROV-02 LM Studio and DeepSeek seeded, idempotent, a deleted seed is not re-created | VERIFIED | `ensure_seeded` and the `LlmProviderSeed` marker table. test_llm_providers_service.py passes. |
| 3 | PROV-03 Connection check after save and on the button, with a classified Russian error | VERIFIED | `check_provider` in providers.py. API and service tests pass. The Settings section is in index.html:364. The browser UAT in 12-06 (S1-S10) is reported to have passed. |
| 4 | PROV-04 Picker grouped by provider (`optgroup`), "Provider · model" entries, disabled or failing providers excluded | VERIFIED | `optgroup` rendering in app.js:1465 (header picker) and :2556 (scheduler select). The model cache lives in providers.py. |
| 5 | PROV-05 All LLM calls routed via `provider_id`, with a fallback to LM Studio when it is missing | VERIFIED | `resolve_client` is used in ws.py:717, titles.py:161, context_engine.py:632 and headless.py:243. `ScheduledTask.provider_id` exists. test_llm_providers_routing.py and test_scheduler_providers.py pass. |
| 6 | PROV-06 A deleted or disabled provider gives `PROVIDER_UNAVAILABLE` in chat and a `failed` scheduled run, with no fallback | VERIFIED | ws.py:725 sends the error code. headless.py:44/246 holds the Russian message. Routing tests pass. See advisory WR-02. |
| 7 | PROV-07 The DeepSeek title request is verified live through the new routing | UNCERTAIN (`unverifiable_runtime`) | The code path and the opt-in test exist (tests/test_live_deepseek_title.py). The run was skipped because there is no real key. Not verified. |

**Score:** 6/7 verified. Targeted run: `pytest` over the 6 provider test files gave 109 passed, 1 skipped (the live test). 12-06-SUMMARY reports a full suite of 1148 passed, 1 skipped. I did not re-run the full suite.

### Requirements Coverage

All 7 IDs (PROV-01..07) appear in the ROADMAP and REQUIREMENTS.md, and there are no orphaned IDs. PROV-01..06 are SATISFIED. PROV-07 is NEEDS HUMAN. REQUIREMENTS.md marks PROV-07 `[x]` and "Complete" and backlog 999.11 says "Status: code path delivered", so the closure must wait for the live run.

### Anti-Patterns
No TBD, FIXME or XXX markers in agent/providers.py or agent/providers_api.py. The code review (12-REVIEW.md) found 0 blockers and 6 advisory warnings.

- **WR-01:** secret exfiltration via a user-controlled base URL combined with any `.env` variable name.
- **WR-02:** SQLite rowid reuse can re-target scheduled tasks and chats to a new provider after a delete. This weakens the PROV-06 "no fallback" guarantee in an edge case.
- **WR-03:** `_lm_studio_for(None)` ignores the user's seeded LM Studio row.
- **WR-04:** blocking `.env` read on every secret resolution.
- **WR-05:** stale model cache after an update or delete.
- **WR-06:** model refetch on every selection can finish out of order.

None blocks the phase goal, but WR-01 and WR-02 are worth fixing before the phase is treated as final.

### Human Verification Required

1. **Live DeepSeek title check (PROV-07)**
   - **Test:** Set a real `DEEPSEEK_API_KEY` in `.env`, then run `RUN_LIVE_DEEPSEEK=1 pytest tests/test_live_deepseek_title.py -q -rs`.
   - **Expected:** HTTP 200, finish_reason `stop`, and a non-empty short title.
   - **Why human:** It is a paid external call and needs a real credential.

### Gaps Summary

No blocking gaps. Phase 12 delivers PROV-01..06 in the code, with passing tests. PROV-07 needs the live run. After it passes, REQUIREMENTS.md and the backlog 999.11 status can honestly be closed.

---

_Verified: 2026-10-02_
_Verifier: Claude (bm-verifier)_
