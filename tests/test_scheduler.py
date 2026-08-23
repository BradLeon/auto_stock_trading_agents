"""Phase 10: scheduler — NYSE session gating (offline calendar data)."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from ats.runtime import scheduler

ASIA = ZoneInfo("Asia/Shanghai")


def _freeze(monkeypatch, moment: datetime, machine_tz=ASIA):
    """Pin wall-clock time, keeping tz conversion real.

    `now(None)` returns the naive MACHINE-local time (default: Asia, matching the
    box this runs on) — that is what the old `datetime.now().date()` saw, so these
    tests fail against the pre-fix behaviour rather than passing vacuously.
    """
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return moment.astimezone(machine_tz).replace(tzinfo=None)
            return moment.astimezone(tz)

    monkeypatch.setattr(scheduler, "datetime", Frozen)


def test_today_is_the_market_date_not_the_machine_date(monkeypatch):
    """20:00 ET Monday is already Tuesday 08:00 in Asia.

    The scheduler must call it Monday: this date drives the NYSE session check, the
    events calendar and the Monday macro/sector gates. Using the naive local date
    (the old behaviour) shifted all of them a day whenever the job fired after
    ~12:00 ET — which is exactly when the amc score window runs.
    """
    monday_evening_et = datetime(2026, 7, 27, 20, 0, tzinfo=scheduler.ET)
    _freeze(monkeypatch, monday_evening_et)

    assert monday_evening_et.astimezone(ASIA).date() == date(2026, 7, 28)  # premise
    assert scheduler._today_et() == date(2026, 7, 27)
    assert scheduler._today_et().weekday() == 0                            # still Monday


def test_today_agrees_with_local_date_at_the_morning_window(monkeypatch):
    """11:00 ET (the bmo window) is 23:00 the same day in Asia — no shift either way."""
    _freeze(monkeypatch, datetime(2026, 7, 27, 11, 0, tzinfo=scheduler.ET))
    assert scheduler._today_et() == date(2026, 7, 27)


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


def test_yahoo_news_is_backfilled_once_for_the_shared_universe(monkeypatch):
    from ats import config
    from ats.data import yahoo_news

    calls = []
    monkeypatch.setattr(config, "load_news_sources", lambda: {
        "yahoo_news": {"enabled": True, "backfill_days": 7,
                       "stale_after_hours": 72}})
    monkeypatch.setattr(config, "load_pead_global", lambda: {
        "targets": ["AMD"], "observe": ["TSM"], "monitor": {"lookback_days": 7}})
    monkeypatch.setattr(config, "load_pead_config", lambda _symbol: SimpleNamespace(
        signal_chain=[SimpleNamespace(symbol="NVDA"), SimpleNamespace(symbol="TSM")]))
    monkeypatch.setattr(yahoo_news, "backfill", lambda symbols, *a, **k:
                        calls.append(symbols) or yahoo_news.YahooNewsBatch(
                            (), "zero_matches"))

    scheduler._news_backfill_daily()

    assert calls == [["AMD", "TSM", "NVDA"]]
