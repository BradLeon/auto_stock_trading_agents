"""The legacy-retirement register: tombstone, fail-closed gate, two-phase purge.

The point of this file is not coverage — it is that the three failure modes the
registry exists to prevent are each pinned by a test: a tombstone silently losing to an
in-use entry, a retired read being indistinguishable from an empty one, and an
unconfirmed purge that deletes anyway.
"""

from __future__ import annotations

import pytest

from ats.workflow.legacy_retirement import (
    REASON_NOT_FOUND,
    REASON_NO_DATA,
    REASON_RETIRED,
    RetirementConflictError,
    RetirementRegistry,
    RetirementTombstone,
    registry_from_mapping,
)


def _tombstone(identifier: str = "old.path", **kwargs) -> RetirementTombstone:
    params = dict(identifier=identifier, capability_domain="workflow/test",
                  replaced_by="new.path", target_phase="A",
                  exit_condition="consumers reach zero", status="retired")
    params.update(kwargs)
    return RetirementTombstone(**params)


# --- 7.1 registration ------------------------------------------------------ #

def test_a_registered_tombstone_can_be_read_back() -> None:
    registry = RetirementRegistry([_tombstone(missing_condition="")])
    found = registry.tombstone("old.path")
    assert found is not None
    assert found.replaced_by == "new.path"
    assert found.may_exit is True
    assert registry.identifiers() == ("old.path",)


def test_a_pending_tombstone_must_state_what_it_is_waiting_for() -> None:
    with pytest.raises(ValueError, match="missing_condition"):
        _tombstone(status="pending", missing_condition="")


def test_a_tombstone_carries_the_exit_criterion_not_just_the_target() -> None:
    stone = _tombstone(status="pending", missing_condition="consumers not migrated",
                       consumers=("cli",), consumer_zero_criterion="cli reads envelope")
    assert stone.may_exit is False
    assert stone.consumer_zero_criterion == "cli reads envelope"


# --- 7.2 mutual exclusion -------------------------------------------------- #

def test_an_identifier_in_both_places_fails_the_registry() -> None:
    with pytest.raises(RetirementConflictError) as caught:
        RetirementRegistry([_tombstone()], active=["old.path"])
    assert caught.value.identifiers == ["old.path"]


def test_a_retired_identifier_is_removed_from_the_in_use_inventory() -> None:
    """Registering an exit and keeping the in-use entry is the conflict; dropping the
    in-use entry is the correct sequence."""
    registry = RetirementRegistry([_tombstone()])
    assert registry.active() == ()
    registry.mark_active("still.here")
    assert registry.active() == ("still.here",)


def test_a_pending_item_may_still_be_in_use() -> None:
    """Pending means 'registered, not yet removable' — it must not conflict."""
    registry = RetirementRegistry(
        [_tombstone(status="pending", missing_condition="consumers remain")],
        active=["old.path"])
    assert registry.read_gate("old.path") == ("ok", "")


# --- 7.3 fail-closed read gate --------------------------------------------- #

def test_the_three_outcomes_carry_distinct_reason_codes() -> None:
    registry = RetirementRegistry([_tombstone()], active=["live.path"])
    assert registry.read_gate("old.path") == ("retired", REASON_RETIRED)
    assert registry.read_gate("unknown.path") == ("not_found", REASON_NOT_FOUND)
    assert registry.read_gate("live.path", has_data=False) == ("no_data", REASON_NO_DATA)
    codes = {registry.read_gate("old.path")[1], registry.read_gate("unknown.path")[1],
             registry.read_gate("live.path", has_data=False)[1]}
    assert codes == {REASON_RETIRED, REASON_NOT_FOUND, REASON_NO_DATA}


def test_a_retired_read_is_not_silently_served_by_the_replacement() -> None:
    """The gate says 'retired', never 'here is something similar'."""
    registry = RetirementRegistry([_tombstone(replaced_by="new.path")])
    kind, reason = registry.read_gate("old.path")
    assert (kind, reason) == ("retired", REASON_RETIRED)


