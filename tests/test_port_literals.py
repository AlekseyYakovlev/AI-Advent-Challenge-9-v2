"""Guard: default-port literals stay out of application code."""

import re
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
PORT_RE: re.Pattern[str] = re.compile(r"(?<![\d.])800[01](?!\d)")
SUFFIXES: set[str] = {".py", ".js", ".html"}
ALLOWED_LINES: set[tuple[str, str]] = {
    ("ui/static/app.js", "const AGENT_PORT = 8001;"),
    ("ui/static/login.html", "const AGENT_PORT = 8001;"),
}


def _scan_files() -> list[Path]:
    """Collect application source files that must not hardcode default ports."""
    files: list[Path] = [ROOT / "run.py"]
    for directory in ("agent", "shared", "ui"):
        files.extend(p for p in (ROOT / directory).rglob("*") if p.suffix in SUFFIXES)
    return [
        p for p in files
        if p.is_file()
        and "/vendor/" not in p.as_posix()
        and p.relative_to(ROOT).as_posix() != "shared/config.py"
    ]


def test_no_default_port_literals_in_application_code() -> None:
    """Ports 8000/8001 appear only in shared/config.py and the two AGENT_PORT constants."""
    offenders: list[str] = []
    for path in _scan_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if PORT_RE.search(line) and (rel, line.strip()) not in ALLOWED_LINES:
                offenders.append(f"{rel}:{number}")
    assert not offenders, f"default-port literals found: {offenders}"


def test_e2e_scripts_do_not_patch_agent_state() -> None:
    """Isolated-copy scripts no longer patch agent/state.py."""
    for name in ("e2e_kb_playwright.py", "e2e_llm_providers_playwright.py"):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert 'patch("agent/state.py"' not in text, name
