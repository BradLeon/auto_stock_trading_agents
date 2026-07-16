"""Context Memory store + performance tracking (no network)."""

from datetime import datetime, timezone

from ats.memory import compute_performance
from ats.memory.store import TradingMemory
from ats.schemas.memory import PerformanceRecord, TradeLogEntry

NOW = datetime.now(timezone.utc)


def test_save_and_read_roundtrip():
    mem = TradingMemory(":memory:")
    mem.save_trades([TradeLogEntry(order_id="o1", cycle_id="cycle-1", symbol="NVDA",
                                   action="buy", qty=50, status="filled", submitted_at=NOW)],
                    cycle_id="cycle-1", source="chief", context="{}")
    mem.save_performance(PerformanceRecord(cycle_id="cycle-1", as_of=NOW,
                                           net_liquidation=100000))

    assert mem.last_performance().net_liquidation == 100000
    trades = mem.recent_trades("NVDA")
    assert trades and trades[0]["status"] == "filled" and trades[0]["source"] == "chief"


def test_performance_carries_cumulative_forward():
    prev = PerformanceRecord(cycle_id="c1", as_of=NOW, net_liquidation=100000,
                             cumulative_pnl=0.0)
    # No portfolio -> net liq carried forward, zero daily PnL.
    p = compute_performance(cycle_id="c2", as_of=NOW, portfolio=None, previous=prev,
                            order_results=[], fallback_net_liq=100000)
    assert p.net_liquidation == 100000
    assert p.daily_pnl == 0.0
    assert p.cumulative_pnl == 0.0
