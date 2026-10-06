---
phase: quick-261006-mby
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - agent/tool_guard.py
  - agent/scheduler_tools.py
  - tests/test_scheduler_tools.py
  - tests/test_scheduler_providers.py
  - agent/schedule.py
  - tests/test_scheduler_schedule.py
  - agent/events.py
  - tests/test_scheduler_events.py
  - .planning/ROADMAP.md
autonomous: true
requirements: [BACKLOG-999.7, BACKLOG-999.8, BACKLOG-999.9]

must_haves:
  truths:
    - "schedule_task creates a job only when the chat's current leaf is the user's own message with un-negated, non-question scheduling intent; otherwise it returns code schedule_not_requested and stores nothing"
    - "cancel_scheduled_task cancels only when the latest user message asks to cancel (questions/hypotheticals rejected) AND names the target job by its numeric id or a significant title word"
    - "next_cron_run never returns an instant <= after_utc, including during the repeated hour of a DST fall-back night (fold=1)"
    - "Repeatedly feeding next_cron_run its own result across a fall-back night yields a strictly increasing sequence (no per-tick refire)"
    - "/ws/events closes with code 1008 within one recheck interval after the user's session row is deleted or expired"
    - "A binary frame on /ws/events closes the socket with 1003 instead of raising KeyError (no 1011)"
    - "ROADMAP backlog no longer lists 999.7, 999.8, 999.9; their phase dirs are deleted; 999.10 is untouched"
  artifacts:
    - path: "agent/tool_guard.py"
      provides: "user_asked_to_schedule, tightened user_asked_to_cancel, message_names_task"
      contains: "def user_asked_to_schedule"
    - path: "agent/scheduler_tools.py"
      provides: "schedule intent gate + cancel bound to task id/title"
      contains: "schedule_not_requested"
    - path: "agent/schedule.py"
      provides: "fold-safe strictly-monotonic next_cron_run"
    - path: "agent/events.py"
      provides: "periodic session re-validation and non-text frame handling"
      contains: "EVENTS_SESSION_RECHECK_SECONDS"
  key_links:
    - from: "agent/scheduler_tools.py::_schedule_task"
      to: "agent/tool_guard.py::user_asked_to_schedule"
      via: "latest-user-leaf check before scheduler_ops.create_scheduled_task"
      pattern: "user_asked_to_schedule"
    - from: "agent/scheduler_tools.py::_cancel_scheduled_task"
      to: "agent/tool_guard.py::message_names_task"
      via: "binding check with the loaded job's id and title"
      pattern: "message_names_task"
    - from: "agent/events.py::ws_events"
      to: "shared.models.Session"
      via: "non-refreshing session re-check on receive/timeout"
      pattern: "_session_still_valid"
---

<objective>
Close backlog items 999.7 (WR-05/WR-06), 999.8 (WR-04) and 999.9 (WR-07 + IN-05) from the phase 08 scheduler code review, with tests for each, then remove those three backlog entries.

Purpose: stop prompt-injected unattended jobs and over-broad cancels, stop the DST fall-back refire loop, and stop /ws/events streaming per-user data after logout/expiry.
Output: hardened agent/tool_guard.py + agent/scheduler_tools.py, fold-safe agent/schedule.py, re-validating agent/events.py, new tests, cleaned ROADMAP backlog.
</objective>

<execution_context>
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/workflows/execute-plan.md
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/milestones/v2.0-phases/08-scheduler-day-18/08-REVIEW.md
@agent/tool_guard.py
@agent/scheduler_tools.py
@agent/schedule.py
@agent/events.py
@tests/test_scheduler_tools.py
@tests/test_scheduler_schedule.py
@tests/test_scheduler_events.py

<interfaces>
From agent/tool_guard.py (existing, lines ~110-127):
- `_CANCEL_INTENT_RE` (RU/EN cancel verbs, word-bounded), `_NEGATION_WORDS` frozenset, `_NEGATION_WINDOW_WORDS = 2`, `_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")`
- `def user_asked_to_cancel(text: str) -> bool` — currently: any cancel-verb match not preceded (2 words) by a negation word.

