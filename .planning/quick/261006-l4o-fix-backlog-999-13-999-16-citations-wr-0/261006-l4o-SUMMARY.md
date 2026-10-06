# Quick 261006-l4o: Fix backlog 999.13-999.16 (citations WR-01..WR-04) Summary

Strict-RAG citations hardened: short quotes can no longer be exact, auto-picked quotes carry their own state and a neutral chip, quote parsing no longer leaks or inflates refs, and the gated reply path has rollback and an error frame.

## Commits
- 82060f4: rag_cite hardening (EXACT_MIN_CHARS=25, STATE_AUTO, parse_tail cap swallow, quotes-only answer, assert removed)
- 26cf234: neutral «подобрана автоматически» chip (app.js), static test, e2e whitelist includes "auto"
- 44e986b: `_complete_gated_turn` persist-first with rollback, user-message cleanup, `RAG_GATED_FAILED` error frame, active_streams registration; D-15 comment on strict default in shared/database.py

## Verification (actually run)
- tests/test_rag_cite.py + test_rag_turn.py + test_rag_eval_cite.py: 115 passed
- tests/test_rag_cite_static.py: 33 passed; e2e script parses (not run, needs live app)
- tests/test_rag_ws.py: 21 passed (new failure test was red before the change)
- Full `pytest tests/ -q`: 1879 passed, 11 skipped

## Deviations
- Task 1 RED phase was a collection ImportError (STATE_AUTO missing) rather than per-test assertion failures; tests then went green after implementation. No separate test commit (plan asked for one commit per task).
- Gated turn now sends `token` after persist (previously before), so a failed store never leaves a streamed reply on the client. Frame order on success unchanged (token, done).
- `.planning/HANDOFF.json` shows as modified in the worktree (line-ending noise); not staged.

## Known Stubs
None.

## Self-Check: PASSED
Commits 82060f4, 26cf234, 44e986b exist; modified files present.
