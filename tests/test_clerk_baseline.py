"""期初建账: open the books from the broker's positions, seal history, check positions against fills."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import baseline, clerk
from ats.memory import get_store
from ats.schemas.portfolio import PortfolioSnapshot, Position

NOW = datetime.now(timezone.utc)
DAY = NOW.date().isoformat()


class _Broker:
    def __init__(self, portfolio):
        self.portfolio = portfolio

    def get_portfolio(self):
        return self.portfolio


def _portfolio(*, nvda=10.0, at=NOW):
    return PortfolioSnapshot(
        as_of=at, account_id="DU1", net_liquidation=100000.0, cash=5000.0, positions=[
            Position(symbol="NVDA", qty=nvda, avg_cost=100.0, market_price=110.0, market_value=nvda * 110.0,
                     unrealized_pnl=nvda * 10.0, weight=0.1)])


@pytest.fixture
def store():
    return get_store()


def _exception(store, kind, subject, status="open"):
    store.conn.execute(
        "INSERT INTO ledger_exceptions (exception_id, kind, subject_key, status, first_seen_at, last_seen_at, "
        "created_at, updated_at, basis, detail_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'orig', '{\"keep\": 1}')",
        (f"x-{subject}", kind, subject, status, NOW.isoformat(), NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    store.conn.commit()


def _run(store, rid="run_prior", status="completed", kind="full"):
    store.conn.execute(
        "INSERT INTO clerk_runs (run_id, kind, window_start, window_end, as_of, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (rid, kind, DAY, DAY, DAY, status, NOW.isoformat()))
    store.conn.commit()


def _fill(store, exec_id, side, shares, at, symbol="NVDA"):
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, instrument_key, side, shares, price, time, origin, captured_at) "
        "VALUES (?, ?, ?, ?, ?, 100, ?, 'manual', ?)", (exec_id, symbol, symbol, side, shares, at, at))
    store.conn.commit()


def _establish(store, *, apply, portfolio=None):
    return baseline.establish_baseline(store=store, broker=_Broker(portfolio or _portfolio()), as_of=DAY,
                                       operator="boss", reason="test", apply=apply)


def test_a_dry_run_reports_the_plan_and_writes_nothing(store):
    _exception(store, "reconciliation_gap", "g1")
    _run(store)
    plan = _establish(store, apply=False)
    assert plan["sealed"] == {"reconciliation_gap": 1} and plan["voided_runs"] == ["run_prior"]
    assert not plan["applied"] and not baseline.baseline_date(store)
    assert store.conn.execute("SELECT status FROM ledger_exceptions").fetchone()[0] == "open"


def test_applying_seals_history_without_deleting_it(store):
    _exception(store, "reconciliation_gap", "g1")
    _exception(store, "broken_link", "b1")
    _run(store)
    assert _establish(store, apply=True)["applied"]
    rows = store.conn.execute("SELECT status, basis, detail_json, resolved_at FROM ledger_exceptions").fetchall()
    assert [r["status"] for r in rows] == ["waived", "waived"]
    assert all("orig" in r["basis"] and "期初建账封存" in r["basis"] and r["resolved_at"] for r in rows)
    assert all(r["detail_json"] == '{"keep": 1}' for r in rows)
    assert baseline.baseline_date(store) == DAY
    assert store.conn.execute("SELECT status FROM clerk_runs WHERE run_id = 'run_prior'").fetchone()[0] == "voided"
    assert baseline.latest_snapshot(store)["positions"][0]["symbol"] == "NVDA"
    from ats.execution import state_api

    assert state_api.completeness(store).status == "complete"


def test_the_baseline_is_idempotent_and_cannot_be_moved(store):
    _establish(store, apply=True)
    again = _establish(store, apply=True)
    assert again["already"] and not again["applied"]
    with pytest.raises(ValueError):
        baseline.establish_baseline(store=store, broker=_Broker(_portfolio()), as_of="2099-01-01",
                                    operator="boss", reason="x", apply=True)


def test_windows_before_the_baseline_are_never_registered_as_gaps(store):
    old = (NOW - timedelta(days=3)).isoformat()
    _fill(store, "e-old", "BOT", 5, old)
    store.set_meta(baseline.BASELINE_KEY, DAY)
    assert clerk._register_missed_windows(store, broker_available=False, window_end=DAY) == []


def test_positions_check_passes_when_fills_explain_the_change(store):
    before = baseline.snapshot_payload(_portfolio(nvda=10, at=NOW - timedelta(days=1)))
    after = baseline.snapshot_payload(_portfolio(nvda=15, at=NOW))
    _fill(store, "e1", "BOT", 5, (NOW - timedelta(hours=3)).isoformat())
    result = baseline.position_changes(store, before, after)
    assert result["mismatched"] == []
    row = result["rows"][0]
    assert (row["before"], row["traded"], row["expected"], row["actual"]) == (10, 5, 15, 15)


def test_positions_check_names_the_symbol_whose_fill_is_missing(store):
    before = baseline.snapshot_payload(_portfolio(nvda=10, at=NOW - timedelta(days=1)))
    after = baseline.snapshot_payload(_portfolio(nvda=15, at=NOW))
    result = baseline.position_changes(store, before, after)          # the +5 was never recorded as a fill
    assert [r["symbol"] for r in result["mismatched"]] == ["NVDA"] and result["mismatched"][0]["difference"] == 5


def test_a_fill_outside_the_two_snapshots_is_not_counted(store):
    before = baseline.snapshot_payload(_portfolio(nvda=10, at=NOW - timedelta(days=1)))
    after = baseline.snapshot_payload(_portfolio(nvda=10, at=NOW))
    _fill(store, "e-old", "BOT", 5, (NOW - timedelta(days=5)).isoformat())
    assert baseline.position_changes(store, before, after)["mismatched"] == []


def test_no_previous_snapshot_means_this_is_the_starting_point(store):
    assert baseline.position_changes(store, None, baseline.snapshot_payload(_portfolio()))["baseline"] is True
