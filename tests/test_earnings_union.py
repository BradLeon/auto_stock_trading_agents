"""Earnings print detection: Finnhub + yfinance union, offline.

Fixtures mirror shapes measured live on 2026-07-25. The behaviours locked down here
are the ones that make scoring triggerable at all:
  * a PAST print is visible (next_earnings() only ever looked forward, which is why
    the scheduler's score branch was unreachable);
  * an actual EPS means "已公布" — an observation, not a forecast;
  * the session comes from yfinance's real clock, because Finnhub's `hour` is blank
    for 5 of 13 targets;
  * an out-of-band timestamp stays "unknown" instead of defaulting to "amc".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from ats.data import earnings_calendar as ec

ET = ec.ET


def _stub(monkeypatch, fh_rows, yf_rows):
    monkeypatch.setattr(ec, "_finnhub_window", lambda s, a, b: list(fh_rows))
    monkeypatch.setattr(ec, "_yf_prints", lambda s, a, b: list(yf_rows))


def _fh(d: date, hour="", *, q=2, y=2026, eps_actual=None, eps_est=2.97,
        rev_actual=None, rev_est=None) -> dict:
    return {"date": d, "hour": hour, "quarter": q, "year": y, "eps_actual": eps_actual,
            "eps_estimate": eps_est, "rev_actual": rev_actual, "rev_estimate": rev_est}


def _yf(d: date, hh=16, mm=0, *, eps_actual=None, eps_est=2.91) -> dict:
    return {"date": d, "at": datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET),
            "eps_actual": eps_actual, "eps_estimate": eps_est}


# --------------------------------------------------------------------------- #
# Session classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("hh,mm,expect", [
    (16, 0, "amc"),      # GOOG / LRCX / KLAC / SKHY
    (18, 30, "amc"),
    (8, 0, "bmo"),       # VRT
    (5, 0, "bmo"),
    (11, 0, "dmh"),
    (2, 0, "unknown"),   # overnight foreign filing
    (23, 0, "unknown"),
    (0, 0, "unknown"),   # midnight placeholder — must NOT become "amc"
])
def test_session_from_clock(hh, mm, expect):
    assert ec._session_from_clock(datetime(2026, 7, 22, hh, mm, tzinfo=ET)) == expect


def test_session_unknown_without_a_timestamp():
    assert ec._session_from_clock(None) == "unknown"


# --------------------------------------------------------------------------- #
# The GOOG case that motivated all of this
# --------------------------------------------------------------------------- #
def test_past_print_is_visible_and_marked_reported(monkeypatch):
    """GOOG 2026-07-22 amc, epsActual 9.11 — the print the scheduler never scored."""
    _stub(monkeypatch,
          [_fh(date(2026, 7, 22), "amc", eps_actual=9.11, rev_actual=103_617_000_000.0)],
          [_yf(date(2026, 7, 22), 16, 0, eps_actual=9.11)])

    p = ec.last_print("GOOG", as_of=date(2026, 7, 25))
    assert p is not None
    assert p.date == date(2026, 7, 22)
    assert (p.session, p.session_source) == ("amc", "yf-clock")
    assert (p.quarter, p.year) == (2, 2026)
    assert p.eps_actual == 9.11
    assert p.reported is True
    assert set(p.sources) == {"finnhub", "yfinance"}


def test_unreported_print_is_not_marked_reported(monkeypatch):
    """Date has arrived but no actual yet -> must not trigger scoring."""
    _stub(monkeypatch, [_fh(date(2026, 7, 22), "amc")], [])
    p = ec.last_print("SKHY", as_of=date(2026, 7, 25))
    assert p is not None and p.reported is False


# --------------------------------------------------------------------------- #
# Source-quality handling
# --------------------------------------------------------------------------- #
def test_clock_beats_blank_finnhub_hour(monkeypatch):
    """Finnhub `hour` is blank for SKHY/CRDO/MRVL — the timestamp still resolves it."""
    _stub(monkeypatch, [_fh(date(2026, 7, 29), "", eps_actual=1.4)],
          [_yf(date(2026, 7, 29), 8, 0, eps_actual=1.4)])
    p = ec.last_print("VRT", as_of=date(2026, 7, 29))
    assert (p.session, p.session_source) == ("bmo", "yf-clock")


def test_finnhub_hour_used_when_yfinance_is_absent(monkeypatch):
    """yfinance intermittently fails outright ('KLAC may be delisted')."""
    _stub(monkeypatch, [_fh(date(2026, 7, 28), "amc", eps_actual=8.2)], [])
    p = ec.last_print("KLAC", as_of=date(2026, 7, 28))
    assert (p.session, p.session_source) == ("amc", "finnhub-hour")
    assert p.sources == ("finnhub",)


def test_session_unknown_when_neither_source_can_say(monkeypatch):
    """Must stay 'unknown' (-> try both windows), never guess 'amc'."""
    _stub(monkeypatch, [_fh(date(2026, 7, 22), "", eps_actual=1.0)], [])
    assert ec.last_print("CRDO", as_of=date(2026, 7, 25)).session == "unknown"


def test_one_day_offset_is_the_same_event(monkeypatch):
    """The feeds routinely differ by a day; that must not become two prints."""
    _stub(monkeypatch, [_fh(date(2026, 7, 22), "amc", eps_actual=9.11)],
          [_yf(date(2026, 7, 23), 16, 0, eps_actual=9.11)])
    prints = ec.recent_and_next_prints("GOOG", as_of=date(2026, 7, 25), fwd_days=0)
    assert len(prints) == 1
    assert prints[0].sources == ("finnhub", "yfinance")


def test_large_disagreement_keeps_both_and_warns(monkeypatch, caplog):
    """SKHY: the two feeds differ by 6 days — collapsing them would be a guess."""
    _stub(monkeypatch, [_fh(date(2026, 7, 22), "", q=2)],
          [_yf(date(2026, 7, 28), 16, 0)])
    with caplog.at_level("WARNING"):
        prints = ec.recent_and_next_prints("SKHY", as_of=date(2026, 7, 28), fwd_days=0)
    assert [p.date for p in prints] == [date(2026, 7, 22), date(2026, 7, 28)]
    assert any("disagree" in r.message for r in caplog.records)


def test_yfinance_only_event_survives(monkeypatch):
    _stub(monkeypatch, [], [_yf(date(2026, 7, 28), 16, 0, eps_actual=4.87)])
    p = ec.last_print("SKHY", as_of=date(2026, 7, 28))
    assert p is not None and p.sources == ("yfinance",) and p.eps_actual == 4.87


def test_both_sources_dead_degrades_to_none(monkeypatch):
    """Sources must degrade, never raise into the scheduler."""
    def boom(*a):
        raise RuntimeError("network down")

    monkeypatch.setattr(ec, "_finnhub_window", boom)
    monkeypatch.setattr(ec, "_yf_prints", boom)
    assert ec.last_print("GOOG", as_of=date(2026, 7, 25)) is None
    assert ec.recent_and_next_prints("GOOG", as_of=date(2026, 7, 25)) == []


# --------------------------------------------------------------------------- #
# Windowing
# --------------------------------------------------------------------------- #
def test_last_print_ignores_the_future(monkeypatch):
    today = date(2026, 7, 25)
    _stub(monkeypatch, [_fh(today + timedelta(days=3), "amc")], [])
    assert ec.last_print("KLAC", as_of=today) is None


def test_next_print_is_strictly_future(monkeypatch):
    today = date(2026, 7, 25)
    _stub(monkeypatch, [_fh(date(2026, 7, 22), "amc", eps_actual=9.11),
                        _fh(date(2026, 10, 27), "amc", q=3)], [])
    nxt = ec.next_print("GOOG", as_of=today)
    assert nxt is not None and nxt.date == date(2026, 10, 27)
