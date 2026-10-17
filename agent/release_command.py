"""Grammar, plan types and WebSocket frames of the /release command."""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from shared.logger import get_logger

logger = get_logger(__name__)

RELEASE_SERVER_NAME = "release-git"
GITHUB_SERVER_NAME = "github"

FRAME_PLAN = "release_plan"
FRAME_PROGRESS = "release_progress"
FRAME_RESULT = "release_result"
CONFIRM_FRAME_TYPE = "release_confirm"

STEP_KEYS: tuple[str, ...] = ("commit", "tag", "push", "release")

MSG_NO_PLAN = "Нет плана релиза. Сначала введите /release."
MSG_NOTHING_TO_RELEASE = (
    "В диапазоне {range} нет значимых изменений (отфильтровано {dropped} коммитов). "
    "Укажите другой диапазон: /release {example}"
)
MSG_VERSION_TAKEN = "Версия {version} уже существует как тег. Выберите другую."
MSG_VERSION_NOT_GREATER = "Версия должна быть больше последнего тега ({last})."
MSG_VERSION_FORMAT = "Формат версии: vX.Y, например v4.1."
MSG_DIRTY_TREE = (
    "Рабочее дерево не чистое. Закоммитьте или отмените изменения и повторите /release."
)
MSG_HEAD_CHANGED = "HEAD изменился после построения плана. Пересоздайте план."
MSG_WRITE_ACCESS_MISSING = (
    "Шаг недоступен: нет доступа на запись. Тег отправлен, Release не создан. "
    "Используйте текст ниже для ручной публикации."
)
MSG_LLM_FAILED = "Не удалось получить черновик от модели. Повторите /release или смените модель."
MSG_NOT_MAIN = "Релиз готовится из ветки {branch}, а не main."
MSG_USAGE = "Формат: /release, /release v4.0..HEAD или /release confirm [vX.Y] [название]."
MSG_SERVER_NOT_CONFIGURED = (
    "Сервер release-git не настроен. Добавьте его в «MCP серверы» с флагом --allow-release "
    "(см. docs/USER_GUIDE.md)."
)
MSG_SERVER_NOT_CONNECTED = "Сервер release-git не подключился."
MSG_PLAN_STALE = "План устарел: есть более новый план релиза."
MSG_CONFIRM_RUNNING = "Подтверждение уже выполняется."
MSG_LOCKED_AFTER_COMMIT = "Версия и название зафиксированы: CHANGELOG.md уже закоммичен."
MSG_GITHUB_PRS_SKIPPED = "Сервер GitHub недоступен: Pull Request не учтены."
MSG_REMOTE_TAGS_UNKNOWN = (
    "Не удалось прочитать теги origin: проверка занятых версий только по локальным тегам."
)
MSG_VERSION_FALLBACK = "Модель не выбрала версию из кандидатов; версия выбрана по правилу."
MSG_MANUAL_RELEASE_HINT = (
    "Создайте черновик Release вручную на GitHub для тега {tag} и вставьте текст ниже."
)

_RELEASE_PATTERN = re.compile(
    r"^\s*/release(?:\s+(?P<arg>.*?))?\s*$", re.IGNORECASE | re.DOTALL
)
_RANGE = re.compile(r"[^\s.-][^\s]*?\.\.[^\s.][^\s]*")
_VERSION_ARG = re.compile(r"v\d+(?:\.\d+)*", re.IGNORECASE)

_STEP_LABELS: dict[str, str] = {
    "commit": "Коммит CHANGELOG.md",
    "tag": "Аннотированный тег {version}",
    "push": "Push ветки и тега",
    "release": "Черновик GitHub Release",
}


