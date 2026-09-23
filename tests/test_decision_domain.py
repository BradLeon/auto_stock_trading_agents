"""Phase B task group 2: decision domain model, hashing and audit repository."""

from __future__ import annotations

import sqlite3

import pytest

from ats.decision.hashing import decision_hash
from ats.decision.repository import (DecisionAuditRepository,
                                     RevisionImmutableError,
                                     TransitionConflictError,
                                     transition_idempotency_key)
from ats.decision.state import (CycleStatus, TERMINAL_STATUSES, is_terminal,
                                revision_authorization_blockers)

ORDERS_A = [{"symbol": "AMD", "action": "buy", "notional_usd": 5000.0}]


def _repo(tmp_path, name="audit.sqlite"):
    from ats.memory.store import TradingMemory
    return DecisionAuditRepository(TradingMemory(tmp_path / name))


# --- 2.1 status enum ---------------------------------------------------------- #

def test_status_terminality_is_enumerated_completely():
    executable = {CycleStatus.DRAFT, CycleStatus.PENDING_RISK,
                  CycleStatus.RISK_REJECTED, CycleStatus.PENDING_APPROVAL}
    assert TERMINAL_STATUSES == {CycleStatus.EXECUTED, CycleStatus.MANUAL_REVIEW,
                                 CycleStatus.NO_ACTION,
                                 CycleStatus.APPROVAL_REJECTED,
                                 CycleStatus.SUPERSEDED}
    for status in CycleStatus:
        assert is_terminal(status) == (status not in executable)


