"""Per-chat task memory for RAG chats: document, merge, snapshot, extraction."""

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.rag_rank import _DIGIT_TOKEN_RE, _normalise_ws, clean_llm_text, stems
from shared.logger import get_logger

logger = get_logger(__name__)

GOAL_MAX_CHARS = 300
ITEM_MAX_CHARS = 200
LIST_CAP = 12
DEDUP_JACCARD = 0.8
USER_OVERLAP_MIN = 0.5
EXTRACT_MAX_TOKENS = 400

_WHITESPACE_RE: re.Pattern[str] = re.compile(r"\s+")
_FENCE_RE: re.Pattern[str] = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class TaskMemoryItem(BaseModel):
    """One clarified point or constraint with a stable id."""

    id: int
    text: str


class TaskMemoryDoc(BaseModel):
    """The task-memory document of one chat: goal, clarified points, constraints."""

    goal: str | None = None
    clarified: list[TaskMemoryItem] = Field(default_factory=list)
    constraints: list[TaskMemoryItem] = Field(default_factory=list)
    next_id: int = 1

    def is_empty(self) -> bool:
        """Return True when the document holds no goal and no items."""
        return not self.goal and not self.clarified and not self.constraints


class TaskMemoryDelta(BaseModel):
    """What one turn adds to the document, as reported by the extraction call."""

    model_config = ConfigDict(extra="ignore", strict=True)

    goal: str | None = None
    goal_changed: bool = False
    clarified: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class MergeInfo:
    """What a merge changed: new goal flag, ids of new items, items dropped by the cap."""

    goal_new: bool
    new_ids: tuple[int, ...]
    dropped_by_cap: int


def _collapse(text: str, limit: int) -> str:
    """Strip, collapse whitespace and clip to a character limit."""
    return _WHITESPACE_RE.sub(" ", text or "").strip()[:limit].strip()


def _jaccard(left: set[str], right: set[str]) -> float:
    """Jaccard similarity of two stem sets; 0 when either is empty."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _is_duplicate(text: str, known: list[str]) -> bool:
    """Return True when text equals a known item after normalisation or is a near copy."""
    normal = _normalise_ws(text)
    text_stems = stems(text)
    for other in known:
        if _normalise_ws(other) == normal:
            return True
        if _jaccard(text_stems, stems(other)) >= DEDUP_JACCARD:
            return True
    return False


def _merge_goal(doc: TaskMemoryDoc, delta: TaskMemoryDelta) -> bool:
    """Apply the sticky-goal rule in place; return True when the goal changed."""
    goal = _collapse(delta.goal or "", GOAL_MAX_CHARS)
    if not goal:
        return False
    if not doc.goal:
        doc.goal = goal
        return True
    if delta.goal_changed and _normalise_ws(goal) != _normalise_ws(doc.goal):
        doc.goal = goal
        return True
    return False


def _merge_list(
    doc: TaskMemoryDoc, target: list[TaskMemoryItem], texts: list[str]
) -> tuple[list[int], int]:
    """Append unique items to target in place; return (new ids, count dropped by the cap)."""
    new_ids: list[int] = []
    dropped = 0
    for raw in texts:
        text = _collapse(raw, ITEM_MAX_CHARS)
        if not text:
            continue
        known = [item.text for item in doc.clarified + doc.constraints]
        if _is_duplicate(text, known):
            continue
        if len(target) >= LIST_CAP:
            dropped += 1
            continue
        target.append(TaskMemoryItem(id=doc.next_id, text=text))
        new_ids.append(doc.next_id)
        doc.next_id += 1
    return new_ids, dropped


def merge_delta(doc: TaskMemoryDoc, delta: TaskMemoryDelta) -> tuple[TaskMemoryDoc, MergeInfo]:
    """Merge a delta into a copy of the document by the code-owned rules."""
    merged = doc.model_copy(deep=True)
    goal_new = _merge_goal(merged, delta)
    clarified_ids, dropped_clarified = _merge_list(merged, merged.clarified, delta.clarified)
    constraint_ids, dropped_constraints = _merge_list(
        merged, merged.constraints, delta.constraints
    )
    info = MergeInfo(
        goal_new=goal_new,
        new_ids=tuple(clarified_ids + constraint_ids),
        dropped_by_cap=dropped_clarified + dropped_constraints,
    )
    return merged, info


def _digits_subset(text: str, user_text: str) -> bool:
    """Return True when every number of text also occurs in the user message."""
    return set(_DIGIT_TOKEN_RE.findall(text)) <= set(_DIGIT_TOKEN_RE.findall(user_text))


def _is_user_stated(text: str, user_stems: set[str], user_text: str) -> bool:
    """Return True when enough of the item's words and all its numbers come from the user."""
    if not _digits_subset(text, user_text):
        return False
    item_stems = stems(text)
    if not item_stems:
        return bool(_DIGIT_TOKEN_RE.findall(text))
    return len(item_stems & user_stems) / len(item_stems) >= USER_OVERLAP_MIN


