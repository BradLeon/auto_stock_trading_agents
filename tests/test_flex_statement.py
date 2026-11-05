"""Flex Query back-fill and the pre-midnight capture (blockers B11, tasks 11.12). Offline: no Flex, no TWS."""

from __future__ import annotations

import pytest

from ats.execution.baseline import BASELINE_KEY
from ats.memory import get_store
from ats.trader import fills_capture, flex_statement, reconcile

TOKEN = "secret-token-123"


def _trade(**kw) -> str:
    base = dict(accountId="U1", currency="USD", assetCategory="STK", symbol="GOOG", description="ALPHABET",
                conid="208813720", multiplier="1", strike="", expiry="", dateTime="20261103;093157", putCall="",
                tradeDate="20261103", exchange="IBKRATS", quantity="20", tradePrice="279.7", ibCommission="-1.00006",
                buySell="BUY", ibOrderID="5789115958", ibExecID="0000f75a.6ae9e406.01.01", orderReference="")
    base.update(kw)
    return "<Trade " + " ".join(f'{k}="{v}"' for k, v in base.items()) + " />"


def _statement(*trades: str) -> str:
    return ('<FlexQueryResponse queryName="ats-trades" type="AF"><FlexStatements count="1">'
            '<FlexStatement accountId="U1" fromDate="20261028" toDate="20261103"><Trades>'
            + "".join(trades) + "</Trades></FlexStatement></FlexStatements></FlexQueryResponse>")


GOOG = _trade()
OPTION = _trade(assetCategory="OPT", symbol="QQQ   270115P00680000", multiplier="100", strike="680", expiry="20270115",
                putCall="P", dateTime="20261103;093001", quantity="-1", tradePrice="26.41", ibCommission="-1.0887046",
                buySell="SELL", exchange="PHLX", ibExecID="0002920b.6ae9d55c.02.01", ibOrderID="5789023998")
FX = _trade(assetCategory="CASH", symbol="USD.HKD", ibExecID="fx.1")
OLD = _trade(symbol="NVDA", dateTime="20261028;101839", quantity="-20", tradePrice="217.56", buySell="SELL",
             ibExecID="00010168.6ae1d1f2.01.01")


def test_parse_reads_eastern_time_options_and_skips_fx():
    fills = {f["symbol"] + f["side"]: f for f in flex_statement.parse_trades(_statement(GOOG, OPTION, FX))}
    assert set(fills) == {"GOOGBOT", "QQQSLD"}            # the FX row is not a position
    goog, opt = fills["GOOGBOT"], fills["QQQSLD"]
    assert goog["time"] == "2026-11-03T14:31:57+00:00"    # 09:31:57 EST
    assert (goog["shares"], goog["commission"], goog["perm_id"]) == (20.0, 1.00006, "5789115958")
    assert opt["instrument_key"] == "QQQ   270115P00680000" and opt["strike"] == 680.0 and opt["multiplier"] == 100.0
    assert opt["shares"] == 1.0 and opt["side"] == "SLD" and opt["right"] == "P"


def test_parse_follows_daylight_saving():
    [fill] = flex_statement.parse_trades(_statement(_trade(dateTime="20260915;093000")))
    assert fill["time"] == "2026-09-15T13:30:00+00:00"    # EDT, not EST


def test_parse_refuses_an_unreadable_row():
    with pytest.raises(flex_statement.FlexUnavailable):
        flex_statement.parse_trades(_statement(_trade(buySell="HOLD")))


def test_fetch_waits_for_generation_and_never_echoes_the_token():
    sent = ("<FlexStatementResponse><Status>Success</Status><ReferenceCode>77</ReferenceCode>"
            "<Url>https://example.invalid/GetStatement</Url></FlexStatementResponse>")
    waiting = ("<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode>"
               "<ErrorMessage>in progress</ErrorMessage></FlexStatementResponse>")
    replies, urls = [sent, waiting, _statement(GOOG)], []

    def get(url):
        urls.append(url)
        return replies.pop(0)

    body = flex_statement.fetch_statement(TOKEN, "123", get=get, sleep=lambda s: None)
    assert "<Trade " in body and urls[1].startswith("https://example.invalid/GetStatement?")

    refused = "<FlexStatementResponse><Status>Fail</Status><ErrorCode>1012</ErrorCode><ErrorMessage>Token has expired</ErrorMessage></FlexStatementResponse>"
    with pytest.raises(flex_statement.FlexUnavailable) as err:
        flex_statement.fetch_statement(TOKEN, "123", get=lambda url: refused, sleep=lambda s: None)
    assert TOKEN not in str(err.value) and "1012" in str(err.value)


