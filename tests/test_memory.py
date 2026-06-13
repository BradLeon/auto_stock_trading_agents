"""Phase 8: Context Memory store + performance tracking (no network)."""

from datetime import datetime, timezone

from ats.memory import compute_performance
from ats.memory.store import TradingMemory
from ats.graph.state import TradingState
from ats.schemas.decision import BossApproval, TradeDecision
from ats.schemas.market import Ticker
from ats.schemas.memory import PerformanceRecord, TradeLogEntry
from ats.schemas.reports import FundamentalReport

NOW = datetime.now(timezone.utc)


def _state():
    return TradingState(
        cycle_id="cycle-1", as_of=NOW, watchlist=[Ticker(symbol="NVDA")],
        fundamental_reports=[FundamentalReport(as_of=NOW, symbol="NVDA", signal="bullish",
                                               conviction=0.6, thesis="strong")],
        decisions=[TradeDecision(symbol="NVDA", action="buy", notional_usd=10000)],
        manager_summary="buy NVDA",
        approval=BossApproval(status="approved"),
        order_results=[TradeLogEntry(order_id="o1", cycle_id="cycle-1", symbol="NVDA",
                                     action="buy", qty=50, status="filled", submitted_at=NOW)],
    )


def test_save_and_read_roundtrip():
    mem = TradingMemory(":memory:")
    perf = PerformanceRecord(cycle_id="cycle-1", as_of=NOW, net_liquidation=100000)
    mem.save_cycle(_state(), perf)

    assert mem.last_performance().net_liquidation == 100000
    reports = mem.recent_reports("NVDA")
    assert reports and reports[0]["signal"] == "bullish"
    trades = mem.recent_trades("NVDA")
    assert trades and trades[0]["status"] == "filled"


def test_performance_carries_cumulative_forward():
    prev = PerformanceRecord(cycle_id="c1", as_of=NOW, net_liquidation=100000,
                             cumulative_pnl=0.0)
    # No portfolio -> net liq carried forward, zero daily PnL.
    p = compute_performance(cycle_id="c2", as_of=NOW, portfolio=None, previous=prev,
                            order_results=[], fallback_net_liq=100000)
    assert p.net_liquidation == 100000
    assert p.daily_pnl == 0.0
    assert p.cumulative_pnl == 0.0
