"""Clerk 日报: the account in plain language; pushed only when something needs a person."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import baseline, clerk_report
from ats.memory import get_store
from ats.schemas.portfolio import PortfolioSnapshot, Position

NOW = datetime.now(timezone.utc)
DAY = NOW.date().isoformat()
OK_COUNTS = {
    "reconcile": {"fills_seen": 1, "fills_new": 1, "linked": 0, "manual": 1, "unattributed": 0,
                  "pnl_backfilled": 0, "status_resolved": 0, "errors": []},
    "positions": {"saved": True, "positions": 2},
}


@pytest.fixture
def store():
    return get_store()


def _portfolio(at, *, nvda=15.0):
    return PortfolioSnapshot(
        as_of=at, net_liquidation=100000.0, cash=5000.0, daily_pnl=250.0, positions=[
            Position(symbol="NVDA", qty=nvda, avg_cost=100.0, market_price=110.0, market_value=nvda * 110,
                     unrealized_pnl=nvda * 10, weight=0.0165),
            Position(symbol="COHR", sec_type="OPT", qty=-2, avg_cost=1247.0, market_price=22.85,
                     market_value=-4570.0, unrealized_pnl=-2076.0, weight=-0.045, multiplier=100.0,
                     expiry="20261120", strike=210.0, right="P")])


def _run(store, *, rid="run_a", status="completed", counts=OK_COUNTS, error=None, snapshot=True, nvda=15.0):
    started, finished = NOW - timedelta(minutes=1), NOW + timedelta(minutes=1)
    if snapshot:
        baseline.save_run_snapshot(store, _portfolio(NOW, nvda=nvda))
    store.conn.execute(
        "INSERT OR REPLACE INTO clerk_runs (run_id, kind, window_start, window_end, as_of, status, started_at, "
        "finished_at, counts_json, error) VALUES (?, 'full', ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, DAY, DAY, DAY, status, started.isoformat(), finished.isoformat(), json.dumps(counts), error))
    store.conn.commit()
    return {"run_id": rid, "status": status}


def _fill(store, exec_id="e1", symbol="NVDA", at=None):
    at = at or (NOW - timedelta(minutes=30)).isoformat()
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, instrument_key, side, shares, price, time, origin, commission, "
        "captured_at) VALUES (?, ?, ?, 'BOT', 5, 100.5, ?, 'manual', 1.0, ?)", (exec_id, symbol, symbol, at, at))
    store.conn.commit()


def _exception(store, kind="reconciliation_gap", first_seen="2026-09-03T00:00:00+00:00"):
    store.conn.execute(
        "INSERT INTO ledger_exceptions (exception_id, kind, subject_key, window_start, window_end, status, "
        "first_seen_at, last_seen_at, created_at, updated_at) VALUES (?, ?, ?, '2026-09-01', '2026-09-02', 'open', "
        "?, ?, ?, ?)", (f"x-{kind}-{first_seen}", kind, kind, first_seen, NOW.isoformat(), NOW.isoformat(),
                        NOW.isoformat()))
    store.conn.commit()


def _report(store, out, **kw):
    d = clerk_report.collect(store, out, source="测试")
    return d, clerk_report.render(d)


def _silence(monkeypatch):
    pushed = []
    monkeypatch.setattr("ats.runtime.digest._push", lambda *args: pushed.append(args))
    monkeypatch.setattr("ats.runtime.digest._write_md", lambda name, body: None)
    return pushed


def test_the_report_lists_holdings_with_cost_price_and_profit(store):
    _, text = _report(store, _run(store))
    assert "| NVDA | 股票 | 15 | 100.00 | 110.00 | $1,650 | 1.7% | +150 | +10.0% |" in text
    assert "| COHR 2026-11-20 认沽 210 | 期权 | -2 | 12.47 | 22.85 | -$4,570 |" in text
    assert "| **合计** |" in text and "$100,000" in text


def test_a_trade_is_listed_with_its_source_and_a_quiet_day_says_so(store):
    _fill(store)
    _, text = _report(store, _run(store))
    assert "| NVDA | 买入 | 5 | 100.50 |" in text and "手工下单" in text
    other = get_store()
    other.conn.execute("DELETE FROM fills")
    other.conn.commit()
    _, quiet = _report(store, _run(store, rid="run_b"))
    assert "没有成交" in quiet and "最近 10 笔成交" not in quiet


def test_the_position_check_compares_with_the_previous_snapshot(store):
    baseline.put_snapshot(store, baseline.snapshot_payload(_portfolio(NOW - timedelta(days=1), nvda=10.0)),
                          period="baseline")
    _fill(store)                                  # +5 explains 10 -> 15
    d, text = _report(store, _run(store))
    assert "| NVDA | 10 | +5 | 15 | 15 | 一致 |" in text and d["changes"]["mismatched"] == []
    assert "有 0 件事" not in text and "没有需要你处理的事" in text


def test_a_missing_fill_is_called_out_in_plain_words(store):
    baseline.put_snapshot(store, baseline.snapshot_payload(_portfolio(NOW - timedelta(days=1), nvda=10.0)),
                          period="baseline")
    d, text = _report(store, _run(store))         # shares went 10 -> 15 with no fill recorded
    assert "**对不上，差 +5**" in text
    assert any("NVDA（应有 10，券商实际 15）" in item for item in clerk_report.attention(d))


def test_the_first_snapshot_is_the_starting_point(store):
    _, text = _report(store, _run(store))
    assert "这是第一份快照" in text and "今天是期初建账的起点" in text


def test_a_failed_run_says_why_and_how_to_rerun(store):
    out = _run(store, status="failed", snapshot=False, error="reconcile: fetch failed: cannot reach IBKR at 127.0.0.1",
               counts={"reconcile": {"errors": ["fetch failed: cannot reach IBKR at 127.0.0.1"]}})
    d, text = _report(store, out)
    assert "今天的账没有对完" in text and "ats clerk run" in text and d["tws"] == "没连上"


def test_a_reused_run_is_labelled_and_never_pushed(store, monkeypatch):
    pushed = _silence(monkeypatch)
    _exception(store, first_seen=NOW.isoformat())
    out = {**_run(store), "reused": True}
    _, text = _report(store, out)
    assert "复用了该窗口已有的结果" in text
    assert clerk_report.publish(out, source="定时")["alerts"] == 0 and not pushed


def test_a_standing_exception_is_explained_but_does_not_push(store, monkeypatch):
    pushed = _silence(monkeypatch)
    _exception(store)
    out = _run(store)
    _, text = _report(store, out)
    assert "2026-09-01 至 2026-09-02 这段时间的成交没能和券商核对" in text
    assert clerk_report.publish(out, source="定时")["alerts"] == 0 and not pushed


def test_a_new_exception_or_mismatch_pushes_once(store, monkeypatch):
    pushed = _silence(monkeypatch)
    baseline.put_snapshot(store, baseline.snapshot_payload(_portfolio(NOW - timedelta(days=1), nvda=10.0)),
                          period="baseline")
    _exception(store, first_seen=NOW.isoformat())
    out = _run(store)
    assert clerk_report.publish(out, source="定时")["alerts"] == 2 and len(pushed) == 1
    kind, title, body = pushed[0]
    assert kind == "info" and "持仓数量与成交记录对不上" in body and "没能和券商核对" in body


def test_the_sealed_history_is_mentioned_not_listed(store):
    store.set_meta(baseline.BASELINE_KEY, DAY)
    store.conn.execute(
        "INSERT INTO ledger_exceptions (exception_id, kind, subject_key, status, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES ('w1', 'broken_link', 'w1', 'waived', ?, ?, ?, ?)",
        (NOW.isoformat(),) * 4)
    store.conn.commit()
    d, text = _report(store, _run(store))
    assert "之前遗留的 1 条提示已封存" in text and d["exceptions"] == []


def test_a_report_failure_does_not_raise(store):
    assert "error" in clerk_report.publish({"run_id": "no-such-run"}, source="定时")
