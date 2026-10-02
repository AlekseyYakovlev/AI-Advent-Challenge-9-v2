---
phase: 09-auto-rename-chats-with-llm-day-21
reviewed: 2026-10-02T00:36:24Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - agent/state.py
  - agent/titles.py
  - agent/ws.py
  - docs/API_SPEC.md
  - docs/ARCHITECTURE.md
  - docs/TESTING_GUIDE.md
  - docs/USER_GUIDE.md
  - tests/conftest.py
  - tests/test_cascade_delete.py
  - tests/test_titles.py
  - tests/test_titles_ws.py
  - ui/static/app.js
findings:
  critical: 1
  warning: 3
  info: 12
  total: 16
status: issues_found
---

# Phase 09: Code Review Report

**Reviewed:** 2026-10-02T00:36:24Z
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Scope: the Day 21 auto-titling change (`git diff b9ee552..HEAD`). `agent/titles.py`,
`tests/test_titles.py` and `tests/test_titles_ws.py` were read in full; the other files were
reviewed through their phase diff plus the surrounding code they touch (`agent/events.py`,
`agent/llm_client.py::complete_chat`, `shared/database.py`, `ui/supervisor.py`, `agent/main.py::delete_chat`).

Tier mapping used below: Critical = BLOCKER, Warning = WARNING, Info = advisory; the `CV-*`
items are CONVENTION tier (never blocking) and are counted under `info` in the frontmatter.

What held up under adversarial reading:

- **XSS:** the title reaches the DOM only through `textContent` (`renderChatList` at
  `ui/static/app.js:1010`, header at `:2577` and `:2596`). No `innerHTML` path was added.
- **Set-once race:** `UPDATE chat SET title=? WHERE id=? AND title='New Chat'` plus `rowcount`
  (`agent/titles.py:146-155`) is correct; a rename, a delete or a second job cannot be overwritten,
  and the frame is published only by the job that won.
- **Owner-only delivery:** the frame is published to `chat.user_id` taken from the DB row, and
  `EventHub.publish` fans out only to that user's queues. An ownerless chat publishes nothing.
- **Turn isolation:** `schedule_title_generation` is synchronous and awaits nothing, the job uses
  its own session and no chat lock, and `generate_and_apply_title` catches everything. The LLM call
  itself cannot delay or fail the `done` frame.
- **Registry cleanup:** `cleanup_chat_caches` pops and cancels; `_forget_task` only removes its own
  entry, so a cancelled job cannot evict a newer one.
- **Stale ORM state:** `chat.title` / `user_msg.parent_id` are read after several commits, which is
  safe only because `async_session_factory` sets `expire_on_commit=False` (`shared/database.py:30`).

What did not hold up: the two text-cleaning helpers run on the **whole** 100 000-character message
before any truncation, and both are quadratic. That synchronous CPU work happens on the Agent's
single event loop, so the "can never block the chat turn" property is true for the network call but
not for the string processing around it (CR-01). The remaining findings are a log leak of title text
on DB errors, and correctness defects in the sanitizer.

Verification method: the regexes and helpers were copied verbatim into a standalone script and
timed/executed (no app start, no test-suite run, no ports touched). The measurements quoted below
come from that script on this machine.

## Critical Issues

### CR-01: Quadratic regex work on the full 100k message blocks the Agent event loop (authenticated DoS)

**File:** `agent/titles.py:33`, `agent/titles.py:43-55`, `agent/titles.py:73-79`, `agent/titles.py:118`

**Issue:** Both sanitizing paths process the untruncated text and only cut afterwards. The inbound
message may be up to `CONTENT_MAX_LENGTH = 100_000` characters (`agent/schemas.py:293`), and the
assistant text is unbounded.

1. `fallback_title` calls `_strip_markup(user_text)` on the full message (`:118`).
   `_TAG_LIKE_RE = <[^>]*>` (`:33`) is quadratic when `<` is frequent and `>` is rare: at every `<`
   the class runs to the end of the string and backtracks. Measured:

   | input | time |
   |---|---|
   | `"<" * 50_000` | 0.95 s |
   | `"<" * 100_000` | 3.76 s |
   | `"x < y "` repeated to 100 000 chars | 0.62 s |

