"""Tool-use rule text and a heuristic detecting claimed-but-not-performed actions."""

import re
from datetime import datetime, timedelta
from typing import Any

TOOL_TRACE_HEADER = "[Tool calls actually executed for this reply]"

# Caps dispatched tool rounds per chat turn so a model cannot loop forever.
# Ten rounds because "commit, push to a branch, open an MR" alone takes about six.
MAX_TOOL_ROUNDS = 15

TOOL_USE_RULE = (
    "Tool use rule: never say an action was performed unless you called the "
    "corresponding tool in this reply. Past actions mentioned in the history are not a "
    "reason to skip a tool call - if an action needs a tool, call it."
)

ACTION_CLAIM_REMINDER = (
    "You did not call any tool in your previous reply, so that action was NOT performed. "
    "If the request needs a tool, call it now. Otherwise, tell the user plainly that you "
    "did not perform the action."
)

ACTION_ANNOUNCE_REMINDER = (
    "You described the next step but did not call any tool, so nothing was done. "
    "Call the needed tool now, and keep going until every step of the user's request is done."
)

TOOL_ERROR_REMINDER = (
    "A tool call above returned an error. Read the error message, fix the arguments or try a "
    "different tool or approach, and continue the task. Do not give up after one error."
)

MULTI_STEP_TOOL_HINT = (
    "Finish ALL steps of a multi-step request by calling tools one after another before "
    "the final answer. Filesystem tools act on the LOCAL disk; GitLab tools act on the REMOTE "
    "repository. To find the local folder for a GitLab task, first call list_projects for the "
    "project path (group/name): the matching local folder is named like its last segment and "
    "its <folder>/.git/config remote URL points to that project. Never explore folders whose "
    "remote is another project. Remote file paths are relative to the repository root, never "
    "local folder names. To commit local changes, read the local files, then call commit_files "
    "(branch, commit message, actions), then create_merge_request. When a tool returns an "
    "error, read it and try another approach."
)

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_PREVIEW_MAX_CHARS = 200


def strip_tool_use_rule(messages: list[dict[str, Any]]) -> None:
    """Drop the appended TOOL_USE_RULE suffix from a leading system message, in place."""
    if not messages:
        return
    first = messages[0]
    if first.get("role") != "system":
        return
    content = first.get("content")
    if not isinstance(content, str):
        return
    suffix = "\n\n" + TOOL_USE_RULE
    if not content.endswith(suffix):
        return
    messages[0] = {**first, "content": content[: -len(suffix)]}


_PARTICIPLE_ENDINGS = r"(?:л|ла|ли|н|на|но|ны)"
_PAST_ONLY_ENDINGS = r"(?:л|ла|ли)"
_YO_PARTICIPLE = r"[её]н(?:а|о|ы)?"

_ACTION_VERB_RE = re.compile(
    r"(?<!\w)(?:"
    rf"созда{_PARTICIPLE_ENDINGS}"
    rf"|записа{_PARTICIPLE_ENDINGS}"
    rf"|скопирова{_PARTICIPLE_ENDINGS}"
    rf"|переименова{_PARTICIPLE_ENDINGS}"
    rf"|удали{_PAST_ONLY_ENDINGS}|удал{_YO_PARTICIPLE}"
    rf"|перемести{_PAST_ONLY_ENDINGS}|перемещ{_YO_PARTICIPLE}"
    rf"|сохрани{_PAST_ONLY_ENDINGS}|сохран{_YO_PARTICIPLE}"
    r"|created|wrote|written|copied|deleted|removed|moved|saved|renamed"
    r")(?!\w)",
    re.IGNORECASE,
)

_OBJECT_NOUN_RE = re.compile(
    r"файл|каталог|папк|директори|документ|памят|задач"
    r"|file|folder|directory|document|memory|task",
    re.IGNORECASE,
)