From agent/scheduler_tools.py:
- `_error(code, message) -> dict`, `_MSG_CANCEL_NOT_REQUESTED`
- `async def _latest_message_asks_to_cancel(session, user_id, chat_id) -> bool` — loads Chat, checks owner, loads leaf Message, requires role == "user".
- `_schedule_task(session, user_id, chat_id, args)` — ungated today; `_cancel_scheduled_task(...)` — gate then `scheduler_ops.cancel_task(session, user_id, task_id)` which raises SchedulerNotFoundError / SchedulerConflictError.
- headless runs do NOT expose scheduler tools (`HEADLESS_TOOL_ALLOWLIST = {"save_long_term_memory"}` in agent/headless.py) — the gate only has to handle interactive chat turns.

From agent/schedule.py:
- `_to_local_naive(value_utc, tz) -> datetime` (astimezone(tz).replace(tzinfo=None); keeps fold attr, but CronSim ignores it)
- `_from_local_naive(naive, tz) -> datetime` (tz None -> naive.astimezone(); else naive.replace(tzinfo=tz); both honour `fold` per PEP 495)
- `next_cron_run(expr, after_utc, tz=None) -> datetime` (UTC-aware); maps CronSimError/StopIteration/ValueError to ScheduleValidationError(MSG_CRON_INVALID). Used by scheduler claim logic, so fixing it here fixes next_run_after_claim too.

From agent/events.py:
- `EVENTS_RECEIVE_TIMEOUT_SECONDS = 60.0`; `ws_events(websocket)` loop: `asyncio.wait_for(websocket.receive_text(), timeout=...)`, `continue` on timeout; finally unsubscribes + cancels pump.
- `get_current_user_ws(session_id, db)` in agent/dependencies.py SLIDES the session expiry (writes expires_at) — do NOT reuse it for the periodic re-check, or an open tab would keep the session alive forever.
- `shared.models.Session` (imported as SessionRow elsewhere): `token_hash`, `user_id`, `expires_at`; `shared.auth.hash_session_token(token)`.