2. `_snippet` calls `_neutralize_tags(text)` on the full text and slices to 500/300 afterwards
   (`:54-55`). The fixpoint loop removes one nesting level per pass, so
   `"<user_" * k + "<user_message>" + "message>" * k` needs `k` full passes. Measured at the 100k
   limit: 7143 passes, 1.22 s. This one needs no fallback; it runs on every first turn, for both
   `user_text` and `assistant_text`.

All of this is synchronous code inside the title task, i.e. on the Agent's only event loop. While it
runs, every user's token stream, REST call and WebSocket is frozen. The supervisor pings `/health`
with a 2.0 s timeout every 3 s and restarts the Agent on one failed ping
(`ui/supervisor.py:144`, `:204-206`), so a 3.76 s stall can get the Agent killed and restarted,
dropping every in-flight stream and all in-memory state.

Reachability: any logged-in user can send the message as the first turn of a new chat. Vector 2 is
unconditional. Vector 1 needs the LLM title to be unusable, which is the normal case for a reasoning
model (documented in `docs/ARCHITECTURE.md:319`), for a title call that times out, or when the model
answers with punctuation only. It is also reachable without malice by pasting a large log or code
block that contains many `<` and few `>`.

Severity note: impact is availability only, and it requires an authenticated account. It is rated
Critical because one message stalls or restarts the process for all users, and because it breaks the
phase's stated guarantee that titling cannot affect the chat flow.

**Fix:** Bound the input before any regex runs, and make the tag regex linear.

```python
_TAG_LIKE_RE = re.compile(r"<[^<>]*>")  # cannot scan past the next "<": linear

_PRE_CUT_FACTOR = 4


def _snippet(text: str, limit: int) -> str:
    """Bound the input, neutralize tags, collapse whitespace and cut to the limit."""
    bounded = text[: limit * _PRE_CUT_FACTOR]
    cleaned = _WHITESPACE_RE.sub(" ", _neutralize_tags(bounded)).strip()
    return cleaned[:limit]


def fallback_title(user_text: str) -> str | None:
    """Derive a title from the first user message when the LLM gives nothing usable."""
    raw = user_text[: TITLE_MAX_CHARS * _PRE_CUT_FACTOR] if isinstance(user_text, str) else ""
    text = _strip_markup(raw)
    ...
```

Cutting first is safe for the breakout guard because neutralization still runs after the cut. Add a
regression test that feeds `"<" * 100_000` and the nested-tag string through `fallback_title` and
`build_title_messages` and asserts a wall-clock bound.

## Warnings

### WR-01: DB failure log line leaks the title text (user message content) into logs

**File:** `agent/titles.py:187-188`

**Issue:** `logger.warning("chat_title_failed", chat_id=chat_id, error=str(exc))` logs the string of
whatever `apply_title` raised. For a SQLAlchemy error that string contains the bound parameters. The
engine is created without `hide_parameters=True` (`shared/database.py:26`). Reproduced format:

```
(sqlite3.OperationalError) ...
[SQL: UPDATE chat SET title=? WHERE id=?]
[parameters: ('секретный текст пользователя', 1)]
```

On the fallback path the title is the first 50 characters of the user's message, so a
`database is locked` error (the most likely failure here: the job writes without the chat lock,
concurrently with the turn) writes message content into the structured log. This contradicts the
project rule that logs must not contain sensitive user data; the success path already takes care to
log only `length=len(title)`.

**Fix:**

```python
    except Exception as exc:
        logger.warning("chat_title_failed", chat_id=chat_id, error_type=type(exc).__name__)
```

If the message is wanted, log `str(getattr(exc, "orig", exc))`, which is the driver message without
statement and parameters.

### WR-02: Sanitizer deletes legitimate characters and whole spans, producing wrong titles

**File:** `agent/titles.py:33-34`, `agent/titles.py:73-79`

**Issue:** `_strip_markup` removes every `*`, `_`, `#`, `` ` ``, `~` anywhere in the text, and
replaces everything between any `<` and the next `>` with a space. It is applied to the model output
and, in `fallback_title`, to the user's own message. Results from the actual functions:

| input | `clean_title` / `fallback_title` |
|---|---|
| `Основы C# и F# для новичков` | `Основы C и F для новичков` |
| `Fix user_id bug in __init__` | `Fix userid bug in init` |
| `Что такое snake_case?` | `Что такое snakecase` |
| `Сравнение a < b и c > d в Python` | `Сравнение a d в Python` |

