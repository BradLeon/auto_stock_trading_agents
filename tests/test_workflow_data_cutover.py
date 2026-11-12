"""Phase 10.4 acceptance: orchestration inputs, release records and rollback."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ats.data.pipelines.structured.release import ReleaseManager, load_release_overlay
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.graph.chief import assemble_context
from ats.graph.chief_state import ChiefDecisionState


def _release_manager(tmp_path) -> ReleaseManager:
    repository = SQLiteStructuredRepository(
        tmp_path / "data.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    return ReleaseManager(repository, path=tmp_path / "releases.yaml")


def test_workflow_boundaries_keep_memory_outputs_out_of_data_products(monkeypatch):
    from ats.data.products import workflow_data_boundary

    chief = workflow_data_boundary("chief-graph")

    # No rollout mode: it was only logged and read as "the Chief never switched" (removed 2026-11-12).
    assert chief.consumer == "chief_graph" and not hasattr(chief, "mode")
    assert "decision" in chief.memory_outputs[0]
    # The legacy scheduler's boundary went with the scheduler (2026-10-20).
    import pytest
    with pytest.raises(ValueError, match="unknown workflow data boundary"):
        workflow_data_boundary("runtime_scheduler")


def test_chief_uses_boundary_metadata_without_routing_memory_through_data(monkeypatch):
    from ats.data.products import WorkflowDataBoundary
    import ats.data.products as products
    from ats.graph import chief as chief_graph

    seen = []
    monkeypatch.setattr(products, "workflow_data_boundary", lambda consumer: (
        seen.append(consumer) or WorkflowDataBoundary(
            consumer="chief_graph", persistent_inputs=(),
            runtime_inputs=(), memory_outputs=("decisions",))))
    monkeypatch.setattr("ats.trader.execute.pead_event_data", lambda: {"events": []})
    state = ChiefDecisionState(
        cycle_id="boundary-test", as_of=chief_graph._now(), source="chief",
        decide=False, dry_run=True,
    )

    assert assemble_context(state)["event_data"] == {"events": []}
    assert seen == ["chief_graph"]


def test_release_confirmation_product_keeps_event_date_guard(monkeypatch):
    from ats.data.products import earnings

    class Print:
        date = date(2026, 8, 4)
        reported = False
        eps_actual = None

    monkeypatch.setattr(
        "ats.data.pipelines.unstructured.official.release_filed_on_or_after",
        lambda symbol, *, expected_date: (expected_date == Print.date, f"8-K {symbol}"),
    )

    assert earnings.confirm_reported("MSFT", Print()) == (True, "8-K MSFT")


def test_release_and_rollback_are_isolated_per_consumer(tmp_path):
    manager = _release_manager(tmp_path)

    # `chief_graph` no longer carries a rollout mode (removed 2026-11-12), so it is not a releasable consumer.
    assert manager.check_consumer("chief_graph", mode="shadow")["ready"] is False

    check = manager.check_consumer("pead_graph", mode="platform")
    assert check["ready"] is True

    assert manager.apply(check)["applied"] is True
    rollback = manager.rollback(kind="consumer", target_id="pead_graph", mode="legacy")
    assert rollback["previous_mode"] == "platform"

    overlay = load_release_overlay(tmp_path / "releases.yaml")
    assert overlay["consumers"] == {"pead_graph": "legacy"}
    assert [row["action"] for row in overlay["history"]] == ["publish", "rollback"]