Environment note: the Windows dev box has NO tzdata package (`ZoneInfo("America/New_York")` raises). DST tests must use a hand-written PEP 495 tzinfo, not zoneinfo.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1 (999.7): intent gate for schedule_task, cancel bound to the named job</name>
  <files>agent/tool_guard.py, agent/scheduler_tools.py, tests/test_scheduler_tools.py, tests/test_scheduler_providers.py</files>
  <behavior>
    - user_asked_to_schedule True: "напомни через 5 минут проверить почту", "запланируй отчёт каждый день в 9:00", "каждые 10 минут проверяй сервер", "run p in a minute", "remind me tomorrow at 9", "schedule a daily report", "every hour check the build", "Можешь напомнить через час?"
    - user_asked_to_schedule False: "", "покажи мои задания", "не напоминай мне", "don't schedule anything", "How do I schedule a job?", "прочитай файл", text whose only scheduling word sits inside a tool-result-like quote is out of scope (gate reads only the user's leaf message)
    - user_asked_to_cancel keeps every existing True/False parametrize case passing (including "Не могли бы вы отменить задание?") and newly returns False for "How do I cancel a job?", "should I stop the 5 min job?", "а если удалить задание?"
    - message_names_task("отмени задание 3", 3, "job") True; ("отмени задание 13", 3, "job") False; ("cancel the report job", 7, "Daily report") True; ("отмени отчёт", 7, "Отчет по продажам") True (ё/е and inflection via stem); ("отмени задание", 7, "Задание") False (generic words never bind); ("cancel job 5", 7, "Weather check") False
    - Dispatch: schedule_task with no leaf / assistant leaf / "покажи задания" leaf -> ok False, code schedule_not_requested, zero ScheduledTask rows; with leaf "напомни через минуту" -> created as before
    - Dispatch: cancel with leaf "отмени задание {other_id}" leaves job ACTIVE (cancel_not_requested); leaf "отмени задание {job.id}" or naming a title word cancels; foreign/missing id still not_found
  </behavior>
  <action>
In agent/tool_guard.py (per WR-05/WR-06 fix guidance):
1. Add `_POLITE_REQUEST_RE` matching a sentence START of polite-request forms: не могли бы|могли бы|можешь|можете|можно|could you|can you|would you|will you|please|пожалуйста (case-insensitive, after stripping). Add private helper `_sentence_requests(text: str, pattern: re.Pattern[str]) -> bool`: split text with `_SENTENCE_SPLIT_RE`; for each stripped sentence, for each `pattern` match apply the existing 2-word `_NEGATION_WORDS` window (move the current logic from user_asked_to_cancel into this helper); a non-negated match counts only if the sentence does not end with "?" OR the sentence starts with a polite-request form. Return True on the first counting match.
2. Rewrite `user_asked_to_cancel(text)` as `_sentence_requests(text, _CANCEL_INTENT_RE)` (same signature, docstring updated: questions/hypotheticals rejected unless phrased as a polite request).
3. Add `_SCHEDULE_INTENT_RE` (word-bounded, IGNORECASE) covering RU: напомни(те)?|напомнить|запланируй(те)?|запланировать|по расписанию|расписани\w*|кажд(ый|ую|ое|ые|ого)|ежедневно|ежечасно|еженедельно|через\s+(\d+|минуту|час|день|неделю|полчаса)|завтра|послезавтра|в\s+\d{1,2}[:.]\d{2}; EN: remind|schedule|scheduled|every|daily|hourly|weekly|tomorrow|later|in\s+(a|an|\d+)\s+(sec|second|min|minute|hour|day|week)s?|at\s+\d{1,2}(:\d{2})?\s*(am|pm)?|cron. Add `def user_asked_to_schedule(text: str) -> bool` = `_sentence_requests(text, _SCHEDULE_INTENT_RE)`. "run p in a minute" (existing ws test) MUST match.
4. Add `def message_names_task(text: str, task_id: int, title: str) -> bool`: normalize text and title with casefold + "ё"->"е". True if the id appears as a standalone number (regex `(?<!\d)ID(?!\d)`). Otherwise tokenize both with `[\w]+`; title tokens of length >= 4 whose 5-char (or full, if shorter) stem is NOT in a generic-stem set {"зада", "задач", "задан", "task", "tasks", "job", "jobs", "sched", "распи", "напом", "remin"} (compare by stem prefix) are significant; True if any message token startswith a significant title token's stem. Docstring: binds the cancel to what the user named; residual risk = a message naming several jobs authorises each of them.

In agent/scheduler_tools.py:
5. Generalise `_latest_message_asks_to_cancel` into `async def _latest_user_message(session, user_id, chat_id) -> str | None` (same checks: chat exists, chat.user_id == user_id, leaf exists, leaf.role == "user"; returns content or None).
6. `_schedule_task`: before the model check, `text = await _latest_user_message(...)`; if text is None or not user_asked_to_schedule(text): `logger.warning("scheduler_schedule_gate_blocked", user_id=user_id, chat_id=chat_id)` and return `_error("schedule_not_requested", _MSG_SCHEDULE_NOT_REQUESTED)` where the English message says the user did not ask to schedule anything in their latest message; do not create a job; ask the user to confirm explicitly. Update the tool description to say it is refused unless the user's LATEST message explicitly asks to do something later/periodically.
7. `_cancel_scheduled_task`: first load the job with `await session.get(ScheduledTask, task_id)`; if None or `row.user_id != user_id` return the existing not_found error (no title leak). Then gate: flag true AND text not None AND user_asked_to_cancel(text) AND message_names_task(text, task_id, row.title); else log `scheduler_cancel_gate_blocked` and return cancel_not_requested with `_MSG_CANCEL_NOT_REQUESTED` extended to "...the user must name the job by its id or title". Then call scheduler_ops.cancel_task as today. Update tool description: the user's latest message must name the job (id or title).

Tests (write first, RED then GREEN):
8. tests/test_scheduler_tools.py: add parametrized tests for user_asked_to_schedule (True/False lists above), extra False cases for user_asked_to_cancel, and message_names_task cases. Update existing helpers/tests that dispatch schedule_task to seed `_seed_chat(user_id, "напомни через минуту")` (they currently seed no leaf and would now be blocked). Update existing cancel-success tests to seed text containing the job id (seed the chat AFTER `_make_job`, e.g. f"отмени задание {job.id}"); keep the blocked-cancel tests meaningful. Add new dispatch tests: schedule blocked without intent (no leaf, assistant leaf, "покажи задания") -> code schedule_not_requested and no rows; cancel blocked when message names a different id; cancel succeeds when message names a title word.
9. tests/test_scheduler_providers.py `test_schedule_task_tool_inherits_chat_provider` (~line 134): make the chat's leaf a user message with scheduling intent so it still passes.
  </action>
  <verify>
    <automated>pytest tests/test_scheduler_tools.py tests/test_scheduler_providers.py tests/test_tool_guard.py tests/test_tool_guard_ws.py -q</automated>
  </verify>
  <done>New gate/binding tests pass, all pre-existing scheduler tool, provider and tool_guard tests pass; schedule_task cannot create a job without a scheduling-intent user leaf; cancel requires the job id or a significant title word in the latest user message.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2 (999.8): fold-safe, strictly monotonic next_cron_run</name>
  <files>agent/schedule.py, tests/test_scheduler_schedule.py</files>
  <behavior>
    - Test tz `_FallBackZone` (hand-written PEP 495 tzinfo, no zoneinfo): EDT -4h before the transition, EST -5h after; fall-back at 2026-11-01 06:00 UTC (local 02:00 EDT -> 01:00 EST), so local 01:00-01:59 occurs twice (fold=0 is EDT = 05:xx UTC, fold=1 is EST = 06:xx UTC)
    - next_cron_run("50 1 * * *", 2026-11-01 05:45 UTC, tz) == 2026-11-01 05:50 UTC (first pass)
    - next_cron_run("50 1 * * *", 2026-11-01 06:45 UTC, tz) == 2026-11-01 06:50 UTC (second pass, fold=1) — today returns 05:50, which is in the past
    - next_cron_run("*/5 * * * *", 2026-11-01 06:45 UTC, tz) == 06:50 UTC
    - Chained: start at 2026-11-01 04:00 UTC with "*/5 * * * *", feed each result back 60 times: every result > its input, and no result is <= the previous one
    - Existing MSK tests and test_next_cron_run_at_datetime_edge_is_validation_error still pass
  </behavior>
  <action>
In tests/test_scheduler_schedule.py add a module-level `class _FallBackZone(tzinfo)` with constants for the transition: `utcoffset(dt)` uses the naive wall time (dt.replace(tzinfo=None)): before 2026-11-01 01:00 -> -4h; in [01:00, 02:00) -> -4h if dt.fold == 0 else -5h; otherwise -5h. `dst()` returns 1h/0 correspondingly, `tzname()` "EDT"/"EST". Override `fromutc(dt)`: utc naive = dt.replace(tzinfo=None); if utc < 2026-11-01 06:00 return (utc - 4h) with tzinfo=self, fold=0; else local = utc - 5h, and set fold=1 when local wall time is in [01:00, 02:00) on 2026-11-01. Write the behavior tests above (RED: second-pass and chained tests fail today).

In agent/schedule.py (per WR-04): rewrite `next_cron_run` so its result is strictly after `as_aware_utc(after_utc)`:
- `after = as_aware_utc(after_utc)`, `local_cursor = _to_local_naive(after_utc, tz)`.
- Loop up to a module constant `_CRON_MAX_FOLD_STEPS = 1000` iterations: `local_next = next(CronSim(expr, local_cursor))` (keep the existing CronSimError/StopIteration/ValueError -> ScheduleValidationError(MSG_CRON_INVALID) mapping); `candidate = _from_local_naive(local_next.replace(fold=0), tz)`; if candidate > after return it; else try the second occurrence `_from_local_naive(local_next.replace(fold=1), tz)` and return it if > after (this is what makes 01:50 fold=1 fire in the repeated hour); otherwise set `local_cursor = local_next` and continue.
- If the cap is exhausted raise ScheduleValidationError(MSG_CRON_INVALID) (never loop forever, never return a past slot).
- Add a short comment explaining WHY (CronSim works on naive wall time and ignores fold; on the fall-back night a naive slot can map to an instant before `after`).
- Leave `_to_local_naive`/`_from_local_naive` signatures unchanged (fold is honoured by both astimezone paths). Do not touch interval/once math.
  </action>
  <verify>
    <automated>pytest tests/test_scheduler_schedule.py tests/test_scheduler_service.py tests/test_scheduler_runner.py -q</automated>
  </verify>
  <done>Fall-back tests pass including the fold=1 second-pass case and the 60-step strictly increasing chain; all existing schedule/service/runner tests pass.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3 (999.9): /ws/events session re-validation and non-text frames; backlog cleanup</name>
  <files>agent/events.py, tests/test_scheduler_events.py, .planning/ROADMAP.md</files>
  <behavior>
    - After the user's Session rows are deleted from the DB (simulating logout), an open /ws/events socket is closed by the server with code 1008 within ~1 s when EVENTS_RECEIVE_TIMEOUT_SECONDS and EVENTS_SESSION_RECHECK_SECONDS are monkeypatched to 0.05; the hub subscriber count for the user drops to 0
    - Same when the Session row's expires_at is set into the past
    - While the session is valid, a text ping frame keeps the socket open and published frames are still delivered (existing delivery tests keep passing)
    - Sending a binary frame closes the socket with code 1003 (not 1011) and the user is unsubscribed
    - The periodic re-check does NOT extend Session.expires_at
  </behavior>
  <action>
In agent/events.py (per WR-07 + IN-05):
1. Add constant `EVENTS_SESSION_RECHECK_SECONDS = 60.0` and `async def _session_still_valid(session_id: str, user_id: int) -> bool`: open `async_session_factory()`, select `shared.models.Session` by `token_hash == hash_session_token(session_id)`; return True only if the row exists, `row.user_id == user_id` and `as_aware_utc(row.expires_at) > datetime.now(timezone.utc)` (use agent.schedule.as_aware_utc or an equivalent local helper). Read-only: never write expires_at (unlike get_current_user_ws, which slides expiry). Catch `SQLAlchemyError` -> log warning `events_ws_recheck_failed` and return True (a transient DB error must not disconnect everyone).
2. In `ws_events`, keep `session_id` (non-None after the handshake check). Replace the receive loop: `message = await asyncio.wait_for(websocket.receive(), timeout=EVENTS_RECEIVE_TIMEOUT_SECONDS)`; on `asyncio.TimeoutError` fall through to the recheck. If `message["type"] == "websocket.disconnect"` break. If it is a receive message without a `"text"` key (binary): log `events_ws_non_text_frame` (warning, user_id) and close with code 1003, reason "Text frames only", then break. Text frames are pings and are ignored. After every receive or timeout, if `time.monotonic() - last_check >= EVENTS_SESSION_RECHECK_SECONDS`, call `_session_still_valid`; when False log `events_ws_session_gone` and close with code 1008, reason "Unauthorized", then break; otherwise update last_check. Wrap each `websocket.close` in `try/except RuntimeError` (socket may already be closed). Keep the `while not pump.done()` condition, `except WebSocketDisconnect`, and the existing `finally` exactly (unsubscribe before any await).
3. Module docstring/structlog only; type hints on everything; no bare except.

Tests in tests/test_scheduler_events.py (follow the existing TestClient + login_test_client + `_wait_for_subscribers`/portal patterns in that file): monkeypatch `agent.events.EVENTS_RECEIVE_TIMEOUT_SECONDS` and `EVENTS_SESSION_RECHECK_SECONDS` to 0.05; delete the user's Session rows via `client.portal.call(...)` with an async helper using async_session_factory (and a second test that sets expires_at to the past); then assert `ws.receive()` yields a close with code 1008 (or WebSocketDisconnect with code 1008 from receive_json) and subscriber count reaches 0. Binary test: `ws.send_bytes(b"\x00")` -> close code 1003, subscriber count 0. Expiry-not-extended test: record expires_at, keep the socket open across a few recheck intervals, assert expires_at unchanged.

4. Backlog cleanup (final step, after all tests pass): in .planning/ROADMAP.md delete the three sections `### Phase 999.7: ...`, `### Phase 999.8: ...`, `### Phase 999.9: ...` (each heading through its "- [ ] TBD ..." plan line and trailing blank line); grep the file for any other "999.7"/"999.8"/"999.9" references (e.g. a backlog summary list) and remove those lines too. KEEP `### Phase 999.10` and everything else intact. Delete the directories with `git rm -r` (or rm -rf if untracked): .planning/phases/999.7-harden-schedule-task-and-cancel-gate, .planning/phases/999.8-cron-dst-fall-back-fold, .planning/phases/999.9-events-ws-session-revalidation. Do NOT touch .planning/phases/999.10-phase-08-review-info-cleanup.
  </action>
  <verify>
    <automated>pytest tests/ -q && test ! -d .planning/phases/999.7-harden-schedule-task-and-cancel-gate && test ! -d .planning/phases/999.8-cron-dst-fall-back-fold && test ! -d .planning/phases/999.9-events-ws-session-revalidation && test -d .planning/phases/999.10-phase-08-review-info-cleanup && ! grep -qE "Phase 999\.(7|8|9):" .planning/ROADMAP.md && grep -q "Phase 999.10:" .planning/ROADMAP.md</automated>
  </verify>
  <done>Session-gone and expired-session sockets close with 1008, binary frames close with 1003, recheck does not slide expiry, full test suite green; ROADMAP has no 999.7/999.8/999.9 entries, their phase dirs are gone, 999.10 remains.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| LLM/tool output -> scheduler tools | Model tool calls may be steered by MCP/file/web content (prompt injection) |
| browser/non-browser client -> /ws/events | Long-lived socket carrying per-user job titles, prompts and run results |
| OS clock/DST -> scheduler claim loop | Wall-clock ambiguity feeds persistent job state |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-mby-01 | Elevation of Privilege | scheduler_tools._schedule_task | mitigate | Code-level gate: latest chat leaf must be the user's own message with non-negated, non-question scheduling intent (user_asked_to_schedule) before create_scheduled_task |
| T-mby-02 | Tampering | scheduler_tools._cancel_scheduled_task | mitigate | Cancel requires cancel intent (questions rejected) AND the job id or significant title word in the user's message (message_names_task); ownership check before revealing title |
| T-mby-03 | Denial of Service | schedule.next_cron_run | mitigate | Strictly-after loop with fold=1 retry and 1000-step cap; never returns a past slot, so no per-tick refire / run_count exhaustion |
| T-mby-04 | Information Disclosure | events.ws_events | mitigate | Read-only session re-check every EVENTS_SESSION_RECHECK_SECONDS; close 1008 when session missing/expired/owned by another user |
| T-mby-05 | Denial of Service | events.ws_events | mitigate | receive() + explicit binary-frame handling closes 1003 instead of KeyError/1011 |
| T-mby-06 | Elevation of Privilege | message_names_task | accept | A message naming several jobs authorises each; documented residual risk, user-visible and recoverable |
</threat_model>

<verification>
- `pytest tests/ -q` green.
- Grep: `grep -n "user_asked_to_schedule\|message_names_task" agent/scheduler_tools.py` shows both wired; `grep -n "receive_text" agent/events.py` returns nothing; `grep -n "EVENTS_SESSION_RECHECK_SECONDS" agent/events.py` present.
- ROADMAP backlog: 999.7/999.8/999.9 gone, 999.10 present.
</verification>

<success_criteria>
- 999.7: schedule_task gated by explicit user scheduling intent; cancel bound to the id/title the user named; questions/hypotheticals rejected; tests cover both.
- 999.8: next_cron_run strictly monotonic across a DST fall-back night (fixed hand-written DST zone test).
- 999.9: /ws/events re-validates the session periodically and closes 1008 when gone; binary frames close 1003; tests cover both.
- Backlog entries 999.7-999.9 and their phase dirs removed; 999.10 kept.
- Commits follow project rules (no Co-Authored-By line).
</success_criteria>

<output>
Create `.planning/quick/261006-mby-fix-scheduler-backlog-999-7-999-9-schedu/261006-mby-SUMMARY.md` when done
</output>
