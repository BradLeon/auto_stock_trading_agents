"""Phase B task group 7: the execution authorization gate.

7.1 the authorization is DERIVED from the audit store (cycle + current revision
    + passed review + valid approval); missing prerequisites fail construction.
7.2/7.9 bare instructions (no authorization) never reach the broker.
7.3 pre-submit re-verification rejects incomplete/stale/wrongly-bound authorizations.
7.4 `max_snapshot_age_seconds` default 60, yaml-loaded.
7.5/7.6 a stale portfolio snapshot voids the approval and routes back to the
    risk gate: fresh review + fresh approval, never the old blessing.
7.7 order identity = cycle + revision + sequence within the revision.
7.8 retry with an uncertain prior outcome never produces a second logical order.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.decision.repository import DecisionAuditRepository
from ats.execution.authorization import (AuthorizationError,
                                         ExecutionAuthorization,
                                         build_authorization,
                                         validate_authorization)
from ats.graph.chief_state import ChiefDecisionState
from ats.memory.store import TradingMemory
from ats.runtime.cli import run_decision_graph
from ats.schemas.decision import TradeDecision
from ats.schemas.memory import TradeLogEntry

NOW = datetime.now(timezone.utc)


def _repo(tmp_path) -> DecisionAuditRepository:
    return DecisionAuditRepository(TradingMemory(tmp_path / "g7.sqlite"))


def _order(notional: float) -> dict:
    return {"symbol": "NVDA", "action": "buy", "notional_usd": notional}


def _authorized_cycle(tmp_path, cycle_id: str = "c1") -> DecisionAuditRepository:
    """Cycle with revision + approved review + approval — fully authorized."""
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id=cycle_id, trigger_source="manual")
    rev = repo.append_revision(cycle_id=cycle_id, orders=[_order(1_000.0)],
                               rationale="v1")
    repo.record_review(
        review_id=f"{cycle_id}:r1:review:r1", cycle_id=cycle_id,
        revision_no=rev["revision_no"], decision_hash=rev["decision_hash"],
        ruleset_version="ruleset-1", portfolio_snapshot_id="pf:1",
        market_as_of=NOW.isoformat(), verdict="approved")
    repo.record_approval(
        approval_id=f"{cycle_id}:r1:approval:r1", cycle_id=cycle_id,
        revision_no=rev["revision_no"], decision_hash=rev["decision_hash"],
        decision="approved", reviewer="boss",
        idempotency_key=f"key-{cycle_id}")
    repo.transition(cycle_id, to_status="pending_approval", actor="risk_gate",
                    revision_no=rev["revision_no"])
    return repo


# --- 7.1 derivation from the store ---------------------------------------------- #

def test_build_fails_without_review_or_approval(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    repo.append_revision(cycle_id="c1", orders=[_order(1_000.0)], rationale="v1")
    with pytest.raises(AuthorizationError) as ei:
        build_authorization(repo, "c1")
    assert "review" in str(ei.value) and "approval" in str(ei.value)


def test_build_fails_on_rejected_review(tmp_path):
    repo = _repo(tmp_path)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    rev = repo.append_revision(cycle_id="c1", orders=[_order(1_000.0)], rationale="v1")
    repo.record_review(
        review_id="rv", cycle_id="c1", revision_no=rev["revision_no"],
        decision_hash=rev["decision_hash"], ruleset_version="r",
        portfolio_snapshot_id="pf", market_as_of=NOW.isoformat(), verdict="rejected")
    with pytest.raises(AuthorizationError):
        build_authorization(repo, "c1")


def test_build_succeeds_with_all_prerequisites(tmp_path):
    repo = _authorized_cycle(tmp_path)
    auth = build_authorization(repo, "c1")
    assert auth.cycle_id == "c1" and auth.revision_no == 1
    assert auth.ruleset_version == "ruleset-1"
    assert auth.portfolio_snapshot_id == "pf:1"


# --- 7.2/7.9 bare instructions are refused --------------------------------------- #

def test_place_orders_without_authorization_is_rejected(broker):
    from ats.memory import get_store
    from ats.trader import execute as texec

    d = TradeDecision(symbol="NVDA", action="buy", qty=10, rationale="r")
    entries, fills = texec.place_orders([(d, 10.0)], "c1", revision_no=1,
                                        authorization=None)
    assert broker.placed == []                       # broker never touched
    assert fills == []
    assert [e.status for e in entries] == ["rejected"]
    assert "authorization" in entries[0].error
    assert get_store().conn.execute(
        "SELECT COUNT(*) FROM trades").fetchone()[0] == 0


# --- 7.3 pre-submission re-verification ------------------------------------------ #

def test_validate_rejects_incomplete_authorization(tmp_path):
    repo = _authorized_cycle(tmp_path)
    auth = ExecutionAuthorization(cycle_id="c1", revision_no=1, decision_hash="",
                                  review_id="", review_at="", approval_id="",
                                  approval_at="", ruleset_version="",
                                  portfolio_snapshot_id="", market_as_of="")
    reasons = validate_authorization(repo, auth)
    assert reasons and "incomplete" in reasons[0]


def test_validate_rejects_hash_mismatch_stale_review_and_approval(tmp_path):
    repo = _authorized_cycle(tmp_path)
    auth = build_authorization(repo, "c1")
    # A new revision invalidates hash binding + review + approval in one shot.
    repo.append_revision(cycle_id="c1", orders=[_order(500.0)], rationale="v2")
    reasons = validate_authorization(repo, auth)
    joined = "; ".join(reasons)
    assert "hash_mismatch" in joined
    assert "review_not_effective" in joined
    assert "approval_not_effective" in joined


def test_validate_rejects_missing_portfolio_snapshot(tmp_path):
    repo = _authorized_cycle(tmp_path)
    auth = build_authorization(repo, "c1")
    reasons = validate_authorization(repo, auth, snapshot_as_of=None)
    assert "missing_portfolio_snapshot" in reasons


def test_validate_rejects_stale_snapshot(tmp_path):
    repo = _authorized_cycle(tmp_path)
    auth = build_authorization(repo, "c1")
    stale = NOW - timedelta(seconds=120)
    reasons = validate_authorization(repo, auth, snapshot_as_of=stale,
                                     max_snapshot_age_seconds=60, now=NOW)
    assert any(r.startswith("snapshot_stale") for r in reasons)
    ok = validate_authorization(repo, auth, snapshot_as_of=NOW,
                                max_snapshot_age_seconds=60, now=NOW)
    assert ok == []


# --- 7.4 configuration ------------------------------------------------------------ #

def test_max_snapshot_age_seconds_loaded_with_default():
    from ats.config import RiskConfig, get_config

    assert get_config().app.risk.max_snapshot_age_seconds == 60
    assert RiskConfig().max_snapshot_age_seconds == 60   # yaml key missing → default


# --- 7.5/7.6 stale snapshot routes back to review in the graph -------------------- #

def _pf(fresh: bool = True):
    from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot

    as_of = datetime.now(timezone.utc) - (
        timedelta(seconds=300) if not fresh else timedelta(seconds=0))
    return PortfolioSnapshot(as_of=as_of, net_liquidation=1_000_000.0,
                             cash=900_000.0, gross_exposure=100_000.0,
                             daily_pnl=0.0, positions=[],
                             exposure=ExposureBreakdown())


def test_stale_snapshot_re_reviews_and_reapproves_before_execution(
        broker, approve_all, monkeypatch):
    """First pass: stale snapshot → authorization refused → back to risk gate.
    Second pass: fresh snapshot → new review + new approval → executes."""
    from ats.memory import get_store
    from ats.trader import portfolio as tport

    calls = {"n": 0}

    def snapshot():
        calls["n"] += 1
        return _pf(fresh=calls["n"] > 1)

    monkeypatch.setattr("ats.trader.portfolio.snapshot", snapshot)

    state = ChiefDecisionState(
        cycle_id="chief-g7-stale", as_of=NOW, source="chief", decide=False,
        dry_run=False,
        seed_decisions=[TradeDecision(symbol="NVDA", action="buy",
                                      notional_usd=10_000, rationale="r")])
    run_decision_graph(state, channel=approve_all)

    assert len(broker.placed) == 1                   # executed, on the second pass
    repo = DecisionAuditRepository(get_store())
    reviews = repo.conn.execute(
        "SELECT revision_no, verdict FROM decision_risk_reviews "
        "WHERE cycle_id = 'chief-g7-stale' ORDER BY review_id").fetchall()
    assert [(r["revision_no"], r["verdict"]) for r in reviews] == [(1, "approved"),
                                                                   (1, "approved")]
    cycle = repo.get_cycle("chief-g7-stale")
    assert cycle["status"] == "executed"
    # one revision, TWO review rows — the re-review is a fact of its own
    assert len(repo.list_revisions("chief-g7-stale")) == 1


# --- 7.7 order identity derivation ------------------------------------------------- #

def test_client_order_id_distinguishes_revisions():
    from ats.memory import get_store

    store = get_store()
    r1 = store.client_order_id("chief-a", 1, 0, "NVDA", "buy")
    r2 = store.client_order_id("chief-a", 2, 0, "NVDA", "buy")
    assert r1 != r2                                  # the whole point of 7.7
    assert store.client_order_id("chief-a", 1, 0, "NVDA", "buy") == r1  # stable


def test_order_ref_format_fits_ibkr_field():
    from ats.broker.ibkr import order_ref

    ref = order_ref("chief-20260922-200000", 2, 1, "GOOG")
    assert ref == "ats:chief-20260922-200000:r2:1:GOOG"
    assert len(order_ref("c" * 50, 12, 34, "GOOG")) <= 60


# --- 7.8 retry idempotency ---------------------------------------------------------- #

def test_retry_with_uncertain_outcome_does_not_resubmit(broker):
    from ats.memory import get_store
    from ats.trader import execute as texec

    store = get_store()
    # First attempt recorded as `submitted` — its outcome is uncertain.
    store.save_trades([TradeLogEntry(order_id="9", cycle_id="c1", symbol="NVDA",
                                     action="buy", qty=10, status="submitted",
                                     submitted_at=NOW, revision_no=1, order_seq=0)],
                      cycle_id="c1", source="manual")
    d = TradeDecision(symbol="NVDA", action="buy", qty=10, rationale="r")
    # place_orders trusts the graph-level gate; the unit checks only the
    # uncertain-outcome branch, so any non-empty authorization payload works.
    auth = {"cycle_id": "c1"}

    entries, fills = texec.place_orders([(d, 10.0)], "c1", revision_no=1,
                                        authorization=auth)
    assert broker.placed == []                       # no second logical order
    assert fills == []
    assert entries[0].status == "submitted"          # stays for reconciliation
    assert "reconciliation" in entries[0].error
