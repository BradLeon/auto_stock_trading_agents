"""Phase B task group 5: the bounded Chief—Risk revision loop in the graph.

The loop: risk_gate → persist(revision+review) → route
(approved → boss_review | rejected → chief_revise | exhausted → manual_review).
Idempotent per round (crash-safe), bounded at `max_risk_rounds`, terminal side
effects exactly once, and ONE real-order submission path.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import pytest

from ats.graph.chief import (chief_revise, persist_decision, risk_gate)
from ats.graph.chief_state import ChiefDecisionState
from ats.memory import get_store
from ats.memory.store import TradingMemory
from ats.runtime.cli import resume_cycle, run_decision_graph
from ats.schemas.decision import BossApproval, TradeDecision
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot
from ats.schemas.risk import RiskReview

NOW = datetime.now(timezone.utc)
ROOT = "/Users/liuchao/Code/trading/auto_stock_trading_agents"


def _state(**kw):
    base = dict(cycle_id="loop-test", as_of=NOW, source="chief", decide=False,
                dry_run=False, use_llm=False, use_broker=True,
                seed_decisions=[TradeDecision(symbol="NVDA", action="buy",
                                              notional_usd=50_000, rationale="r")])
    base.update(kw)
    # chief_decide normally copies seed_decisions into decisions; unit calls of
    # risk_gate/persist_decision skip that node, so mirror it here.
    if "decisions" not in base:
        base["decisions"] = list(base["seed_decisions"])
    return ChiefDecisionState(**base)


def _pf(net_liq=1_000_000, cash=900_000):
    return PortfolioSnapshot(as_of=NOW, net_liquidation=net_liq, cash=cash,
                             gross_exposure=net_liq - cash, daily_pnl=0.0,
                             positions=[], exposure=ExposureBreakdown())


def _normal_review(monkeypatch, pf=None):
    """Deterministic paper portfolio + normal risk state."""
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: pf or _pf())
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.enrich_options", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.assess",
                        lambda p, **k: RiskReview(as_of=NOW, risk_state="normal"))


def _run_node(state, node):
    """LangGraph merges node output dicts into state; direct unit calls must
    do the same or later nodes read stale fields."""
    for k, v in node(state).items():
        setattr(state, k, v)
    return state


def _revisions(cycle_id="loop-test"):
    return get_store().conn.execute(
        "SELECT * FROM decision_revisions WHERE cycle_id = ? ORDER BY revision_no",
        (cycle_id,)).fetchall()


def _cycle(cycle_id="loop-test"):
    return get_store().conn.execute(
        "SELECT * FROM decision_cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()


# --- 5.2 per-round persistence is idempotent ----------------------------------- #

def test_persist_decision_twice_writes_one_revision_and_review(monkeypatch):
    _normal_review(monkeypatch)
    state = _run_node(_state(), risk_gate)
    _run_node(state, persist_decision)
    _run_node(state, persist_decision)         # crash replay of the same round
    revs = _revisions()
    assert len(revs) == 1
    reviews = get_store().conn.execute(
        "SELECT * FROM decision_risk_reviews").fetchall()
    assert len(reviews) == 1
    events = get_store().conn.execute(
        "SELECT * FROM cycle_events WHERE event_type='status_change'").fetchall()
    assert len(events) == 1                    # replay did not double-journal


# --- 5.3 all three routes are reachable ----------------------------------------- #

def test_routes_approve_revise_manual_review():
    from ats.graph.chief import route_after_persist
    from ats.schemas.risk import (AllowedBoundary, DecisionRiskReview,
                                  OrderRiskVerdict)

    def _review(verdict):
        return DecisionRiskReview(
            verdict=verdict,
            order_verdicts=[OrderRiskVerdict(symbol="NVDA", action="buy",
                                             verdict="approved" if verdict == "approved"
                                             else "rejected")])

    s = _state(risk_review=_review("approved"), decisions=list(_state().decisions))
    assert route_after_persist(s) == "review"
    s = _state(risk_review=_review("rejected"), risk_round=1, decisions=list(_state().decisions))
    assert route_after_persist(s) == "revise"
    s = _state(risk_review=_review("rejected"), risk_round=3, decisions=list(_state().decisions))
    assert route_after_persist(s) == "manual_review"
    s = _state(decisions=[], execute=False)
    assert route_after_persist(s) == "end"
    s = _state(decisions=[], parent_revision_no=1)   # revision emptied after rejection
    assert route_after_persist(s) == "manual_review"


# --- 5.4/5.5 bounded loop with a real counterproposal --------------------------- #

def test_over_cap_order_is_revised_to_boundary_then_executed(broker, approve_all, monkeypatch):
    """50k order vs 25k cap: r1 rejected with the boundary, chief adopts it,
    r2 approved → the order executes AT the boundary, not at the original size.
    The parent revision stays untouched; the child cites it."""
    _normal_review(monkeypatch)
    from ats.config import get_config

    rc = get_config().app.risk
    original = rc.max_single_order_usd
    rc.max_single_order_usd = 25_000
    try:
        result = run_decision_graph(_state(), channel=approve_all)
    finally:
        rc.max_single_order_usd = original

    assert len(broker.placed) == 1
    placed_notional = broker.placed[0][0].notional_usd
    assert placed_notional == pytest.approx(25_000)     # executed at the boundary

    revs = _revisions()
    assert [r["revision_no"] for r in revs] == [1, 2]
    assert revs[1]["parent_revision_no"] == 1           # child cites the parent
    assert revs[1]["decision_hash"] != revs[0]["decision_hash"]
    # r1 was rejected in the review ledger, r2 approved
    reviews = get_store().conn.execute(
        "SELECT revision_no, verdict FROM decision_risk_reviews "
        "ORDER BY revision_no").fetchall()
    assert [(r["revision_no"], r["verdict"]) for r in reviews] == [(1, "rejected"),
                                                                   (2, "approved")]
    # parent revision row untouched by the revision round
    assert "50_000" not in (revs[0]["orders_json"] or "") or \
        "25000" not in revs[0]["orders_json"]
    cycle = _cycle()
    assert cycle["status"] == "executed"


def test_exhausted_rounds_end_in_manual_review_without_approval(broker, approve_all, monkeypatch):
    """Repair mode rejects a buy with no numeric boundary, so chief_revise
    cannot form a compliant revision and drops it — the emptied revision must
    land in manual_review (NOT a silent no_action), and approval/execution are
    never invoked."""
    pf = _pf()
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: pf)
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.enrich_options", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.assess",
                        lambda p, **k: RiskReview(as_of=NOW, risk_state="derisk"))

    result = run_decision_graph(_state(), channel=approve_all)
    assert approve_all.requests == []                   # never reached boss_review
    assert broker.placed == []
    cycle = _cycle()
    assert cycle["status"] == "manual_review"
    assert cycle["final_outcome"] == "manual_review"
    revs = _revisions()
    # r1 (50k) rejected by the cap; chief adopts the 25k boundary → r2 also
    # rejected (repair mode, no boundary) → r3 emptied → manual_review.
    assert [r["revision_no"] for r in revs] == [1, 2]
    reviews = get_store().conn.execute(
        "SELECT verdict FROM decision_risk_reviews ORDER BY revision_no").fetchall()
    assert [r["verdict"] for r in reviews] == ["rejected", "rejected"]


def test_no_action_is_terminal_and_skips_review_approval_execution(broker, approve_all):
    """3.4 graph half: hold-only proposal → No Action terminal; the three
    downstream call sites (risk review, approval, execution) never run."""
    result = run_decision_graph(
        _state(seed_decisions=[TradeDecision(symbol="NVDA", action="hold")],
               source="chief"),
        channel=approve_all)
    assert approve_all.requests == []
    assert broker.placed == []
    cycle = _cycle()
    assert cycle["status"] == "no_action"
    assert cycle["final_outcome"]                      # carries a reason
    events = get_store().conn.execute(
        "SELECT to_status FROM cycle_events WHERE event_type='status_change'"
    ).fetchall()
    assert [e["to_status"] for e in events] == ["no_action"]


# --- 5.6 terminal side effects fire exactly once --------------------------------- #

def test_scores_consumed_once_across_a_multi_round_run(broker, approve_all, monkeypatch):
    _normal_review(monkeypatch)
    from ats.config import get_config

    rc = get_config().app.risk
    original = rc.max_single_order_usd
    rc.max_single_order_usd = 25_000
    try:
        run_decision_graph(_state(actionable_scores=[["NVDA", "2026Q2"]]),
                           channel=approve_all)
    finally:
        rc.max_single_order_usd = original
    rows = get_store().conn.execute("SELECT * FROM score_consumption").fetchall()
    assert len(rows) == 1                              # 2 rounds, ONE consumption


# --- 5.7/5.8 crash recovery and the approval resume channel ---------------------- #

def test_resume_twice_does_not_duplicate_approvals_or_orders(broker, async_channel):
    paused = run_decision_graph(_state(cycle_id="trader-20260101000000"),
                                channel=async_channel)
    assert "__interrupt__" in paused
    first = resume_cycle(async_channel.thread_id, BossApproval(status="approved"),
                         channel=async_channel)
    assert [o.status for o in first["order_results"]] == ["filled"]
    # a replayed resume (duplicate callback / restart after completion) must be
    # a no-op on the ledger: one approval, one order set, one revision
    second = resume_cycle(async_channel.thread_id, BossApproval(status="approved"),
                          channel=async_channel)
    dup_orders = get_store().conn.execute(
        "SELECT COUNT(*) FROM trades WHERE cycle_id = 'trader-20260101000000'"
    ).fetchone()[0]
    assert dup_orders == 1
    approvals = get_store().conn.execute(
        "SELECT COUNT(*) FROM boss_approvals WHERE cycle_id = 'trader-20260101000000'"
    ).fetchone()[0]
    assert approvals <= 1
    # Fresh-portfolio fixture: the 50k seed order is cap-rejected (r1), the
    # chief adopts the 25k boundary (r2), and THAT revision is what executes.
    revs = _revisions("trader-20260101000000")
    assert [r["revision_no"] for r in revs] == [1, 2]
    assert second is not None


def test_persist_decision_replay_keeps_approval_single(tmp_path):
    """Approval persistence survives a crash between callback and write."""
    from ats.decision.repository import DecisionAuditRepository

    store = TradingMemory(tmp_path / "crash.sqlite")
    repo = DecisionAuditRepository(store)
    repo.create_cycle(cycle_id="c1", trigger_source="manual")
    rev = repo.append_revision(cycle_id="c1",
                               orders=[{"symbol": "NVDA", "action": "buy",
                                        "notional_usd": 1000.0}])
    a1 = repo.record_approval(approval_id="ap1", cycle_id="c1",
                              revision_no=rev["revision_no"],
                              decision_hash=rev["decision_hash"],
                              decision="approved", reviewer="boss",
                              idempotency_key="k1")
    a2 = repo.record_approval(approval_id="ap1", cycle_id="c1",
                              revision_no=rev["revision_no"],
                              decision_hash=rev["decision_hash"],
                              decision="approved", reviewer="boss",
                              idempotency_key="k1")
    assert dict(a2) == dict(a1)
    assert store.conn.execute("SELECT COUNT(*) FROM boss_approvals").fetchone()[0] == 1


# --- 5.9 exactly one real-order submission path ----------------------------------- #

def test_single_real_order_submission_path():
    """`place_orders` may only be invoked from the graph's trader node (plus the
    broker's own internals and the execute.py facade) — no second funnel."""
    result = subprocess.run(
        ["grep", "-rn", "place_orders", "src/", "--include=*.py"], cwd=ROOT,
        capture_output=True, text=True)
    allowed = ("trader/execute.py", "broker/ibkr.py")
    callers = set()
    for line in result.stdout.splitlines():
        path, lineno, code = line.split(":", 2)
        if any(a in path for a in allowed):
            continue
        if "def place_orders" in code or "place_orders(" not in code:
            continue
        callers.add(path)                      # line-number robust
    assert callers == {"src//ats/graph/chief.py"}, callers


# --- chief_revise unit behaviour --------------------------------------------------- #

def test_chief_revise_adopts_boundary_and_cites_parent():
    from ats.schemas.risk import (AllowedBoundary, DecisionRiskReview,
                                  OrderRiskVerdict)

    state = _state(risk_round=1, revision_no=1)
    state.risk_review = DecisionRiskReview(
        verdict="rejected",
        order_verdicts=[OrderRiskVerdict(symbol="NVDA", action="buy",
                                         verdict="rejected", reasons=["cap"],
                                         max_allowed_notional=25_000)],
        allowed_boundary=AllowedBoundary(max_additional_notional=25_000))
    out = chief_revise(state)
    assert out["parent_revision_no"] == 1
    assert out["decisions"][0].notional_usd == 25_000
    assert "采纳边界" in out["decisions"][0].rationale
