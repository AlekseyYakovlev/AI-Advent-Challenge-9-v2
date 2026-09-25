"""Tool-use rule text and a heuristic detecting claimed-but-not-performed actions."""

import re
from typing import Any

TOOL_TRACE_HEADER = "[Tool calls actually executed for this reply]"

# Caps dispatched tool rounds per chat turn so a model cannot loop forever.
MAX_TOOL_ROUNDS = 5

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
