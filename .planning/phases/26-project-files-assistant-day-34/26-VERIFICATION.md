---
phase: 26-project-files-assistant-day-34
verified: 2026-10-17T00:00:00Z
status: gaps_found
score: 9/10 must-haves verified
has_blocking_gaps: true
overrides_applied: 0
gaps:
  - truth: "Read and search see only git-visible files; ignored files (.env, *.db) never appear in read_file/search_text/find_usages hits and paths stay inside the root (26-01 truth, D-07, PFILE-01, threat T-26-01/T-26-02)"
    status: failed
    severity: blocking
    reason: "find_usages opens files from visible_paths() with os.path.join(root, item) and no resolve_inside_root / realpath check (mcp_servers/project_git.py ~L929-946). A git-visible symlink named x.py pointing at an ignored .env or a file outside the root is opened and parsed, and matching source lines are returned as snippets (KEY=\"sk-...\" is valid Python). read_file/write_file/edit_file resolve through realpath; find_usages does not. Confirmed by code reading (26-REVIEW CR-01). Could not be reproduced live: symlink creation is denied on this Windows account (WinError 1314), and no test covers a read-side symlink."
    artifacts:
      - path: "mcp_servers/project_git.py"
        issue: "find_usages loop lacks containment/visibility check on the symlink target"
      - path: "tests/test_mcp_project_files.py"
        issue: "only write-path symlink test exists; no find_usages/read symlink test"
    missing:
      - "In find_usages, resolve each item with resolve_inside_root and skip (count as skipped) if it escapes the root or its target is not itself git-visible (fix in 26-REVIEW CR-01)"
      - "Test: symlink to ignored .env and to an outside file, find_usages must not return their lines (skip when os.symlink unavailable)"
---

# Phase 26: Project files assistant (Day 34) Verification Report

**Phase Goal:** An assistant that works with project files on its own: given a goal, it reads, searches across several files, analyses them and creates or changes files, with changes saved or shown as a diff and the run reproducible. Scenarios: find every usage of a component/API, and generate a new file (README / ADR / changelog).
**Verified:** 2026-10-17
**Status:** gaps_found
**Re-verification:** No, initial verification

ROADMAP has no explicit Success Criteria list for this phase; must-haves are derived from the goal, the ten PFILE requirements and the plan truths.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | PFILE-01 read tools `read_file`/`search_text`/`find_usages` over git-visible files, never empty, capped, truncation marker | PARTIAL, see gap | Tools exist in `mcp_servers/project_git.py` (1241 lines); tests in `tests/test_mcp_project_files.py` pass. `find_usages` symlink containment hole (CR-01) breaks the "ignored files never read" invariant. |
| 2 | PFILE-02 write tools registered only with `--allow-write`; direct call refused | VERIFIED | `require_write()` at L991, called by `write_file` L1123 and `edit_file` L1157; registration conditional on `args.allow_write` L1234; gate tests pass. |
| 3 | PFILE-03 writes save immediately and return unified diff; `edit_file` exactly-once | VERIFIED | Code at L1123-1200; diff/edit tests pass; real ADR run produced an all-added `run.patch`. |
| 4 | PFILE-04 forbidden write targets refused with reason and fix | VERIFIED | `check_write_target` covers outside root, `.git` (incl. aliases), `.env*`, ignored, device names, binary, size; parametrized tests pass (325 passed in the phase test set). Minor: IN-02 (trailing-space device name) not covered. |
| 5 | PFILE-05 goal as ordinary chat message drives tools in the existing loop with visible cards; answer lists changed files | VERIFIED (capability); model compliance is partial | No new command; e2e on isolated copy shows write card and file created (`eval_out/day34/e2e.md` PASS). Tool descriptions instruct listing changed files. The local 9B model failed to list files in 3 of 4 scenarios (documented in the report); this is model quality, not an implementation gap. |
| 6 | PFILE-06 coloured diff in tool-call cards via createElement/textContent | VERIFIED | `tests/test_diff_card_ui.py` passes; e2e checks classes, `+2 -2` summary and `<img onerror>` inertness. Weakness IN-05: UI tests are source greps. |
| 7 | PFILE-07 `scripts/files_task.py`: HTTP endpoint, dirty-root/own-repo refusal, temperature 0, run.json + .patch | VERIFIED | Script and `tests/test_files_task.py` pass against a mocked endpoint. WR-06 miscount of `---`/`+++`-looking content lines is a warning. |
| 8 | PFILE-08 pytest coverage plus isolated-copy e2e script | VERIFIED | `scripts/e2e_project_files_playwright.py` exists, e2e record PASS on 18000/18001; phase tests pass (325 passed, 1 skipped). WR-04/WR-05 are script defects (exit-code masking, dead teardown loop). |
| 9 | PFILE-09 `Day34_report.md` (Russian): four scenarios on local model, logs and patches, failures visible | VERIFIED | Report and `eval_out/day34/{usages,adr,changelog,readme}/` each have run.json, run.patch, answer.md. Report states honestly that no scenario passed all automatic checks, changelog and readme failed in substance. The requirement demands honest recording, not success, so it is met. |
| 10 | PFILE-10 docs describe tools, gate, diff card, scripts; guarded | VERIFIED | `tests/test_docs_mcp_servers.py` passes within the run; Day34_report and docs updated by commits 81098c9, d242e5f. |

