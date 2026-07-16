"""Trader agent — analytics math, approval-gated execution, store round-trips
(hermetic; a FakeBroker replaces IBKR, no live TWS)."""

from datetime import datetime, timezone

from ats.memory import get_store
from ats.schemas.decision import BossApproval, TradeDecision
from ats.schemas.memory import PerformanceRecord, TradeLogEntry
from ats.schemas.portfolio import PortfolioSnapshot, Position
from ats.trader import analytics, execute as texec

NOW = datetime.now(timezone.utc)


def _perf(nav, cum=0.0, i=0):
    return PerformanceRecord(cycle_id=f"c{i}", as_of=datetime(2026, 1, 1 + i), net_liquidation=nav,
                             cumulative_pnl=cum)


# --------------------------------------------------------------------------- #
# analytics
# --------------------------------------------------------------------------- #
def test_total_return_and_drawdown():
    hist = [_perf(100000, 0, 0), _perf(110000, 10000, 1), _perf(99000, -1000, 2),
            _perf(104500, 4500, 3)]
    assert analytics.total_return_pct(hist) == 4.5
    assert analytics.max_drawdown_pct(hist) == -10.0     # 110k -> 99k


def test_trade_stats():
    fills = [{"realized_pnl": 300}, {"realized_pnl": -100}, {"realized_pnl": 200},
             {"realized_pnl": None}, {"realized_pnl": 0}]
    s = analytics.trade_stats(fills)
    assert s["closed_trades"] == 3 and s["win_rate"] == round(2 / 3, 3)
    assert s["profit_factor"] == 5.0                      # (300+200)/100


def test_benchmark_and_summary():
    hist = [_perf(100000, 0, 0), _perf(105000, 5000, 1)]
    out = analytics.summarize(hist, [{"realized_pnl": 50}], {"SPY": [400.0, 408.0]})
    assert out["total_return_pct"] == 5.0
    assert out["benchmarks"]["SPY"]["return_pct"] == 2.0
    assert out["benchmarks"]["SPY"]["alpha_pct"] == 3.0   # 5 - 2


# --------------------------------------------------------------------------- #
# execution — approval gating + context persistence
# --------------------------------------------------------------------------- #
class FakeBroker:
    placed: list = []

    def __init__(self, *a, **k):
        pass

    def place_orders(self, items, cycle_id, wait=3.0):
        FakeBroker.placed = list(items)
        return [TradeLogEntry(order_id="1", cycle_id=cycle_id, symbol=d.symbol, action=d.action,
                              qty=q, status="filled", submitted_at=NOW, filled_at=NOW,
                              avg_fill_price=100.0, rationale=d.rationale) for d, q in items]

    def get_fills(self):
        return [{"exec_id": "e1", "symbol": "AAPL", "side": "BOT", "shares": 1, "price": 100,
                 "time": NOW.isoformat(), "realized_pnl": None, "commission": 1.0, "order_id": "1"}]


def _patch(monkeypatch, approval_status="approved"):
    FakeBroker.placed = []
    monkeypatch.setattr(texec, "IBKRBroker", FakeBroker)
    monkeypatch.setattr(texec, "_last_price", lambda s: 100.0)
    # execute() now runs the decision graph; keep its risk gate off live TWS.
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: None)

    class Ch:
        def request_approval(self, req):
            return BossApproval(status=approval_status, reviewer="test", reviewed_at=NOW)

    monkeypatch.setattr("ats.channel.get_channel", lambda kind=None: Ch())


def test_execute_rejected_places_nothing_but_logs_context(monkeypatch):
    _patch(monkeypatch, "rejected")
    d = TradeDecision(symbol="AAPL", action="buy", qty=10, rationale="test")
    entries = texec.execute([d], source="manual")
    assert FakeBroker.placed == []                        # no order placed
    assert all(e.status == "cancelled" for e in entries)
    rows = get_store().recent_trades("AAPL")
    assert rows and rows[0]["source"] == "manual" and "rejected" in (rows[0]["context"] or "")


def test_execute_approved_places_and_persists(monkeypatch):
    _patch(monkeypatch, "approved")
    d = TradeDecision(symbol="AAPL", action="buy", qty=5, rationale="buy the dip")
    entries = texec.execute([d], source="manual")
    assert len(FakeBroker.placed) == 1 and entries[0].status == "filled"
    row = get_store().recent_trades("AAPL")[0]
    assert row["status"] == "filled" and "buy the dip" in (row["context"] or "")
    assert get_store().recent_fills("AAPL")                # fills persisted


def test_execute_dry_run_no_order(monkeypatch):
    _patch(monkeypatch, "approved")
    d = TradeDecision(symbol="MSFT", action="buy", qty=3)
    texec.execute([d], source="manual", dry_run=True)
    assert FakeBroker.placed == []


def test_hold_filtered():
    out = texec.execute([TradeDecision(symbol="X", action="hold")], source="manual")
    assert out == []


# --------------------------------------------------------------------------- #
# store round-trips
# --------------------------------------------------------------------------- #
def test_fills_dedup_and_performance_snapshot():
    store = get_store()
    n1 = store.upsert_fills([{"exec_id": "a", "symbol": "NVDA", "realized_pnl": 100},
                             {"exec_id": "b", "symbol": "NVDA", "realized_pnl": -20}])
    n2 = store.upsert_fills([{"exec_id": "b", "symbol": "NVDA", "realized_pnl": -20},
                             {"exec_id": "c", "symbol": "NVDA", "realized_pnl": 50}])
    assert n1 == 2 and n2 == 1                            # 'b' deduped
    assert len(store.recent_fills("NVDA")) == 3

    store.save_performance(_perf(100000, 0, 0))
    store.save_performance(_perf(102000, 2000, 1))
    hist = store.performance_history()
    assert [h.net_liquidation for h in hist] == [100000, 102000]   # chronological


def test_order_status_mapping():
    from ats.broker.ibkr import _map_status

    assert _map_status("Filled") == "filled"
    assert _map_status("PreSubmitted") == "submitted"
    assert _map_status("Cancelled") == "cancelled"
    assert _map_status("ValidationError") == "rejected"   # IBKR reject (bad TIF / closed mkt)
    assert _map_status("Inactive") == "rejected"


def test_trades_migration_columns():
    cols = {r["name"] for r in get_store().conn.execute("PRAGMA table_info(trades)")}
    assert {"limit_price", "filled_at", "error", "realized_pnl", "source", "context"} <= cols
