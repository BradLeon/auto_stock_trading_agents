"""Phase B task group 3: research snapshot, cycle creation, legacy double-write."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.decision.repository import DecisionAuditRepository
from ats.decision.snapshot import (IncompleteResearchSnapshotError,
                                   build_research_snapshot, open_decision_cycle,
                                   record_no_action, supersede_cycle)
from ats.decision.state import CycleStatus, TERMINAL_STATUSES
from ats.memory.store import TradingMemory
from ats.workflow.run_contracts import TaskRegistry, WorkflowTaskSpec

NOW = "2026-09-23T00:00:00+00:00"


def _expiry(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(
        timespec="seconds")


def _envelope(**kwargs):
    params = dict(
        role="layer_analysis",
        payload={"layer": "L3", "status": "expanding",
                 "summary": "orders accelerating",
                 "findings": ["book-to-bill 1.4"], "confidence": 0.8},
        scope=ProjectionScope(kind="layer", id="L3"), as_of=NOW,
        input_refs=["obs-1"], data_vintage_refs=["dataset@2026-09-21"],
        valid_until=_expiry(1))
    params.update(kwargs)
    return build_envelope(**params)


def _registry():
    return TaskRegistry([
        WorkflowTaskSpec(task_id="layer", agent_role="layer_analysis"),
        WorkflowTaskSpec(task_id="macro", agent_role="macro_review"),
        # Optional tasks never gate the cycle.
        WorkflowTaskSpec(task_id="deep_dive", agent_role="technical_review",
                         required_for_decision=False),
    ])


def _repo(tmp_path, name="snap.sqlite"):
    return DecisionAuditRepository(TradingMemory(tmp_path / name))


# --- 3.1 snapshot builder ------------------------------------------------------ #

def test_snapshot_items_trace_back_to_concrete_envelopes(tmp_path):
    env_layer = _envelope()
    env_macro = _envelope(scope=ProjectionScope(kind="portfolio"))
    snapshot = build_research_snapshot(
        registry=_registry(),
        projections={"layer": env_layer, "macro": env_macro},
        scope=ProjectionScope(kind="portfolio"))
    by_task = {item.task_id: item for item in snapshot.items}
    assert by_task["layer"].projection_id == env_layer.projection_id
    assert by_task["layer"].content_hash == env_layer.content_hash
    assert by_task["layer"].as_of == env_layer.as_of
    assert by_task["layer"].reusable and by_task["layer"].freshness == "fresh"
    assert by_task["macro"].projection_id == env_macro.projection_id
    # optional task absent → not a gap; the snapshot is still complete
    assert "deep_dive" not in by_task
    assert snapshot.complete


def test_snapshot_reports_missing_and_stale_with_reasons(tmp_path):
    expired = _envelope(valid_until="2026-09-22T00:00:00+00:00")
    wrong_scope = _envelope(scope=ProjectionScope(kind="layer", id="L1"))
    snapshot = build_research_snapshot(
        registry=_registry(),
        projections={"layer": expired, "macro": None,
                     "deep_dive": wrong_scope},
        scope=ProjectionScope(kind="portfolio"))
    by_task = {item.task_id: item for item in snapshot.items}
    assert by_task["macro"].reason == "missing"
    assert by_task["macro"].freshness == "missing"
    assert by_task["layer"].reason == "expired"
    assert by_task["layer"].freshness == "stale"
    assert not snapshot.complete
    assert {item.task_id for item in snapshot.gaps()} == {"layer", "macro"}


def test_reuse_reasons_use_the_phase_a_vocabulary(tmp_path):
    # the layer task was ASKED about L2; the envelope speaks about L3
    wrong_scope = _envelope(scope=ProjectionScope(kind="layer", id="L1"))
    snapshot = build_research_snapshot(
        registry=_registry(),
        projections={"layer": wrong_scope, "macro": None},
        scope=ProjectionScope(kind="portfolio"),
        required_scopes={"layer": ProjectionScope(kind="layer", id="L2")})
    by_task = {item.task_id: item for item in snapshot.items}
    assert by_task["layer"].reason == "scope_mismatch"


# --- 3.2 refuse to create on incomplete input ----------------------------------- #

def test_incomplete_snapshot_blocks_cycle_creation_entirely(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(IncompleteResearchSnapshotError):
        open_decision_cycle(
            repo, cycle_id="c1", trigger_source="manual", registry=_registry(),
            projections={"layer": _envelope(), "macro": None},
            scope=ProjectionScope(kind="portfolio"))
    # nothing at all was written — no cycle, no revisions, no events
    assert repo.get_cycle("c1") is None
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM decision_revisions").fetchone()[0] == 0
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM cycle_events").fetchone()[0] == 0


def test_complete_snapshot_creates_cycle_with_frozen_snapshot(tmp_path):
    repo = _repo(tmp_path)
    env = _envelope()
    cycle, snapshot = open_decision_cycle(
        repo, cycle_id="c1", trigger_source="schedule", trigger_id="s-1",
        registry=_registry(), projections={"layer": env, "macro": env},
        scope=ProjectionScope(kind="portfolio"))
    assert cycle["status"] == CycleStatus.DRAFT.value
    import json
    stored = json.loads(cycle["research_snapshot"])
    assert stored["scope"] == "portfolio"
    assert {item["task_id"] for item in stored["items"]} == {"layer", "macro"}
    assert snapshot.complete


# --- 3.3 superseded on expiry, inputs never swapped ------------------------------ #

def test_snapshot_expiry_supersedes_cycle_instead_of_swapping_inputs(tmp_path):
    repo = _repo(tmp_path)
    env = _envelope()
    open_decision_cycle(
        repo, cycle_id="c1", trigger_source="manual", registry=_registry(),
        projections={"layer": env, "macro": env},
        scope=ProjectionScope(kind="portfolio"))
    original_snapshot = repo.get_cycle("c1")["research_snapshot"]

    # mid-cycle: the layer projection expires
    expired = _envelope(valid_until="2026-09-22T00:00:00+00:00")
    refreshed = build_research_snapshot(
        registry=_registry(), projections={"layer": expired, "macro": env},
        scope=ProjectionScope(kind="portfolio"))
    assert not refreshed.complete
    assert supersede_cycle(repo, "c1", reason="research_snapshot_stale")

    cycle = repo.get_cycle("c1")
    assert cycle["status"] == CycleStatus.SUPERSEDED.value
    assert cycle["superseded_reason"] == "research_snapshot_stale"
    assert cycle["final_outcome"] == "superseded"
    # the original snapshot was NOT replaced
    assert cycle["research_snapshot"] == original_snapshot
    # the superseded cycle is terminal: it cannot resume with new inputs
    with pytest.raises(Exception):
        repo.transition("c1", to_status=CycleStatus.PENDING_RISK, actor="chief")
    # the new inputs can only open a NEW cycle
    fresh = _envelope(as_of="2026-09-24T00:00:00+00:00")
    cycle2, _ = open_decision_cycle(
        repo, cycle_id="c2", trigger_source="manual", registry=_registry(),
        projections={"layer": fresh, "macro": fresh},
        scope=ProjectionScope(kind="portfolio"))
    assert cycle2["cycle_id"] == "c2"


# --- 3.4 No Action domain half (graph-level test lands with group 5) -------------- #

def test_no_action_is_a_formal_terminal_outcome_with_reason(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    assert record_no_action(repo, "c1", reason="signal consumed, no edge")
    cycle = repo.get_cycle("c1")
    assert cycle["status"] == CycleStatus.NO_ACTION.value
    assert cycle["final_outcome"] == "signal consumed, no edge"
    assert CycleStatus.NO_ACTION in TERMINAL_STATUSES
    with pytest.raises(Exception):
        repo.transition("c1", to_status=CycleStatus.PENDING_RISK, actor="chief")


# --- 3.5 legacy double-write ------------------------------------------------------ #

def test_mirror_keeps_legacy_read_entry_points_working(tmp_path):
    repo = _repo(tmp_path)
    store = repo.store
    repo.create_cycle(cycle_id="chief-20260923-000000", trigger_source="manual")
    rev = repo.append_revision(
        cycle_id="chief-20260923-000000",
        orders=[{"symbol": "AMD", "action": "buy", "notional_usd": 5000.0,
                 "rationale": "L3 expanding"}],
        rationale="r")
    repo.mirror_revision_to_legacy("chief-20260923-000000",
                                   rev["revision_no"], as_of=NOW)
    # old read entry points return the mirrored content unchanged
    run = store.last_chief_run()
    assert run is not None and run["cycle_id"] == "chief-20260923-000000"
    assert [d["symbol"] for d in run["decisions"]] == ["AMD"]
    decisions = store.recent_decisions(symbol="AMD")
    assert decisions[0]["notional_usd"] == 5000.0
    # replay-safe: mirroring twice does not duplicate rows
    repo.mirror_revision_to_legacy("chief-20260923-000000",
                                   rev["revision_no"], as_of=NOW)
    assert len(store.recent_decisions(symbol="AMD")) == 1


def test_mirror_skips_legacy_unknown_revisions(tmp_path):
    repo = _repo(tmp_path)
    store = repo.store
    store.conn.execute(
        "INSERT INTO decision_revisions (cycle_id, revision_no, decision_hash, "
        "revision_source, legacy_ref, created_at) "
        "VALUES ('c-old', 1, NULL, 'legacy_unknown', '[]', 't')")
    repo.mirror_revision_to_legacy("c-old", 1, as_of=NOW)
    assert store.recent_decisions() == []
