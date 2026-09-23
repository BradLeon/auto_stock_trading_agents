"""The three architecture constraints, as executable checks.

Each test pins one half of a rule: that the violation is caught *with* enough
information to act on, and that the legitimate counterpart is not caught. A guard that
only ever passes is indistinguishable from no guard at all, so the last test injects a
real violation and asserts the check fails.
"""

from __future__ import annotations

import textwrap

import pytest

from ats.workflow import architecture_guards as guards
from ats.workflow.architecture_guards import (
    FIRST_BATCH_EXCEPTIONS,
    ExceptionEntry,
    Violation,
    scan_agents,
    scan_module,
)


def _write(tmp_path, name: str, source: str):
    module = tmp_path / name
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(textwrap.dedent(source), encoding="utf-8")
    return module


def _root(tmp_path):
    """A fake repo root whose paths still end with the recognised role prefixes."""
    root = tmp_path / "repo"
    (root / "src" / "ats" / "agents").mkdir(parents=True, exist_ok=True)
    return root


def _relative(path, root) -> str:
    return path.relative_to(root).as_posix()


# --- 8.1 analyst input boundary -------------------------------------------- #

def test_an_undeclared_cross_role_read_is_reported_with_reader_and_location(tmp_path):
    root = _root(tmp_path)
    module = _write(
        root / "src/ats/agents/technical", "review.py", """
        def run():
            store.task_projection_envelopes(agent_role="macro_review")
        """)
    found = scan_module(module, root=root)
    assert len(found) == 1
    violation = found[0]
    assert violation.kind == "cross_role_read"
    assert violation.target == "macro_review"
    assert "technical_analyst reads macro_review" in violation.detail
    assert violation.lineno > 0


def test_the_two_declared_dependencies_are_allowed(tmp_path):
    root = _root(tmp_path)
    sector = _write(
        root / "src/ats/agents/sector", "assemble.py", """
        def run():
            store.task_projection_envelopes(agent_role="layer_analysis")
        """)
    fundamental = _write(
        root / "src/ats/agents/pead", "research.py", """
        def run():
            store.reusable_task_projection(agent_role="information_brief")
        """)
    assert scan_module(sector) == []
    assert scan_module(fundamental) == []


def test_reading_a_shared_data_product_is_not_a_cross_role_read(tmp_path):
    """Reading facts is not reading an opinion — the guard must tell them apart."""
    root = _root(tmp_path)
    module = _write(
        root / "src/ats/agents/sector", "assemble.py", """
        def run():
            products.observations(entity="NVDA")
            products.facts(entity="NVDA")
        """)
    assert scan_module(module) == []


# --- 8.2 no direct provider calls ------------------------------------------- #

def test_an_agent_importing_a_source_adapter_is_reported(tmp_path):
    root = _root(tmp_path)
    module = _write(
        root / "src/ats/agents/macro", "assemble.py", """
        from ats.data.adapters.structured.ons import fetch
        """)
    found = scan_module(module, root=root)
    assert [v.kind for v in found] == ["provider_import"]
    assert found[0].target == "ats.data.adapters.structured.ons"


def test_an_agent_importing_a_named_provider_is_reported(tmp_path):
    root = _root(tmp_path)
    module = _write(
        root / "src/ats/agents/pead", "monitor.py", """
        from ...data import defeatbeta
        """)
    found = scan_module(module, root=root)
    assert [v.target for v in found] == ["ats.data.defeatbeta"]


def test_reading_through_a_data_product_is_allowed(tmp_path):
    root = _root(tmp_path)
    module = _write(
        root / "src/ats/agents/sector", "assemble.py", """
        from ...data.products import routing
        """)
    assert scan_module(module) == []


# --- 8.3 no opinion writeback ------------------------------------------------ #

def test_writing_a_conclusion_as_a_shared_fact_is_reported(tmp_path):
    root = _root(tmp_path)
    module = _write(
        root / "src/ats/agents/sector", "assemble.py", """
        def run():
            data_store().save_evidence_observation(obs)
        """)
    found = scan_module(module, root=root)
    assert [v.kind for v in found] == ["opinion_writeback"]
    assert found[0].target == "save_evidence_observation"


def test_citing_lineage_and_writing_to_workflow_memory_is_allowed(tmp_path):
    root = _root(tmp_path)
    module = _write(
        root / "src/ats/agents/sector", "assemble.py", """
        def run():
            memory.save_task_projection_envelope(envelope)
            memory.save_sector_review(review)
        """)
    assert scan_module(module) == []


# --- 8.4 exception list ------------------------------------------------------ #

def test_the_guard_passes_on_the_current_tree_with_the_declared_exceptions():
    assert scan_agents() == []


def test_every_exception_is_a_specific_module_and_target_with_a_reason():
    for entry in FIRST_BATCH_EXCEPTIONS:
        assert entry.module.startswith("src/ats/agents/"), entry
        assert entry.module.endswith(".py"), entry
        assert "*" not in entry.module and "*" not in entry.target, entry
        assert len(entry.reason) > 10, entry


def test_an_undeclared_violation_still_fails_even_with_the_list_loaded(tmp_path):
    root = _root(tmp_path)
    _write(
        root / "src/ats/agents/technical", "review.py", """
        from ...data import defeatbeta
        """)
    found = scan_agents(root / "src" / "ats" / "agents", root=root)
    assert [v.target for v in found] == ["ats.data.defeatbeta"]


# --- 8.5 failure is failure -------------------------------------------------- #

def test_a_detected_violation_is_a_failure_not_a_warning(tmp_path):
    """There is no severity field, no skip flag and no tolerance threshold: a violation
    is a `Violation`, and the suite asserts on the list being empty."""
    root = _root(tmp_path)
    _write(
        root / "src/ats/agents/technical", "review.py", """
        def run():
            store.task_projection_envelopes(agent_role="macro_review")
        """)
    found = scan_agents(root / "src" / "ats" / "agents", root=root)
    assert found, "the guard must fail, not degrade to a warning"
    assert isinstance(found[0], Violation)
    assert not hasattr(found[0], "severity")


def test_a_declared_exception_stops_that_violation_only(tmp_path):
    root = _root(tmp_path)
    module = _write(
        root / "src/ats/agents/macro", "assemble.py", """
        from ...data import factset
        from ...data import websearch
        """)
    allowed = (ExceptionEntry(module=_relative(module, root),
                              target="ats.data.factset", reason="declared for the test"),)
    found = scan_module(module, root=root, exceptions=allowed)
    assert [v.target for v in found] == ["ats.data.websearch"]


def test_sector_may_only_read_layer_projections():
    """Phase D 3.8：行业分析师唯一的跨角色读取许可是 layer_analyst。"""
    assert guards.ALLOWED_CROSS_ROLE_READS.get("sector_analyst") == ("layer_analyst",)
    found = [v for v in guards.scan_agents()
             if v.kind == "cross_role_read"
             and v.module.startswith("src/ats/agents/sector")]
    assert found == []
