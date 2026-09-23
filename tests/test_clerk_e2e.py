"""Phase C end-to-end acceptance (§15.4, task 9.2).

paper 模式一次完整链路：下单（链完整）→ 成交 → 对账归属 → 补偿（迟到/
部分）→ Clerk 编排 → 绩效/归因读模型 → Internal State（带完整性标记）→
下一轮 Chief 经 Internal State 读到交易历史。全程 FakeBroker/桩，无真实下单。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import clerk as clerk_mod
from ats.execution import rebuild, state_api
from ats.memory import get_store
from ats.schemas.memory import TradeLogEntry
from ats.trader import reconcile as rec

NOW = datetime.now(timezone.utc)
CYCLE = "chief-e2e-20260923"
CHAIN = {"revision_no": 1, "decision_hash": "e" * 32,
         "approval_id": f"{CYCLE}:r1:approval"}


@pytest.fixture
def store():
    return get_store()


def _order(*, status="submitted", qty=10.0):
    return TradeLogEntry(order_id="7", cycle_id=CYCLE, symbol="NVDA", action="buy",
                         qty=qty, status=status, submitted_at=NOW,
                         order_ref=f"ats:{CYCLE}:r1:0:NVDA:buy", **CHAIN)


def _fill(*, exec_id, shares, price=100.0, pnl=None, minutes_later=1, order_id="7"):
    return {"exec_id": exec_id, "symbol": "NVDA", "side": "BOT", "shares": shares,
            "price": price, "time": (NOW + timedelta(minutes=minutes_later)).isoformat(),
            "realized_pnl": pnl, "commission": 1.0, "order_id": order_id,
            "perm_id": "", "order_ref": f"ats:{CYCLE}:r1:0:NVDA:buy"}


class _ReconcileBroker:
    """Serves a canned execution list, like reqExecutions would."""

    def __init__(self, fills):
        self._fills = fills

    def get_fills(self, lookback_days=30):
        return list(self._fills)

    def completed_orders(self, **k):
        return []


class _StubBroker:
    """Hermetic stand-in for IBKRBroker: empty execution feed + flat portfolio."""

    def __init__(self, *a, **k):
        pass

    def get_fills(self, lookback_days=30):
        return []

    def completed_orders(self, **k):
        return []

    def get_portfolio(self):
        from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot

        return PortfolioSnapshot(as_of=datetime.now(timezone.utc), net_liquidation=1_000_000,
                                 cash=990_000, gross_exposure=10_000, daily_pnl=0.0,
                                 positions=[], exposure=ExposureBreakdown())


# --------------------------------------------------------------------------- #

def test_full_ledger_chain_paper_mode(store, monkeypatch):
    # 1) paper 下单：链完整的系统订单进入账本（授权网关的输出即此形态）。
    store.save_trades([_order()], cycle_id=CYCLE, source="chief")
    rid = store.conn.execute("SELECT rowid FROM trades").fetchone()["rowid"]

    # 2) 成交（部分）：10 股只成交 6 股 → partial 留痕。
    b1 = _ReconcileBroker([_fill(exec_id="x1", shares=6.0, pnl=None)])
    s1 = rec.reconcile(b1, store=store)
    assert s1["linked"] == 1
    row = store.conn.execute("SELECT * FROM trades WHERE rowid=?", (rid,)).fetchone()
    assert row["status"] == "partial"
    assert row["filled_qty"] == pytest.approx(6.0)
    assert row["avg_fill_price"] == pytest.approx(100.0)
    fill1 = store.conn.execute(
        "SELECT * FROM fills WHERE exec_id='x1'").fetchone()
    assert fill1["origin"] == "system"
    assert fill1["cycle_id"] == CYCLE and fill1["revision_no"] == 1

    # 3) 补偿：次日回填其余 4 股（迟到成交）→ 满量收口，终态依据 broker。
    b2 = _ReconcileBroker([_fill(exec_id="x1", shares=6.0),
                           _fill(exec_id="x2", shares=4.0, minutes_later=26 * 60,
                                 pnl=120.0)])
    s2 = rec.reconcile(b2, store=store)
    assert s2["fills_new"] >= 1
    row = store.conn.execute("SELECT * FROM trades WHERE rowid=?", (rid,)).fetchone()
    assert row["status"] == "filled"
    assert row["filled_qty"] == pytest.approx(10.0)
    assert row["realized_pnl"] == pytest.approx(120.0)
    # 成交量加权均价：(6*100 + 4*100) / 10
    assert row["avg_fill_price"] == pytest.approx(100.0)
    late = store.conn.execute(
        "SELECT late_backfill FROM fills WHERE exec_id='x2'").fetchone()
    assert late["late_backfill"] == 1

    # 4) Clerk 编排：封闭 stub broker（无 TWS 依赖）完成一次幂等运行并留痕。
    perf_mod = __import__("ats.trader.performance", fromlist=["IBKRBroker"])
    monkeypatch.setattr(perf_mod, "IBKRBroker", _StubBroker)
    out = clerk_mod.clerk_run(store=store, broker=_StubBroker(),
                              window_start=NOW.date().isoformat(),
                              window_end=NOW.date().isoformat())
    assert out["status"] == "completed"
    run = store.conn.execute(
        "SELECT * FROM clerk_runs WHERE run_id=?", (out["run_id"],)).fetchone()
    assert run["status"] == "completed"
    # 幂等：同窗口重放复用，不产生第二行。
    out2 = clerk_mod.clerk_run(store=store, broker=_StubBroker(),
                               window_start=NOW.date().isoformat(),
                               window_end=NOW.date().isoformat())
    assert out2["status"] == "completed"
    n_runs = store.conn.execute("SELECT COUNT(*) n FROM clerk_runs").fetchone()["n"]
    assert n_runs == 1

    # 5) 绩效/归因读模型：从不可变事实重建，方法版本与指纹留痕。
    perf = rebuild.rebuild_performance(store, period=NOW.strftime("%Y-%m"))
    assert perf["status"] in ("rebuilt", "skipped") and perf["source_facts_hash"]
    attr = rebuild.rebuild_attribution(store, period=NOW.strftime("%Y-%m"))
    assert attr["status"] in ("rebuilt", "skipped")
    models = store.conn.execute(
        "SELECT kind, method_version FROM ledger_read_models").fetchall()
    assert {m["kind"] for m in models} >= {"performance", "attribution"}

    # 6) Internal State：下一轮消费方读到带 as-of 与完整性标记的状态。
    state = state_api.get_internal_state(store)
    assert state.as_of is not None
    assert state.trades and state.fills
    assert state.completeness.status == "complete"          # 无未清偿异常

    # 7) Chief 的 track-record 段经 Internal State API 读到成交。
    from ats.agents.chief import assemble

    block = assemble._track_record_block()
    assert "近期成交" in block and "NVDA" in block
