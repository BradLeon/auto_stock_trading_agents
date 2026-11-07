"""Fill currency → USD: the rate is kept beside the native amounts, never assumed to be 1 (Clerk report §4)."""

from __future__ import annotations

import pytest

from ats.execution import clerk_report, state_api
from ats.journal import episodes as ej
from ats.memory import get_store
from ats.trader import flex_statement, fx, fx_backfill

from tests.test_flex_statement import _statement, _trade


@pytest.fixture
def store():
    return get_store()


def _fill(exec_id, symbol, side="BOT", shares=2000.0, price=14.25, currency="HKD", time="2026-11-06T01:33:14+00:00",
          **extra):
    return {"exec_id": exec_id, "symbol": symbol, "side": side, "shares": shares, "price": price, "time": time,
            "commission": 23.61, "realized_pnl": 0.0, "order_id": "", "perm_id": "", "order_ref": "",
            "sec_type": "STK", "currency": currency, "multiplier": 1.0, **extra}


def _row(store, exec_id):
    return dict(store.conn.execute("SELECT * FROM fills WHERE exec_id = ?", (exec_id,)).fetchone())


def test_usd_rate_from_account_rates_for_a_usd_and_for_a_non_usd_base():
    rates = {"USD": 1.0, "HKD": 0.1285, "EUR": 1.16}
    assert fx.usd_rate("USD", "USD", rates) == 1.0
    assert fx.usd_rate("HKD", "USD", rates) == pytest.approx(0.1285)
    assert fx.usd_rate("GBP", "USD", rates) is None                      # unknown stays unknown, never 1.0
    # an account whose base is EUR quotes rates as EUR per unit; USD per unit is the ratio
    assert fx.usd_rate("HKD", "EUR", {"EUR": 1.0, "USD": 0.86, "HKD": 0.11}) == pytest.approx(0.11 / 0.86)
    assert fx.usd_rate("HKD", "EUR", {"EUR": 1.0, "HKD": 0.11}) is None


def test_money_shows_the_native_amount_and_the_usd_equivalent_and_never_calls_hkd_dollars():
    hk = {"currency": "HKD", "fx_to_usd": 0.1285, "fx_source": "flex"}
    assert fx.money(28500, hk) == "HK$28,500（≈ $3,662）"
    assert fx.money(-823.484, {"currency": "EUR", "fx_to_usd": 1.16, "fx_source": "flex"}, signed=True) \
        == "-€823（≈ -$955）"
    assert fx.money(23.61, hk, digits=2) == "HK$23.61（≈ $3.03）"
    assert fx.money(28500, {**hk, "fx_source": "ibkr_live"}) == "HK$28,500（≈ $3,662，估算汇率）"
    assert fx.money(28500, {"currency": "HKD"}) == "HK$28,500（汇率缺失）"           # not "$28,500"
    assert fx.money(5758, {"currency": "USD"}) == "$5,758"
    assert fx.money(503, {"currency": ""}) == "币种未记 503"


def test_the_store_keeps_a_rate_it_is_given_and_marks_usd_and_never_guesses_one(store):
    store.upsert_fills([_fill("a", "7709", fx_to_usd=0.1285, fx_source="flex"),
                        _fill("b", "GOOG", currency="USD", price=287.9),
                        _fill("c", "7709", currency="HKD"),
                        _fill("d", "OLD", currency="")])
    assert (_row(store, "a")["fx_to_usd"], _row(store, "a")["fx_source"]) == (0.1285, "flex")
    assert (_row(store, "b")["fx_to_usd"], _row(store, "b")["fx_source"]) == (1.0, "usd")
    assert (_row(store, "c")["fx_to_usd"], _row(store, "c")["fx_source"]) == (None, "missing")
    assert (_row(store, "d")["fx_to_usd"], _row(store, "d")["fx_source"]) == (None, "currency_unknown")
    assert _row(store, "a")["price"] == 14.25 and _row(store, "a")["commission"] == 23.61   # native kept as is


