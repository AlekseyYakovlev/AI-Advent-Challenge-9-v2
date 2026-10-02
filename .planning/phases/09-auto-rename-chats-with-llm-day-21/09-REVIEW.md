---
phase: 09-auto-rename-chats-with-llm-day-21
reviewed: 2026-10-02T10:24:52Z
depth: standard
files_reviewed: 14
files_reviewed_list:
  - agent/llm_client.py
  - agent/state.py
  - agent/titles.py
  - agent/ws.py
  - docs/API_SPEC.md
  - docs/ARCHITECTURE.md
  - docs/TESTING_GUIDE.md
  - docs/USER_GUIDE.md
  - tests/conftest.py
  - tests/test_cascade_delete.py
  - tests/test_llm_complete_chat.py
  - tests/test_titles.py
  - tests/test_titles_ws.py
  - ui/static/app.js
findings:
  critical: 0
  warning: 4
  info: 15
  total: 19
status: issues_found
---

# Phase 09: Code Review Report

**Reviewed:** 2026-10-02T10:24:52Z
**Depth:** standard
**Files Reviewed:** 14
**Status:** issues_found

## Summary

This is a re-review after gap-closure plan 09-05 (`git diff 2bfbc2b..HEAD`). It replaces the
previous report (1 critical, 3 warnings, 12 info).

Scope read in full: `agent/llm_client.py`, `agent/titles.py`, `agent/state.py`,
`tests/test_titles.py`, `tests/test_titles_ws.py`, `tests/test_llm_complete_chat.py`,
`tests/test_cascade_delete.py`, `tests/conftest.py`. `agent/ws.py` and `ui/static/app.js` were
reviewed in their phase-9 parts only (title hook at `agent/ws.py:960-965`; `applyChatTitleUpdate`,
`handleEventFrame`, `refreshChatsAfterEventsReconnect` at `ui/static/app.js:2566-2597`); both are
unchanged since the previous review. Docs were checked against the code.

Tier mapping: Critical = BLOCKER, Warning = WARNING, Info = advisory. `CV-*` items are CONVENTION
tier (never blocking) and are counted under `info`.

Verification method: a standalone probe script imported the real `agent.titles` and
`agent.llm_client` against a scratch database and timed/executed the cases quoted below. The four
phase test files were run against a scratch `DB_PATH`: 86 passed. The app was not started, ports
8000/8001 were not touched, and no source file was modified.

### Result in one paragraph

The previous critical finding (quadratic regex work on the 100 000-character user message) is fixed
for both user-reachable vectors. No new critical issue was found. The three previous warnings are
all still open: 09-05 did not address them. One new warning is the remaining unbounded regex path
in `clean_title`. The new code in 09-05 (`complete_chat_detailed`, the 400/422 retry, the single
timeout, the new log events) is correct in what it does; the findings against it are advisory.

### What held up in the new code

- **Single timeout over both attempts.** `asyncio.wait_for(_complete_title(...), 20.0)` wraps the
  first call and the repeat. Measured with a 0.5 s limit, a first attempt answering HTTP 400 after
  0.3 s and a second attempt hanging: total 0.511 s, result `None`, both calls made. The repeat
  cannot extend the budget.
- **Retry is bounded.** Exactly one repeat, only on `httpx.HTTPStatusError` with status 400 or 422;
  a second rejection propagates to `request_title` and yields the fallback. HTTP 500, connection
  errors and timeouts are not repeated.
- **`complete_chat` callers are unaffected.** The method keeps its signature, its exact five-key
  payload and its `body["choices"][0]["message"]["content"]` return. The only callers are
  `agent/context_engine.py:645` and `agent/invariants.py:299`; neither sends `reasoning_effort`.
  `test_complete_chat_payload_is_five_keys` pins the payload.
- **`extra_body` cannot override core keys.** The core dict is applied last
  (`agent/llm_client.py:89-98`).
