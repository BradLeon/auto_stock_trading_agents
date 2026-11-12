"""Backfilling history from an IBKR trade-confirmation statement.

The text below mimics what pypdf extracts from the real 交易确认报告 (synthetic
account and numbers). What matters: stock and option executions are taken (FX
is not), each with its contract identity, the batch
is refused unless it reproduces the statement's own totals, a fill already
captured from TWS is not doubled, and nothing is attributed to the system
without evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ats.memory import get_store
from ats.trader import statement_import as si

STATEMENT = """交易确认报告
交易
股票
USD
U1234567 FORM 2026-10-15, 09:53:14 2026-10-16 - SELL -40 108.4000 4,336.00 -1.09 0.00 MIDPX C;P
   U1234567 FORM 2026-10-15, 09:53:14 2026-10-16 NASDAQ SELL -25 108.4000 2,710.00 -1.06 0.00 MIDPX C;P
   U1234567 FORM 2026-10-15, 09:53:14 2026-10-16 NASDAQ SELL -15 108.4000 1,626.00 -0.03 0.00 MIDPX C;P
   总数 FORM (卖出) -40 108.4000 4,336.00 -1.09 0.00 
U1234567 SHV 2026-10-22, 09:56:47 2026-10-23 - BUY 300 110.287551667 -33,086.27 -1.50 0.00 LMT IM;O;P
   U1234567 SHV 2026-10-22, 09:56:47 2026-10-23 DARK BUY 189 110.2888 -20,844.58 -1.00 0.00 LMT IA;O;P
   U1234567 SHV 2026-10-22, 09:56:47 2026-10-23 DARK BUY 100 110.2850 -11,028.50 -0.45 0.00 LMT O;P
   U1234567 SHV 2026-10-22, 09:56:47 2026-10-23 DARK BUY 11 110.2893 -1,213.18 -0.06 0.00 LMT O;P
   总数 SHV (买入) 300 110.287551667
9 -33,086.27 -1.50 0.00 
U1234567 ASML 2026-10-28, 10:20:47 2026-10-29 - BUY 2 1,653.9000 -3,307.80 -1.00 0.00 LMT O
   U1234567 ASML 2026-10-28, 10:20:47 2026-10-29 NASDAQ BUY 2 1,653.9000 -3,307.80 -1.00 0.00 LMT O
   总数 ASML (买入) 2 1,653.9000 -3,307.80 -1.00 0.00 
U1234567 ASML 2026-11-02, 10:00:00 2026-11-03 - SELL -1 1,700.0000 1,700.00 -1.00 0.00 LMT C
   U1234567 ASML 2026-11-02, 10:00:00 2026-11-03 NASDAQ SELL -1 1,700.0000 1,700.00 -1.00 0.00 LMT C
   总数 ASML (卖出) -1 1,700.0000 1,700.00 -1.00 0.00 
股票和指数期权
USD
U1234567 AAPL 19MAR27 300 C 2026-09-08, 10:27:49 2026-09-09 - BUY 1 37.4000 -3,740.00 -1.04 0.00 LMT O
   U1234567 AAPL 19MAR27 300 C 2026-09-08, 10:27:49 2026-09-09 MIAX BUY 1 37.4000 -3,740.00 -1.04 0.00 LMT O
   总数 AAPL  270319C00300000 (买入) 1 37.4000 -3,740.00 -1.04 0.00 
U1234567 AXTI 20NOV26 50 P 2026-10-02, 10:00:00 2026-10-05 - SELL -5 4.8000 2,400.00 1.53 0.00 LMT O
   U1234567 AXTI 20NOV26 50 P 2026-10-02, 10:00:00 2026-10-05 CBOE SELL -5 4.8000 2,400.00 1.53 0.00 LMT O
   总数 AXTI  261120P00050000 (卖出) -5 4.8000 2,400.00 1.53 0.00 
外汇
HKD
U1234567 USD.HKD 2026-10-15, 02:14:30 2026-10-20 - BUY 1,000 7.7700 -7,770.00 -2.00 0.00 MKT
   U1234567 USD.HKD 2026-10-15, 02:14:30 2026-10-20 IDEALFX BUY 1,000 7.7700 -7,770.00 -2.00 0.00 MKT