_NEGATION_OR_FUTURE_RE = re.compile(
    r"(?<!\w)(?:не|not|will|be|can|будет|будут)\s|n't",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_NEGATION_WINDOW_CHARS = 15


def _is_negated_or_future(sentence: str, verb_start: int) -> bool:
    """Return True when the text right before the verb negates it or makes it future."""
    window = sentence[max(0, verb_start - _NEGATION_WINDOW_CHARS):verb_start]
    return _NEGATION_OR_FUTURE_RE.search(window) is not None


_CANCEL_INTENT_RE = re.compile(
    r"(?<!\w)(?:"
    r"(?:отмени|отменить|отмена|останови|остановить|прекрати|прекратить|удали|удалить"
    r"|удаление|убери|убрать|выключи|выключить)(?:те)?"
    r"|cancel|stop|delete|remove|disable"
    r")(?!\w)",
    re.IGNORECASE,
)

_NEGATION_WORDS = frozenset(
    {"не", "нельзя", "don't", "don’t", "dont", "not", "never"},
)
_NEGATION_WINDOW_WORDS = 2


def user_asked_to_cancel(text: str) -> bool:
    """Return True when the text asks to cancel/stop/delete something and does not negate it."""
    for match in _CANCEL_INTENT_RE.finditer(text):
        preceding = re.findall(r"[\w'’]+", text[: match.start()].lower())
        if not _NEGATION_WORDS.intersection(preceding[-_NEGATION_WINDOW_WORDS:]):
            return True
    return False


def _sentence_claims_action(sentence: str) -> bool:
    """Return True when one sentence states a completed action on a file-like object."""
    stripped = sentence.strip()
    if not stripped or stripped.endswith("?"):
        return False
    if _OBJECT_NOUN_RE.search(stripped) is None:
        return False
    return any(
        not _is_negated_or_future(stripped, match.start())
        for match in _ACTION_VERB_RE.finditer(stripped)
    )


def looks_like_action_claim(text: str) -> bool:
    """Return True when the reply claims a completed file/memory/task action.

    A cheap heuristic: past-tense action verb plus an object noun in the same
    non-question sentence, ignoring negated and future phrasings.
    """
    if not text:
        return False
    return any(_sentence_claims_action(sentence) for sentence in _SENTENCE_SPLIT_RE.split(text))


_FUTURE_VERB_RE = re.compile(
    r"(?<!\w)(?:создам|запишу|прочитаю|проверю|выполню|сделаю|отображу|покажу|открою|посмотрю"
    r"|получу|закоммичу|запушу|отправлю|удалю|сохраню|перемещу|скопирую|выведу|найду|начну"
    r"|создаю)(?!\w)",
    re.IGNORECASE,
)

_SEQUENCED_FIRST_PERSON_RE = re.compile(
    r"(?<!\w)(?:сначала|сейчас|теперь|далее|затем|потом)(?!\w)[^.!?\n]*?(?<!\w)[а-яё]+[ую](?!\w)",
    re.IGNORECASE,
)

_ENGLISH_INTENT_RE = re.compile(
    r"(?<!\w)(?:I['’]ll|I will|I['’]m going to|I am going to|let me)\s+(?!know\b)\w+",
    re.IGNORECASE,
)


def _sentence_announces_action(sentence: str) -> bool:
    """Return True when one non-question sentence states a next step the speaker will take."""
    stripped = sentence.strip()
    if not stripped or stripped.endswith("?"):
        return False
    return (
        _FUTURE_VERB_RE.search(stripped) is not None
        or _SEQUENCED_FIRST_PERSON_RE.search(stripped) is not None
        or _ENGLISH_INTENT_RE.search(stripped) is not None
    )


def looks_like_action_announcement(text: str) -> bool:
    """Return True when the reply announces a next step but calls no tool for it.

    A cheap heuristic, the counterpart of looks_like_action_claim: a first-person future
    action phrase in a non-question sentence, in a reply that does not end with a question.
    """
    if not text or text.rstrip().endswith("?"):
        return False
    return any(
        _sentence_announces_action(sentence) for sentence in _SENTENCE_SPLIT_RE.split(text)
    )


def build_clock_line(now: datetime) -> str:
    """Return the system-message line giving the current local date, time and UTC offset."""
    offset: timedelta = now.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "-" if total_minutes < 0 else "+"
    hours, minutes = divmod(abs(total_minutes), 60)
    return (
        f"Current local date and time: {now.strftime('%Y-%m-%d %H:%M')} "
        f"(UTC{sign}{hours:02d}:{minutes:02d})"
    )


def _preview(text: str) -> str:
    """Collapse whitespace to single spaces and cut to the preview length."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) > _PREVIEW_MAX_CHARS:
        return collapsed[: _PREVIEW_MAX_CHARS - 1] + "…"
    return collapsed


def build_tool_fallback_summary(results: list[dict[str, Any]], user_text: str) -> str:
    """Return a fixed summary of tool results for a turn whose final model text was empty."""
    russian = _CYRILLIC_RE.search(user_text) is not None
    header = (
        "Инструменты выполнены, но модель не дала ответа. Результаты:"
        if russian
        else "Tools ran but the model gave no answer. Results:"
    )
    lines: list[str] = [header]
    for result in results:
        mcp = result.get("mcp")
        label = f"{mcp['server_name']}/{mcp['tool']}" if mcp else result.get("name", "?")
        if result.get("ok"):
            status = "OK"
        else:
            status = "ошибка" if russian else "error"
        body = result.get("result_text") or result.get("content") or ""
        lines.append(f"- {label}: {status} — {_preview(str(body))}")
    return "\n".join(lines)


def _hold_start(buf: str) -> int:
    """Return the index from which buf may still turn into a header and must be withheld."""
    start = len(buf)
    for length in range(min(len(TOOL_TRACE_HEADER) - 1, len(buf)), 0, -1):
        if TOOL_TRACE_HEADER.startswith(buf[-length:]):
            start = len(buf) - length
            break
    while start > 0 and buf[start - 1].isspace():
        start -= 1
    return start


class TraceLeakFilter:
    """Streaming filter that drops a model-imitated tool-trace block and what follows it."""

    def __init__(self) -> None:
        self._pending: str = ""
        self._dropped: bool = False

    def feed(self, chunk: str) -> str:
        """Return the part of the chunk that is safe to emit now."""
        if self._dropped:
            return ""
        buf: str = self._pending + chunk
        idx: int = buf.find(TOOL_TRACE_HEADER)
        if idx != -1:
            self._dropped = True
            self._pending = ""
            return buf[:idx].rstrip()
        start: int = _hold_start(buf)
        self._pending = buf[start:]
        return buf[:start]

    def flush(self) -> str:
        """Return any withheld text once the stream ends."""
        if self._dropped:
            return ""
        tail: str = self._pending
        self._pending = ""
        return tail
