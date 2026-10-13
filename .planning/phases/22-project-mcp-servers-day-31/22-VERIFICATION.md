---
phase: 22-project-mcp-servers-day-31
verified: 2026-10-13T00:00:00Z
status: passed
score: 4/4 roadmap success criteria verified (SC3 live-model half closed by UAT on 2026-10-13)
has_blocking_gaps: false
overrides_applied: 0
gaps: []
human_verification:
  - test: "PGIT-04: run `python scripts/e2e_mcp_git_playwright.py` with LM Studio running and a chat model loaded (ports 18000/18001 free)"
    expected: "RESULT: PASS. The user adds local-git in Settings, asks which branch the project is on, a tool-call card mcp__local-git__git_current_branch is visible, the answer names the checked-out branch; after a branch switch in the temporary repo the second answer follows"
    why_human: "Needs a real local model; the run was blocked in preflight (LM Studio ConnectError). Recorded as 'not run' in 22-CHECKS.md"
  - test: "Optional: add github server in Settings with a real token and confirm the token shows as GITHUB_PERSONAL_ACCESS_TOKEN=••• after saving"
    expected: "Connects, lists 16 read-only tools, token masked in the form"
    why_human: "Visual UI check. API-level masking is covered by an always-on test, so this is confirmation only"
warnings:
  - "WR-01 (22-REVIEW): a git output above the 4 MiB hard cap becomes a tool error instead of a truncated result; affects only extreme output, not the documented truncation SC"
  - "WR-02: if --root is a subfolder of a larger repo, omitting `path` shows changes outside the root; documented use (root = repo top) is unaffected"
  - "WR-03: tracked secrets in diffs are visible to the model; a policy decision, no must-have requires filtering"
  - "WR-04: test_mcp_real_github.py has a developer-specific default binary path; skip-guarded, harmless in CI"
---

# Phase 22: Project MCP servers (Day 31) Verification Report

**Phase Goal:** The assistant can read the live state of the project's working copy through MCP, and the GitHub server is connected as the base for Pull Request work
**Verified:** 2026-10-13
**Status:** human_needed
**Re-verification:** No, initial verification

Evidence file for recorded checks: `22-CHECKS.md` (referenced, as planned in 22-05).

## Observable Truths (ROADMAP success criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `mcp_list_tools.py` against the new server lists the 5 read-only tools | VERIFIED | Re-ran in this verification: `Server: local-git`, `Tools (5)`, git_current_branch, git_status, git_diff, git_tracked_files, git_recent_commits (`mcp_servers/project_git.py`, 660 lines, five tool functions at lines 421-575) |
| 2 | Path outside root / `-`-argument rejected with clear error; long diff truncated with explicit marker | VERIFIED | Path gate, ref gate, timeout kill and truncation covered by tests (core + stdio). Re-ran `test_mcp_project_git_core.py`, `test_mcp_project_git.py`, docs and real-github tests: 187 passed, 2 skipped. Live call with `../x` returned a clear isError message. Review confirms no traversal or option-injection bypass. Caveat WR-01 (output > 4 MiB hard cap) is an edge case |
| 3 | User adds server in Settings per docs, asks which branch, gets the current branch with visible tool call | PARTIAL: UNCERTAIN (`unverifiable_runtime`) | Docs section "Сервер git проекта (local-git)" in `docs/USER_GUIDE.md:397` gives form values; `test_chat_dispatch_returns_branch` proves the dispatch path returns the branch as `mcp__local-git__git_current_branch`. The browser run with a real model was NOT run (LM Studio unavailable); not marked passed |
| 4 | With documented config the app connects to github-mcp-server read-only, lists tools; token masked after saving | VERIFIED | 22-CHECKS.md: v2.0.2 binary, 16 tools, exact expected set, no write tools; 3 real-binary tests passed (connect, read call `list_branches`, token never returned by API). Masking test is always-on and passed in my re-run. Docs `USER_GUIDE.md:425-451` |

**Score:** 3 verified, 1 partially verified (live-model piece pending a human).

## Requirements Coverage

| Requirement | Plans | Status | Evidence |
|-------------|-------|--------|----------|
| PGIT-01 | 22-01, 22-03 | SATISFIED | Server with five tools, tool listing re-run |
| PGIT-02 | 22-01, 22-03 | SATISFIED | Gates, timeout, truncation marker tested (see WR-01 edge) |
| PGIT-03 | 22-03, 22-04, 22-05 | SATISFIED | Docs form values + docs guard test + chat-dispatch test |
| PGIT-04 | 22-05 | NEEDS HUMAN | Script exists (`scripts/e2e_mcp_git_playwright.py`) but never executed end to end; recorded "not run". REQUIREMENTS.md correctly still shows Pending and `[ ]` |
| GHUB-01 | 22-02, 22-04, 22-06 | SATISFIED | Docs section with command, args, toolsets, token variable; masking test; no token pattern in docs (guard test) |
| GHUB-02 | 22-02, 22-06 | SATISFIED | Recorded real-binary listing and pytest in 22-CHECKS.md |

All six requirement IDs from the PLAN frontmatter appear in REQUIREMENTS.md; no orphaned requirements for Phase 22.

## Anti-Patterns / Review

No TBD/FIXME blockers were introduced per the review (0 critical). Four warnings (WR-01..04) listed in frontmatter; none breaks a must-have, hence `has_blocking_gaps: false`. Recommended backlog items: WR-01 (cap handling) and WR-02 (toplevel == root check).

## Test Suite

Full suite (per 22-CHECKS.md): 2945 passed, 1 failed (`tests/test_kb_api.py::test_create_happy_path`, unrelated KB upload area, passes alone, leftover on-disk state), 11 skipped. Not blocking for this phase. Not re-run in full here.

## Human Verification Required

1. **PGIT-04 browser run on a real local model** (see frontmatter). Until `RESULT: PASS` is recorded, PGIT-04 and the live half of SC3 stay open.
2. Optional visual check of token mask in the Settings form.

## Gaps Summary

No code gaps. The only open item is the live-model PGIT-04 check, which cannot be done without LM Studio; status is therefore `human_needed`, not `passed`.

---

_Verified: 2026-10-13_
_Verifier: Claude (gsd-verifier)_

## UAT Follow-up (2026-10-13, `/bm:verify-work 22`)

Both human verification items were run and passed; see `22-HUMAN-UAT.md` and `22-CHECKS.md`.

1. PGIT-04: `python scripts/e2e_mcp_git_playwright.py` returned `RESULT: PASS (attempt 1 of 2, model qwen/qwen3.5-9b)` after a timing fix in the check script itself (it read the model picker before the page had filled it). SC3 is now fully verified and PGIT-04 is marked complete in REQUIREMENTS.md.
2. Token mask: with the real binary and token the Settings form showed `GITHUB_PERSONAL_ACCESS_TOKEN=•••`; the value did not appear in the DOM, frontend state, API responses or the app log.

Status changed from `human_needed` to `passed`. Warnings WR-01..WR-04 and the unrelated `tests/test_kb_api.py::test_create_happy_path` full-suite failure remain open as noted above.