金融产品信息
股票
"""


@pytest.fixture
def store():
    return get_store()


@pytest.fixture
def statement(monkeypatch, tmp_path):
    def use(text: str = STATEMENT) -> Path:
        monkeypatch.setattr(si, "read_text", lambda path: text)
        return tmp_path / "statement.pdf"
    return use


def test_stocks_and_options_are_taken_fx_is_not_and_totals_reconcile():
    parsed = si.parse(STATEMENT)
    assert [f["symbol"] for f in parsed.fills] == ["FORM", "FORM", "SHV", "SHV", "SHV",
                                                    "ASML", "ASML", "AAPL", "AXTI"]
    assert dict(parsed.skipped) == {"外汇": 1}
    si.verify(parsed)        # SHV rows sum to -33,086.26: within per-row rounding


def test_an_option_row_carries_its_contract():
    call, put = [f for f in si.parse(STATEMENT).fills if f["sec_type"] == "OPT"]
    assert (call["instrument_key"], call["right"], call["strike"], call["expiry"],
            call["multiplier"]) == ("AAPL  270319C00300000", "C", 300.0, "20270319", 100.0)
    assert put["instrument_key"] == "AXTI  261120P00050000"
    # A positive statement commission is a rebate: a negative cost in the ledger.
    assert put["commission"] == -1.53


def test_stock_rows_keep_the_contract_free_exec_ids_of_the_first_import():
    """Production already holds stock rows from the stock-only import; their ids
    must not move, or a re-import would double them."""
    form = si.parse(STATEMENT).fills[0]
    legacy = "|".join(str(form[k]) for k in ("symbol", "time", "side", "shares", "price",
                                              "commission", "venue")) + "|0"
    import hashlib
    assert form["exec_id"] == "stmt:" + hashlib.sha256(legacy.encode()).hexdigest()[:20]


def test_statement_times_are_eastern_and_follow_daylight_saving():
    times = {(f["symbol"], f["side"]): f["time"] for f in si.parse(STATEMENT).fills}
    assert times[("ASML", "BOT")] == "2026-10-28T14:20:47+00:00"        # EDT
    assert times[("ASML", "SLD")] == "2026-11-02T15:00:00+00:00"        # EST


def test_exec_ids_are_stable_and_distinct():
    a, b = si.parse(STATEMENT), si.parse(STATEMENT)
    ids = [f["exec_id"] for f in a.fills]
    assert ids == [f["exec_id"] for f in b.fills] and len(set(ids)) == len(ids)
    assert all(i.startswith("stmt:") for i in ids)


def test_a_quantity_that_misses_its_total_refuses_the_batch():
    broken = STATEMENT.replace("NASDAQ SELL -15 108.4000", "NASDAQ SELL -14 108.4000")
    with pytest.raises(si.StatementMismatch, match="FORM"):
        si.verify(si.parse(broken))


def test_an_unparsed_trade_line_refuses_the_batch():
    broken = STATEMENT.replace("NASDAQ BUY 2 1,653.9000", "NASDAQ BUY two 1,653.9000")
    with pytest.raises(si.StatementMismatch):
        si.verify(si.parse(broken))


def test_import_writes_manual_fills_and_skips_what_tws_already_captured(store, statement):
    store.upsert_fills([{"exec_id": "tws-1", "symbol": "ASML", "side": "BOT", "shares": 2.0,
                         "price": 1653.9, "time": "2026-10-28T14:20:47+00:00",
                         "commission": 1.0, "order_id": "0", "perm_id": "1", "order_ref": ""}])
    out = si.import_statement(statement(), store=store)
    assert out["duplicates_of_tws"] == 1 and out["inserted"] == 8
    assert out["origins"] == {"manual": 8} and out["by_type"] == {"STK": 6, "OPT": 2}
    rows = store.conn.execute(
        "SELECT origin, link_confidence, realized_pnl FROM fills WHERE exec_id LIKE 'stmt:%'"
    ).fetchall()
    assert {tuple(r) for r in rows} == {("manual", "untagged_after_cutoff", None)}
    assert store.conn.execute("SELECT COUNT(*) FROM ledger_exceptions").fetchone()[0] == 0


def test_reimporting_is_a_no_op(store, statement):
    si.import_statement(statement(), store=store)
    assert si.import_statement(statement(), store=store)["inserted"] == 0


def test_dry_run_writes_nothing(store, statement):
    out = si.import_statement(statement(), store=store, dry_run=True)
    assert out["inserted"] == 0 and out["origins"] == {"manual": 9}
    assert store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0


def test_a_mismatched_statement_writes_nothing(store, statement):
    with pytest.raises(si.StatementMismatch):
        si.import_statement(statement(STATEMENT.replace("-25 108.4000", "-24 108.4000")),
                            store=store)
    assert store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0


def test_rows_from_the_stock_only_import_get_their_contract_columns(store, statement):
    si.import_statement(statement(), store=store)
    store.conn.execute("UPDATE fills SET sec_type = NULL, currency = NULL")
    out = si.import_statement(statement(), store=store)
    assert out["inserted"] == 0 and out["contract_backfilled"] == 9
    assert store.conn.execute(
        "SELECT COUNT(*) FROM fills WHERE sec_type IS NULL").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# Transaction History CSV (date-only rows)
# --------------------------------------------------------------------------- #
TXN = """Statement,Header,域名称,域值
Statement,Data,Title,Transaction History
总结,Header,域名称,域值
总结,Data,变更,{change}
Transaction History,Header,日期,账户,说明,交易类型,代码,数量,价格,Price Currency,总额,佣金,净额
Transaction History,Data,2026-08-31,U***1,AAPL 19MAR27 300 C,买,AAPL  270319C00300000,1.0,37.5,USD,-3750.0,-1.0423,-3751.0423
Transaction History,Data,2026-08-12,U***1,"外汇交易基础货币净额: -2,697 EUR.USD",外汇交易组成部分,EUR.USD,-2697.0,1.1548,USD,4.2031,-2.0,4.2031
Transaction History,Data,2026-08-12,U***1,COHERENT CORP,买,COHR,15.0,347.52,USD,-5212.8,-1.000045,-5213.800045
Transaction History,Data,2026-08-11,U***1,CSOP SK HYNIX,买,7709,300.0,28.82,HKD,-1101.84624,-2.29392,-1104.14016
Transaction History,Data,2026-08-11,U***1,COHR 16OCT26 260 P,买,COHR  261016P00260000,1.0,15.3,USD,-1530.0,0.1407,-1529.8593
Transaction History,Data,2026-08-11,U***1,COHR 16OCT26 260 P,卖,COHR  261016P00260000,-1.0,17.4,USD,1740.0,-1.086434,1738.913566
Transaction History,Data,2026-08-06,U***1,SGOV 现金红利,股息,SGOV,-,-,-,61.06,-,61.06
"""
NET = (-3751.0423 + 4.2031 - 5213.800045 - 1104.14016 - 1529.8593 + 1738.913566 + 61.06)


@pytest.fixture
def txn(tmp_path):
    def use(change: float = NET) -> Path:
        path = tmp_path / "TRANSACTIONS.csv"
        path.write_text(TXN.format(change=change), encoding="utf-8")
        return path
    return use


def test_a_transaction_csv_parses_trades_and_reconciles_to_the_cash_change(txn):
    parsed = si.parse_transactions(si.read_text(txn()))
    assert not parsed.unparsed
    assert [(f["instrument_key"], f["side"]) for f in parsed.fills] == [
        ("COHR  261016P00260000", "SLD"), ("COHR  261016P00260000", "BOT"),
        ("7709", "BOT"), ("COHR", "BOT"), ("AAPL  270319C00300000", "BOT")]
    hk = parsed.fills[2]
    # 总额/佣金 are in base currency: the row's own ratio converts them back.
    assert hk["currency"] == "HKD" and hk["commission"] == pytest.approx(18.0, abs=0.01)
    assert dict(parsed.skipped) == {"外汇": 1, "股息": 1}


def test_rows_of_one_day_keep_their_order_oldest_first(txn):
    """The file lists newest first: on 08-11 the 260P sale is the last (oldest)
    line, so it comes first."""
    day = [f for f in si.parse_transactions(si.read_text(txn())).fills
           if f["date"] == "2026-08-11"]
    assert [(f["sec_type"], f["side"]) for f in sorted(day, key=lambda f: f["time"])] == [
        ("OPT", "SLD"), ("OPT", "BOT"), ("STK", "BOT")]
    times = [f["time"] for f in si.parse_transactions(si.read_text(txn())).fills]
    assert len(set(times)) == len(times)


def test_a_csv_whose_rows_do_not_sum_to_the_cash_change_writes_nothing(store, txn):
    with pytest.raises(si.StatementMismatch):
        si.import_statement(txn(change=NET + 1.0), store=store)
    assert store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0


def test_the_broker_record_names_the_contract_of_an_uncontracted_tws_fill(store, txn):
    store.upsert_fills([
        {"exec_id": "aapl", "symbol": "AAPL", "side": "BOT", "shares": 1.0, "price": 37.5,
         "time": "2026-08-31T15:05:52+00:00", "commission": 1.0, "order_id": "0"},
        {"exec_id": "eur", "symbol": "EUR", "side": "SLD", "shares": 2697.0, "price": 1.1548,
         "time": "2026-08-12T13:40:31+00:00", "commission": 2.0, "order_id": "0"},
        {"exec_id": "cohr", "symbol": "COHR", "side": "BOT", "shares": 15.0, "price": 347.52,
         "time": "2026-08-12T13:37:03+00:00", "commission": 1.0, "order_id": "0"}])
    # An earlier "contract unknown" call is superseded by the broker's record.
    store.classify_fill("aapl", sec_type="OPT", instrument_key="AAPL OPT?", multiplier=100.0,
                        basis="price", decided_by="boss")
    out = si.import_statement(txn(), store=store)
    assert out["duplicates_of_tws"] == 2 and out["inserted"] == 3
    cls = dict(store.conn.execute(
        "SELECT exec_id, instrument_key FROM fill_classifications").fetchall())
    assert cls == {"aapl": "AAPL  270319C00300000", "eur": "EUR"}
    assert store.conn.execute(
        "SELECT sec_type FROM fill_classifications WHERE exec_id='eur'").fetchone()[0] == "CASH"
    again = si.import_statement(txn(), store=store)
    assert again["inserted"] == 0 and again["legacy_classified"] == []
