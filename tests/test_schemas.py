"""Phase 1: data contract behavior."""

from ats.schemas.decision import BossApproval, TradeDecision


def _d(sym, action="buy"):
    return TradeDecision(symbol=sym, action=action)


def test_approved_subset_filters_decisions():
    proposed = [_d("NVDA"), _d("GOOGL"), _d("AAPL")]
    appr = BossApproval(status="approved", approved_symbols=["NVDA", "GOOGL"])
    out = {d.symbol for d in appr.effective_decisions(proposed)}
    assert out == {"NVDA", "GOOGL"}


def test_rejected_only_runs_direct_instructions():
    proposed = [_d("NVDA")]
    appr = BossApproval(status="rejected", direct_instructions=[_d("TSLA", "sell")])
    out = appr.effective_decisions(proposed)
    assert [(d.symbol, d.action) for d in out] == [("TSLA", "sell")]


def test_modified_overrides_replace_proposed():
    proposed = [_d("NVDA")]
    appr = BossApproval(status="modified", overrides=[_d("AAPL", "trim")])
    out = appr.effective_decisions(proposed)
    assert [d.symbol for d in out] == ["AAPL"]