For a programming-oriented chat app these are common first messages; the stored title is silently
wrong (`C#` becomes `C`, identifiers are altered, a comparison loses its operands). Removing the
characters buys no safety: the frontend renders through `textContent`.

**Fix:** Strip markdown only where it is markup, and strip only real tags.

```python
_TAG_LIKE_RE = re.compile(r"</?[A-Za-z][^<>]*>")           # real tags only; "a < b" survives
_MD_WRAP_RE = re.compile(r"(\*\*|__|\*|`|~~)(.+?)\1")       # paired emphasis/code markers
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")               # leading heading marker only
```

Keep the `replace("<", "").replace(">", "")` step if angle brackets must never reach the client,
but do it after the tag pass so only the two characters are lost, not the text between them. Add
table rows for `C#`, `user_id` and `a < b` to `test_clean_title_accepts_and_normalizes`.

### WR-03: A closing `</think>` without an opening tag passes reasoning text through as the title

**File:** `agent/titles.py:103-105`

**Issue:** The guard handles a complete `<think>...</think>` block and a dangling opening tag only.
Chat templates that prefill `<think>` in the prompt (common for local reasoning models in LM Studio)
make the model emit `reasoning...</think>answer` with no opening tag in `content`. Then
`_THINK_BLOCK_RE` does not match, `re.search(r"<think", ...)` does not match, and `_strip_markup`
turns `</think>` into a space:

```
clean_title("reasoning about it</think>Real title") -> "reasoning about it Real title"
```

The stored title is the model's reasoning. `docs/ARCHITECTURE.md:319` states that reasoning models
get the fallback title; that holds only when the opening tag is present.

**Fix:**

```python
    text = _THINK_BLOCK_RE.sub("", raw)
    if "</think>" in text.lower():
        text = text[text.lower().rindex("</think>") + len("</think>"):]
    if re.search(r"<think", text, re.IGNORECASE):
        return None
```

Add `("thinking</think>План поездки", "План поездки")` to the accept table.

## Info

### IN-01: `asyncio.CancelledError` is swallowed instead of propagated

**File:** `agent/titles.py:185-186`
**Issue:** `except asyncio.CancelledError: return` makes a cancelled job finish as a normal
completion (`task.cancelled()` is `False`). The project convention is "clean up and propagate". There
is nothing to clean up here, and a cancelled task does not produce a "never retrieved" warning, so
the clause is unnecessary.
**Fix:** Delete the clause (`CancelledError` is not an `Exception`, so the generic handler below does
not catch it), or re-raise.

### IN-02: `TITLE_MAX_TOKENS = 30` leaves almost no headroom for a 50-character Cyrillic title

**File:** `agent/titles.py:19`
**Issue:** The prompt allows up to 50 characters, and `complete_chat` discards `finish_reason`, so a
title cut by the token limit is indistinguishable from a complete one and is stored mid-word.
Measured with `cl100k_base`: `Рецепт украинского борща с пампушками и чесноком` (48 chars) is 29
tokens. Local-model tokenizers are usually less efficient on Cyrillic than that. Not proven to
truncate, hence Info.
**Fix:** Raise the limit to about 64; `clean_title` already caps the result at 50 characters on a
word boundary.

### IN-03: Invisible and bidirectional format characters survive sanitizing

**File:** `agent/titles.py:35`
**Issue:** `_CONTROL_RE` covers C0/C1 only. `clean_title(RLO + "evil" + ZWSP + " title")` (RLO = U+202E, ZWSP = U+200B)
returns the string unchanged, so U+202E (RTL override), U+200B-U+200F and U+2066-U+2069 can reach the sidebar and
visually reorder or pad the title. Reachable through indirect prompt injection (tool or MCP output in
`assistant_text`). Display-only impact.
**Fix:** Drop format characters: `"".join(ch for ch in text if unicodedata.category(ch) != "Cf")`.

### IN-04: Wrapper-tag neutralization matches only the exact bare tag

