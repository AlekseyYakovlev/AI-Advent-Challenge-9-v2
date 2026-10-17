"""Tests for the pure decision helpers of scripts/support_probe.py."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "support_probe.py"

_spec = importlib.util.spec_from_file_location("support_probe", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
support_probe = importlib.util.module_from_spec(_spec)
sys.modules["support_probe"] = support_probe
_spec.loader.exec_module(support_probe)


def _results(*sources: str) -> list[dict[str, Any]]:
    """Ranked search results with the given sources."""
    return [{"source": s, "score": 0.5} for s in sources]


def test_faq_rank_is_one_based_position() -> None:
    assert support_probe.faq_rank(_results("README.md", "docs/FAQ.md")) == 2


def test_faq_rank_none_when_absent() -> None:
    assert support_probe.faq_rank(_results("README.md")) is None


def test_hint_kept_when_not_worse() -> None:
    keep, reason = support_probe.hint_decision(2, [2, 1])
    assert keep is True
    assert reason.startswith("kept")


def test_hint_disabled_on_none() -> None:
    keep, reason = support_probe.hint_decision(1, [1, None])
    assert keep is False
    assert "T-1002" in reason


def test_hint_disabled_on_worse_rank() -> None:
    keep, _ = support_probe.hint_decision(1, [2, 1])
    assert keep is False


def test_hint_disabled_when_bare_rank_missing() -> None:
    keep, _ = support_probe.hint_decision(None, [1, 1])
    assert keep is False


def test_current_threshold_parsed() -> None:
    assert support_probe.current_support_threshold("DEFAULT_SUPPORT_THRESHOLD: float = 0.5\n") == 0.5


def _record(faq_best: int = 6) -> dict[str, Any]:
    row = {"kind": "support", "query": "q", "best_score": 0.6, "best_source": "README.md", "faq_rank": None}
    return {
        "date": "2026-01-01",
        "status": "ran",
        "embedding_model": "m",
        "chunking": {},
        "chunk_count": 1,
        "files": ["README.md"],
        "rows": [row],
        "current_threshold": 0.45,
        "threshold": {"value": 0.45, "reason": "kept"},
        "hint": {"keep": True, "reason": "kept: fine"},
        "faq_best": faq_best,
    }


def test_render_contains_table_and_decisions() -> None:
    text = support_probe.render_probe_markdown(_record())
    assert "| Kind | Query | Best score | Best source | FAQ rank |" in text
    assert "Threshold: current 0.45" in text
    assert "Ticket-aware query: keep enabled" in text


def test_render_not_run_has_reason() -> None:
    text = support_probe.render_probe_markdown(
        {"date": "d", "status": "not run", "reason": "LM Studio down", "current_threshold": 0.45}
    )
    assert "not run" in text and "LM Studio down" in text


def test_render_adds_faq_ranking_note_when_low() -> None:
    assert "## FAQ ranking" in support_probe.render_probe_markdown(_record(faq_best=2))
