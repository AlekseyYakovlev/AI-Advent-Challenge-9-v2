"""Formatting helpers for human-readable sizes and durations."""

_UNITS: tuple[str, ...] = ("Б", "КБ", "МБ", "ГБ")


def format_size(size_bytes: int) -> str:
    """Return a size in bytes as a short human-readable string."""
    if size_bytes < 0:
        raise ValueError("size must not be negative")
    value = float(size_bytes)
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    if unit == 0:
        return f"{int(value)} {_UNITS[unit]}"
    return f"{value:.1f} {_UNITS[unit]}"


def format_duration(seconds: float) -> str:
    """Return a duration in seconds as minutes and seconds."""
    if seconds < 0:
        raise ValueError("duration must not be negative")
    total = int(seconds)
    minutes, rest = divmod(total, 60)
    if minutes == 0:
        return f"{rest} с"
    return f"{minutes} мин {rest} с"
