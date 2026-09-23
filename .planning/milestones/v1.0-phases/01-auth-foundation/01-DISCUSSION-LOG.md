# Phase 1: Auth Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-19
**Phase:** 1-Auth Foundation
**Areas discussed:** Session mechanism, Account creation & bootstrap admin, Login UI integration, Password hashing & policy

---

## Session Mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| DB-backed Session table | New `Session` SQLModel table, opaque token in cookie maps to row, supports real revocation | ✓ |
| Signed-cookie middleware | Starlette `SessionMiddleware`, no new table, no server-side revocation | |

**User's choice:** DB-backed Session table

| Option | Description | Selected |
|--------|-------------|----------|
| 30-day sliding window | Expiry extends on each active use | ✓ |
| Session-only | Cookie dropped on browser close | |
| Fixed 7-day expiry | Hard cutoff regardless of activity | |

**User's choice:** 30-day sliding window

| Option | Description | Selected |
|--------|-------------|----------|
| Allow multiple concurrent sessions | New login doesn't invalidate prior sessions | ✓ |
| Single session per user | New login invalidates all prior sessions | |

**User's choice:** Allow multiple concurrent sessions

| Option | Description | Selected |
|--------|-------------|----------|
| Delete server-side session row + clear cookie | Real revocation on logout | ✓ |
| Just clear the cookie client-side | Simpler, but no real revocation | |

**User's choice:** Delete server-side session row + clear cookie
**Notes:** All four recommended defaults accepted without change.

---

## Account Creation & Bootstrap Admin

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-generate + print to console | Random bootstrap admin credentials printed once on first run | ✓ |
| Fixed credentials from .env | Predictable but plaintext password in config | |
| First-run setup wizard | New one-time UI flow | |

**User's choice:** Auto-generate + print to console

| Option | Description | Selected |
|--------|-------------|----------|
| Any logged-in user, via "Add user" UI action | No public signup surface | ✓ |
| Public /register page | Open signup, no invite/approval gate | |

**User's choice:** Any logged-in user, via "Add user" UI action

| Option | Description | Selected |
|--------|-------------|----------|
| The bootstrap admin user owns pre-migration data | Single migration path | ✓ |
| A separate dedicated "legacy" user | Extra concept for little benefit | |

**User's choice:** The bootstrap admin user
**Notes:** All three recommended defaults accepted without change.

---

## Login UI Integration

| Option | Description | Selected |
|--------|-------------|----------|
| Login gate inside index.html | No new HTML file, single-page app.js state toggle | |
| Separate ui/static/login.html page | Dedicated page, classic navigate-then-redirect flow | ✓ |

**User's choice:** Separate ui/static/login.html page
**Notes:** Deviated from the recommended embedded-gate option — deliberate choice, captured as D-08 in CONTEXT.md.

| Option | Description | Selected |
|--------|-------------|----------|
| Detect 401 on next request, show login gate with banner | Reactive only, no timers | ✓ |
| Proactively track expiry client-side | Extra client-side complexity | |

**User's choice:** Detect 401 on next request, redirect to login.html with a banner

| Option | Description | Selected |
|--------|-------------|----------|
| Cookie on WS handshake is sufficient | Validated once at connect, same as origin check | ✓ |
| Also validate per-message inside WS loop | Extra DB lookup per message | |

**User's choice:** Cookie on WS handshake is sufficient

---

## Password Hashing & Policy

| Option | Description | Selected |
|--------|-------------|----------|
| argon2id via pwdlib | Modern, memory-hard, confirmed compatible with actual Python 3.13 runtime | ✓ |
| bcrypt via passlib | Well-established, slightly weaker than argon2id | |

**User's choice:** argon2id via pwdlib

| Option | Description | Selected |
|--------|-------------|----------|
| Minimum length only (e.g. 8 chars) | One simple server-side check | |
| No enforced policy | Accept any non-empty password | ✓ |

**User's choice:** No enforced policy
**Notes:** Deviated from the recommended minimum-length option — deliberate choice for this low-stakes coursework context, captured as D-12 in CONTEXT.md.

---

## Claude's Discretion

- Exact `Session`/`User` table field set and indexes beyond what's required by the locked decisions
- Cookie attributes (`HttpOnly=True`, `Secure` omitted for local http, `SameSite=Lax`)
- Exact markup/styling of `login.html` and the "session expired" banner
- Where the "Add user" UI lives (modal vs. settings panel vs. dedicated page)
- Expired-session-row cleanup strategy (lazy deletion vs. other in-request approach; no new background process)

## Deferred Ideas

None — discussion stayed within phase scope.