- **New log events carry no text.** `chat_title_llm_unusable` logs `model`, `finish_reason`,
  `content_empty`, `has_reasoning`, `completion_tokens`. `chat_title_reasoning_control_rejected` logs
  `model` and `status_code` only; the response body is not logged. `chat_title_llm_failed` logs
  `str(exc)`, which for httpx errors is the status line and URL, with no request body and no
  `Authorization` header. The exception to this is `chat_title_failed` (WR-01).
- **Cancellation.** `request_title` catches `Exception`, so `CancelledError` from
  `cleanup_chat_caches` still propagates out of the `wait_for`.

### Status of the previous findings

| Previous ID | Topic | Status now |
|---|---|---|
| CR-01 | Quadratic regex on the full user message | **Fixed** for `fallback_title` and `_snippet` (measured 0.0001 s on the former 3.76 s and 1.22 s inputs). One unbounded path remains in `clean_title`; see WR-04 |
| WR-01 | DB failure log line leaks title text | **Still open** (reproduced); see WR-01 |
| WR-02 | Sanitizer deletes legitimate characters and spans | **Still open** (reproduced); see WR-02 |
| WR-03 | `</think>` without opening tag passes reasoning as title | **Still open** (reproduced); see WR-03 |
| IN-01 | `CancelledError` swallowed | Still open; IN-01 |
| IN-02 | `TITLE_MAX_TOKENS = 30` headroom | Still open; IN-02 (the needed `finish_reason` is now available) |
| IN-03 | Bidi / invisible format characters survive | Still open; IN-03 |
| IN-04 | Wrapper-tag regex matches only the bare tag | Still open; IN-04 |
| IN-05 | Label-only first line discards the title | Still open; IN-05 |
| IN-06 | Function-local import hides a cycle | Still open; IN-06 |
| IN-07 | Title frame overwritten by in-flight `loadChats()` | Still open; IN-07 |
| IN-08 | Duplicate WS test | Still open; IN-08 |
| IN-09 | API spec overstated the fallback | **Fixed** (`docs/API_SPEC.md:291-298` reworded) |
| CV-01..03 | Swallowed catches in `app.js` | Unchanged, pre-existing code outside the phase diff |

## Warnings

### WR-01: `chat_title_failed` writes the user's message text into the log on a DB error

**File:** `agent/titles.py:226-227` (claim contradicted at `docs/TESTING_GUIDE.md:174`)

**Issue:** `logger.warning("chat_title_failed", chat_id=chat_id, error=str(exc))` logs the string of
whatever `apply_title` raised. A SQLAlchemy error string contains the bound parameters, and the
engine is created without `hide_parameters=True` (`shared/database.py:26`). On the fallback path the
title is the first 50 characters of the user's message. Reproduced with the real module (title call
failing, `UPDATE` raising `OperationalError`):

```
warning chat_title_failed {'chat_id': '1', 'error': "(sqlite3.OperationalError) no such table: chat
[SQL: UPDATE chat SET title=? WHERE chat.id = ? AND chat.title = ?]
[parameters: ('мой пароль от банка 12345', 1, 'New Chat')]
```

The realistic trigger is `database is locked`: the job writes without the chat lock while other
writers are active. This breaks the project rule that logs hold no user data, and it makes the
statement "Title log events never contain message or model text" in `docs/TESTING_GUIDE.md:174`
false. `test_title_logs_never_contain_text` (`tests/test_titles.py:583-599`) covers only the
unusable-answer and success paths, so the leak is not caught.

**Fix:**

```python
    except Exception as exc:
        logger.warning(
            "chat_title_failed",
            chat_id=chat_id,
            error_type=type(exc).__name__,
            error=str(getattr(exc, "orig", exc)),
        )
```

`exc.orig` is the driver message without statement and parameters. Add a test that makes
`apply_title` raise a `sqlalchemy.exc.OperationalError` built with parameters and asserts the secret
text is absent from every recorded log value.

