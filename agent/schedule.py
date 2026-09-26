"""Pure schedule math and validation for scheduled jobs (cron, interval, one-shot)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

from cronsim import CronSim, CronSimError

from shared.config import settings
from shared.models import ScheduleType

TITLE_MAX_LENGTH = 200
PROMPT_MAX_LENGTH = 4000
CRON_MAX_LENGTH = 100
MAX_SCHEDULE_SPAN_SECONDS = 10 * 365 * 24 * 3600
MAX_DELAY_SECONDS = MAX_SCHEDULE_SPAN_SECONDS
MAX_INTERVAL_SECONDS = MAX_SCHEDULE_SPAN_SECONDS
MAX_RUNS_LIMIT = 1_000_000

MSG_TITLE_PROMPT_REQUIRED = "Заполните название и промпт"
MSG_BAD_SCHEDULE = "Укажите корректное расписание"
MSG_RUN_AT_PAST = "Время запуска уже прошло"
MSG_CRON_INVALID = "Некорректное cron-выражение. Нужно 5 полей: минута час день месяц день_недели."
MSG_MAX_RUNS_INVALID = "Максимум запусков должен быть не меньше 1"
MSG_MAX_RUNS_TOO_LARGE = f"Максимум запусков не больше {MAX_RUNS_LIMIT}"

_CRON_FIELD_COUNT = 5
_CRON_PROBE_START = datetime(2000, 1, 1)


def msg_min_interval(minimum: int) -> str:
    """Build the too-short-interval message for the given minimum in seconds."""
    return f"Минимальный интервал — {minimum} секунд"


class ScheduleValidationError(ValueError):
    """Schedule input rejected; `message` is Russian text safe to show to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message


@dataclass(frozen=True)
class ScheduleSpec:
    """Validated schedule definition ready to be stored on a job."""

    schedule_type: ScheduleType
    run_at: datetime | None
    interval_seconds: int | None
    cron_expr: str | None
    max_runs: int | None


