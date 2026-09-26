"""Pure unit tests for scheduler schedule math and validation."""

from datetime import datetime, timedelta, timezone

import pytest

from agent import schedule as sched
from agent.schedule import (
    MSG_BAD_SCHEDULE,
    MSG_CRON_INVALID,
    MAX_DELAY_SECONDS,
    MAX_INTERVAL_SECONDS,
    MAX_RUNS_LIMIT,
    MSG_MAX_RUNS_INVALID,
    MSG_MAX_RUNS_TOO_LARGE,
    MSG_RUN_AT_PAST,
    ScheduleSpec,
    ScheduleValidationError,
    as_aware_utc,
    build_schedule_spec,
    initial_next_run,
    msg_min_interval,
    next_cron_run,
    next_interval_run,
    next_run_after_claim,
    next_run_on_resume,
    parse_run_at,
    validate_cron,
)
from shared.models import ScheduleType

UTC = timezone.utc
MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 9, 26, 10, 0, 0, tzinfo=UTC)


def test_validate_cron_accepts_five_fields() -> None:
    assert validate_cron("*/5 * * * *") == "*/5 * * * *"


def test_validate_cron_normalizes_whitespace() -> None:
    assert validate_cron("  0   12 * * 1 ") == "0 12 * * 1"


@pytest.mark.parametrize(
    "expr",
    ["* * * * * *", "@daily", "61 * * * *", "0 0 31 2 *", "*/0 * * * *", "", "1 " * 60],
)
def test_validate_cron_rejects_bad_expressions(expr: str) -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        validate_cron(expr)
    assert exc_info.value.message == MSG_CRON_INVALID


def test_validate_cron_rejects_over_max_length() -> None:
    expr = "0 " * 50 + "0"
    assert len(expr) > 100
    with pytest.raises(ScheduleValidationError):
        validate_cron(expr)


def test_next_cron_run_is_strictly_after_in_local_time() -> None:
    after = datetime(2026, 9, 26, 7, 5, 0, tzinfo=UTC)
    result = next_cron_run("*/5 * * * *", after, tz=MSK)
    assert result == datetime(2026, 9, 26, 7, 10, 0, tzinfo=UTC)
    assert result.tzinfo is not None and result.utcoffset() == timedelta(0)


def test_next_cron_run_rolls_to_next_day_when_local_slot_passed() -> None:
    after = datetime(2026, 9, 26, 7, 0, 0, tzinfo=UTC)
    result = next_cron_run("0 9 * * *", after, tz=MSK)
    assert result == datetime(2026, 9, 27, 6, 0, 0, tzinfo=UTC)


def test_next_cron_run_without_tz_returns_utc_aware() -> None:
    result = next_cron_run("* * * * *", NOW)
    assert result > NOW
    assert result.utcoffset() == timedelta(0)


def test_next_interval_run_keeps_future_anchor() -> None:
    anchor = NOW + timedelta(seconds=30)
    assert next_interval_run(anchor, 60, NOW) == anchor


def test_next_interval_run_next_slot() -> None:
    now = NOW + timedelta(seconds=30)
    assert next_interval_run(NOW, 60, now) == NOW + timedelta(seconds=60)


def test_next_interval_run_after_downtime_yields_one_slot() -> None:
    now = datetime(2026, 9, 26, 12, 0, 30, tzinfo=UTC)
    result = next_interval_run(NOW, 60, now)
    assert result == datetime(2026, 9, 26, 12, 1, 0, tzinfo=UTC)
    assert result > now


def test_next_interval_run_exact_slot_moves_strictly_forward() -> None:
    now = NOW + timedelta(seconds=120)
    assert next_interval_run(NOW, 60, now) == NOW + timedelta(seconds=180)


def test_parse_run_at_naive_is_local_wall_time() -> None:
    assert parse_run_at("2026-09-26T14:05:00", tz=MSK) == datetime(
        2026, 9, 26, 11, 5, tzinfo=UTC
    )


def test_parse_run_at_honours_offset() -> None:
    assert parse_run_at("2026-09-26T14:05:00+00:00", tz=MSK) == datetime(
        2026, 9, 26, 14, 5, tzinfo=UTC
    )


def test_parse_run_at_garbage_raises() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        parse_run_at("garbage")
    assert exc_info.value.message == MSG_BAD_SCHEDULE


def _once(**kwargs: object) -> ScheduleSpec:
    params: dict[str, object] = {
        "delay_seconds": None,
        "run_at": None,
        "interval_seconds": None,
        "cron": None,
        "max_runs": None,
        "now": NOW,
        "tz": MSK,
    }
    params.update(kwargs)
    return build_schedule_spec("once", **params)  # type: ignore[arg-type]


def test_build_once_with_delay() -> None:
    spec = _once(delay_seconds=60)
    assert spec.schedule_type == ScheduleType.ONCE
    assert spec.run_at == NOW + timedelta(seconds=60)
    assert spec.max_runs is None


def test_build_once_forces_max_runs_none() -> None:
    assert _once(delay_seconds=60, max_runs=5).max_runs is None


