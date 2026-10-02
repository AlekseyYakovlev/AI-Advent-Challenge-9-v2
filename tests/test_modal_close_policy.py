"""Source guard: modals close only via their x/Cancel buttons, never by backdrop click or Escape."""

import re
from pathlib import Path

import pytest

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"

_BACKDROP_EVENTS: tuple[str, ...] = ("click", "mousedown", "mouseup", "pointerdown", "pointerup")


def _modal_ids() -> list[str]:
    """Return every overlay id ending in '-modal' declared in index.html."""
    html: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return sorted(set(re.findall(r'id="([a-z0-9-]+-modal)"', html)))


def _app_js() -> str:
    """Return app.js with full-line // comments removed."""
    src: str = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


def test_index_declares_known_modals() -> None:
    """The discovery regex finds the four known modals."""
    assert {
        "settings-modal",
        "add-user-modal",
        "scheduler-create-modal",
        "scheduler-run-modal",
    } <= set(_modal_ids())


@pytest.mark.parametrize("modal_id", _modal_ids(), ids=_modal_ids())
def test_no_backdrop_click_closer(modal_id: str) -> None:
    """No pointer listener is attached to a modal overlay and no target===overlay check exists."""
    src: str = _app_js()
    events: str = "|".join(_BACKDROP_EVENTS)
    listener = re.compile(
        r"\$\(\s*['\"]" + re.escape(modal_id) + r"['\"]\s*\)\s*\.addEventListener\(\s*['\"](" + events + r")['\"]"
    )
    assert not listener.search(src), f"{modal_id} has a pointer listener"
    target_check = re.compile(r"target\s*===\s*\$\(\s*['\"]" + re.escape(modal_id) + r"['\"]\s*\)")
    assert not target_check.search(src), f"{modal_id} has a backdrop target check"


@pytest.mark.parametrize("modal_id", _modal_ids(), ids=_modal_ids())
def test_modal_has_bound_x_button(modal_id: str) -> None:
    """Each modal has a btn-close-<stem> button in the HTML that is bound to click in app.js."""
    stem: str = modal_id[: -len("-modal")]
    html: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert f'id="btn-close-{stem}"' in html
    assert f"$('btn-close-{stem}').addEventListener('click'" in _app_js()


def test_escape_key_does_not_close_modals() -> None:
    """Encodes assumption A-01 of plan 10-01; deleting this one test allows an Escape closer again."""
    src: str = _app_js()
    assert not re.search(r"\.key\s*===\s*['\"](Escape|Esc)['\"]", src)
    assert not re.search(r"keyCode\s*===\s*27", src)


def test_unrelated_handlers_survive() -> None:
    """Enter-to-send and chat-list context menu bindings are still present."""
    src: str = _app_js()
    assert "$('message-input').addEventListener('keydown'" in src
    assert "$('chat-list').addEventListener('contextmenu'" in src
