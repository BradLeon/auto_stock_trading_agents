"""Phase 10: scheduler — NYSE session gating (offline calendar data)."""

from datetime import date

from ats.runtime import scheduler


def test_session_on_weekday():
    assert scheduler.is_trading_session(date(2026, 6, 15)) is True  # Monday


def test_no_session_on_weekend():
    assert scheduler.is_trading_session(date(2026, 6, 13)) is False  # Saturday


def test_no_session_on_holiday():
    assert scheduler.is_trading_session(date(2026, 1, 1)) is False   # New Year's Day


def test_chief_daily_skips_when_not_a_session(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_trading_session", lambda *a, **k: False)
    monkeypatch.setattr("ats.runtime.cli.run_chief", lambda **kw: calls.append(kw))
    scheduler._chief_daily(dry_run=True)
    assert calls == []


def test_chief_daily_runs_with_scheduled_source(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_trading_session", lambda *a, **k: True)
    monkeypatch.setattr("ats.runtime.cli.run_chief", lambda **kw: calls.append(kw))
    scheduler._chief_daily(dry_run=True)
    assert len(calls) == 1
    assert calls[0]["source"] == "scheduled" and calls[0]["dry_run"] is True