@pytest.fixture
def store():
    s = get_store()
    s.set_meta(BASELINE_KEY, "2026-11-03")
    return s


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(flex_statement, "credentials", lambda: (TOKEN, "123"))


def _row(store, symbol):
    return store.conn.execute("SELECT * FROM fills WHERE symbol = ?", (symbol,)).fetchall()


def test_backfill_inserts_only_what_the_ledger_lacks(store, creds):
    # the hand-entered GOOG row has another exec id for the same execution (stmt: rows); the old NVDA is pre-baseline
    store.upsert_fills([{"exec_id": "stmt:twslog:abc", "symbol": "GOOG", "side": "BOT", "shares": 20.0, "price": 279.7,
                         "time": "2026-11-03T14:31:57+00:00", "commission": 1.0, "instrument_key": "GOOG",
                         "sec_type": "STK", "multiplier": 1.0, "currency": "USD"}])
    store.set_meta("last_reconcile_at", "2026-11-04T23:00:00+00:00")
    out = flex_statement.backfill(store=store, fetch=lambda t, q: _statement(GOOG, OPTION, OLD))
    assert out["status"] == "ok" and out["statement_rows"] == 3
    assert (out["before_baseline"], out["already_recorded"], out["missing"], out["inserted"]) == (1, 1, 1, 1)
    assert len(_row(store, "GOOG")) == 1 and _row(store, "NVDA") == []
    [opt] = _row(store, "QQQ")
    assert (opt["exec_id"], opt["origin"], opt["instrument_key"]) == ("0002920b.6ae9d55c.02.01", "manual",
                                                                      "QQQ   270115P00680000")
    assert store.get_meta("last_reconcile_at") == "2026-11-04T23:00:00+00:00"   # a back-fill opens no window
    again = flex_statement.backfill(store=store, fetch=lambda t, q: _statement(GOOG, OPTION, OLD))
    assert (again["missing"], again["inserted"]) == (0, 0)                       # idempotent


def test_backfill_dry_run_writes_nothing(store, creds):
    out = flex_statement.backfill(store=store, dry_run=True, fetch=lambda t, q: _statement(GOOG))
    assert out["missing"] == 1 and out["inserted"] == 0 and _row(store, "GOOG") == []


def test_backfill_failure_is_reported_not_raised(store, creds):
    def boom(token, query):
        raise flex_statement.FlexUnavailable(f"refused {token}")

    out = flex_statement.backfill(store=store, fetch=boom)
    assert out["status"] == "unavailable" and TOKEN not in out["error"]


def test_backfill_without_credentials_is_skipped(store):          # the autouse guard hides the real token
    assert flex_statement.backfill(store=store)["status"] == "skipped"


class FakeBroker:
    def __init__(self, fills):
        self._fills = fills

    def get_fills(self):
        return self._fills

    def completed_orders(self):
        return []


def test_capture_records_fills_without_moving_the_clerk_window(store):
    store.set_meta("last_reconcile_at", "2026-11-04T23:00:00+00:00")
    fill = {"exec_id": "e-night", "symbol": "NVDA", "side": "BOT", "shares": 20.0, "price": 208.04,
            "time": "2026-11-04T08:10:47+00:00", "commission": 1.0, "order_id": "", "perm_id": "", "order_ref": ""}
    out = fills_capture.capture(broker=FakeBroker([fill]), store=store)
    assert out["fills_new"] == 1 and len(_row(store, "NVDA")) == 1
    assert store.get_meta("last_reconcile_at") == "2026-11-04T23:00:00+00:00"
    reconcile.reconcile(FakeBroker([fill]), store=store)                         # a Clerk read still advances it
    assert store.get_meta("last_reconcile_at") != "2026-11-04T23:00:00+00:00"


def test_the_clerk_reconcile_step_carries_the_flex_summary(store):
    from ats.execution import clerk

    out = clerk._step_reconcile(store, broker=FakeBroker([]), dry_run=False)
    assert out["flex"]["status"] == "skipped" and out["errors"] == []