def test_a_write_to_a_retired_path_fails_instead_of_being_redirected() -> None:
    registry = RetirementRegistry([_tombstone()], active=["live.path"])
    assert registry.write_gate("old.path") == (False, REASON_RETIRED)
    assert registry.write_gate("live.path") == (True, "")


# --- 7.4 two-phase purge ---------------------------------------------------- #

def test_an_unconfirmed_purge_only_reports_its_scope() -> None:
    registry = RetirementRegistry([_tombstone()])
    registry.declare_purge_scope("old.path", ["old.path.a", "old.path.b"])
    plan = registry.purge("old.path")
    assert plan.executed is False
    assert plan.scope == ("old.path.a", "old.path.b")
    assert registry.audit_log() == ()


def test_a_confirmed_purge_leaves_an_audit_record() -> None:
    registry = RetirementRegistry([_tombstone()])
    registry.declare_purge_scope("old.path", ["old.path.a"])
    plan = registry.purge("old.path", confirm=True, note="consumers migrated")
    assert plan.executed is True
    record = registry.audit_log()[0]
    assert record.identifier == "old.path"
    assert record.action == "purge"
    assert record.scope == ("old.path.a",)
    assert record.note == "consumers migrated"
    assert record.data_retained_elsewhere is False


def test_the_retention_declaration_is_recorded_not_inferred() -> None:
    registry = RetirementRegistry([_tombstone()])
    registry.purge("old.path", confirm=True, exported=True, note="parquet kept")
    assert registry.audit_log()[0].data_retained_elsewhere is True
    assert registry.audit_log()[0].at


# --- 7.5 first batch -------------------------------------------------------- #

def test_the_registered_batch_covers_every_documented_item() -> None:
    """design.md 的待退项登记表逐项核对，不允许多登记也不允许漏登记。
    Phase A 8 项 + Phase B 6 项（任务 8.1）。"""
    from ats.workflow import legacy_retirement as mod

    registry = mod.load_registry()
    expected = {
        "workflow_memory.evidence_observations",
        "workflow_memory.evidence_facts",
        "workflow_memory.evidence_fact_projections",
        "workflow_memory.evidence_failures",
        "task_projections.legacy_columns",
        "evidence_fact_projections.legacy_observation_id",
        "scheduler.hardcoded_serial_run",
        "legacy_read_models",
        # ── Phase C（任务 6.7：旧直读点先行登记；其余五项随任务 8.1 批次追加）──
        "store.direct_trade_reads",
        "reconcile.origin_manual_fallback",
        "reconcile.partial_fill_as_filled",
        "scheduler.adhoc_journal_jobs",
        "performance.legacy_snapshot",
        "journal.report.render_ledger",
        # ── Phase D（任务 2.9：层级配置字段退役）──
        "layer_verdict.allocation",
        # ── Phase B ──
        "risk.checks.in_place_clipping",
        "agents.risk_validator.apply_guardrails",
        "runtime.server.in_process_resume_dedup",
        "memory.store.legacy_decision_write_path",
        "trades.client_order_id.legacy_derivation",
        "risk.checks.pre_trade_adapter",
    }
    assert set(registry.identifiers()) == expected
    for stone in registry.tombstones():
        assert stone.replaced_by and stone.exit_condition
        if stone.status == "pending":
            assert stone.missing_condition


def test_every_registered_item_names_its_consumer_zero_criterion() -> None:
    from ats.workflow import legacy_retirement as mod

    for stone in mod.load_registry().tombstones():
        assert stone.consumer_zero_criterion, stone.identifier


def test_the_legacy_projection_columns_are_registered_but_not_deleted() -> None:
    from ats.workflow import legacy_retirement as mod

    stone = mod.load_registry().tombstone("task_projections.legacy_columns")
    assert stone is not None and stone.status == "pending"
    assert stone.replaced_by == "task_projection_envelopes"


def test_no_physical_purge_has_been_performed() -> None:
    from ats.workflow import legacy_retirement as mod

    assert mod.load_registry().audit_log() == ()


def test_a_mapping_with_a_conflict_fails_to_build() -> None:
    with pytest.raises(RetirementConflictError):
        registry_from_mapping({
            "active": ["x"],
            "retired": [{"identifier": "x", "status": "retired",
                         "exit_condition": "done"}],
        })
