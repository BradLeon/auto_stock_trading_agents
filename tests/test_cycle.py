"""Phase 2: end-to-end dry-run of the graph skeleton (stub nodes)."""

from ats.runtime.cli import run_cycle


def test_dry_run_approve_executes_orders(approve_all):
    result = run_cycle(dry_run=True, offline=True, use_llm=False, channel=approve_all)

    # HITL actually fired exactly once.
    assert len(approve_all.requests) == 1

    # Parallel fan-out aggregated one fundamental report per watchlist name.
    assert len(result["fundamental_reports"]) == 3
    assert len(result["technical_reports"]) == 3
    assert result["macro_report"] is not None
    assert result["risk_guardrails"] is not None

    # Manager proposed a buy per bullish name; all approved -> all filled.
    orders = result["order_results"]
    assert len(orders) == 3
    assert {o.symbol for o in orders} == {"NVDA", "GOOGL", "AAPL"}
    assert all(o.status == "filled" for o in orders)


def test_reject_blocks_execution(reject_all):
    result = run_cycle(dry_run=True, offline=True, use_llm=False, channel=reject_all)
    assert result["approval"].status == "rejected"
    assert result["order_results"] == []
