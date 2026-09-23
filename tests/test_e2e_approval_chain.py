"""Phase B task 9.1 — end-to-end acceptance of the approval chain (§15.2).

Checklist (one test per item):
  首轮通过 / 一次驳回后通过 / 多次驳回后通过 / 全部驳回 / 超过三轮 /
  No Action / 审批拒绝 / 快照过期后重新风控 / 券商提交结果不确定 /
  默认 paper-dry-run（9.3）。

All runs go through the ONE real funnel: the chief decision graph, with the
hermetic broker fixture and a fresh paper portfolio.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.decision.repository import DecisionAuditRepository
from ats.graph.chief_state import ChiefDecisionState
from ats.memory import get_store
from ats.memory.store import TradingMemory
from ats.runtime.cli import run_decision_graph
from ats.schemas.decision import BossApproval, TradeDecision
from ats.schemas.memory import TradeLogEntry
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot
from ats.schemas.risk import RiskReview

NOW = datetime.now(timezone.utc)


def _pf(fresh: bool = True, net_liq: float = 1_000_000.0) -> PortfolioSnapshot:
    as_of = datetime.now(timezone.utc) - (
        timedelta(seconds=300) if not fresh else timedelta(seconds=0))
    return PortfolioSnapshot(as_of=as_of, net_liquidation=net_liq,
                             cash=net_liq * 0.9, gross_exposure=net_liq * 0.1,
                             daily_pnl=0.0, positions=[],
                             exposure=ExposureBreakdown())


def _repo() -> DecisionAuditRepository:
    return DecisionAuditRepository(get_store())


def _state(**kw) -> ChiefDecisionState:
    base = dict(cycle_id="e2e-test", as_of=datetime.now(timezone.utc),
                source="chief", decide=False, dry_run=False, use_llm=False,
                use_broker=True)
    base.update(kw)
    return ChiefDecisionState(**base)


def _buy(notional: float, symbol: str = "NVDA") -> TradeDecision:
    return TradeDecision(symbol=symbol, action="buy", notional_usd=notional,
                         rationale="e2e")


class _Scripted:
    """Sync channel returning scripted verdicts in order."""

    is_async = False

    def __init__(self, verdicts: list[str]):
        self.verdicts = list(verdicts)
        self.requests: list = []

    def push(self, msg) -> None:
        pass

    def request_approval(self, req) -> BossApproval:
        self.requests.append(req)
        status = self.verdicts.pop(0) if self.verdicts else "rejected"
        return BossApproval(status=status, reviewer="e2e",
                            reviewed_at=datetime.now(timezone.utc),
                            channel="e2e")


@pytest.fixture
def reviewed(monkeypatch):
    """Fresh portfolio + deterministic normal risk state (paper account)."""
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: _pf())
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.enrich_options", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.assess",
                        lambda p, **k: RiskReview(as_of=datetime.now(timezone.utc),
                                                  risk_state="normal"))


def _cap(monkeypatch, value: float):
    from ats.config import get_config

    rc = get_config().app.risk
    original = rc.max_single_order_usd
    rc.max_single_order_usd = value
    return original


# --- 首轮通过 -------------------------------------------------------------------- #

def test_first_round_passes_and_executes(broker, approve_all, reviewed, monkeypatch):
    original = _cap(monkeypatch, 25_000.0)
    try:
        result = run_decision_graph(_state(cycle_id="e2e-first", seed_decisions=[_buy(10_000)]),
                                    channel=approve_all)
    finally:
        get_config_restore(original)
    assert [o.status for o in result["order_results"]] == ["filled"]
    repo = _repo()
    chain = repo.read_chain("e2e-first")
    assert [r["revision_no"] for r in chain["revisions"]] == [1]
    assert [rv["verdict"] for rv in chain["reviews"]] == ["approved"]
    assert chain["cycle"]["status"] == "executed"


def get_config_restore(original: float) -> None:
    from ats.config import get_config

    get_config().app.risk.max_single_order_usd = original


# --- 一次驳回后通过 --------------------------------------------------------------- #

def test_one_rejection_then_passes_at_the_boundary(broker, approve_all, reviewed,
                                                   monkeypatch):
    original = _cap(monkeypatch, 25_000.0)
    try:
        result = run_decision_graph(_state(cycle_id="e2e-one-reject", seed_decisions=[_buy(50_000)]),
                                    channel=approve_all)
    finally:
        get_config_restore(original)
    repo = _repo()
    chain = repo.read_chain("e2e-one-reject")
    assert [(rev["revision_no"], rv["verdict"])
            for rev, rv in zip(chain["revisions"], chain["reviews"])] == [
        (1, "rejected"), (2, "approved")]
    assert chain["cycle"]["status"] == "executed"
    # executed at the boundary, not the original size
    assert result["order_results"][0].qty == pytest.approx(250)


# --- 多次驳回后通过（两条订单同轮各被驳回，修订后同轮通过）----------------------- #

def test_multiple_rejections_then_pass(broker, approve_all, reviewed, monkeypatch):
    original = _cap(monkeypatch, 25_000.0)
    try:
        result = run_decision_graph(
            _state(cycle_id="e2e-multi-reject",
                   seed_decisions=[_buy(50_000, "NVDA"), _buy(40_000, "MSFT")]),
            channel=approve_all)
    finally:
        get_config_restore(original)
    repo = _repo()
    reviews = repo.conn.execute(
        "SELECT revision_no, verdict, violations_json FROM decision_risk_reviews "
        "WHERE cycle_id = 'e2e-multi-reject' ORDER BY review_id").fetchall()
    assert [r["verdict"] for r in reviews] == ["rejected", "approved"]
    import json as _json

    r1_blocks = [v for v in _json.loads(reviews[0]["violations_json"])
                 if v["rule_id"] == "max_single_order_usd"]
    assert len(r1_blocks) == 2                       # BOTH orders rejected in r1
    assert [o.status for o in result["order_results"]] == ["filled", "filled"]
    assert chain_status("e2e-multi-reject") == "executed"


def chain_status(cycle_id: str) -> str:
    return _repo().get_cycle(cycle_id)["status"]


# --- 全部驳回（修复态无边界，修订被驳空 → 人工复核）-------------------------------- #

def test_all_rejected_ends_in_manual_review(broker, approve_all, monkeypatch):
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: _pf())
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.enrich_options", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.assess",
                        lambda p, **k: RiskReview(as_of=datetime.now(timezone.utc),
                                                  risk_state="derisk"))
    channel = _Scripted(["approved"])
    result = run_decision_graph(_state(cycle_id="e2e-all-reject", seed_decisions=[_buy(50_000)]),
                                channel=channel)
    assert channel.requests == []                    # never reached approval
    assert all(o.status != "filled" for o in result["order_results"])
    assert chain_status("e2e-all-reject") == "manual_review"


# --- 超过三轮（轮次已耗尽 → 人工复核，不产生第四轮修订）--------------------------- #

def test_exhausted_rounds_go_straight_to_manual_review(broker, approve_all, reviewed,
                                                       monkeypatch):
    original = _cap(monkeypatch, 25_000.0)
    try:
        run_decision_graph(_state(cycle_id="e2e-exhausted", risk_round=3,
                                  seed_decisions=[_buy(50_000)]), channel=approve_all)
    finally:
        get_config_restore(original)
    repo = _repo()
    # r1 rejected → the round budget is already spent: manual_review, no r2.
    assert len(repo.list_revisions("e2e-exhausted")) == 1
    assert chain_status("e2e-exhausted") == "manual_review"


# --- No Action -------------------------------------------------------------------- #

def test_no_action_terminal(broker, approve_all):
    result = run_decision_graph(
        _state(cycle_id="e2e-no-action",
               seed_decisions=[TradeDecision(symbol="NVDA", action="hold", rationale="r")]),
        channel=approve_all)
    assert chain_status("e2e-no-action") == "no_action"
    assert result["order_results"] == []


# --- 审批拒绝 ---------------------------------------------------------------------- #

def test_boss_rejection_is_a_terminal_without_orders(broker, reviewed):
    channel = _Scripted(["rejected"])
    result = run_decision_graph(_state(cycle_id="e2e-boss-reject", seed_decisions=[_buy(10_000)]),
                                channel=channel)
    assert all(o.status != "filled" for o in result["order_results"])
    assert chain_status("e2e-boss-reject") == "approval_rejected"
    repo = _repo()
    chain = repo.read_chain("e2e-boss-reject")
    assert chain["approvals"] and chain["approvals"][0]["decision"] == "rejected"


# --- 快照过期后重新风控 ------------------------------------------------------------- #

def test_stale_snapshot_re_reviews_then_executes(broker, approve_all, reviewed,
                                                 monkeypatch):
    calls = {"n": 0}

    def snapshot():
        calls["n"] += 1
        return _pf(fresh=calls["n"] > 1)

    monkeypatch.setattr("ats.trader.portfolio.snapshot", snapshot)
    result = run_decision_graph(_state(cycle_id="e2e-stale", seed_decisions=[_buy(10_000)]),
                                channel=approve_all)
    assert [o.status for o in result["order_results"]] == ["filled"]
    repo = _repo()
    reviews = repo.conn.execute(
        "SELECT verdict FROM decision_risk_reviews WHERE cycle_id = 'e2e-stale' "
        "ORDER BY review_id").fetchall()
    assert [r["verdict"] for r in reviews] == ["approved", "approved"]  # re-reviewed
    assert len(repo.list_revisions("e2e-stale")) == 1                    # same revision
    assert chain_status("e2e-stale") == "executed"


# --- 券商提交结果不确定 -------------------------------------------------------------- #

def test_uncertain_broker_outcome_is_not_resubmitted(broker, approve_all, reviewed,
                                                     monkeypatch):
    """The broker acks but the outcome is uncertain (submitted, not filled):
    a retry of the same intent must not produce a second logical order."""
    from ats.trader import execute as texec

    def uncertain_place_orders(self, items, cycle_id, wait=3.0, revision_no=0):
        return [TradeLogEntry(order_id="7", cycle_id=cycle_id, symbol=d.symbol,
                              action=d.action, qty=q, status="submitted",
                              submitted_at=datetime.now(timezone.utc),
                              rationale=d.rationale, revision_no=revision_no,
                              order_seq=i)
                for i, (d, q) in enumerate(items)]

    monkeypatch.setattr(broker, "place_orders", uncertain_place_orders)
    result = run_decision_graph(_state(cycle_id="e2e-uncertain", seed_decisions=[_buy(10_000)]),
                                channel=approve_all)
    assert [o.status for o in result["order_results"]] == ["submitted"]

    # retry the same intent (same cycle/revision/seq): locally already submitted
    d = result["order_results"][0]
    decision = TradeDecision(symbol=d.symbol, action=d.action, qty=d.qty, rationale="r")
    entries, _ = texec.place_orders([(decision, d.qty)], "e2e-uncertain",
                                    revision_no=1, authorization={"cycle_id": "e2e-uncertain"})
    assert entries[0].status == "submitted"          # unchanged, pending reconciliation
    assert "reconciliation" in entries[0].error
    rows = get_store().conn.execute(
        "SELECT COUNT(*) FROM trades WHERE cycle_id = 'e2e-uncertain'").fetchone()[0]
    assert rows == 1                                 # ONE logical order


# --- 9.3 默认 paper / dry-run：默认状态不触碰券商 ----------------------------------- #

def test_default_state_is_dry_run_and_never_places(broker, approve_all):
    assert ChiefDecisionState.model_fields["dry_run"].default is True
    # Full chain with the DEFAULT dry_run=True (deliberately not overridden):
    # approval may happen, but no order ever reaches the broker.
    default_state = ChiefDecisionState(
        cycle_id="e2e-dry-default", as_of=datetime.now(timezone.utc),
        source="chief", decide=False, use_llm=False, use_broker=True,
        seed_decisions=[_buy(10_000)])
    result = run_decision_graph(default_state, channel=approve_all)
    assert broker.placed == []
    assert result["order_results"] and \
        all(o.status == "cancelled" for o in result["order_results"])