def test_build_once_zero_delay_rejected() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        _once(delay_seconds=0)
    assert exc_info.value.message == MSG_BAD_SCHEDULE


def test_build_once_both_delay_and_run_at_rejected() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        _once(delay_seconds=60, run_at="2026-09-26T14:00:00")
    assert exc_info.value.message == MSG_BAD_SCHEDULE


def test_build_once_neither_delay_nor_run_at_rejected() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        _once()
    assert exc_info.value.message == MSG_BAD_SCHEDULE


def test_build_once_run_at_in_past_rejected() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        _once(run_at="2026-09-26T10:00:00")
    assert exc_info.value.message == MSG_RUN_AT_PAST


def test_build_once_run_at_future_accepted() -> None:
    spec = _once(run_at="2026-09-26T14:05:00")
    assert spec.run_at == datetime(2026, 9, 26, 11, 5, tzinfo=UTC)


def test_build_interval_below_minimum_rejected() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        build_schedule_spec(
            "interval",
            delay_seconds=None,
            run_at=None,
            interval_seconds=5,
            cron=None,
            max_runs=None,
            now=NOW,
            min_interval_seconds=10,
        )
    assert exc_info.value.message == msg_min_interval(10)
    assert msg_min_interval(10) == "Минимальный интервал — 10 секунд"


def test_build_interval_max_runs_zero_rejected() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        build_schedule_spec(
            "interval",
            delay_seconds=None,
            run_at=None,
            interval_seconds=60,
            cron=None,
            max_runs=0,
            now=NOW,
        )
    assert exc_info.value.message == MSG_MAX_RUNS_INVALID


def test_build_interval_keeps_max_runs() -> None:
    spec = build_schedule_spec(
        "interval",
        delay_seconds=None,
        run_at=None,
        interval_seconds=60,
        cron=None,
        max_runs=3,
        now=NOW,
    )
    assert spec.schedule_type == ScheduleType.INTERVAL
    assert spec.interval_seconds == 60
    assert spec.max_runs == 3


def test_build_cron_bad_expression_rejected() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        build_schedule_spec(
            "cron",
            delay_seconds=None,
            run_at=None,
            interval_seconds=None,
            cron="bad",
            max_runs=None,
            now=NOW,
        )
    assert exc_info.value.message == MSG_CRON_INVALID


def test_build_cron_normalizes_expression() -> None:
    spec = build_schedule_spec(
        "cron",
        delay_seconds=None,
        run_at=None,
        interval_seconds=None,
        cron=" 0  9 * * * ",
        max_runs=None,
        now=NOW,
    )
    assert spec.cron_expr == "0 9 * * *"


def test_build_unknown_schedule_type_rejected() -> None:
    with pytest.raises(ScheduleValidationError) as exc_info:
        build_schedule_spec(
            "weekly",
            delay_seconds=None,
            run_at=None,
            interval_seconds=None,
            cron=None,
            max_runs=None,
            now=NOW,
        )
    assert exc_info.value.message == MSG_BAD_SCHEDULE


def test_initial_next_run_once() -> None:
    run_at = NOW + timedelta(minutes=5)
    spec = ScheduleSpec(ScheduleType.ONCE, run_at, None, None, None)
    assert initial_next_run(spec, NOW) == run_at


def test_initial_next_run_interval() -> None:
    spec = ScheduleSpec(ScheduleType.INTERVAL, None, 60, None, None)
    assert initial_next_run(spec, NOW) == NOW + timedelta(seconds=60)


def test_initial_next_run_cron() -> None:
    spec = ScheduleSpec(ScheduleType.CRON, None, None, "0 9 * * *", None)
    assert initial_next_run(spec, NOW, tz=MSK) == datetime(2026, 9, 27, 6, 0, tzinfo=UTC)


def test_next_run_after_claim_once_is_none() -> None:
    result = next_run_after_claim(
        ScheduleType.ONCE, old_next=NOW, now=NOW, interval_seconds=None, cron_expr=None
    )
    assert result is None


def test_next_run_after_claim_interval_keeps_cadence() -> None:
    now = NOW + timedelta(hours=2, seconds=30)
    result = next_run_after_claim(
        ScheduleType.INTERVAL, old_next=NOW, now=now, interval_seconds=60, cron_expr=None
    )
    assert result == datetime(2026, 9, 26, 12, 1, 0, tzinfo=UTC)
    assert result > now


def test_next_run_after_claim_cron_computed_from_now() -> None:
    old_next = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
    result = next_run_after_claim(
        ScheduleType.CRON,
        old_next=old_next,
        now=NOW,
        interval_seconds=None,
        cron_expr="0 9 * * *",
        tz=MSK,
    )
    assert result == datetime(2026, 9, 27, 6, 0, tzinfo=UTC)


def test_next_run_on_resume_interval() -> None:
    result = next_run_on_resume(
        ScheduleType.INTERVAL,
        run_at=None,
        interval_seconds=60,
        cron_expr=None,
        now=NOW,
    )
    assert result == NOW + timedelta(seconds=60)