def as_aware_utc(value: datetime) -> datetime:
    """Return value as a UTC-aware datetime (naive values are taken to be UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_local_naive(value_utc: datetime, tz: tzinfo | None) -> datetime:
    """Convert a UTC instant to naive wall-clock time in tz (OS local zone when None)."""
    try:
        return as_aware_utc(value_utc).astimezone(tz).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError) as exc:
        raise ScheduleValidationError(MSG_BAD_SCHEDULE) from exc


def _from_local_naive(naive: datetime, tz: tzinfo | None) -> datetime:
    """Interpret naive wall-clock time in tz (OS local zone when None) and return UTC-aware."""
    try:
        aware = naive.astimezone() if tz is None else naive.replace(tzinfo=tz)
        return aware.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ScheduleValidationError(MSG_BAD_SCHEDULE) from exc


def validate_cron(expr: str) -> str:
    """Validate a 5-field cron expression and return it whitespace-normalized."""
    stripped = expr.strip()
    if not stripped or len(stripped) > CRON_MAX_LENGTH:
        raise ScheduleValidationError(MSG_CRON_INVALID)
    fields = stripped.split()
    # CronSim also accepts 6-field expressions; the product contract is exactly 5.
    if len(fields) != _CRON_FIELD_COUNT:
        raise ScheduleValidationError(MSG_CRON_INVALID)
    normalized = " ".join(fields)
    try:
        next(CronSim(normalized, _CRON_PROBE_START))
    except (CronSimError, StopIteration, ValueError) as exc:
        raise ScheduleValidationError(MSG_CRON_INVALID) from exc
    return normalized


def next_cron_run(expr: str, after_utc: datetime, tz: tzinfo | None = None) -> datetime:
    """Return the first cron slot strictly after after_utc, evaluated in local wall time."""
    local_after = _to_local_naive(after_utc, tz)
    try:
        local_next = next(CronSim(expr, local_after))
    except (CronSimError, StopIteration, ValueError) as exc:
        raise ScheduleValidationError(MSG_CRON_INVALID) from exc
    return _from_local_naive(local_next, tz)


def _add_seconds(value: datetime, seconds: int) -> datetime:
    """Add seconds to value, reporting a result outside the datetime range as invalid input."""
    try:
        return value + timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ScheduleValidationError(MSG_BAD_SCHEDULE) from exc


def next_interval_run(anchor_utc: datetime, interval_seconds: int, now_utc: datetime) -> datetime:
    """Return the next slot on the anchor's cadence that is strictly after now_utc."""
    anchor = as_aware_utc(anchor_utc)
    now = as_aware_utc(now_utc)
    if anchor > now:
        return anchor
    steps = int((now - anchor).total_seconds() // interval_seconds) + 1
    return _add_seconds(anchor, interval_seconds * steps)


def parse_run_at(value: str, tz: tzinfo | None = None) -> datetime:
    """Parse an ISO timestamp (naive means local wall time) into a UTC-aware datetime."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (ValueError, AttributeError) as exc:
        raise ScheduleValidationError(MSG_BAD_SCHEDULE) from exc
    if parsed.tzinfo is None:
        return _from_local_naive(parsed, tz)
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ScheduleValidationError(MSG_BAD_SCHEDULE) from exc


def _is_int(value: object) -> bool:
    """Return True for real integers (bool is excluded)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_max_runs(max_runs: int | None) -> int | None:
    """Return max_runs when it is None or a positive integer."""
    if max_runs is None:
        return None
    if not _is_int(max_runs) or max_runs < 1:
        raise ScheduleValidationError(MSG_MAX_RUNS_INVALID)
    if max_runs > MAX_RUNS_LIMIT:
        raise ScheduleValidationError(MSG_MAX_RUNS_TOO_LARGE)
    return max_runs


def _build_once(
    delay_seconds: int | None, run_at: str | None, now: datetime, tz: tzinfo | None
) -> ScheduleSpec:
    """Build a one-shot spec from exactly one of delay_seconds or run_at."""
    has_run_at = run_at is not None and run_at != ""
    if (delay_seconds is not None) == has_run_at:
        raise ScheduleValidationError(MSG_BAD_SCHEDULE)
    if delay_seconds is not None:
        if not _is_int(delay_seconds) or not 1 <= delay_seconds <= MAX_DELAY_SECONDS:
            raise ScheduleValidationError(MSG_BAD_SCHEDULE)
        target = _add_seconds(as_aware_utc(now), delay_seconds)
    else:
        target = parse_run_at(run_at or "", tz)
        if target <= as_aware_utc(now):
            raise ScheduleValidationError(MSG_RUN_AT_PAST)
        if target > _add_seconds(as_aware_utc(now), MAX_DELAY_SECONDS):
            raise ScheduleValidationError(MSG_BAD_SCHEDULE)
    return ScheduleSpec(ScheduleType.ONCE, target, None, None, None)


def build_schedule_spec(
    schedule_type: str,
    *,
    delay_seconds: int | None,
    run_at: str | None,
    interval_seconds: int | None,
    cron: str | None,
    max_runs: int | None,
    now: datetime,
    tz: tzinfo | None = None,
    min_interval_seconds: int | None = None,
) -> ScheduleSpec:
    """Validate raw schedule inputs and return a normalized ScheduleSpec."""
    try:
        kind = ScheduleType(schedule_type)
    except ValueError as exc:
        raise ScheduleValidationError(MSG_BAD_SCHEDULE) from exc

    if kind == ScheduleType.ONCE:
        return _build_once(delay_seconds, run_at, now, tz)

    if kind == ScheduleType.INTERVAL:
        minimum = (
            settings.SCHEDULER_MIN_INTERVAL_SECONDS
            if min_interval_seconds is None
            else min_interval_seconds
        )
        if not _is_int(interval_seconds):
            raise ScheduleValidationError(MSG_BAD_SCHEDULE)
        if interval_seconds is None or interval_seconds < minimum:
            raise ScheduleValidationError(msg_min_interval(minimum))
        if interval_seconds > MAX_INTERVAL_SECONDS:
            raise ScheduleValidationError(MSG_BAD_SCHEDULE)
        return ScheduleSpec(
            ScheduleType.INTERVAL, None, interval_seconds, None, _validate_max_runs(max_runs)
        )

    if cron is None:
        raise ScheduleValidationError(MSG_CRON_INVALID)
    return ScheduleSpec(ScheduleType.CRON, None, None, validate_cron(cron), _validate_max_runs(max_runs))


def initial_next_run(spec: ScheduleSpec, now: datetime, tz: tzinfo | None = None) -> datetime:
    """Return the first fire time for a freshly created job."""
    if spec.schedule_type == ScheduleType.ONCE and spec.run_at is not None:
        return as_aware_utc(spec.run_at)
    if spec.schedule_type == ScheduleType.INTERVAL and spec.interval_seconds is not None:
        return _add_seconds(as_aware_utc(now), spec.interval_seconds)
    if spec.schedule_type == ScheduleType.CRON and spec.cron_expr is not None:
        return next_cron_run(spec.cron_expr, now, tz)
    raise ScheduleValidationError(MSG_BAD_SCHEDULE)


def next_run_after_claim(
    schedule_type: ScheduleType,
    *,
    old_next: datetime,
    now: datetime,
    interval_seconds: int | None,
    cron_expr: str | None,
    tz: tzinfo | None = None,
) -> datetime | None:
    """Return the slot to store once a due run is claimed (None when the job is finished)."""
    if schedule_type == ScheduleType.ONCE:
        return None
    if schedule_type == ScheduleType.INTERVAL and interval_seconds is not None:
        return next_interval_run(old_next, interval_seconds, now)
    if schedule_type == ScheduleType.CRON and cron_expr is not None:
        # From now, never from old_next: a long outage must not replay missed cron slots.
        return next_cron_run(cron_expr, now, tz)
    raise ScheduleValidationError(MSG_BAD_SCHEDULE)


def next_run_on_resume(
    schedule_type: ScheduleType,
    *,
    run_at: datetime | None,
    interval_seconds: int | None,
    cron_expr: str | None,
    now: datetime,
    tz: tzinfo | None = None,
) -> datetime:
    """Return the fire time for a job that is being resumed from pause."""
    if schedule_type == ScheduleType.INTERVAL and interval_seconds is not None:
        return _add_seconds(as_aware_utc(now), interval_seconds)
    if schedule_type == ScheduleType.CRON and cron_expr is not None:
        return next_cron_run(cron_expr, now, tz)
    if schedule_type == ScheduleType.ONCE and run_at is not None:
        # May already be in the past; it then fires late on the next tick.
        return as_aware_utc(run_at)
    raise ScheduleValidationError(MSG_BAD_SCHEDULE)