**Score:** 9/10 truths verified.

On the 26-07 substance failures: changelog and readme were not created by the model (it stopped after reading, fallback answer text). Nothing in PFILE-09 or the 26-07 plan requires these to succeed; they require honest recording, and the ADR scenario demonstrates the "generate a new file" half of the goal end to end (new `docs/adr/001-...md`, +319 -0, with diff). Reported as a warning, not a gap.

### Requirements Coverage

| Requirement | Source Plan(s) | Status | Evidence |
|-------------|----------------|--------|----------|
| PFILE-01 | 26-01 | PARTIAL (gap, CR-01) | see truth 1 |
| PFILE-02 | 26-04 | SATISFIED | truth 2 |
| PFILE-03 | 26-04 | SATISFIED | truth 3 |
| PFILE-04 | 26-04 | SATISFIED | truth 4 |
| PFILE-05 | 26-04, 26-05, 26-07 | SATISFIED | truth 5 |
| PFILE-06 | 26-02, 26-05 | SATISFIED | truth 6 |
| PFILE-07 | 26-03, 26-06 | SATISFIED | truth 7 |
| PFILE-08 | 26-01..26-06 | SATISFIED | truth 8 |
| PFILE-09 | 26-06, 26-07 | SATISFIED | truth 9 |
| PFILE-10 | 26-01, 26-08 | SATISFIED | truth 10 |

All ten IDs appear in plan frontmatter and in REQUIREMENTS.md (marked Complete); no orphaned requirements. REQUIREMENTS.md says PFILE-01 Complete, which should be reopened until CR-01 is fixed.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase test files | `pytest tests/test_mcp_project_files.py tests/test_mcp_project_git.py tests/test_mcp_project_git_core.py tests/test_files_task.py tests/test_files_eval.py tests/test_diff_card_ui.py tests/test_docs_mcp_servers.py -n0` | 325 passed, 1 skipped | PASS |
| Symlink leak in find_usages | live repro | not possible (symlink privilege denied, WinError 1314) | SKIP; code inspection confirms missing check |

### Anti-Patterns Found

No TBD/FIXME/XXX markers in phase files. Open review warnings (not goal-blocking): WR-01 read tools rely only on .gitignore (no explicit `.env`/`.git` deny, inconsistent with write path), WR-02 `_write_atomic` loses file mode, WR-03 `files_eval` not-run records can overwrite earlier evidence, WR-04 e2e masks FAIL as NOT RUN, WR-05 dead teardown loop, WR-06 patch stats miscount; IN-01..IN-05.

### Human Verification Required

None mandatory beyond the user-recorded Day 34 video (deliverable, outside code verification).

### Gaps Summary

One blocking gap: CR-01. The 26-01 plan truth and D-07 state that ignored files never appear in `find_usages` hits and that paths stay inside the root. `find_usages` skips the realpath containment that every other tool uses, so a git-visible symlink leaks ignored or out-of-root content. It does not stop the assistant from working (the goal is functionally met), but it is a real violation of a stated security must-have with a small, well-specified fix. Recommend fixing CR-01 together with WR-01 (explicit `.env`/`.git` deny on the read path) and adding the symlink tests. If the owner judges the symlink scenario out of threat model for a local single-user tool, an override can be recorded:

```yaml
overrides:
  - must_have: "Read and search see only git-visible files; ignored files never appear in find_usages hits"
    reason: "symlinks to ignored files are out of the local single-user threat model"
    accepted_by: "{name}"
    accepted_at: "{ISO timestamp}"
```

---

_Verified: 2026-10-17_
_Verifier: Claude (bm-verifier)_