def test_next_run_on_resume_cron() -> None:
    result = next_run_on_resume(
        ScheduleType.CRON,
        run_at=None,
        interval_seconds=None,
        cron_expr="0 9 * * *",
        now=NOW,
        tz=MSK,
    )
    assert result == datetime(2026, 9, 27, 6, 0, tzinfo=UTC)


def test_next_run_on_resume_once_keeps_past_run_at() -> None:
    past = NOW - timedelta(hours=1)
    result = next_run_on_resume(
        ScheduleType.ONCE, run_at=past, interval_seconds=None, cron_expr=None, now=NOW
    )
    assert result == past


def test_as_aware_utc_naive_gets_utc() -> None:
    naive = datetime(2026, 9, 26, 10, 0, 0)
    result = as_aware_utc(naive)
    assert result.tzinfo == UTC
    assert result.replace(tzinfo=None) == naive


def test_as_aware_utc_converts_offset() -> None:
    aware = datetime(2026, 9, 26, 13, 0, 0, tzinfo=MSK)
    assert as_aware_utc(aware) == datetime(2026, 9, 26, 10, 0, 0, tzinfo=UTC)


def test_module_avoids_zoneinfo_and_utcnow() -> None:
    source = open(sched.__file__, encoding="utf-8").read()
    assert "zoneinfo" not in source
    assert "utcnow" not in source


# --- out-of-range inputs are validation errors, never raw OverflowError/OSError ---------


def _spec(schedule_type: str, **fields: object) -> ScheduleSpec:
    base: dict[str, object] = {
        "delay_seconds": None,
        "run_at": None,
        "interval_seconds": None,
        "cron": None,
        "max_runs": None,
    }
    base.update(fields)
    return build_schedule_spec(schedule_type, now=NOW, tz=MSK, **base)


@pytest.mark.parametrize("delay", [MAX_DELAY_SECONDS + 1, 10**12, 10**30])
def test_once_delay_beyond_limit_is_rejected(delay: int) -> None:
    with pytest.raises(ScheduleValidationError) as info:
        _spec("once", delay_seconds=delay)
    assert info.value.message == MSG_BAD_SCHEDULE


def test_once_delay_at_limit_is_accepted() -> None:
    spec = _spec("once", delay_seconds=MAX_DELAY_SECONDS)
    assert spec.run_at == NOW + timedelta(seconds=MAX_DELAY_SECONDS)


@pytest.mark.parametrize("interval", [MAX_INTERVAL_SECONDS + 1, 10**12, 10**30])
def test_interval_beyond_limit_is_rejected(interval: int) -> None:
    with pytest.raises(ScheduleValidationError) as info:
        _spec("interval", interval_seconds=interval)
    assert info.value.message == MSG_BAD_SCHEDULE


@pytest.mark.parametrize("max_runs", [MAX_RUNS_LIMIT + 1, 10**30])
def test_max_runs_beyond_limit_is_rejected(max_runs: int) -> None:
    with pytest.raises(ScheduleValidationError) as info:
        _spec("interval", interval_seconds=60, max_runs=max_runs)
    assert info.value.message == MSG_MAX_RUNS_TOO_LARGE


def test_max_runs_at_limit_is_accepted() -> None:
    assert _spec("interval", interval_seconds=60, max_runs=MAX_RUNS_LIMIT).max_runs == MAX_RUNS_LIMIT


@pytest.mark.parametrize(
    "run_at",
    [
        "0001-01-01T00:00:00",
        "0001-01-01T00:00:00+05:00",
        "9999-12-31T23:59:59",
        "9999-12-31T23:59:59-12:00",
        "2037-01-01T00:00:00",
    ],
)
def test_run_at_extremes_are_validation_errors(run_at: str) -> None:
    with pytest.raises(ScheduleValidationError):
        _spec("once", run_at=run_at)


@pytest.mark.parametrize("run_at", ["0001-01-01T00:00:00", "0001-01-01T00:00:00+05:00"])
def test_parse_run_at_year_one_is_validation_error(run_at: str) -> None:
    for tz in (None, MSK):
        with pytest.raises(ScheduleValidationError):
            parse_run_at(run_at, tz)


def test_slot_math_overflow_is_validation_error() -> None:
    huge = ScheduleSpec(ScheduleType.INTERVAL, None, 10**13, None, None)
    with pytest.raises(ScheduleValidationError):
        initial_next_run(huge, NOW)
    with pytest.raises(ScheduleValidationError):
        next_run_on_resume(
            ScheduleType.INTERVAL, run_at=None, interval_seconds=10**13, cron_expr=None, now=NOW
        )
    with pytest.raises(ScheduleValidationError):
        next_interval_run(NOW - timedelta(seconds=1), 10**13, NOW)


def test_next_cron_run_at_datetime_edge_is_validation_error() -> None:
    edge = datetime(9999, 12, 31, 23, 59, 30, tzinfo=UTC)
    with pytest.raises(ScheduleValidationError):
        next_cron_run("* * * * *", edge, tz=MSK)
