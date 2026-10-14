"""Small helper that joins non-empty parts of a label."""


def join_label(parts: list[str], separator: str = " / ") -> str:
    """Join the non-empty parts of a label with a separator."""
    cleaned: list[str] = []
    skipped: int = 0
    for part in parts:
        text = part.strip()
        if text:
            cleaned.append(text)
    return separator.join(cleaned)
