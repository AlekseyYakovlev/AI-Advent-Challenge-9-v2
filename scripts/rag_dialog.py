"""Multi-turn RAG dialog scenarios driven through the real WebSocket chat on an isolated copy."""

import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCRIPTS_DIR: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import rag_eval  # noqa: E402

FORBIDDEN_PORTS: frozenset[int] = frozenset({8000, 8001})
FORBIDDEN_DB_NAMES: frozenset[str] = frozenset({"app.db"})
MODEL_QUOTE_STATES: frozenset[str] = frozenset({"exact", "fuzzy"})
MEMORY_FIELDS: tuple[str, ...] = ("goal", "clarified", "constraints")


def report(message: str) -> None:
    """Print one terminal line (scripts are the one place where terminal output is allowed)."""
    print(message, flush=True)


def load_scenarios(path: Path, require_frozen: bool = True) -> list[dict[str, Any]]:
    """Load the dialog fixture and return its scenarios; a draft fixture is refused."""
    data: dict[str, Any] = rag_eval.load_fixture(path, require_frozen)
    scenarios: list[dict[str, Any]] = data["scenarios"]
    return scenarios


def normalise(text: str | None) -> str:
    """Lowercase and collapse whitespace for comparison."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def turn_verdict(frame_type: str, rag: dict[str, Any] | None) -> str:
    """Classify a turn as ok, gated, model_idk, no_rag or error."""
    if frame_type != "done":
        return "error"
    if not rag:
        return "no_rag"
    if rag.get("gated"):
        return "gated"
    verdict = rag.get("verdict")
    if verdict == "model_idk":
        return "model_idk"
    if verdict == "ok":
        return "ok"
    return "no_rag"


def _item_texts(items: Any) -> list[str]:
    """Normalised texts of a snapshot list whose items are id/text dicts or plain strings."""
    texts: list[str] = []
    for item in items or []:
        raw = item.get("text") if isinstance(item, dict) else item
        texts.append(normalise(str(raw or "")))
    return texts


def memory_check(
    expect_memory: dict[str, list[str]] | None, snapshot: dict[str, Any] | None
) -> tuple[int, int]:
    """Count expected memory entries and how many of them the snapshot holds (substring match)."""
    expected = 0
    found = 0
    for field in MEMORY_FIELDS:
        wanted = [normalise(entry) for entry in (expect_memory or {}).get(field, [])]
        expected += len(wanted)
        if not snapshot:
            continue
        if field == "goal":
            haystacks = [normalise(snapshot.get("goal"))]
        else:
            haystacks = _item_texts(snapshot.get(field))
        found += sum(1 for entry in wanted if any(entry in text for text in haystacks))
    return expected, found


def article_in_sources(article: str, sources: list[dict[str, Any]] | None) -> bool:
    """True when a source section names exactly this article."""
    return any(
        rag_eval.parse_article(source.get("section")) == article for source in sources or []
    )


def _quote_counts(rag: dict[str, Any] | None) -> tuple[int, int, int]:
    """Model-verified, auto and unverified quote counts of a payload."""
    model = auto = unverified = 0
    for quote in (rag or {}).get("quotes") or []:
        if quote.get("auto"):
            auto += 1
        elif quote.get("state") in MODEL_QUOTE_STATES:
            model += 1
        else:
            unverified += 1
    return model, auto, unverified


def _goal_checks(
    turn_spec: dict[str, Any], answer: str, snapshot: dict[str, Any] | None, first_goal: str | None
) -> dict[str, Any]:
    """Goal-text, keyword and goal-kept fields; all None without a task-memory snapshot."""
    result: dict[str, Any] = {
        "goal_text": None,
        "goal_unchanged": None,
        "keywords_ok": None,
        "goal_kept": None,
    }
    if snapshot is None:
        return result
    goal = snapshot.get("goal")
    result["goal_text"] = goal or None
    if first_goal is not None:
        result["goal_unchanged"] = normalise(goal) == normalise(first_goal)
    keywords = turn_spec.get("expect_keywords") or []
    if keywords:
        lowered = normalise(answer)
        result["keywords_ok"] = all(normalise(word) in lowered for word in keywords)
    result["goal_kept"] = result["goal_unchanged"] is not False and result["keywords_ok"] is not False
    return result


def compute_checks(
    turn_spec: dict[str, Any],
    frame_type: str,
    answer: str,
    rag: dict[str, Any] | None,
    first_goal: str | None,
) -> dict[str, Any]:
    """Automatic per-turn checks; a gated, refused or failed turn is recorded, never raised."""
    rag_data = rag if frame_type == "done" else None
    snapshot = (rag_data or {}).get("task_memory")
    search = (rag_data or {}).get("search") or {}
    model, auto, unverified = _quote_counts(rag_data)
    expected, found = memory_check(turn_spec.get("expect_memory"), snapshot)
    article = turn_spec.get("expect_article")
    sources = (rag_data or {}).get("sources") or []
    checks: dict[str, Any] = {
        "sources_present": bool(sources),
        "quotes_model": model,
        "quotes_auto": auto,
        "quotes_unverified": unverified,
        "verdict": turn_verdict(frame_type, rag_data),
        "memory_expected": expected,
        "memory_found": found,
        "memory_ok": (found == expected) if expected else None,
        "article_expected": article,
        "article_ok": article_in_sources(article, sources) if article else None,
        "condensed": bool(search.get("condensed")),
        "condensed_query": search.get("rewritten"),
        "memory_failed": snapshot.get("failed") if snapshot else None,
    }
    checks.update(_goal_checks(turn_spec, answer, snapshot, first_goal))
    return checks


def assert_isolated(db_path: Path, ui_port: int, agent_port: int) -> None:
    """Refuse the user's ports, the live database and any database inside the repository."""
    busy = sorted({ui_port, agent_port} & FORBIDDEN_PORTS)
    if busy:
        raise RuntimeError(f"refusing to use the live app port(s) {busy}")
    resolved = Path(db_path).resolve()
    if resolved.name in FORBIDDEN_DB_NAMES:
        raise RuntimeError(f"refusing to use the live database {resolved.name}")
    if resolved.is_relative_to(REPO_ROOT.resolve()):
        raise RuntimeError(f"refusing a database inside the repository: {resolved}")