def test_terminal_cycle_never_returns_to_executable_state(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    repo.transition("c1", to_status=CycleStatus.NO_ACTION, actor="chief")
    for target in (CycleStatus.PENDING_RISK, CycleStatus.PENDING_APPROVAL,
                   CycleStatus.EXECUTED):
        with pytest.raises(TransitionConflictError):
            repo.transition("c1", to_status=target, actor="chief")


def test_legacy_unknown_revisions_are_blocked_from_authorization():
    assert "revision_source:legacy_unknown" in revision_authorization_blockers(
        {"revision_source": "legacy_unknown", "decision_hash": "h"})
    assert revision_authorization_blockers(
        {"revision_source": "chief", "decision_hash": ""}) == \
        ["missing_decision_hash"]
    assert revision_authorization_blockers(
        {"revision_source": "chief", "decision_hash": "h"}) == []


# --- 2.2 decision_hash -------------------------------------------------------- #

def test_same_semantics_hash_equal_regardless_of_serialization():
    h1 = decision_hash(orders=[{"symbol": "AMD", "action": "buy",
                                "notional_usd": 5000.0}],
                       rationale="conviction", input_refs=["q2-transcript"])
    # different key order, different order-list order, padded whitespace
    h2 = decision_hash(orders=[{"notional_usd": 5000.0, "action": "buy",
                                "symbol": "AMD"},
                               {"symbol": "NVDA", "notional_usd": 1000.0,
                                "action": "trim"}][:1],
                       rationale="conviction", input_refs=["q2-transcript"])
    assert h1 == h2


def test_any_substantive_change_moves_the_hash():
    base = dict(orders=[{"symbol": "AMD", "action": "buy",
                         "notional_usd": 5000.0}],
                rationale="conviction", input_refs=["q2-transcript"])
    h0 = decision_hash(**base)
    variants = [
        dict(base, orders=[{"symbol": "AMD", "action": "buy",
                            "notional_usd": 6000.0}]),          # notional
        dict(base, orders=[{"symbol": "AMD", "action": "trim",
                            "notional_usd": 5000.0}]),          # action
        dict(base, orders=[{"symbol": "NVDA", "action": "buy",
                            "notional_usd": 5000.0}]),          # symbol
        dict(base, rationale="changed mind"),                    # rationale
        dict(base, input_refs=["q3-transcript"]),                # input refs
        dict(base, orders=[{"symbol": "AMD", "action": "buy",
                            "notional_usd": 5000.0},
                           {"symbol": "NVDA", "action": "trim",
                            "notional_usd": 1000.0}]),          # extra order
    ]
    for kwargs in variants:
        assert decision_hash(**kwargs) != h0


def test_hash_matches_phase_a_convention_length():
    h = decision_hash(orders=ORDERS_A, rationale="r")
    assert len(h) == 32 and int(h, 16) >= 0


# --- 2.3 gapless unique revision_no ------------------------------------------- #

def test_revision_numbers_are_gapless_and_unique(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    for i in range(3):
        row = repo.append_revision(
            cycle_id="c1", orders=[{"symbol": "AMD", "action": "buy",
                                    "notional_usd": 1000.0 + i}])
        assert row["revision_no"] == i + 1
    numbers = [r["revision_no"] for r in repo.list_revisions("c1")]
    assert numbers == [1, 2, 3]


def test_concurrent_writers_allocate_distinct_revision_numbers(tmp_path):
    # Two repository objects over the SAME database file: serialized writes
    # must yield distinct, gapless numbers, never duplicates.
    repo_a = _repo(tmp_path, name="shared.sqlite")
    repo_b = _repo(tmp_path, name="shared.sqlite")
    repo_a.create_cycle(cycle_id="c1", trigger_source="manual")
    seen = set()
    for repo in (repo_a, repo_b, repo_a, repo_b):
        row = repo.append_revision(
            cycle_id="c1",
            orders=[{"symbol": "AMD", "action": "buy",
                     "notional_usd": 100.0 + len(seen)}])
        assert row["revision_no"] not in seen
        seen.add(row["revision_no"])
    assert sorted(seen) == [1, 2, 3, 4]


def test_identical_resubmission_returns_existing_revision(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    first = repo.append_revision(cycle_id="c1", orders=ORDERS_A,
                                 rationale="r")
    again = repo.append_revision(cycle_id="c1", orders=ORDERS_A,
                                 rationale="r")
    assert again["revision_no"] == first["revision_no"]
    assert len(repo.list_revisions("c1")) == 1


# --- 2.4 append-only ----------------------------------------------------------- #

def test_persisted_revision_cannot_be_updated(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    row = repo.append_revision(cycle_id="c1", orders=ORDERS_A, rationale="r")
    with pytest.raises(RevisionImmutableError):
        repo.update_revision("whatever")
    after = repo.get_revision("c1", row["revision_no"])
    assert dict(after) == dict(row)              # row untouched, field by field


# --- 2.5 compare-and-set transitions ------------------------------------------- #

def test_transition_appends_event_and_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    event, changed = repo.transition(
        "c1", to_status=CycleStatus.PENDING_RISK, actor="chief",
        revision_no=1)
    assert changed
    n_events = repo.conn.execute(
        "SELECT COUNT(*) FROM cycle_events").fetchone()[0]
    # replay under the same key: same event, no second row
    replay, changed2 = repo.transition(
        "c1", to_status=CycleStatus.PENDING_RISK, actor="chief",
        revision_no=1, idempotency_key=event["idempotency_key"])
    assert not changed2 and dict(replay) == dict(event)
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM cycle_events").fetchone()[0] == n_events
    # a DIFFERENT semantic transition gets its own key and its own row
    repo.transition("c1", to_status=CycleStatus.PENDING_APPROVAL, actor="risk")
    rows = repo.conn.execute(
        "SELECT to_status FROM cycle_events ORDER BY event_id").fetchall()
    assert [r[0] for r in rows] == ["pending_risk", "pending_approval"]


def test_transition_idempotency_survives_reopening_the_database(tmp_path):
    repo_a = _repo(tmp_path, name="reopen.sqlite")
    repo_a.create_cycle(cycle_id="c1", trigger_source="manual")
    event, _ = repo_a.transition(
        "c1", to_status=CycleStatus.PENDING_RISK, actor="chief")
    # a fresh process / fresh connection: same key → same behaviour
    repo_b = _repo(tmp_path, name="reopen.sqlite")
    replay, changed = repo_b.transition(
        "c1", to_status=CycleStatus.PENDING_RISK, actor="chief",
        idempotency_key=event["idempotency_key"])
    assert not changed and dict(replay) == dict(event)
    assert repo_b.has_transition("c1", event["idempotency_key"])


def test_transition_from_wrong_state_conflicts(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    with pytest.raises(TransitionConflictError):
        repo.transition("c1", to_status=CycleStatus.PENDING_APPROVAL,
                        actor="risk", from_status=CycleStatus.PENDING_APPROVAL)


def test_transition_idempotency_key_excludes_request_moment():
    assert transition_idempotency_key("c1", "draft", "pending_risk", 1) == \
        transition_idempotency_key("c1", "draft", "pending_risk", 1)
    assert transition_idempotency_key("c1", "draft", "pending_risk", 1) != \
        transition_idempotency_key("c1", "draft", "pending_risk", 2)


# --- 2.6 chain read -------------------------------------------------------------- #

def test_chain_read_answers_what_was_approved_from_storage_alone(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    rev = repo.append_revision(cycle_id="c1", orders=ORDERS_A, rationale="r")
    repo.transition("c1", to_status=CycleStatus.PENDING_RISK, actor="chief",
                    revision_no=rev["revision_no"])
    repo.record_review(review_id="rv1", cycle_id="c1",
                       revision_no=rev["revision_no"],
                       decision_hash=rev["decision_hash"],
                       ruleset_version="r1", portfolio_snapshot_id="ps1",
                       market_as_of="2026-09-23", verdict="approved")
    repo.transition("c1", to_status=CycleStatus.PENDING_APPROVAL, actor="risk")
    repo.record_approval(approval_id="ap1", cycle_id="c1",
                         revision_no=rev["revision_no"],
                         decision_hash=rev["decision_hash"],
                         decision="approved", reviewer="boss",
                         idempotency_key="k1")
    repo.transition("c1", to_status=CycleStatus.EXECUTED, actor="trader")

    chain = repo.read_chain("c1")
    assert len(chain["revisions"]) == 1
    assert chain["reviews"][0]["verdict"] == "approved"
    assert chain["approved_revision_hashes"] == [rev["decision_hash"]]
    assert chain["has_execution_approval"]
    assert [e["to_status"] for e in chain["events"]
            if e["event_type"] == "status_change"] == [
        "pending_risk", "pending_approval", "executed"]


# --- 2.7 checkpoint is not audit truth -------------------------------------------- #

def test_recovery_consults_domain_records_not_checkpoints(tmp_path):
    """A 'crashed' process reopens the DB and asks the STORE what already
    happened; replaying its work must not duplicate anything."""
    repo_a = _repo(tmp_path, name="crash.sqlite")
    repo_a.create_cycle(cycle_id="c1", trigger_source="manual")
    rev = repo_a.append_revision(cycle_id="c1", orders=ORDERS_A, rationale="r")
    event, _ = repo_a.transition(
        "c1", to_status=CycleStatus.PENDING_RISK, actor="chief",
        revision_no=rev["revision_no"])

    # --- crash: new process, new connection, no in-memory state ---
    repo_b = _repo(tmp_path, name="crash.sqlite")
    assert repo_b.has_revision("c1", rev["decision_hash"])
    assert repo_b.has_transition("c1", event["idempotency_key"])
    # recovered process re-runs its writes: both are no-ops
    rev2 = repo_b.append_revision(cycle_id="c1", orders=ORDERS_A, rationale="r")
    assert rev2["revision_no"] == rev["revision_no"]
    _, changed = repo_b.transition(
        "c1", to_status=CycleStatus.PENDING_RISK, actor="chief",
        revision_no=rev["revision_no"])
    assert not changed
    assert len(repo_b.list_revisions("c1")) == 1
    # one revision_appended + one status_change, no duplicates
    assert repo_b.conn.execute(
        "SELECT COUNT(*) FROM cycle_events").fetchone()[0] == 2
    assert repo_b.conn.execute(
        "SELECT COUNT(*) FROM cycle_events WHERE event_type='status_change'"
    ).fetchone()[0] == 1
    # a replayed approval callback is likewise a no-op on the record
    first = repo_a.record_approval(approval_id="ap1", cycle_id="c1",
                                   revision_no=rev["revision_no"],
                                   decision_hash=rev["decision_hash"],
                                   decision="approved", reviewer="boss",
                                   idempotency_key="k1")
    replay = repo_b.record_approval(approval_id="ap1", cycle_id="c1",
                                    revision_no=rev["revision_no"],
                                    decision_hash=rev["decision_hash"],
                                    decision="approved", reviewer="boss",
                                    idempotency_key="k1")
    assert dict(replay) == dict(first)
    assert repo_b.conn.execute(
        "SELECT COUNT(*) FROM boss_approvals").fetchone()[0] == 1


def test_unknown_cycle_transition_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(TransitionConflictError):
        repo.transition("ghost", to_status=CycleStatus.PENDING_RISK,
                        actor="chief")
