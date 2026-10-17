"""Node-free source guards for the release plan card in app.js."""

from tests.test_limits_ui import _app_js_source, _function_source
from tests.test_static_js_syntax import check_js_balance

_PLAN_FUNCTIONS: list[str] = [
    "buildReleasePlanCard",
    "validateReleaseVersion",
    "releaseStepRow",
    "sendReleaseConfirm",
]
_RESULT_FUNCTIONS: list[str] = [
    "updateReleaseStep",
    "applyReleaseResult",
    "markReleaseCardsStale",
]


def _needles(body: str, needles: list[str], where: str) -> None:
    """Assert every needle occurs in body, naming the missing one."""
    for needle in needles:
        assert needle in body, f"{where}: missing {needle!r}"


def _case_body(name: str) -> str:
    """Return the handleWsMessage text between `case '<name>'` and the next case."""
    body: str = _function_source(_app_js_source(), "handleWsMessage")
    marker: str = f"case '{name}'"
    assert marker in body, f"handleWsMessage: missing {marker}"
    rest: str = body.split(marker, 1)[1]
    nxt: int = rest.find("case '")
    return rest if nxt == -1 else rest[:nxt]


def test_plan_functions_defined() -> None:
    """The plan-side release helpers are top-level functions."""
    src: str = _app_js_source()
    for name in _PLAN_FUNCTIONS:
        _function_source(src, name)


def test_plan_card_strings() -> None:
    """The plan card carries the exact UI-SPEC copy and classes."""
    body: str = _function_source(_app_js_source(), "buildReleasePlanCard")
    _needles(
        body,
        [
            "'region'",
            "'План релиза'",
            "RAG_CHIP_NEUTRAL",
            "'Подтвердить релиз'",
            "'Или введите /release confirm'",
            "'План хранится до подтверждения или перезапуска агента.'",
            "Черновик CHANGELOG (Добавлено / Исправлено / Изменено)",
            "max-h-96",
            "Обоснование версии",
            "aria-describedby",
            "Отфильтровано",
        ],
        "buildReleasePlanCard",
    )
    assert "createElement('details')" in body or "<details" in body


def test_plan_step_glyph_map() -> None:
    """The step status map has every glyph and Russian status word."""
    src: str = _app_js_source()
    body: str = src + _function_source(src, "releaseStepRow")
    _needles(
        body,
        ["○", "…", "✓", "↷", "✗", "⊘", "выполнено", "пропущено", "ошибка", "недоступно"],
        "step status map",
    )


def test_plan_validate_version_copy() -> None:
    """Version validation uses the format regex and the exact error copy."""
    body: str = _function_source(_app_js_source(), "validateReleaseVersion")
    assert "^v(\\d+)\\.(\\d+)$" in body, "validateReleaseVersion: missing format regex"
    _needles(
        body,
        [
            "Формат версии: vX.Y, например v4.1.",
            "Версия должна быть больше последнего тега",
            "уже существует как тег. Выберите другую.",
        ],
        "validateReleaseVersion",
    )


def test_plan_send_confirm_frame() -> None:
    """The confirm sender emits the release_confirm frame and disables the button."""
    body: str = _function_source(_app_js_source(), "sendReleaseConfirm")
    _needles(
        body,
        [
            "type: 'release_confirm'",
            "plan_id",
            "state.selectedModel",
            "state.selectedProviderId",
            "disabled = true",
            "Выполняется…",
        ],
        "sendReleaseConfirm",
    )


def test_plan_switch_case() -> None:
    """The release_plan frame resets streaming state and builds the card."""
    case: str = _case_body("release_plan")
    _needles(
        case,
        ["setStreaming(false)", "removeLoadingBubble()", "unblockInput()", "buildReleasePlanCard"],
        "case release_plan",
    )


def test_plan_js_balanced() -> None:
    """app.js keeps balanced brackets and quotes."""
    assert check_js_balance(_app_js_source()) == []


def test_result_functions_defined() -> None:
    """The result-side release helpers are top-level functions."""
    src: str = _app_js_source()
    for name in _RESULT_FUNCTIONS:
        _function_source(src, name)


def test_result_innerhtml_only_changelog() -> None:
    """innerHTML appears once, in the plan card, fed by renderMarkdown."""
    src: str = _app_js_source()
    for name in _PLAN_FUNCTIONS + _RESULT_FUNCTIONS:
        body: str = _function_source(src, name)
        if name == "buildReleasePlanCard":
            assert body.count("innerHTML") == 1, "buildReleasePlanCard: expected one innerHTML"
            assert "innerHTML = renderMarkdown(" in body, "innerHTML must be fed by renderMarkdown"
        else:
            assert "innerHTML" not in body, f"{name}: innerHTML is forbidden"


def test_result_apply_strings() -> None:
    """applyReleaseResult carries the outcome copy, link guard and copy button."""
    body: str = _function_source(_app_js_source(), "applyReleaseResult")
    _needles(
        body,
        [
            "Готово",
            "Частично выполнено",
            "Ошибка",
            "Заблокировано",
            "Повторить оставшиеся шаги",
            "Скопировать текст релиза",
            "Текст релиза для ручной публикации",
            "https://github.com/",
            "noopener noreferrer",
            "Пересоздать план",
            "navigator.clipboard",
        ],
        "applyReleaseResult",
    )


def test_result_stale_copy() -> None:
    """Older cards are marked stale."""
    body: str = _function_source(_app_js_source(), "markReleaseCardsStale")
    assert "План устарел" in body


def test_result_switch_cases() -> None:
    """release_progress and release_result frames are routed and reset streaming."""
    assert "updateReleaseStep" in _case_body("release_progress")
    case: str = _case_body("release_result")
    _needles(
        case,
        ["setStreaming(false)", "removeLoadingBubble()", "unblockInput()", "applyReleaseResult"],
        "case release_result",
    )