def filter_user_stated(delta: TaskMemoryDelta, user_text: str) -> tuple[TaskMemoryDelta, int]:
    """Drop items and a goal that do not come from the user's message; return the drop count."""
    user_stems = stems(user_text)
    dropped = 0
    kept: dict[str, list[str]] = {"clarified": [], "constraints": []}
    for field in ("clarified", "constraints"):
        for text in getattr(delta, field):
            if _is_user_stated(text, user_stems, user_text):
                kept[field].append(text)
            else:
                dropped += 1
    goal = delta.goal
    goal_changed = delta.goal_changed
    if goal and not (stems(goal) & user_stems):
        goal = None
        goal_changed = False
        dropped += 1
    filtered = TaskMemoryDelta(
        goal=goal,
        goal_changed=goal_changed,
        clarified=kept["clarified"],
        constraints=kept["constraints"],
    )
    return filtered, dropped


def parse_delta(raw: str | None) -> TaskMemoryDelta | None:
    """Parse the extraction reply into a delta; None when it is not a valid JSON object."""
    text = _FENCE_RE.sub("", clean_llm_text(raw).strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
        if not isinstance(data, dict):
            return None
        return TaskMemoryDelta.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return None


def _items_payload(items: list[TaskMemoryItem]) -> list[dict[str, Any]]:
    """Items as id/text dicts."""
    return [{"id": item.id, "text": item.text} for item in items]


def task_state_dict(doc: TaskMemoryDoc) -> dict[str, Any]:
    """The document as the API shape: goal plus the two item lists."""
    return {
        "goal": doc.goal,
        "clarified": _items_payload(doc.clarified),
        "constraints": _items_payload(doc.constraints),
    }


def build_snapshot(doc: TaskMemoryDoc, info: MergeInfo | None, failed: bool) -> dict[str, Any]:
    """Snapshot of the document after a turn, stored in the RAG payload and sent to the UI."""
    snapshot = task_state_dict(doc)
    snapshot["new"] = {
        "goal": bool(info and info.goal_new),
        "ids": list(info.new_ids) if info else [],
    }
    snapshot["failed"] = failed
    return snapshot


def doc_from_snapshot(snapshot: dict[str, Any]) -> TaskMemoryDoc:
    """Rebuild a document from a snapshot; an empty document when it is malformed."""
    try:
        goal = snapshot.get("goal")
        clarified = [TaskMemoryItem.model_validate(item) for item in snapshot.get("clarified", [])]
        constraints = [
            TaskMemoryItem.model_validate(item) for item in snapshot.get("constraints", [])
        ]
        if goal is not None and not isinstance(goal, str):
            return TaskMemoryDoc()
    except (AttributeError, TypeError, ValidationError):
        return TaskMemoryDoc()
    ids = [item.id for item in clarified + constraints]
    return TaskMemoryDoc(
        goal=goal,
        clarified=clarified,
        constraints=constraints,
        next_id=max(ids) + 1 if ids else 1,
    )


def render_prompt_lines(doc: TaskMemoryDoc) -> str | None:
    """The memory block for the system prompt; None for an empty document."""
    if doc.is_empty():
        return None
    lines = ["Память задачи (этот чат):"]
    if doc.goal:
        lines.append(f"Цель диалога: {doc.goal}")
    if doc.clarified:
        lines.append("Уточнено пользователем: " + "; ".join(i.text for i in doc.clarified))
    if doc.constraints:
        lines.append(
            "Ограничения и термины (соблюдай их): " + "; ".join(i.text for i in doc.constraints)
        )
    return "\n".join(lines)


def set_goal(doc: TaskMemoryDoc, goal: str) -> TaskMemoryDoc:
    """Return a copy with the goal set (stripped, clipped); an empty goal clears it."""
    updated = doc.model_copy(deep=True)
    updated.goal = _collapse(goal, GOAL_MAX_CHARS) or None
    return updated


def remove_item(doc: TaskMemoryDoc, item_id: int) -> TaskMemoryDoc | None:
    """Return a copy without the item with that id, or None when no list holds it."""
    updated = doc.model_copy(deep=True)
    for items in (updated.clarified, updated.constraints):
        for item in items:
            if item.id == item_id:
                items.remove(item)
                return updated
    return None