### WR-02: Sanitizer deletes legitimate characters and whole spans, producing wrong titles

**File:** `agent/titles.py:38-39`, `agent/titles.py:80-86`

**Issue:** `_strip_markup` removes every `*`, `_`, `#`, `` ` ``, `~` anywhere in the text and
replaces everything between any `<` and the next `>` with a space. It runs on the model output and,
in `fallback_title`, on the user's own message. Output of the real functions:

| input | result |
|---|---|
| `Основы C# и F# для новичков` | `Основы C и F для новичков` |
| `Fix user_id bug in __init__` | `Fix userid bug in init` |
| `Сравнение a < b и c > d в Python` | `Сравнение a d в Python` |
| `Как в C# сделать user_id?` (fallback) | `Как в C сделать userid?` |

For a programming-oriented chat these are ordinary first messages. The stored title is silently
wrong. Removing the characters adds no safety because the frontend renders through `textContent`.

**Fix:** Strip markdown only where it is markup, and strip only real tags.

```python
_TAG_LIKE_RE = re.compile(r"</?[A-Za-z][^<>]*>")        # real tags only; "a < b" survives
_MD_WRAP_RE = re.compile(r"(\*\*|__|\*|`|~~)(.+?)\1")    # paired emphasis / code markers
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")            # leading heading marker only
```

Replace `_MD_WRAP_RE` matches with group 2. Keep `replace("<", "").replace(">", "")` after the tag
pass if angle brackets must never reach the client. Add `C#`, `user_id` and `a < b` rows to
`test_clean_title_accepts_and_normalizes`. The `[^<>]` class also makes the tag regex linear, which
closes WR-04 at the regex level.

### WR-03: A closing `</think>` without an opening tag stores the model's reasoning as the title

**File:** `agent/titles.py:110-112`

**Issue:** The guard handles a complete `<think>...</think>` block and a dangling opening tag. Chat
templates that prefill `<think>` make the model emit `reasoning...</think>answer` with no opening tag
in `content`. Then `_THINK_BLOCK_RE` does not match, `re.search(r"<think", ...)` does not match, and
`_strip_markup` turns `</think>` into a space:

```
clean_title("reasoning about it</think>Real title") -> "reasoning about it Real title"
```

`reasoning_effort: "none"` lowers the frequency, but the documented known limit
(`docs/ARCHITECTURE.md:333-335`) is exactly a backend that accepts the field and ignores it, and
after an HTTP 400/422 the repeat is sent with no reasoning control at all.

**Fix:**

```python
    text = _THINK_BLOCK_RE.sub("", raw)
    lowered = text.lower()
    if "</think>" in lowered:
        text = text[lowered.rindex("</think>") + len("</think>"):]
    if re.search(r"<think", text, re.IGNORECASE):
        return None
```

Add `("thinking</think>План поездки", "План поездки")` to the accept table.

### WR-04: `clean_title` still runs the quadratic regex on unbounded model output

**File:** `agent/titles.py:106-116` (regex at `agent/titles.py:38`)

**Issue:** 09-05 bounded the input of `fallback_title` and `_snippet`, but `clean_title` applies
`_THINK_BLOCK_RE` to the whole response and `_strip_markup` to the whole first line with no length
cut. `_TAG_LIKE_RE = <[^>]*>` is unchanged and still quadratic. Measured:

```
clean_title("<" * 100_000)  ->  3.63 s
```

This is synchronous work on the Agent's event loop, the same stall the previous CR-01 described
(the supervisor health ping times out at 2.0 s).

Reachability is narrower than CR-01, which is why this is a warning and not critical: the only bound
on the input is the backend honouring `max_tokens: 30`. With a compliant backend 30 tokens are a few
hundred characters and the cost is negligible. The bound is not enforced by this code; an
OpenAI-compatible backend or proxy that ignores `max_tokens` removes it, and the httpx timeout and
the 20 s `wait_for` do not limit CPU time spent after the response arrived.