class StepState(str, Enum):
    """Lifecycle state of one release step."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ReleaseCommand:
    """A parsed /release invocation."""

    kind: Literal["plan", "confirm", "usage"]
    range_spec: str | None = None
    version: str | None = None
    title: str | None = None
    plan_id: str | None = None


@dataclass(frozen=True)
class ToolOutcome:
    """Result of one release or GitHub tool call."""

    ok: bool
    text: str
    truncated: bool = False


ToolCaller = Callable[[str, str, dict[str, Any]], Awaitable[ToolOutcome]]
FrameSender = Callable[[dict[str, Any]], Awaitable[None]]


def _default_steps() -> dict[str, StepState]:
    """Return a fresh step map with every step pending."""
    return {key: StepState.PENDING for key in STEP_KEYS}


@dataclass
class ReleasePlan:
    """A prepared release awaiting confirmation, plus its execution state."""

    plan_id: str
    chat_id: int
    user_id: int
    range_spec: str
    branch: str | None
    head_sha: str
    last_tag: str
    candidates: list[str]
    taken: list[str]
    version: str
    title: str
    rationale: str
    changelog_body: str
    counts: dict[str, int]
    warnings: list[str]
    coverage_missing: list[str]
    owner_repo: str | None
    blocked_reason: str | None = None
    steps: dict[str, StepState] = field(default_factory=_default_steps)
    commit_sha: str | None = None
    release_text: str | None = None
    release_url: str | None = None
    running: bool = False
    stale: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def new_plan_id() -> str:
    """Return a fresh random plan identifier."""
    return uuid.uuid4().hex


def parse_release(content: str) -> ReleaseCommand | None:
    """Parse chat text into a /release command, or None when it is not one."""
    match = _RELEASE_PATTERN.match(content)
    if match is None:
        return None
    arg = (match.group("arg") or "").strip()
    if not arg:
        return ReleaseCommand(kind="plan")
    head, _, rest = arg.partition(" ")
    if head.lower() == "confirm":
        return _parse_confirm(rest.strip())
    if "..." in arg or _RANGE.fullmatch(arg) is None:
        return ReleaseCommand(kind="usage")
    return ReleaseCommand(kind="plan", range_spec=arg)


def _parse_confirm(rest: str) -> ReleaseCommand:
    """Split the confirm arguments into an optional version and an optional title."""
    if not rest:
        return ReleaseCommand(kind="confirm")
    first, _, tail = rest.partition(" ")
    if _VERSION_ARG.fullmatch(first):
        return ReleaseCommand(kind="confirm", version=first, title=tail.strip() or None)
    return ReleaseCommand(kind="confirm", title=rest)


def _str_or_none(value: Any) -> str | None:
    """Return the value when it is a non-empty string."""
    return value if isinstance(value, str) and value else None


def parse_release_frame(raw: Any) -> ReleaseCommand | None:
    """Parse the typed release_confirm button frame into a confirm command."""
    if not isinstance(raw, dict) or raw.get("type") != CONFIRM_FRAME_TYPE:
        return None
    return ReleaseCommand(
        kind="confirm",
        version=_str_or_none(raw.get("version")),
        title=_str_or_none(raw.get("title")),
        plan_id=_str_or_none(raw.get("plan_id")),
    )


def is_release_command(raw: Any) -> bool:
    """Return True for a /release chat message or the release_confirm frame."""
    if not isinstance(raw, dict):
        return False
    if raw.get("type") == CONFIRM_FRAME_TYPE:
        return True
    content = raw.get("content")
    return isinstance(content, str) and parse_release(content) is not None


def step_label(key: str, version: str) -> str:
    """Return the Russian label of a step."""
    return _STEP_LABELS[key].format(version=version)


def _steps_payload(
    statuses: dict[str, StepState], version: str
) -> list[dict[str, str]]:
    """Render step statuses as label rows in the fixed step order."""
    return [
        {"key": key, "label": step_label(key, version), "status": statuses[key].value}
        for key in STEP_KEYS
    ]


def plan_frame(plan: ReleasePlan, tag_message: str) -> dict[str, Any]:
    """Build the release_plan frame shown as a plan card."""
    return {
        "type": FRAME_PLAN,
        "plan_id": plan.plan_id,
        "range": plan.range_spec,
        "branch": plan.branch,
        "head_sha": plan.head_sha[:8],
        "last_tag": plan.last_tag,
        "candidates": list(plan.candidates),
        "taken": list(plan.taken),
        "version": plan.version,
        "title": plan.title,
        "tag_message": tag_message,
        "rationale": plan.rationale,
        "changelog": plan.changelog_body,
        "counts": dict(plan.counts),
        "warnings": list(plan.warnings),
        "coverage_missing": list(plan.coverage_missing),
        "blocked": plan.blocked_reason,
        "steps": _steps_payload(plan.steps, plan.version),
        "stored": False,
    }


def progress_frame(
    plan: ReleasePlan, key: str, status: StepState, detail: str | None = None
) -> dict[str, Any]:
    """Build the release_progress frame for one step transition."""
    return {
        "type": FRAME_PROGRESS,
        "plan_id": plan.plan_id,
        "step": key,
        "status": status.value,
        "detail": detail,
    }


def result_frame(
    plan: ReleasePlan,
    outcome: Literal["done", "partial", "failed", "blocked"],
    statuses: dict[str, StepState],
    reason: str | None,
    hint: str | None,
    release_text: str,
    tag_message: str,
) -> dict[str, Any]:
    """Build the final release_result frame."""
    return {
        "type": FRAME_RESULT,
        "plan_id": plan.plan_id,
        "outcome": outcome,
        "steps": _steps_payload(statuses, plan.version),
        "reason": reason,
        "hint": hint,
        "release_url": plan.release_url,
        "tag": plan.version,
        "commit_sha": plan.commit_sha[:8] if plan.commit_sha else None,
        "version": plan.version,
        "title": plan.title,
        "tag_message": tag_message,
        "release_text": release_text,
        "locked": plan.steps["commit"] == StepState.DONE,
        "stored": False,
    }
