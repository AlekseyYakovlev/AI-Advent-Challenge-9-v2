# Phase 20: Limits, verification script and report (Day 30) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-12
**Phase:** 20-limits-verification-script-and-report-day-30
**Areas discussed:** Message rate limit, Login lockout, Generation queue, Verification script and report

---

## Message rate limit

### Default value and switching off

| Option | Description | Selected |
|--------|-------------|----------|
| 10/min, 0 = off | Keep today's 10 per minute per chat; 0 disables | |
| 10/min, cannot be disabled | Minimum 1; 0 or negative refuses to start | |
| Higher default (30/min) | 10/min is low for scripts and parallel HTTP requests; 0 also disables | ✓ |

**User's choice:** Higher default (30/min); 0 disables.

### What takes a slot

| Option | Description | Selected |
|--------|-------------|----------|
| All accepted attempts | Every message that passed the check counts, even if the turn later fails; 429 and 409 take no slot | ✓ |
| Only successful turns | A failed turn gives its slot back | |
| Every request | Rejected 429/409 requests also extend the window | |

**User's choice:** All accepted attempts.

### What the browser user sees on WebSocket

| Option | Description | Selected |
|--------|-------------|----------|
| Russian text + seconds | Error frame with `RATE_LIMITED` and `retry_after`; UI shows the wait in seconds, typed text stays; HTTP 429 carries the same code and seconds | ✓ |
| Code and text only | No countdown, no UI change | |
| Disable the send button | As the first option plus a button countdown | |

**User's choice:** Russian text + seconds.

### Showing effective limits

| Option | Description | Selected |
|--------|-------------|----------|
| .env and docs only | `.env.example`, `docs/USER_GUIDE.md`, log line at Agent start | ✓ |
| Rows in the Settings «Сеть» section | Read-only rows fed by `GET /api/v1/network` | |
| REST route for the script | No UI, a route the script reads | |

**User's choice:** .env and docs only.

---

## Login lockout

### Counting key

| Option | Description | Selected |
|--------|-------------|----------|
| Username + IP | Blocks only the machine guessing that username | ✓ |
| Username | Blocks the name for every machine; a neighbour can lock an account | |
| IP address | Blocks the machine for every username | |

**User's choice:** Username + IP.

### Threshold and duration

| Option | Description | Selected |
|--------|-------------|----------|
| 5 failures → 5 minutes | Success resets the counter; threshold 0 disables | ✓ |
| 5 failures → 1 minute | Short block, easy to show on video | |
| 3 failures → 15 minutes | Strict | |

**User's choice:** 5 failures → 5 minutes.

### Response while blocked

| Option | Description | Selected |
|--------|-------------|----------|
| 429 + remaining time | `Retry-After`, form shows remaining time; correct password also refused; same answer for unknown usernames | ✓ |
| 429 without time | Generic "try later" | |
| Same 401 as a wrong password | Block not disclosed | |

**User's choice:** 429 + remaining time.

### Storage

| Option | Description | Selected |
|--------|-------------|----------|
| Agent memory | Restart clears blocks; no table | ✓ |
| SQLite | Survives restart; needs table, migration, cleanup | |

**User's choice:** Agent memory.

---

## Generation queue

### What counts toward the cap

| Option | Description | Selected |
|--------|-------------|----------|
| Chat turns: WS + HTTP | One slot per whole turn; scheduler keeps its own cap; background calls excluded | ✓ |
| Chat + scheduler | One shared cap | |
| Every model call | Cap at the LLM client level | |

**User's choice:** Chat turns: WS + HTTP.

### Scope and default

| Option | Description | Selected |
|--------|-------------|----------|
| Shared, default 2 | One cap for all providers; 0 = no cap | ✓ |
| Shared, default 1 | Strictly one turn at a time | |
| LM Studio only, 2 | Cloud providers bypass the queue | |

**User's choice:** Shared, default 2.

### What a waiting client sees

| Option | Description | Selected |
|--------|-------------|----------|
| "In queue" in the UI | WS frame, chat shows «В очереди, ожидание свободного места…»; queue wait time in `stats` | ✓ |
| Only wait time in the response | No UI change | |
| Nothing, log only | Queue is invisible to clients | |

**User's choice:** "In queue" in the UI.

### Queue bounds

| Option | Description | Selected |
|--------|-------------|----------|
| Unbounded | A queued turn always runs; `LLM_TIMEOUT` counts after the queue | ✓ |
| Wait timeout | 503 after a configured wait | |
| Queue length cap | 503 beyond K waiting turns | |

**User's choice:** Unbounded.

---

## Verification script and report

### Script form

| Option | Description | Selected |
|--------|-------------|----------|
| One file, stdlib only | Copy one file to the second machine, run with `python3` | ✓ |
| httpx, repository required | Clone the repo and install `requirements.txt` | |
| One file, httpx via pip | venv + `pip install httpx` | |

**User's choice:** One file, stdlib only.
**Notes:** The user asked which option suits macOS — the second machine is a Mac. Recommendation given: stdlib single file, because `pip install` on macOS needs a venv. The user then stated the Mac has Python 3.14.7, so compatibility with Python 3.9 is not needed; the script targets 3.11+.

### Chats used by the script

| Option | Description | Selected |
|--------|-------------|----------|
| Own chats, deleted afterwards | Marked chats, removed at the end; `--keep` leaves them | ✓ |
| Own chats, kept | `--cleanup` removes them | |
| A given chat | `--chat-id`; parallel load impossible because of `CHAT_BUSY` | |

**User's choice:** Own chats, deleted afterwards.

### Rate-limit demonstration

| Option | Description | Selected |
|--------|-------------|----------|
| Low limit for the run | Operator sets a low value in `.env`; script sends until 429 with a ceiling; report names the value | ✓ |
| At the working limit of 30 | May not trigger on a slow model | |
| Count before the model | 409 would also take a slot; changes an earlier decision | |

**User's choice:** Low limit for the run.

### Output and report

| Option | Description | Selected |
|--------|-------------|----------|
| Console + md and json files | Files moved into `eval_out/day30/`; report assembled in Russian in the Day22–Day25 style | ✓ |
| Console only | Output pasted as a text block | |
| Script writes the whole report | Manual section added by hand | |

**User's choice:** Console + md and json files.

---

## Claude's Discretion

- Setting names, validation, message and UI wording.
- Limiter algorithm and placement; order of rate check vs chat-busy check.
- Client IP source and cleanup of stale lockout entries.
- How `/debug/routes` is closed and how the supervisor readiness probe works afterwards.
- "Queued" frame name and payload, `stats` field name, slot acquisition point.
- Script details: default N, model selection, timeouts, exit codes, how overflow is provoked, output file names.
- Manual UI checklist and report section structure.
- Split of limit tests and `docs/TESTING_GUIDE.md` sections.

## Deferred Ideas

- Limits shown in the Settings «Сеть» section.
- REST route exposing the limits.
- Login lockouts persisted in SQLite.
- Bounded queue with 503.
- Send-button lock with a countdown.
