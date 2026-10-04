"""Data-layer write-side cutover: the twin must be a redirect, not a redesign.

Workflow memory retires a set of data tables (`_retire_data_tables`). The read side
was already repointed at the `data_*` twins, but the WRITE side never was, so every
evidence write hit `sqlite3.OperationalError: no such table: evidence_observations`.
These tests pin the four properties that must hold so it cannot regress:

1. the twins are column-for-column equivalent to the Workflow-memory originals;
2. an observation written through Workflow memory lands in the data layer and reads
   back through the same identity and lineage;
3. no code outside the sanctioned places still names a retired evidence table;
4. an ownership/write-target disagreement is found at INIT, not at first write.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ats.data.stores.unstructured import PlatformUnstructuredRepository
from ats.memory.store import TradingMemory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

TWIN_BY_LEGACY = {
    "evidence_observations": "data_evidence_observations",
    "evidence_facts": "data_evidence_facts",
    "evidence_fact_projections": "data_evidence_projections",
    "evidence_failures": "data_evidence_failures",
}

RETIRED_EVIDENCE = re.compile(
    r"\b(evidence_observations|evidence_facts|evidence_fact_projections|evidence_failures)\b")

# Where a retired evidence table name may still appear: the legacy DDL that an old
# database is migrated FROM, the retirement list itself, and the one-time migrations
# that read those legacy rows. Anywhere else means a write path was left behind.
SANCTIONED = {
    ("ats/memory/store.py", "<module>"),           # _SCHEMA: legacy DDL
    ("ats/memory/store.py", "_retire_data_tables"),
    ("ats/memory/store.py", "_migrate"),
    ("ats/memory/store.py", "_migrate_shared_facts"),
    ("ats/data/stores/ownership.py", "<module>"),  # the boundary registry
}


def _columns(conn, table: str) -> dict[str, str]:
    return {r["name"]: (r["type"] or "").upper()
            for r in conn.execute(f"PRAGMA table_info({table})")}


def _legacy_columns(tmp_path: Path, table: str) -> dict[str, str]:
    """Columns of a Workflow-memory table as they exist AFTER migration.

    `_retire_data_tables` drops them at init, so the post-migration shape is only
    observable with the retirement suppressed — and that shape is what the twin has
    to match, because `_migrate` adds columns the DDL alone does not declare
    (`superseded_at`, `document_version_id`).
    """
    class _KeepsLegacy(TradingMemory):
        def _retire_data_tables(self) -> None:
            pass

    memory = _KeepsLegacy(tmp_path / "legacy.sqlite")
    try:
        return _columns(memory.conn, table)
    finally:
        memory.conn.close()


def _string_constants(tree: ast.Module):
    """Every string constant in the module except docstrings, with its owner name."""
    found: list[tuple[str, int, str]] = []

    def walk(node, owner: str) -> None:
        body = list(node.body)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]                      # the docstring is not code
        for stmt in body:
            # A nested function/class is walked by its own owner name, so its strings
            # are not also attributed to the enclosing scope.
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(stmt, stmt.name)
                continue
            for child in ast.walk(stmt):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    found.append((owner, child.lineno, child.value))

    walk(tree, "<module>")
    return found


def _retired_table_references():
    hits = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for owner, lineno, value in _string_constants(tree):
            if RETIRED_EVIDENCE.search(value):
                hits.append((relative, owner, lineno,
                             sorted(set(RETIRED_EVIDENCE.findall(value)))))
    return hits


@pytest.mark.parametrize("legacy,twin", sorted(TWIN_BY_LEGACY.items()))
def test_data_layer_twins_are_column_for_column_equivalent(tmp_path, legacy, twin):
    """The cutover is a redirect: same columns, same types, same meaning."""
    expected = _legacy_columns(tmp_path, legacy)
    assert expected, f"{legacy} has no post-migration shape to compare against"
    repository = PlatformUnstructuredRepository(tmp_path / "data.sqlite", writable=True)
    try:
        actual = _columns(repository.conn, twin)
        assert actual, f"{twin} does not exist in the data layer"
        assert set(actual) >= set(expected), (
            f"{twin} is missing columns the Workflow-memory original has: "
            f"{sorted(set(expected) - set(actual))}")
        drifted = {c: (expected[c], actual[c]) for c in expected
                   if expected[c] != actual[c]}
        assert not drifted, f"{twin} column types drifted: {drifted}"
    finally:
        repository.close()


def test_an_observation_written_through_workflow_memory_lands_in_the_data_layer(tmp_path):
    """`save_observation` is the收口点: it must not touch a retired table."""
    from ats.schemas.chain import Observation

    memory = TradingMemory(tmp_path / "mem.sqlite")
    obs = Observation(
        id="obs-1", document_id="doc-1", source_url="https://example.invalid/filing",
        entity="NVDA", source_entity="NVDA", metric="hbm_share", concept="",
        period="2026Q2", observation_type="reported_actual", stance="supplier",
        direction="up", value=0.42, unit="ratio", evidence_span="HBM share 42%",
        observed_at=datetime.now(timezone.utc), extraction_confidence=0.9)
    assert memory.save_observation(obs) is True

    rows = memory.observations(entity="NVDA", metric="hbm_share")
    assert [r["id"] for r in rows] == ["obs-1"]
    # ...and it reads back through the data layer's own entry points, by lineage.
    facts = memory.facts(entity="NVDA", document_id="doc-1")
    assert facts and facts[0]["value"] == pytest.approx(0.42)
    projections = memory.fact_projections(fact_id=facts[0]["fact_id"])
    assert projections and projections[0]["legacy_observation_id"] == "obs-1"

    # Deterministic and idempotent: the same observation cannot become two facts.
    assert memory.save_observation(obs) is False
    assert len(memory.facts(entity="NVDA", document_id="doc-1")) == 1


def test_an_observation_failure_is_recorded_in_the_data_layer(tmp_path):
    """`save_observation_failure` is the收口点 for "could not read"."""
    from ats.schemas.chain import ObservationFailure

    memory = TradingMemory(tmp_path / "mem.sqlite")
    memory.save_observation_failure(ObservationFailure(
        document_id="doc-2", entity="MU", reason="paywalled",
        at=datetime.now(timezone.utc)))
    failures = memory.observation_failures()
    assert [f["document_id"] for f in failures] == ["doc-2"]
    assert failures[0]["reason"] == "paywalled"


def test_no_code_outside_the_sanctioned_places_names_a_retired_evidence_table():
    """A write path left behind fails at run time; find it here instead."""
    offenders = [h for h in _retired_table_references() if (h[0], h[1]) not in SANCTIONED]
    assert not offenders, (
        "code still reaches for a retired evidence table: "
        + "; ".join(f"{f}::{owner}:{line} {tables}"
                    for f, owner, line, tables in offenders))


def test_init_refuses_to_open_when_a_write_target_has_no_twin(tmp_path, monkeypatch):
    """The boundary is an ownership claim — a missing twin is an init-time error."""
    assert TradingMemory(tmp_path / "mem.sqlite").data_store() is not None

    monkeypatch.setattr(
        TradingMemory, "_writes_redirected_to_data_layer",
        lambda _self: ("data_evidence_observations", "data_evidence_not_a_real_table"))
    with pytest.raises(RuntimeError, match="no twin"):
        TradingMemory(tmp_path / "mem2.sqlite")
