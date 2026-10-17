"""Retrieval probe for the /support command on an isolated copy of the app.

Usage:
    python scripts/support_probe.py [--out eval_out/day33]

The probe copies the working tree to a scratch directory, starts it on UI :18000 / Agent :18001
with a scratch database, indexes README.md and docs/*.md (including docs/FAQ.md) and searches
support questions, ticket-aware queries and out-of-corpus questions. It records the best score
and best source of each query, applies the written threshold rule of help_eval.threshold_decision
and decides whether the ticket-aware query keeps docs/FAQ.md ranked at least as high as the
bare question. The script never edits source files; it only writes the JSON and the phase record.

When LM Studio or the embedding model is not available the record is written as `not run`.

Exit codes: 0 done, 1 a step aborted, 2 blocked by preflight (not run).
"""

import argparse
import asyncio
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import help_eval  # noqa: E402

REPO_ROOT: Path = help_eval.REPO_ROOT
DEFAULT_OUT: Path = REPO_ROOT / "eval_out" / "day33"
RECORD_PATH: Path = (
    REPO_ROOT / ".planning" / "phases" / "25-support-assistant-day-33" / "25-RETRIEVAL-PROBE.md"
)
SUPPORT_COMMAND_PATH: Path = REPO_ROOT / "agent" / "support_command.py"
SUPPORT_DATA_PATH: Path = REPO_ROOT / "mcp_servers" / "support_data.json"

SUPPORT_QUESTIONS: tuple[str, ...] = (
    "Почему не работает авторизация?",
    "Пишет слишком много неудачных попыток входа, что делать?",
    "После входа снова просит войти",
    "Как передать API-ключ в запросе?",
    "Модель не отвечает, LM Studio is not running",
    "MCP сервер не подключается",
)
OUTSIDE_QUESTIONS: tuple[str, ...] = ("Какая погода завтра в Москве?", "Рецепт борща")
TICKETS: tuple[str, ...] = ("T-1001", "T-1002")
FAQ_SOURCE: str = "docs/FAQ.md"
TOP_K: int = 5
TICKET_MAX_CHARS: int = 6000
FAQ_MIN_BEST: int = 4
EXIT_OK, EXIT_ABORTED, EXIT_PREFLIGHT = help_eval.EXIT_OK, help_eval.EXIT_ABORTED, help_eval.EXIT_PREFLIGHT
NOT_RUN: str = help_eval.NOT_RUN


def report(message: str) -> None:
    """Print one terminal line (scripts are the one place where terminal output is allowed)."""
    help_eval.report(message)


# ---------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------


def faq_rank(results: list[dict[str, Any]], source: str = FAQ_SOURCE) -> int | None:
    """1-based position of the first result whose source equals `source`, or None."""
    for index, item in enumerate(results, start=1):
        if item.get("source") == source:
            return index
    return None


def hint_decision(bare_rank: int | None, hinted_ranks: list[int | None]) -> tuple[bool, str]:
    """Keep the ticket-aware query unless it ranks the FAQ lower than the bare question."""
    for ticket, rank in zip(TICKETS, hinted_ranks):
        if rank is None:
            return False, f"disabled: the ticket-aware query for {ticket} does not return {FAQ_SOURCE}"
        if bare_rank is None or rank > bare_rank:
            shown = "absent" if bare_rank is None else str(bare_rank)
            return False, (
                f"disabled: the ticket-aware query for {ticket} ranks {FAQ_SOURCE} at {rank}, "
                f"the bare question at {shown}"
            )
    return True, f"kept: both ticket-aware queries rank {FAQ_SOURCE} at least as high as the bare question"


def current_support_threshold(text: str | None = None) -> float:
    """Read DEFAULT_SUPPORT_THRESHOLD from the source without importing application modules."""
    source = text if text is not None else SUPPORT_COMMAND_PATH.read_text(encoding="utf-8")
    match = re.search(r"^DEFAULT_SUPPORT_THRESHOLD\s*(?::\s*float)?\s*=\s*([0-9.]+)", source, re.M)
    if match is None:
        raise RuntimeError("DEFAULT_SUPPORT_THRESHOLD not found in agent/support_command.py")
    return float(match.group(1))