def test_the_flex_statement_rate_is_read_per_trade():
    hk = _trade(currency="HKD", symbol="7709", ibExecID="hk1", tradePrice="14.25", quantity="2000", fxRateToBase="0.1285")
    plain = _trade(ibExecID="us1")
    rows = {f["exec_id"]: f for f in flex_statement.parse_trades(_statement(hk, plain))}
    assert (rows["hk1"]["fx_to_usd"], rows["hk1"]["fx_source"]) == (0.1285, "flex")
    assert "fx_to_usd" not in rows["us1"]                                   # no rate in the row ⇒ no claim


class _FakeIB:
    """Just enough ib_insync for BrokerIBKR.get_fills."""

    def __init__(self, fills, summary):
        self._fills, self._summary = fills, summary

    def reqExecutions(self):  # noqa: N802
        return []

    def sleep(self, _):
        return None

    def fills(self):
        return self._fills

    def accountSummary(self):  # noqa: N802
        return self._summary


def test_get_fills_stamps_the_capture_time_rate_from_the_account_exchange_rates(monkeypatch):
    from types import SimpleNamespace as NS

    from ats.broker import ibkr

    value = lambda tag, currency, v: NS(tag=tag, currency=currency, value=str(v))  # noqa: E731
    summary = [value("NetLiquidation", "USD", 100000), value("ExchangeRate", "HKD", 0.1285),
               value("ExchangeRate", "EUR", 1.16)]

    def fill(sym, ccy):
        return NS(contract=NS(symbol=sym, currency=ccy, secType="STK", conId=1, localSymbol=sym, multiplier="",
                              right="", strike=0, lastTradeDateOrContractMonth=""),
                  execution=NS(execId=f"x-{sym}", side="BOT", shares=1, price=1.0, time=None, orderId=0, permId=0,
                               orderRef=""),
                  commissionReport=NS(realizedPNL=None, commission=1.0))

    broker = ibkr.IBKRBroker.__new__(ibkr.IBKRBroker)
    ib = _FakeIB([fill("7709", "HKD"), fill("GOOG", "USD"), fill("XYZ", "CHF")], summary)
    from contextlib import contextmanager

    @contextmanager
    def session():
        yield ib

    broker.session = session
    monkeypatch.setattr(ibkr, "_bounded", lambda ib_, label, fn: None)
    out = {f["symbol"]: f for f in broker.get_fills()}
    assert (out["7709"]["fx_to_usd"], out["7709"]["fx_source"]) == (0.1285, "ibkr_live")
    assert (out["GOOG"]["fx_to_usd"], out["GOOG"]["fx_source"]) == (1.0, "usd")
    assert "fx_to_usd" not in out["XYZ"]                                    # CHF is not in the account's rates


def test_the_report_row_for_a_hong_kong_buy_is_in_hkd_with_the_usd_equivalent(store):
    store.upsert_fills([_fill("a", "7709", fx_to_usd=0.1285, fx_source="flex"),
                        _fill("b", "GOOG", currency="USD", shares=20.0, price=287.9),
                        _fill("c", "ZZZ", currency="EUR", shares=94.0, price=772.0)])
    rows = [dict(r) | {"at": r["time"], "origin": "manual"} for r in store.conn.execute(
        "SELECT * FROM fills ORDER BY exec_id")]
    text = "\n".join(clerk_report._fill_table(rows))
    assert "14.25 HKD" in text and "HK$28,500（≈ $3,662）" in text
    assert "$28,500" not in text.replace("HK$28,500", "")                   # the old bug: HKD printed as dollars
    assert "| 287.90 |" in text and "$5,758" in text                          # USD rows unchanged
    assert "772.00 EUR" in text and "€72,568（汇率缺失）" in text           # a missing rate is said, not assumed