**File:** `agent/titles.py:32`
**Issue:** `</user_message x>`, `</user_message/>` and similar variants are not removed, and a model
may still read them as a closing tag. Impact is bounded: the output is reduced to at most 50
characters of plain text in the sender's own chat, and the system prompt marks the tagged text as
data.
**Fix:** Widen the pattern to `<\s*/?\s*(?:user_message|assistant_answer)\b[^<>]*>`, or simply
replace `<` and `>` in the snippets.

### IN-05: A label-only first line discards a usable title on the next line

**File:** `agent/titles.py:106-110`
**Issue:** For `"Title:\nНастройка сервера"` the first line is `Title:`, the label regex empties it,
and the function returns `None`, so the fallback is used although the model answered correctly.
**Fix:** Iterate over the non-empty lines and return the first one that is usable after cleaning.

### IN-06: Function-local import hides an import cycle

**File:** `agent/titles.py:160-161`
**Issue:** `agent.events` imports `agent.ws`, which imports `agent.titles`, which imports
`agent.events` lazily. The cycle is documented in the comment and in `docs/ARCHITECTURE.md`, but it
contradicts the "no circular dependencies" statement in the project conventions and is fragile
against import reordering.
**Fix:** Move `EventHub`/`hub` into a leaf module (for example `agent/event_hub.py`) that imports
neither `ws` nor `titles`.

### IN-07: A title frame can be overwritten by an in-flight `loadChats()`

**File:** `ui/static/app.js:2566-2579`, `ui/static/app.js:1034-1037`
**Issue:** `applyChatTitleUpdate` mutates the entry in `state.chats`. If a `loadChats()` request
(create, delete, reconnect refresh) was sent before the server committed the title and resolves after
the frame, it replaces `state.chats` with the older list and the sidebar shows `New Chat` again until
the next reload. The window is small. Separately, `state.eventsHasConnected` is never reset on
logout, which only causes one redundant list reload.
**Fix:** Keep a `Map` of titles received by frame and re-apply it in `loadChats()`, or re-fetch the
list in `applyChatTitleUpdate` when a load is pending.

### IN-08: `test_title_lands_after_socket_closed` duplicates `test_title_frame_and_stored_title`

**File:** `tests/test_titles_ws.py:354-371`
**Issue:** Both tests run `_run_first_turn` (which closes the socket on exit) and then wait for the
frame. Nothing in the later test forces the title to land after the close, so it does not test the
behaviour its name claims and adds no coverage.
**Fix:** Gate the title call on an event (as `test_done_is_not_delayed_by_blocked_title_call` does),
close the socket, then release the gate; or delete the test.

### IN-09: API spec overstates the fallback

**File:** `docs/API_SPEC.md:293-294`
**Issue:** "On any failure the title is the first user message cut to 50 characters with `…`." The
ellipsis is added only when the message is longer than 50 characters, markup characters are removed,
and a DB failure or an empty fallback leaves `New Chat` (the architecture doc says this correctly).
**Fix:** Reword to "On an LLM failure the title is derived from the first user message (at most 50
characters, `…` when cut); if that is empty or the write fails, the chat keeps `New Chat`."

## Convention findings (CONVENTION tier, never blocking)

Output of `gsd-tools verify conventions --check --files ui/static/app.js`. All three lines are
outside this phase's diff (pre-existing code); they are listed for completeness only. The Python
files have no rule pack and were skipped by the tool.

### CV-01: Swallowed catch

**File:** `ui/static/app.js:292`
**Deviation:** catch block swallows the error (empty / no rethrow).
**Convention:** architectural-split: error handling; swallowed catches hide failures.
**Suggested fix:** rethrow, wrap (`throw new Error(..., { cause })`), or log-and-handle deliberately.

### CV-02: Swallowed catch

**File:** `ui/static/app.js:536`
**Deviation:** catch block swallows the error (empty / no rethrow).
**Convention:** architectural-split: error handling; swallowed catches hide failures.
**Suggested fix:** rethrow, wrap, or log-and-handle deliberately.

### CV-03: Swallowed catch

**File:** `ui/static/app.js:2478`
**Deviation:** catch block swallows the error (empty / no rethrow).
**Convention:** architectural-split: error handling; swallowed catches hide failures.
**Suggested fix:** rethrow, wrap, or log-and-handle deliberately.

---

_Reviewed: 2026-10-02T00:36:24Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
