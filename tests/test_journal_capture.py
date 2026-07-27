"""Capture the three things the trade record used to throw away.

  1. risk_notes      — why sizing was clipped or an order blocked (printed, then lost)
  2. approval divergence — where the Boss dropped / overrode / added to the proposal
  3. cycles.approval_status — written as NULL before the interrupt, never filled in

(2) matters most. "Did I follow my plan?" — the staple of a human trading journal —
is trivially yes for an agent; it cannot deviate. The meaningful inverse is where the
HUMAN overrode the agent, and that was the one thing not recorded.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ats.memory import get_store
from ats.schemas.decision import BossApproval, TradeDecision
from ats.trader import execute as texec

NOW = datetime.now(timezone.utc)


def _d(symbol="GOOG", action="trim", **kw):
    return TradeDecision(symbol=symbol, action=action, qty=13.0,
                         rationale="scorecard below threshold", **kw)


# --------------------------------------------------------------------------- #
# approval divergence
# --------------------------------------------------------------------------- #
def test_plain_approval_shows_no_divergence():
    d = texec.approval_divergence(BossApproval(status="approved", reviewer="boss"),
                                  [_d("GOOG"), _d("ASML")])
    assert d["diverged"] is False
    assert d["dropped_symbols"] == [] and d["added_symbols"] == []


def test_records_a_symbol_the_boss_dropped():
    approval = BossApproval(status="approved", reviewer="boss",
                            rejected_symbols=["ASML"], comment="ASML 先不动")
    d = texec.approval_divergence(approval, [_d("GOOG"), _d("ASML")])
    assert d["diverged"] is True
    assert d["dropped_symbols"] == ["ASML"]
    assert d["comment"] == "ASML 先不动"


def test_records_a_symbol_the_boss_added():
    approval = BossApproval(status="approved",
                            direct_instructions=[_d("NVDA", "buy")])
    d = texec.approval_divergence(approval, [_d("GOOG")])
    assert d["added_symbols"] == ["NVDA"]
    assert d["direct_instructions"][0]["symbol"] == "NVDA"


def test_records_an_override():
    approval = BossApproval(status="modified", overrides=[_d("GOOG", "sell")])
    d = texec.approval_divergence(approval, [_d("GOOG", "trim")])
    assert d["diverged"] is True
    assert d["overrides"][0]["action"] == "sell"


def test_rejection_is_divergence_even_with_no_symbol_changes():
    d = texec.approval_divergence(BossApproval(status="rejected"), [_d("GOOG")])
    assert d["diverged"] is True
    assert d["effective_symbols"] == []


def test_no_approval_yields_empty():
    assert texec.approval_divergence(None, [_d()]) == {}


# --------------------------------------------------------------------------- #
# per-row context
# --------------------------------------------------------------------------- #
def test_context_carries_only_this_row_decision():
    """Every row used to carry the whole cycle's decisions — 900-1200 bytes × N."""
    all_d = [_d("GOOG"), _d("ASML"), _d("KLAC")]
    ctx = json.loads(texec.trade_context_json(
        "chief", BossApproval(status="approved"), all_d, decision=all_d[0]))
    assert [x["symbol"] for x in ctx["decisions"]] == ["GOOG"]
    # ...but the divergence view still sees the whole cycle, which is what it is about.
    assert ctx["approval"]["proposed_symbols"] == ["GOOG", "ASML", "KLAC"]


def test_context_carries_risk_notes():
    notes = ["GOOG: 仓位由 $5,000 削减至 $3,000（L4 层集中度 19% > 15%）",
             "GOOG: 隔夜单改限价 318.5（参考 317.0 +0.50%）"]
    ctx = json.loads(texec.trade_context_json(
        "pead-chief", BossApproval(status="approved"), [_d()],
        decision=_d(), risk_notes=notes))
    assert ctx["risk_notes"] == notes


def test_context_without_narrowing_keeps_all_decisions():
    """Back-compat: callers that don't pass `decision` behave as before."""
    ctx = json.loads(texec.trade_context_json("manual", None, [_d("GOOG"), _d("ASML")]))
    assert len(ctx["decisions"]) == 2


# --------------------------------------------------------------------------- #
# cycles.approval_status
# --------------------------------------------------------------------------- #
def test_cycle_approval_status_is_backfilled():
    store = get_store()
    store.save_chief_run(cycle_id="c1", as_of=NOW, summary="s", decisions=[])
    assert store.conn.execute(
        "SELECT approval_status FROM cycles WHERE cycle_id='c1'").fetchone()[0] is None

    store.set_cycle_approval("c1", "approved")
    assert store.conn.execute(
        "SELECT approval_status FROM cycles WHERE cycle_id='c1'").fetchone()[0] == "approved"


# --------------------------------------------------------------------------- #
# end-to-end through the persist node
# --------------------------------------------------------------------------- #
def test_persist_writes_divergence_and_notes_per_row():
    from ats.graph.chief import persist
    from ats.graph.chief_state import ChiefDecisionState
    from ats.schemas.memory import TradeLogEntry

    store = get_store()
    store.save_chief_run(cycle_id="c9", as_of=NOW, summary="s", decisions=[])
    decisions = [_d("GOOG"), _d("ASML")]
    entries = [TradeLogEntry(order_id="1", cycle_id="c9", symbol=s, action="trim",
                             qty=1.0, status="filled", submitted_at=NOW)
               for s in ("GOOG", "ASML")]
    state = ChiefDecisionState(
        cycle_id="c9", as_of=NOW, source="chief", dry_run=True, use_llm=False,
        decisions=decisions, order_results=entries,
        risk_notes=["GOOG: 仓位削减 L4 集中度"],
        approval=BossApproval(status="approved", reviewer="boss",
                              rejected_symbols=["ASML"], comment="ASML 先不动"))
    persist(state)

    rows = {r["symbol"]: json.loads(r["context"])
            for r in store.conn.execute("SELECT symbol, context FROM trades")}
    assert rows["GOOG"]["decisions"][0]["symbol"] == "GOOG"      # narrowed per row
    assert rows["ASML"]["decisions"][0]["symbol"] == "ASML"
    assert rows["GOOG"]["approval"]["dropped_symbols"] == ["ASML"]
    assert rows["GOOG"]["approval"]["comment"] == "ASML 先不动"
    assert rows["GOOG"]["risk_notes"] == ["GOOG: 仓位削减 L4 集中度"]
    assert store.conn.execute(
        "SELECT approval_status FROM cycles WHERE cycle_id='c9'").fetchone()[0] == "approved"


def test_persist_without_approval_does_not_crash():
    from ats.graph.chief import persist
    from ats.graph.chief_state import ChiefDecisionState

    persist(ChiefDecisionState(cycle_id="c0", as_of=NOW, source="chief",
                               dry_run=True, use_llm=False))