def faq_best_count(rows: list[dict[str, Any]]) -> int:
    """How many support questions have the FAQ as the best source."""
    return sum(1 for r in rows if r["kind"] == "support" and r["best_source"] == FAQ_SOURCE)


def _cell(text: str) -> str:
    """Escape a table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_probe_markdown(record: dict[str, Any]) -> str:
    """Render the phase record of the probe."""
    lines: list[str] = ["# Retrieval probe: /support threshold and ticket-aware query", ""]
    lines.append(f"- Date: {record.get('date', '')}")
    status = record.get("status", NOT_RUN)
    lines.append(f"- Status: {status}")
    if status == NOT_RUN:
        lines += [
            f"- Reason: {record.get('reason', 'unknown')}",
            "",
            "The probe was not run: no source file was changed, the defaults stay "
            f"(DEFAULT_SUPPORT_THRESHOLD {record.get('current_threshold', 'unchanged')}, "
            "RETRIEVAL_HINT_ENABLED True). The FAQ retrieval assumptions remain unmeasured.",
            "",
        ]
        return "\n".join(lines)
    chunking = record.get("chunking", {})
    lines += [
        f"- Embedding model: {record.get('embedding_model')}",
        f"- Chunking: {chunking.get('strategy')} (size {chunking.get('chunk_size')}, "
        f"overlap {chunking.get('chunk_overlap')})",
        f"- Chunk count: {record.get('chunk_count')}",
        f"- Corpus: {', '.join(record.get('files', []))}",
        "",
        "## Results",
        "",
        "| Kind | Query | Best score | Best source | FAQ rank |",
        "|------|-------|-----------|-------------|----------|",
    ]
    for row in record.get("rows", []):
        score = row["best_score"]
        shown = "none" if score is None else f"{float(score):.3f}"
        rank = row["faq_rank"]
        lines.append(
            f"| {row['kind']} | {_cell(str(row['query']))} | {shown} | "
            f"{row['best_source']} | {'-' if rank is None else rank} |"
        )
    threshold = record["threshold"]
    hint = record["hint"]
    lines += [
        "",
        "## Decisions",
        "",
        f"- Threshold: current {record['current_threshold']}, decision {threshold['value']} "
        f"({threshold['reason']})",
        f"- Ticket-aware query: {'keep enabled' if hint['keep'] else 'disable'} ({hint['reason']})",
        f"- FAQ is the best source for {record['faq_best']} of {len(SUPPORT_QUESTIONS)} support questions",
        "",
    ]
    if record["faq_best"] < FAQ_MIN_BEST:
        lines += [
            "## FAQ ranking",
            "",
            f"{FAQ_SOURCE} is the best source for fewer than {FAQ_MIN_BEST} of the "
            f"{len(SUPPORT_QUESTIONS)} support questions. Competing sources:",
            "",
        ]
        for row in record.get("rows", []):
            if row["kind"] == "support" and row["best_source"] != FAQ_SOURCE:
                lines.append(f"- {_cell(str(row['query']))}: {row['best_source']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Probe run
# ---------------------------------------------------------------------------------------------


def _now() -> str:
    """Current UTC time as ISO text."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ticket_queries(scratch: Path) -> list[tuple[str, str]]:
    """Build the ticket-aware queries from the real get_ticket output of the support server."""
    from agent import support_lookup  # noqa: E402
    from mcp_servers import support_desk  # noqa: E402

    data_copy = scratch / "support_data.json"
    shutil.copyfile(SUPPORT_DATA_PATH, data_copy)
    support_desk.configure(support_desk.ServerConfig(str(data_copy), TICKET_MAX_CHARS))
    queries: list[tuple[str, str]] = []
    for ticket in TICKETS:
        text = await support_desk.get_ticket(ticket)
        queries.append((ticket, support_lookup.retrieval_query(SUPPORT_QUESTIONS[0], text)))
    return queries


async def search(client: Any, base_id: int, query: str) -> list[dict[str, Any]]:
    """Search the base and return the ranked results."""
    response = await client.post(
        f"{help_eval.kb.AGENT_URL}/api/v1/kb/{base_id}/search",
        json={"query": query, "top_k": TOP_K},
        headers=help_eval.dialog._json_headers(),
    )
    response.raise_for_status()
    return list(response.json().get("results") or [])


