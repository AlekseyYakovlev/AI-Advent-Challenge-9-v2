"""Unit tests of the pure helpers of scripts/e2e_support_playwright.py."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS: Path = Path(__file__).resolve().parent.parent / "scripts"


def _load() -> ModuleType:
    """Import the e2e script as a module (Playwright is only imported inside functions)."""
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "e2e_support_playwright", SCRIPTS / "e2e_support_playwright.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e2e = _load()


def test_server_args_one_argument_per_line() -> None:
    """The script path and the --data option are separate lines."""
    assert e2e.server_args("/x/support_desk.py", "/y/data.json") == (
        "/x/support_desk.py\n--data=/y/data.json"
    )


def test_mentions_any_is_case_insensitive() -> None:
    """A fact matches regardless of case; no match gives False."""
    assert e2e.mentions_any("Ошибка ORIGIN при входе", e2e.T2_FACTS)
    assert not e2e.mentions_any("всё хорошо", e2e.T2_FACTS)


def test_live_verdict_not_run_when_lm_down() -> None:
    """With LM Studio down the verdict is never «passed», even for ok=True."""
    assert e2e.live_verdict(False, True) == "not run"
    assert e2e.live_verdict(False, False) == "not run"
    assert e2e.live_verdict(True, True) == "passed"
    assert e2e.live_verdict(True, False) == "failed"


def test_edit_ticket_status_changes_only_the_copy(tmp_path: Path) -> None:
    """The status of the requested ticket changes in the scratch copy; the seed stays intact."""
    seed = Path(__file__).resolve().parent.parent / "mcp_servers" / "support_data.json"
    before = seed.read_bytes()
    copy = tmp_path / "support_data.json"
    copy.write_bytes(before)
    assert e2e.edit_ticket_status(copy, "T-1001", "closed")
    tickets = {t["id"]: t["status"] for t in json.loads(copy.read_text(encoding="utf-8"))["tickets"]}
    assert tickets["T-1001"] == "closed"
    assert not e2e.edit_ticket_status(copy, "T-9999", "closed")
    assert seed.read_bytes() == before
