"""journal doctor — the baseline instrument every later journal stage is measured against.

If the reader is wrong, every conclusion drawn from it is wrong, so these tests pin the
counting rules against a seeded DB rather than trusting the live numbers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ats.journal import doctor
from ats.memory import get_store

NOW = datetime.now(timezone.utc)


@pytest.fixture
def conn():
    return get_store().conn


def _trade(conn, *, cycle="c1", symbol="GOOG", action="trim", status="filled",
           order_id="1", avg_fill_price=None, realized_pnl=None, filled_at=None,
           legacy=False):
    """`legacy=True` omits client_order_id, i.e. a row written before the journal."""
    coid = None if legacy else f"{cycle}:{symbol}:{action}:{order_id}"
    conn.execute(
        "INSERT INTO trades (order_id, cycle_id, symbol, action, qty, order_type, status, "
        "avg_fill_price, submitted_at, rationale, limit_price, filled_at, error, "
        "realized_pnl, source, context, client_order_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, cycle, symbol, action, 10.0, "limit", status, avg_fill_price,
         NOW.isoformat(), "why", None, filled_at, "", realized_pnl, "chief", "{}", coid))
    conn.commit()


def _fill(conn, *, exec_id="e1", symbol="GOOG", order_id="1", pnl=0.0, time="2026-07-23T10:00"):
    conn.execute("INSERT INTO fills (exec_id, symbol, side, shares, price, time, "
                 "realized_pnl, commission, order_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (exec_id, symbol, "SLD", 10.0, 100.0, time, pnl, 0.0, order_id))
    conn.commit()


def _find(sections, title_starts, label_starts):
    for s in sections:
        if s.title.startswith(title_starts):
            for f in s.findings:
                if f.label.strip().startswith(label_starts):
                    return f
    raise AssertionError(f"finding not found: {title_starts} / {label_starts}")


# --------------------------------------------------------------------------- #
# 1. capture
# --------------------------------------------------------------------------- #
def test_flags_missing_realized_pnl(conn):
    """The defect that makes per-trade review impossible: 0/52 in the live DB."""
    _trade(conn, order_id="1")
    _trade(conn, order_id="2", symbol="ASML")
    f = _find(doctor.collect(conn), "1.", "有盈亏")
    assert f.ok is False
    assert f.value.startswith("0/2")


def test_clean_capture_passes(conn):
    _trade(conn, order_id="1", avg_fill_price=100.0, realized_pnl=5.0,
           filled_at=NOW.isoformat())
    sections = doctor.collect(conn)
    assert _find(sections, "1.", "有盈亏").ok is True
    assert _find(sections, "1.", "有成交价").ok is True


def test_unfilled_orders_are_not_counted_as_missing_pnl(conn):
    """A cancelled/errored order owes no fill price and no P&L — measuring those
    against all rows (most of which never filled) misstates the real gap."""
    _trade(conn, order_id="1", status="filled", avg_fill_price=317.07,
           realized_pnl=495.19, filled_at=NOW.isoformat())
    for i, st in enumerate(("error", "cancelled", "expired"), start=2):
        _trade(conn, order_id=str(i), symbol=f"S{i}", status=st)
    sections = doctor.collect(conn)
    assert _find(sections, "1.", "trades 总行数").value == "4"
    assert _find(sections, "1.", "其中已成交").value == "1"
    f = _find(sections, "1.", "有盈亏")
    assert f.ok is True and f.value.startswith("1/1")


def test_flags_zombie_submitted_rows(conn):
    """Submitted with no fill price = we never learned the outcome."""
    _trade(conn, status="submitted", order_id="1")
    _trade(conn, status="filled", order_id="2", avg_fill_price=100.0)
    f = _find(doctor.collect(conn), "1.", "僵尸")
    assert f.ok is False and f.value == "1"


# --------------------------------------------------------------------------- #
# 2. idempotency
# --------------------------------------------------------------------------- #
def test_counts_redundant_rows_not_just_groups(conn):
    """The live case is 3 groups x 5 rows = 12 redundant rows, not 3."""
    for i in range(5):
        _trade(conn, cycle="c1", symbol="GOOG", action="trim", order_id=str(i))
    for i in range(5):
        _trade(conn, cycle="c1", symbol="ASML", action="trim", order_id=f"a{i}")
    f = _find(doctor.collect(conn), "2.", "重复的意图组（新制）")
    assert f.ok is False
    assert f.value == "2"
    assert "多出 8 行" in f.detail


def test_no_duplicates_passes(conn):
    _trade(conn, symbol="GOOG", order_id="1")
    _trade(conn, symbol="ASML", order_id="2")
    assert _find(doctor.collect(conn), "2.", "重复的意图组（新制）").ok is True


def test_legacy_rows_are_reported_separately_not_as_failures(conn):
    """History written before the journal cannot be repaired — the broker only serves
    the current day's executions. Flagging it red forever just trains you to ignore
    the whole report."""
    for i in range(5):                       # the real 2026-07-23 retry storm
        _trade(conn, cycle="old", symbol="GOOG", order_id=str(i), legacy=True)
    _trade(conn, cycle="new", symbol="ASML", order_id="9", status="filled",
           avg_fill_price=100.0, realized_pnl=5.0, filled_at=NOW.isoformat())

    sections = doctor.collect(conn)
    assert _find(sections, "1.", "历史行").value == "5"
    assert _find(sections, "2.", "重复的意图组（新制）").ok is True      # new regime clean
    legacy = _find(sections, "2.", "历史重复")
    assert legacy.ok is None                                        # context, not failure
    assert "1 组 / 多 4 行" in legacy.value


# --------------------------------------------------------------------------- #
# 4. attribution
# --------------------------------------------------------------------------- #
def test_identifies_orphan_fills_as_manual(conn):
    """The account-wide execution feed carries trades the system never placed."""
    _trade(conn, order_id="1")
    _fill(conn, exec_id="mine", order_id="1")
    _fill(conn, exec_id="theirs", symbol="SPCX", order_id="34", pnl=-60.01)
    sections = doctor.collect(conn)
    assert _find(sections, "4.", "可匹配到系统订单").value.startswith("1/2")
    assert _find(sections, "4.", "无主成交 SPCX").value.startswith("order_id=34")


# --------------------------------------------------------------------------- #
# 5. coverage — the claim that must never be silently wrong
# --------------------------------------------------------------------------- #
def test_reports_observation_boundary(conn):
    """Days with no snapshot are unrecoverable; they must not read as 'no trades'."""
    _fill(conn, exec_id="a", time="2026-07-17T10:00")
    _fill(conn, exec_id="b", time="2026-07-23T10:00")
    sections = doctor.collect(conn)
    assert _find(sections, "5.", "有成交记录的交易日").value == "2"
    boundary = _find(sections, "5.", "⚠️ 追溯边界")
    assert "2026-07-17" in boundary.value
    assert "不得当成" in boundary.detail


# --------------------------------------------------------------------------- #
# 6. backfillable material
# --------------------------------------------------------------------------- #
def test_counts_scored_dossiers_only(conn):
    """prep-phase dossiers have no scorecard — they are not backfill material."""
    scored = {"symbol": "GOOG", "scorecard": {"total": 0.72},
              "market_setup": {"expected_move_pct": 5.71},
              "expectation_set": {"consensus_target_price": 210.0}}
    prep = {"symbol": "CRDO", "scorecard": None, "market_setup": {}}
    for sym, payload in (("GOOG", scored), ("CRDO", prep)):
        conn.execute("INSERT INTO pead_dossier VALUES (?,?,?,?,?)",
                     (sym, "Q2 2026", "score", json.dumps(payload), NOW.isoformat()))
    conn.commit()
    sections = doctor.collect(conn)
    assert _find(sections, "6.", "已打分的 scorecard").value == "1"
    assert _find(sections, "6.", "expected_move").value == "1"
    assert _find(sections, "6.", "consensus").value == "1"


def test_tolerates_unparseable_payload(conn):
    conn.execute("INSERT INTO pead_dossier VALUES (?,?,?,?,?)",
                 ("X", "Q1", "prep", "not json", NOW.isoformat()))
    conn.commit()
    assert _find(doctor.collect(conn), "6.", "已打分").value == "0"


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def test_render_marks_only_real_failures(conn):
    _trade(conn, order_id="1", avg_fill_price=100.0, realized_pnl=5.0,
           filled_at=NOW.isoformat())
    text = doctor.render(doctor.collect(conn))
    assert "交易记录体检" in text
    # Neutral facts (counts) must not be rendered as failures.
    assert "❌ 有盈亏" not in text


def test_empty_db_does_not_crash(conn):
    text = doctor.render(doctor.collect(conn))
    assert "0 行" in text