def _row(kind: str, query: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """One recorded query."""
    best = results[0] if results else {}
    return {
        "query": query,
        "kind": kind,
        "best_score": best.get("score"),
        "best_source": best.get("source"),
        "faq_rank": faq_rank(results),
        "top": [{"source": r.get("source"), "score": r.get("score")} for r in results],
    }


def _scores(rows: list[dict[str, Any]], kinds: tuple[str, ...]) -> list[float]:
    """Best scores of the rows of the given kinds."""
    return [float(r["best_score"]) for r in rows if r["kind"] in kinds and r["best_score"] is not None]


async def probe(scratch: Path, out: Path) -> int:
    """Index the documentation, search every query, decide and write the record."""
    queries = await ticket_queries(scratch)
    async with help_eval.isolated_app(scratch, None) as (_run_env, client):
        base, names = await help_eval.upload_base(client)
        rows: list[dict[str, Any]] = []
        for question in SUPPORT_QUESTIONS:
            rows.append(_row("support", question, await search(client, int(base["id"]), question)))
        for ticket, query in queries:
            rows.append(_row("ticket", f"[{ticket}] {query}", await search(client, int(base["id"]), query)))
        for question in OUTSIDE_QUESTIONS:
            rows.append(_row("outside", question, await search(client, int(base["id"]), question)))
        for row in rows:
            report(f"{row['kind']}: {row['best_score']} {row['best_source']} faq_rank={row['faq_rank']}")
    current = current_support_threshold()
    value, reason = help_eval.threshold_decision(
        _scores(rows, ("support", "ticket")), _scores(rows, ("outside",)), current
    )
    bare_rank = rows[0]["faq_rank"]
    hinted = [r["faq_rank"] for r in rows if r["kind"] == "ticket"]
    keep, hint_reason = hint_decision(bare_rank, hinted)
    record: dict[str, Any] = {
        "date": _now(),
        "status": "ran",
        "embedding_model": base.get("embedding_model"),
        "chunking": {
            "strategy": base.get("strategy"),
            "chunk_size": base.get("chunk_size"),
            "chunk_overlap": base.get("chunk_overlap"),
        },
        "chunk_count": base.get("chunk_count"),
        "files": names,
        "rows": rows,
        "current_threshold": current,
        "threshold": {"value": value, "reason": reason},
        "hint": {"keep": keep, "reason": hint_reason},
        "faq_best": faq_best_count(rows),
    }
    help_eval.save_json(out / "support_probe.json", record, [])
    write_record(record)
    report(f"threshold: current {current}, decision {value} ({reason})")
    report(f"ticket-aware query: {'keep' if keep else 'disable'} ({hint_reason})")
    return EXIT_OK


def write_record(record: dict[str, Any]) -> None:
    """Write the phase record."""
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECORD_PATH.write_text(render_probe_markdown(record), encoding="utf-8")


def record_not_run(reason: str) -> None:
    """Record that the probe could not run; nothing else changes."""
    report(f"{NOT_RUN}: {reason}")
    write_record({
        "date": _now(),
        "status": NOT_RUN,
        "reason": reason,
        "current_threshold": current_support_threshold(),
    })


async def amain(args: argparse.Namespace, scratch: Path) -> int:
    """Run the probe after preflight."""
    caps = await help_eval.preflight()
    blocked = caps.hard_block()
    if blocked:
        record_not_run(blocked)
        return EXIT_PREFLIGHT
    try:
        return await probe(scratch, args.out)
    except (RuntimeError, ValueError, OSError, help_eval.httpx.HTTPError) as exc:
        report(f"ABORTED: {type(exc).__name__}: {exc}")
        return EXIT_ABORTED


def main() -> int:
    """Entry point; the whole process works on a scratch database, never app.db."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description="Retrieval probe for /support")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="support_probe_"))
    help_eval.os.environ["DB_PATH"] = str(scratch / "help_eval.db")
    try:
        return asyncio.run(amain(args, scratch))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