def _round_trip(store, symbol, currency, rate):
    base = dict(currency=currency, shares=100.0, realized_pnl=None, commission=0.0, **({"fx_to_usd": rate, "fx_source": "flex"} if rate else {}))
    store.upsert_fills([_fill(f"{symbol}1", symbol, price=30.0, time="2026-10-01T02:00:00+00:00", **base),
                        _fill(f"{symbol}2", symbol, side="SLD", price=40.0, time="2026-10-02T02:00:00+00:00",
                              **base)])


def test_a_non_usd_round_trip_counts_in_the_usd_figures_when_its_rate_is_known(store):
    _round_trip(store, "AXTI", "USD", None)                                # +1000 USD
    _round_trip(store, "7709", "HKD", 0.1285)                              # +HK$1,000 → $128.5
    _round_trip(store, "HY9H", "EUR", None)                                # no rate: stays out, totalled in EUR
    ej.rebuild_all(store=store)
    perf = state_api.get_internal_state(store).performance
    assert perf["realized_total"] == pytest.approx(1000.0 + 128.5)
    assert perf["realized_other_currencies"] == {"EUR": 1000.0}


def test_backfill_prefers_the_statement_then_an_estimate_then_says_missing_and_writes_only_empty_columns(store):
    store.upsert_fills([_fill("s1", "7709"), _fill("e1", "7709", time="2026-11-05T01:33:15+00:00"),
                        _fill("m1", "HY9H", currency="EUR"), _fill("u1", "GOOG", currency="USD"),
                        _fill("k1", "7709", fx_to_usd=0.13, fx_source="flex")])
    live = lambda: ("USD", {"USD": 1.0, "HKD": 0.1285})                    # noqa: E731
    statement = [{"instrument_key": "7709", "side": "BOT", "shares": 2000.0, "price": 14.25,
                  "time": "2026-11-06T01:33:14+00:00", "fx_to_usd": 0.1281}]   # Flex has its own exec ids: match on content

    store.upsert_fills([_fill("b1", "BLANK", currency=""), _fill("b2", "BLANK", currency="HKD",
                                                                 time="2026-11-04T01:33:14+00:00", fx_to_usd=0.13,
                                                                 fx_source="flex")])
    preview = fx_backfill.backfill(store, dry_run=True, flex_fills=statement, live_rates=live)
    assert preview["set"] == {"usd": 0, "flex": 1, "estimate_now": 2, "missing": 1}   # b1: currency filled in preview   # USD rows are stamped on insert
    assert _row(store, "s1")["fx_to_usd"] is None                           # a dry run writes nothing

    fx_backfill.backfill(store, flex_fills=statement, live_rates=live)
    assert (_row(store, "s1")["fx_to_usd"], _row(store, "s1")["fx_source"]) == (0.1281, "flex")
    assert (_row(store, "e1")["fx_to_usd"], _row(store, "e1")["fx_source"]) == (0.1285, "estimate_now")
    assert _row(store, "m1")["fx_to_usd"] is None                           # no EUR rate anywhere: still missing
    assert (_row(store, "u1")["fx_to_usd"], _row(store, "u1")["fx_source"]) == (1.0, "usd")
    assert _row(store, "k1")["fx_to_usd"] == 0.13                           # a rate already there is not replaced
    assert _row(store, "s1")["price"] == 14.25 and _row(store, "s1")["commission"] == 23.61


def test_backfill_fills_a_blank_currency_only_when_the_symbol_agrees_with_itself(store):
    store.upsert_fills([_fill("a", "7709", currency="HKD"), _fill("b", "7709", currency=""),
                        _fill("c", "MIX", currency="USD"), _fill("d", "MIX", currency="EUR"),
                        _fill("e", "MIX", currency=""), _fill("f", "LONE", currency="")])
    out = fx_backfill.backfill(store, live_rates=lambda: ("USD", {"USD": 1.0, "HKD": 0.1285}))
    assert _row(store, "b")["currency"] == "HKD" and _row(store, "b")["fx_to_usd"] == 0.1285
    assert _row(store, "e")["currency"] in ("", None) and _row(store, "f")["currency"] in ("", None)
    assert sorted(out["currency_unresolved"]) == ["LONE", "MIX"]
