"""Chief decision graph — the single trading funnel: risk gate -> approval
interrupt -> place -> persist (hermetic; FakeBroker/FakeChannel, no TWS/LLM)."""

from datetime import datetime, timezone

from ats.graph.chief_state import ChiefDecisionState
from ats.memory import get_store
from ats.runtime.cli import resume_cycle, run_decision_graph
from ats.schemas.decision import BossApproval, TradeDecision
from ats.schemas.portfolio import PortfolioSnapshot
from ats.schemas.risk import RiskReview

NOW = datetime.now(timezone.utc)


def _state(**kw):
    base = dict(cycle_id="trader-test", as_of=NOW, source="manual", decide=False, dry_run=False,
                seed_decisions=[TradeDecision(symbol="NVDA", action="buy", qty=5, rationale="r")])
    base.update(kw)
    return ChiefDecisionState(**base)


def test_approved_places_and_persists(broker, approve_all):
    result = run_decision_graph(_state(), channel=approve_all)
    assert len(broker.placed) == 1
    assert [o.status for o in result["order_results"]] == ["filled"]
    assert len(approve_all.requests) == 1
    assert "source=manual" in approve_all.requests[0].context_summary
    row = get_store().recent_trades("NVDA")[0]
    assert row["source"] == "manual" and row["status"] == "filled"
    assert get_store().recent_fills("NVDA")


def test_rejected_places_nothing_but_logs(broker, reject_all):
    result = run_decision_graph(_state(), channel=reject_all)
    assert broker.placed == []
    assert all(o.status == "cancelled" for o in result["order_results"])
    row = get_store().recent_trades("NVDA")[0]
    assert "rejected" in (row["context"] or "")


def test_dry_run_approved_never_touches_broker(broker, approve_all):
    result = run_decision_graph(_state(dry_run=True), channel=approve_all)
    assert broker.placed == []
    assert all(o.status == "cancelled" for o in result["order_results"])
    assert get_store().recent_trades("NVDA")           # attempt still persisted


def test_auto_approve_runs_without_interrupt(broker):
    class NeverAsk:
        def request_approval(self, req):               # pragma: no cover - must not fire
            raise AssertionError("auto_approve must not consult the channel")

    result = run_decision_graph(_state(auto_approve=True), channel=NeverAsk())
    assert result["approval"].reviewer == "auto" and result["approval"].status == "approved"
    assert len(broker.placed) == 1


def test_hold_only_ends_before_review(broker, approve_all):
    result = run_decision_graph(
        _state(seed_decisions=[TradeDecision(symbol="NVDA", action="hold")]), channel=approve_all)
    assert approve_all.requests == []
    assert result.get("order_results", []) == []
    assert get_store().recent_trades("NVDA") == []


def test_no_execute_stops_after_persist_decision(broker, approve_all):
    state = _state(cycle_id="chief-test", source="chief", execute=False, use_llm=False)
    run_decision_graph(state, channel=approve_all)
    assert approve_all.requests == []                  # never reached boss_review
    run = get_store().last_chief_run()
    assert run and run["cycle_id"] == "chief-test"     # decision persisted regardless
    assert get_store().recent_trades("NVDA") == []


def test_derisk_blocks_buys_before_review(broker, approve_all, monkeypatch):
    pf = PortfolioSnapshot(as_of=NOW, net_liquidation=100000, cash=100000)
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: pf)
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.assess",
                        lambda p, **k: RiskReview(as_of=NOW, risk_state="derisk"))
    result = run_decision_graph(
        _state(seed_decisions=[TradeDecision(symbol="NVDA", action="buy", notional_usd=5000)]),
        channel=approve_all)
    assert approve_all.requests == []                  # risk gate emptied the slate
    assert any("de-risk" in n for n in result["risk_notes"])


def test_trader_thread_resumes_on_chief_graph(broker, async_channel):
    """Async channel: checkpoint at the interrupt, resume_cycle drives to completion."""
    paused = run_decision_graph(_state(cycle_id="trader-20260101000000"), channel=async_channel)
    assert "__interrupt__" in paused
    assert async_channel.thread_id == "trader-20260101000000"
    assert len(async_channel.request.decisions) == 1

    result = resume_cycle(async_channel.thread_id, BossApproval(status="approved"),
                          channel=async_channel)
    assert [o.status for o in result["order_results"]] == ["filled"]
    assert any(n.kind == "fill_report" for n in async_channel.notifications)
