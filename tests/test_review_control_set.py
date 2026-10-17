"""Guards for the frozen control set of test Pull Requests."""

import ast
import json
import math
from pathlib import Path
from typing import Any

import pytest

from agent.code_index import is_indexable
from agent.review_pipeline import (
    ANSWER_MAX_TOKENS,
    MAX_PARTS,
    PROMPT_OVERHEAD_TOKENS,
    RAG_SHARE,
    estimate_tokens,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "review"
MANIFEST = FIXTURES / "control_set.json"
CONTEXT_LENGTH = 65536
FORBIDDEN_WORDS = ("defect", "bug", "planted", "ошибка", "дефект", "todo", "fixme")


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    """Load the control set manifest."""
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _paths(entry: dict[str, Any]) -> set[str]:
    return {f["path"] for f in entry["files"]} | {c["path"] for c in entry["copies"]}


def test_ids_and_control_flags(manifest: dict[str, Any]) -> None:
    """Six entries, five of them control entries."""
    ids = [entry["id"] for entry in manifest["prs"]]
    assert sorted(ids) == sorted(["smoke", "bug", "rules", "arch", "large", "clean"])
    assert sum(1 for entry in manifest["prs"] if entry["control"]) == 5
    assert [e["id"] for e in manifest["prs"] if not e["control"]] == ["smoke"]
    assert manifest["base"] == "Day32" and manifest["branch_prefix"] == "review-test/"


def test_expected_items(manifest: dict[str, Any]) -> None:
    """Every expected finding names a file of its PR and where it can be recognised."""
    keys: list[str] = []
    needs: set[str] = set()
    for entry in manifest["prs"]:
        for item in entry["expected"]:
            assert item["file"] in _paths(entry)
            assert item["needs"] in {"diff", "docs", "code"}
            assert item["summary"]
            keys.append(item["key"])
            needs.add(item["needs"])
    assert sorted(keys) == sorted(
        ["B1", "B2", "B3", "R1", "R2", "R3", "R4", "A1", "A2", "L1", "L2"]
    )
    assert needs == {"diff", "docs", "code"}
    clean = next(e for e in manifest["prs"] if e["id"] == "clean")
    assert clean["expected"] == []


def test_sources_exist_and_parse(manifest: dict[str, Any]) -> None:
    """Every source file exists and every planted Python file is valid code."""
    for entry in manifest["prs"]:
        for item in entry["files"]:
            source = ROOT / item["source"]
            assert source.is_file(), item["source"]
            text = source.read_text(encoding="utf-8")
            if item["path"].endswith(".py"):
                ast.parse(text)
            lowered = text.lower()
            for word in FORBIDDEN_WORDS:
                assert word not in lowered, (item["source"], word)


def test_edits_match_once(manifest: dict[str, Any]) -> None:
    """Each edit of a copied module matches exactly once on disk."""
    for entry in manifest["prs"]:
        for copy in entry["copies"]:
            text = (ROOT / copy["from"]).read_text(encoding="utf-8")
            for edit in copy["edits"]:
                assert text.count(edit["find"]) == 1, (copy["from"], edit["find"][:40])


def test_new_paths_do_not_collide(manifest: dict[str, Any]) -> None:
    """New paths use demo prefixes, so a PR never conflicts with its base."""
    prefixes = ("agent/review_demo_", "ui/review_demo_", "tests/test_review_demo_")
    for entry in manifest["prs"]:
        for path in _paths(entry):
            assert path.startswith(prefixes), path
            assert not (ROOT / path).exists(), path


def test_fixtures_stay_out_of_knowledge_bases() -> None:
    """Fixture files are neither code-indexable nor Markdown, and pytest does not collect them."""
    for path in FIXTURES.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        assert not is_indexable(relative, path.stat().st_size), relative
        assert path.suffix.lower() != ".md", relative
        assert not (path.name.startswith("test_") and path.suffix == ".py"), relative


def test_large_pr_forces_per_file_path(manifest: dict[str, Any]) -> None:
    """The large PR does not fit one call and needs a bounded number of parts."""
    entry = next(e for e in manifest["prs"] if e["id"] == "large")
    available = (
        CONTEXT_LENGTH
        - PROMPT_OVERHEAD_TOKENS
        - ANSWER_MAX_TOKENS
        - int(CONTEXT_LENGTH * RAG_SHARE)
    )
    total = 0
    for copy in entry["copies"]:
        text = (ROOT / copy["from"]).read_text(encoding="utf-8")
        total += estimate_tokens("\n".join("+" + line for line in text.splitlines()))
    assert total > available
    assert math.ceil(total / available) + 1 <= MAX_PARTS
