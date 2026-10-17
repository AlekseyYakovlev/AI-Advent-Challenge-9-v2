"""Tests for the formatting helpers."""

import pytest

from agent.review_demo_format import format_duration, format_size


def test_format_size_small() -> None:
    """Sizes under one kilobyte are shown in bytes."""
    assert format_size(512) == "512 Б"


def test_format_size_kilobytes() -> None:
    """Larger sizes are scaled to the matching unit."""
    assert format_size(2048) == "2.0 КБ"


def test_format_size_negative() -> None:
    """A negative size is rejected."""
    with pytest.raises(ValueError):
        format_size(-1)


def test_format_duration() -> None:
    """Durations are split into minutes and seconds."""
    assert format_duration(75) == "1 мин 15 с"
    assert format_duration(5) == "5 с"