**Fix:** Cut before any regex, as the other two helpers now do.

```python
CLEAN_INPUT_CHARS = 1000

    if not isinstance(raw, str) or TOOL_TRACE_HEADER in raw:
        return None
    raw = raw[:CLEAN_INPUT_CHARS]
```

Do the `TOOL_TRACE_HEADER` check before or after the cut consistently, and also make the regex
linear (`<[^<>]*>`). Add a bound test like `test_fallback_title_bounds_input_before_regex` for
`clean_title`.

## Info

### IN-01: `asyncio.CancelledError` is swallowed instead of propagated

**File:** `agent/titles.py:224-225`
**Issue:** `except asyncio.CancelledError: return` makes a cancelled job end as a normal completion
(`task.cancelled()` is `False`). The project convention is "clean up and propagate"; there is nothing
to clean up here.
**Fix:** Delete the clause (the generic `except Exception` does not catch `CancelledError`) or
re-raise.

### IN-02: A title cut by the token limit is stored mid-word although `finish_reason` is now known

**File:** `agent/titles.py:20`, `agent/titles.py:168-178`
**Issue:** `TITLE_MAX_TOKENS = 30` is tight for a 50-character Cyrillic title (a 48-character example
is 29 `cl100k_base` tokens; local tokenizers are usually less efficient). `request_title` now has
`result.finish_reason` but uses it only for logging, so content with `finish_reason == "length"` is
accepted as a complete title. Truncation was not observed, hence Info.
**Fix:** Raise the limit to about 64 (the result is capped at 50 characters anyway), or when
`finish_reason == "length"` drop the last partial word before storing.

### IN-03: Invisible and bidirectional format characters survive sanitizing

**File:** `agent/titles.py:40`
**Issue:** `_CONTROL_RE` covers C0/C1 only. `clean_title(RLO + "evil" + ZWSP + " title")` (RLO = U+202E, ZWSP = U+200B) returns the
string unchanged, so U+202E, U+200B-U+200F and U+2066-U+2069 can reach the sidebar. Display-only
impact.
**Fix:** `"".join(ch for ch in text if unicodedata.category(ch) != "Cf")`.

### IN-04: Wrapper-tag neutralization matches only the exact bare tag

**File:** `agent/titles.py:37`
**Issue:** `</user_message x>` and `</user_message/>` pass through `build_title_messages` unchanged
(verified). Impact is bounded to at most 50 characters of plain text in the sender's own chat.
**Fix:** `<\s*/?\s*(?:user_message|assistant_answer)\b[^<>]*>`, or replace `<` and `>` in snippets.

### IN-05: A label-only first line discards a usable title on the next line

**File:** `agent/titles.py:113-117`
**Issue:** `clean_title("Title:\nНастройка сервера")` returns `None` (verified); the fallback is used
although the model answered. The prompt ends with `Title:`, which makes this echo plausible.
**Fix:** Iterate over the non-empty lines and return the first one that is usable after cleaning.

### IN-06: Function-local import hides an import cycle

**File:** `agent/titles.py:199-200`
**Issue:** `agent.events` imports `agent.ws`, which imports `agent.titles`, which imports
`agent.events` lazily. Documented, but it contradicts the "no circular dependencies" statement in
the project conventions.
**Fix:** Move `EventHub` / `hub` into a leaf module that imports neither `ws` nor `titles`.

### IN-07: A title frame can be overwritten by an in-flight `loadChats()`

**File:** `ui/static/app.js:2566-2579`, `ui/static/app.js:1034-1037`
**Issue:** `applyChatTitleUpdate` mutates the entry in `state.chats`. A `loadChats()` request sent
before the server committed the title and resolved after the frame replaces `state.chats` with the
older list, and the sidebar shows `New Chat` until the next reload. Small window.
**Fix:** Keep a `Map` of titles received by frame and re-apply it in `loadChats()`.

