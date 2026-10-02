---
phase: 10-modals-close-only-via-x-button-day-21
verified: 2026-10-03T00:00:00Z
status: human_needed
score: 6/6 must-haves verified
has_blocking_gaps: false
human_verification:
  - test: "In the browser, open each of the 4 modals (Settings, Add user, Scheduler create, Scheduler run result); click the dark backdrop, press Escape, then click x"
    expected: "Backdrop click and Escape leave the modal open; x closes it; Cancel and successful Save/Create still close it"
    why_human: "Real click/keyboard behavior in a browser cannot be confirmed by static grep and the source-guard test"
---

# Phase 10: Modals close only via x button - Verification Report

**Phase Goal:** a modal closes only when its x is clicked; clicking the backdrop must not close it
**Status:** human_needed
**Re-verification:** No

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Backdrop click leaves the 4 modals open | VERIFIED | No click/pointer listener on any `*-modal` element in app.js (grep). Comment at app.js:3126 documents the policy. Guard test passes. |
| 2 | x button closes each modal | VERIFIED | Bindings found: app.js:3084 (settings), :3090 (add-user), :2794 (scheduler-create), :2796 (scheduler-run). All four button ids exist in index.html. |
| 3 | Escape does not close any modal (A-01) | VERIFIED | The only keydown listeners are `#message-input` (:3075) and the llm-provider inputs (:3157, Enter only). No Escape handler anywhere. |
| 4 | Cancel buttons and post-submit close still work (A-02) | VERIFIED | Cancel bindings at app.js:2795, :3085, :3091. hideSchedulerModal is still called after create (:2766). |
| 5 | Unrelated handlers intact | VERIFIED | `#message-input` Enter keydown (:3075) and the `#chat-list` contextmenu confirm (:3128-3135) are present. Runtime absence of JS errors was not exercised. |
| 6 | Probe `pytest tests/test_modal_close_policy.py -q` exits 0 | VERIFIED | Ran it myself: 11 passed. |

**Score:** 6/6

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| MODAL-01 | 10-01 (plan frontmatter has `requirements: []`) | Modals close only via x; no backdrop or Escape close | SATISFIED in code | Truths 1-3. REQUIREMENTS.md still shows it as `[ ]` / "Pending" (line 11 and line 97). |

Note: the PLAN frontmatter declares `requirements: []` although ROADMAP maps MODAL-01 to Phase 10. This is a bookkeeping mismatch, not a code gap. The orchestrator should tick MODAL-01 in REQUIREMENTS.md. No orphaned requirements.

## Anti-Patterns

No TBD/FIXME/XXX markers in the phase test file. No backdrop or Escape closers remain in app.js.

## Human Verification Required

### 1. Browser check of the four modals
**Test:** Open Settings, Add user, Scheduler create and Scheduler run result. For each, click the backdrop, press Escape, then click x.
**Expected:** Backdrop and Escape do nothing. x closes the modal. Cancel and a successful submit still close it.
**Why human:** Real DOM event behavior is not confirmed by static analysis.

## Gaps Summary

No gaps. The code and the guard test confirm the goal. Only the runtime browser check remains.

_Verified: 2026-10-03_
_Verifier: Claude (gsd-verifier)_
