---
phase: 27-ai-release-pipeline-day-35
plan: 04
subsystem: agent
tags: [release, mcp, tool-cards, state-machine]
requires: [27-01, 27-02]
provides:
  - agent/tool_frames.py (preview, tool_call_frame)
  - agent/release_mcp.py (ReleaseMcp, resolve_release_rows, ReleaseRows, ReleaseMcpError)
  - agent/release_executor.py (confirm_plan, UNAVAILABLE_MARKERS)
affects: [27-06]
key-files:
  created:
    - agent/tool_frames.py
    - agent/release_mcp.py
    - agent/release_executor.py
    - tests/test_release_mcp.py
    - tests/test_release_executor.py
  modified: []
requirements-completed: [D-02, D-03, D-04, D-05, D-08, D-09, D-13, D-14, D-20, D-21]
duration: ~25 min
completed: 2026-10-17
---

# Phase 27 Plan 04: Release confirm executor and MCP caller Summary

Confirm side of `/release`: a `ReleaseMcp` caller that renders every code-initiated MCP call as an ordinary tool-card frame, and `confirm_plan`, a resumable commit -> tag -> push -> draft Release state machine with re-validation before any write.

## Tasks

| Task | Commits |
|------|---------|
| 1. tool_frames + ReleaseMcp | 399d117 (RED), 7b01846 (GREEN) |
| 2. confirm_plan | d2ebd14 (RED), bf80d0b (GREEN) |

## Verification (actually run)

- `pytest tests/test_release_executor.py tests/test_release_mcp.py -n0`: 26 passed (8 MCP tests against the real `project_git` server on a temp repo; 18 executor cases).
- `pytest tests/test_mcp_release_tools.py tests/test_release_command.py tests/test_release_pipeline.py -n0`: 101 passed.
- Acceptance greps: no logger call carries arguments/text; no delete/rollback/--force words in `release_executor.py` (count 0).

## Deviations from Plan

**1. [Rule 3 - Blocking] Worktree base correction.** HEAD started at 03c151a, not the expected base; reset to 58bfabe per the worktree check (wave 1 was absent otherwise).

**2. Style note.** The new MCP error branch in `ReleaseMcp._run` returns early for `McpConnectionStatus` imported from `agent.schemas` (as `review_github.py` does), not from a shared module.

Otherwise the plan was executed as written. `agent/ws.py` is untouched; a test asserts `tool_call_frame` equals `ws._tool_call_frame`, so 27-06 can switch the import safely.

## Notes for downstream plans

- A Release step answer that is ok but lacks a valid GitHub release `html_url` is recorded as FAILED (never done), per the threat register.
- Blocked results send a `release_result` with outcome `blocked` and the reason (dirty paths listed after MSG_DIRTY_TREE).
- `confirm_plan` sends `notice_frame("release", MSG_CONFIRM_RUNNING)` when `plan.running`; the caller must still hold the per-chat lock (27-06).

## Known Stubs

None.

## Self-Check: PASSED

Files and commits (399d117, 7b01846, d2ebd14, bf80d0b) verified present.
