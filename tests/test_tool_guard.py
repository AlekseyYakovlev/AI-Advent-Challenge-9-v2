"""Unit tests for the claimed-action heuristic."""

import pytest

from agent.tool_guard import looks_like_action_claim


@pytest.mark.parametrize(
    "text",
    [
        "Файл 1.txt создан.",
        "Готово! Я создал файл 1.txt в каталоге test",
        "Текст записан в файл 1.txt",
        "Файл скопирован в 2.txt",
        "Каталог удалён.",
        "I have created the file notes.txt.",
        "File copied to 2.txt.",
    ],
)
def test_claims_are_detected(text: str) -> None:
    """Completed-action statements about file-like objects are flagged."""
    assert looks_like_action_claim(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Привет! Чем могу помочь?",
        "Python был создан Гвидо ван Россумом в 1991 году.",
        "Файл не создан: нет доступа.",
        "Файл будет создан после подтверждения.",
        "Хотите, чтобы я создал файл?",
        "Создание файла — простая операция.",
        "I can create the file for you.",
        "The file will be created when you confirm.",
    ],
)
def test_non_claims_are_ignored(text: str) -> None:
    """Plain chat, questions, negations and future phrasings are not flagged."""
    assert looks_like_action_claim(text) is False