### IN-08: `test_title_lands_after_socket_closed` duplicates `test_title_frame_and_stored_title`

**File:** `tests/test_titles_ws.py:389-406`
**Issue:** Nothing forces the title to land after the socket closed, so the test does not check what
its name says.
**Fix:** Gate the title call on an event as `test_done_is_not_delayed_by_blocked_title_call` does,
close the socket, then release the gate; or delete the test.

### IN-09: Every HTTP 400/422 is labelled "reasoning control rejected", and the rejection is not remembered

**File:** `agent/titles.py:141-147`
**Issue:** The repeat fires on any 400 or 422, including ones unrelated to `reasoning_effort`
(unknown model, context too long, `max_tokens` not accepted). The event
`chat_title_reasoning_control_rejected` then states a cause that was not established, and the
response body that would distinguish the cases is not inspected. Separately, a backend that does
reject the field costs two requests and one INFO line on every new chat, because the outcome is not
cached.
**Fix:** Rename the event to something neutral (`chat_title_retry_without_reasoning_control`) or
check the error body for the field name before repeating. Optionally remember the rejection per
model in a module-level set and skip the first attempt next time.

### IN-10: `complete_chat_detailed` validates some response fields and not others

**File:** `agent/llm_client.py:100-111`
**Issue:** `content` and `completion_tokens` are type-checked, `finish_reason` is passed through as
is. A backend value that is not a string violates the declared `str | None` and is written verbatim
to the `chat_title_llm_unusable` log (verified with a dict value). A top-level body that is not an
object, `choices: [null]`, or a non-object `message` raise `AttributeError` instead of a typed
result; the title caller absorbs this with `except Exception`, but the method reads as tolerant and
is only partly so.
**Fix:**

```python
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        finish = choice.get("finish_reason")
        ...
            finish_reason=finish if isinstance(finish, str) else None,
```

and guard `isinstance(body, dict)` at the top.

### IN-11: Regression tests for the new behaviour are weaker than they look

**File:** `tests/test_titles.py:622-629`, `tests/test_titles.py:266-283`
**Issue:**
1. `test_build_title_messages_bounds_nested_tags` can only fail through its wall-clock assertion
   (`< 0.5`); the two `count` assertions also pass on the unbounded code. The unbounded code measured
   1.22 s on this machine, so on a faster machine a regression would go unnoticed.
   `test_fallback_title_bounds_input_before_regex` does it properly by asserting the length passed
   to `_strip_markup`.
2. No test covers the property the docs state, that the 20 s limit spans both attempts. The timeout
   test has a single slow call; the retry tests have no delay.

**Fix:** In (1) spy on `_neutralize_tags` and assert `len(text) <= limit * SNIPPET_PRECUT_FACTOR`.
In (2) add a test with a small `TITLE_TIMEOUT_SECONDS`, a first call that raises HTTP 400 after a
short sleep and a second call that hangs, asserting two calls, the fallback title and an elapsed
time near the limit.

### IN-12: The title request carries the DeepSeek API key to `LM_STUDIO_BASE_URL` (pre-existing)

**File:** `agent/llm_client.py:392-395`
**Issue:** The shared `llm_client` is built with `base_url=settings.LM_STUDIO_BASE_URL` and
`api_key=settings.DEEPSEEK_API_KEY`, so every request, now including two title requests per new chat
on a rejecting backend, sends `Authorization: Bearer <DeepSeek key>` to whatever host the LM Studio
URL names. Not introduced by this phase (line dates from 2026-09-14); recorded because the phase adds
calls through it.
**Fix:** Send the key only when the base URL is the DeepSeek endpoint, or use a separate setting for
the local backend.

## Convention findings (CONVENTION tier, never blocking)

Output of `gsd-tools verify conventions --check` on the changed files. All three lines are in code
that predates this phase. The Python files have no rule pack and were skipped by the tool.

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

_Reviewed: 2026-10-02T10:24:52Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
