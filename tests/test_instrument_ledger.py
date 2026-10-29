"""The ledger keeps options apart from their underlying, and does not record FX.

IBKR names an option by its underlying symbol, so before contract identity an
AXTI short put and AXTI stock reduced into one "short AXTI" episode. Offline:
canned fills and portfolio snapshots, no TWS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ats.journal import episodes as ej
from ats.journal import episodes as instrument
from ats.journal import marks
from ats.memory import get_store
from ats.schemas.portfolio import PortfolioSnapshot, Position

NOW = datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc)
PUT = "AXTI  261120P00050000"
EXPIRED = "QQQ   261016P00635000"


@pytest.fixture
def store():
    return get_store()


def _fill(exec_id, symbol="AXTI", side="BOT", shares=10.0, price=50.0,
          t="2026-10-01T14:00:00+00:00", **contract):
    return {"exec_id": exec_id, "symbol": symbol, "side": side, "shares": shares,
            "price": price, "time": t, "commission": 1.0, "order_id": "",
            "perm_id": "", "order_ref": "", **contract}


def _put(exec_id, side="SLD", shares=5.0, price=4.8, t="2026-10-02T14:00:00+00:00"):
    return _fill(exec_id, side=side, shares=shares, price=price, t=t, sec_type="OPT",
                 instrument_key=PUT, multiplier=100.0, right="P", strike=50.0,
                 expiry="20261120")


def _snapshot(*positions):
    return PortfolioSnapshot(as_of=NOW, net_liquidation=100_000.0, positions=list(positions))


def _stock_pos(symbol="AXTI", qty=10.0, cost=50.0):
    return Position(symbol=symbol, qty=qty, avg_cost=cost, market_price=55.0,
                    market_value=qty * 55.0)


def _put_pos(qty=-5.0, avg_cost_per_contract=480.31, market=2.17):
    return Position(symbol="AXTI", sec_type="OPT", qty=qty, avg_cost=avg_cost_per_contract,
                    market_price=market, market_value=qty * market * 100, right="P",
                    strike=50.0, expiry="20261120", multiplier=100.0, underlying="AXTI")


def test_the_occ_key_matches_the_statement_and_the_position():
    assert instrument.occ_key("QQQ", "20261016", "P", 635) == EXPIRED
    assert instrument.position_key(_put_pos()) == PUT
    assert instrument.position_key(_stock_pos()) == "AXTI"


def test_tws_contract_fields_name_the_option_and_leave_stocks_as_they_were():
    opt = SimpleNamespace(symbol="AXTI", secType="OPT", conId=1, localSymbol="x",
                          currency="USD", lastTradeDateOrContractMonth="20261120",
                          right="P", strike=50.0, multiplier="100")
    stk = SimpleNamespace(symbol="AXTI", secType="STK", conId=2, localSymbol="AXTI",
                          currency="USD")
    assert instrument.contract_fields(opt)["instrument_key"] == PUT
    assert instrument.contract_fields(opt)["multiplier"] == 100.0
    assert instrument.contract_fields(stk)["instrument_key"] == "AXTI"


def test_fx_conversions_are_never_recorded(store):
    n = store.upsert_fills([_fill("fx1", symbol="EUR", sec_type="CASH"),
                            _fill("s1")])
    assert n == 1
    assert [r[0] for r in store.conn.execute("SELECT exec_id FROM fills")] == ["s1"]


def test_stock_and_put_on_the_same_underlying_are_separate_episodes(store):
    store.upsert_fills([_fill("s1"), _put("p1")])
    ej.rebuild_all(store=store, portfolio=_snapshot(_stock_pos(), _put_pos()))
    eps = {e.episode_id: e for e in store.list_episodes(symbol="AXTI")}
    assert set(eps) == {"AXTI:s1", f"{PUT}:p1"}           # stock id unchanged
    stock, put = eps["AXTI:s1"], eps[f"{PUT}:p1"]
    assert (stock.direction, stock.sec_type) == ("long", "STK")
    assert (put.direction, put.sec_type, put.multiplier) == ("short", "OPT", 100.0)
    assert put.instrument_key == PUT and stock.instrument_key == ""


def test_a_held_option_with_no_fills_seeds_at_its_per_share_cost(store):
    ej.rebuild_all(store=store, portfolio=_snapshot(_put_pos()))
    (seed,) = store.list_episodes(symbol="AXTI")
    assert seed.episode_id == f"{PUT}:pre_tracking"
    assert seed.avg_entry == pytest.approx(4.8031)          # 480.31 per contract / 100
    # short 5 contracts: (2.17 - 4.8031) x -5 x 100
    assert seed.unrealized_pnl == pytest.approx(1316.55, abs=0.01)


def test_an_expired_option_with_no_closing_fill_is_closed_at_expiry(store):
    store.upsert_fills([_fill("q1", symbol="QQQ", side="BOT", shares=1, price=7.42,
                              t="2026-09-01T15:19:38+00:00", sec_type="OPT",
                              instrument_key=EXPIRED, multiplier=100.0, right="P",
                              strike=635.0, expiry="20261016")])
    s = ej.rebuild_all(store=store, portfolio=_snapshot())
    (ep,) = store.list_episodes(symbol="QQQ")
    assert (ep.status, ep.exit_reason, ep.avg_exit) == ("closed", "expired", 0.0)
    assert ep.origin == "manual" and ep.realized_pnl is None and s["expired"] == 1
    assert ep.closed_at.date().isoformat() == "2026-10-16"


def test_no_expiry_is_inferred_while_held_or_without_a_portfolio(store):
    store.upsert_fills([_put("p1")])
    ej.rebuild_all(store=store, portfolio=None)
    assert store.list_episodes(symbol="AXTI")[0].status == "open"
    ej.rebuild_all(store=store, portfolio=_snapshot(_put_pos()))
    assert store.list_episodes(symbol="AXTI")[0].status == "open"


def test_a_classified_legacy_fill_moves_to_its_contract(store):
    store.upsert_fills([_fill("old", symbol="AAPL", shares=1, price=37.5)])
    store.classify_fill("old", sec_type="OPT", instrument_key="AAPL  270319C00300000",
                        multiplier=100.0, basis="test", decided_by="boss")
    ej.rebuild_all(store=store)
    (ep,) = store.list_episodes(symbol="AAPL")
    assert ep.instrument_key == "AAPL  270319C00300000" and ep.multiplier == 100.0
    # The fill row's own facts are untouched.
    assert store.conn.execute(
        "SELECT symbol, sec_type FROM fills WHERE exec_id = 'old'").fetchone()[:] == ("AAPL", None)


def test_a_legacy_fx_fill_classified_as_cash_leaves_the_episodes(store):
    store.upsert_fills([_fill("eur", symbol="EUR", side="SLD", shares=2697, price=1.15)])
    ej.rebuild_all(store=store)
    assert store.list_episodes(symbol="EUR")
    store.classify_fill("eur", sec_type="CASH", instrument_key="EUR", multiplier=1.0,
                        basis="test", decided_by="boss")
    ej.rebuild_all(store=store, portfolio=_snapshot())
    assert store.list_episodes(symbol="EUR") == []


def test_classifying_an_unknown_exec_id_is_refused(store):
    with pytest.raises(KeyError):
        store.classify_fill("nope", sec_type="OPT", instrument_key="X OPT?",
                            multiplier=100.0, basis="", decided_by="boss")


def test_an_option_episode_gets_no_mae_from_the_underlyings_bars(store, monkeypatch):
    store.upsert_fills([_put("p1")])
    ej.rebuild_all(store=store)
    monkeypatch.setattr(marks.prices, "window",
                        lambda *a, **k: pytest.fail("underlying bars used for an option"))
    (ep,) = store.list_episodes(symbol="AXTI")
    assert marks.mark_episode(store, ep).mae_pct is None


def test_marks_price_open_positions_by_contract():
    prices = marks.prices_by_instrument(_snapshot(_stock_pos(), _put_pos()))
    assert prices == {"AXTI": 55.0, PUT: 2.17}
