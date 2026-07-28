"""Stage B3 — weekly invalidation-trigger check.

The one property worth over-testing here: the LLM must never see anything
P&L-shaped. Everything else (gating, horizon_overdue_days arithmetic, the
degrade-on-failure path) is ordinary plumbing.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ats.journal import invalidation as inv
from ats.journal.outputs import InvalidationView
from ats.memory import get_store
from ats.schemas.journal import EpisodeCard, JournalEntry, TradeEpisode

NOW = datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    return get_store()


def _episode(**kw):
    base = dict(episode_id="e1", symbol="GOOG", direction="long", status="open",
               origin="system", basis_source="observed_fills",
               opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
               primary_entry_id="c1:GOOG:open", avg_entry=100.0)
    return TradeEpisode(**{**base, **kw})


def _entry(**kw):
    base = dict(entry_id="c1:GOOG:open", cycle_id="c1", as_of=NOW, symbol="GOOG",
               action="buy", setup="pead_event", invalidation="")
    return JournalEntry(**{**base, **kw})


def _spy(monkeypatch, view):
    captured: dict = {}

    def fake(role, schema, context, *, skill_slug=None):
        captured["role"] = role
        captured["schema"] = schema
        captured["context"] = context
        captured["skill_slug"] = skill_slug
        return view

    monkeypatch.setattr(inv, "run_structured", fake)
    return captured


# --------------------------------------------------------------------------- #
# the blind-context guarantee — the property this stage exists to enforce
# --------------------------------------------------------------------------- #
def test_assert_blind_catches_a_leaked_outcome_field(store):
    ep = _episode(realized_pnl=1234.56)
    card = EpisodeCard(episode=ep, plan=None, legs=[])   # NOT blinded
    with pytest.raises(AssertionError):
        inv._assert_blind(card)


def test_assert_blind_passes_a_properly_blinded_card(store):
    ep = _episode(realized_pnl=1234.56, r_multiple=2.0, mae_pct=-5.0)
    card = EpisodeCard(episode=ep, plan=None, legs=[]).blind()
    inv._assert_blind(card)   # must not raise


def test_llm_context_never_contains_the_realized_pnl_figure(store, monkeypatch):
    """The single most important regression this file guards against: an episode
    that DOES have a real P&L must still produce a context string with no trace of
    it, and the invalidation text (the one thing that should be there) must survive."""
    store.save_journal_entry(_entry(invalidation="若管理层撤回全年指引"))
    ep = _episode(realized_pnl=98765.43, unrealized_pnl=555.0, avg_exit=142.0)
    store.save_episode(ep)
    captured = _spy(monkeypatch, InvalidationView(triggered=False))

    inv.check_all(store=store, as_of=date(2026, 7, 23))

    ctx = captured["context"]
    assert "98765.43" not in ctx and "98765" not in ctx
    assert "555.0" not in ctx and "142.0" not in ctx
    assert "若管理层撤回全年指引" in ctx
    assert captured["skill_slug"] == "journal-invalidation"


# --------------------------------------------------------------------------- #
# gating
# --------------------------------------------------------------------------- #
def test_pre_tracking_episode_is_skipped_without_calling_the_llm(store, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not be called for a pre_tracking episode")

    monkeypatch.setattr(inv, "run_structured", boom)
    ep = _episode(basis_source="ibkr_avg_cost", origin="pre_tracking", primary_entry_id="")
    store.save_episode(ep)
    s = inv.check_all(store=store)
    assert s == {"checked": 0, "triggered": 0, "skipped": 1, "llm_failed": 0}


def test_no_plan_episode_is_skipped(store, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not be called without a plan")

    monkeypatch.setattr(inv, "run_structured", boom)
    ep = _episode(primary_entry_id="")
    store.save_episode(ep)
    s = inv.check_all(store=store)
    assert s["skipped"] == 1 and s["checked"] == 0


def test_no_invalidation_text_is_skipped_but_horizon_still_computed(store, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not be called without invalidation text")

    monkeypatch.setattr(inv, "run_structured", boom)
    store.save_journal_entry(_entry(invalidation="", planned_horizon_days=10))
    ep = _episode(opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    store.save_episode(ep)
    s = inv.check_all(store=store, as_of=date(2026, 7, 15))
    assert s["skipped"] == 1
    got = store.get_episode("e1")
    assert got.horizon_overdue_days == 4    # 14 days held - 10 planned


# --------------------------------------------------------------------------- #
# horizon_overdue_days arithmetic
# --------------------------------------------------------------------------- #
def test_horizon_overdue_is_clamped_at_zero_when_not_yet_due(store, monkeypatch):
    monkeypatch.setattr(inv, "run_structured", lambda *a, **k: InvalidationView(triggered=False))
    store.save_journal_entry(_entry(invalidation="x", planned_horizon_days=30))
    store.save_episode(_episode(opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc)))
    inv.check_all(store=store, as_of=date(2026, 7, 10))
    assert store.get_episode("e1").horizon_overdue_days == 0


def test_no_planned_horizon_leaves_overdue_none(store, monkeypatch):
    monkeypatch.setattr(inv, "run_structured", lambda *a, **k: InvalidationView(triggered=False))
    store.save_journal_entry(_entry(invalidation="x", planned_horizon_days=None))
    store.save_episode(_episode())
    inv.check_all(store=store, as_of=date(2026, 7, 15))
    assert store.get_episode("e1").horizon_overdue_days is None


# --------------------------------------------------------------------------- #
# use_llm=False — deterministic pass only
# --------------------------------------------------------------------------- #
def test_use_llm_false_never_calls_the_model(store, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not be called when use_llm=False")

    monkeypatch.setattr(inv, "run_structured", boom)
    store.save_journal_entry(_entry(invalidation="x", planned_horizon_days=5))
    store.save_episode(_episode(opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc)))
    s = inv.check_all(store=store, use_llm=False, as_of=date(2026, 7, 20))
    assert s["checked"] == 0
    got = store.get_episode("e1")
    assert got.horizon_overdue_days == 14   # 19 days held - 5 planned
    assert got.invalidation_triggered is None   # left alone, never guessed


# --------------------------------------------------------------------------- #
# the LLM verdict path
# --------------------------------------------------------------------------- #
def test_triggered_verdict_is_persisted(store, monkeypatch):
    monkeypatch.setattr(inv, "run_structured",
                        lambda *a, **k: InvalidationView(triggered=True, evidence="指引撤回"))
    store.save_journal_entry(_entry(invalidation="若撤回全年指引"))
    store.save_episode(_episode())
    s = inv.check_all(store=store)
    assert s == {"checked": 1, "triggered": 1, "skipped": 0, "llm_failed": 0}
    assert store.get_episode("e1").invalidation_triggered is True


def test_not_triggered_verdict_is_persisted(store, monkeypatch):
    monkeypatch.setattr(inv, "run_structured", lambda *a, **k: InvalidationView(triggered=False))
    store.save_journal_entry(_entry(invalidation="若撤回全年指引"))
    store.save_episode(_episode())
    inv.check_all(store=store)
    assert store.get_episode("e1").invalidation_triggered is False


def test_llm_failure_degrades_without_guessing(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(inv, "run_structured", boom)
    store.save_journal_entry(_entry(invalidation="若撤回全年指引", planned_horizon_days=10))
    store.save_episode(_episode(opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc)))
    s = inv.check_all(store=store, as_of=date(2026, 7, 15))
    assert s["llm_failed"] == 1 and s["checked"] == 0
    got = store.get_episode("e1")
    assert got.invalidation_triggered is None   # never guessed on failure
    assert got.horizon_overdue_days == 4        # deterministic part still lands


# --------------------------------------------------------------------------- #
# period events/insights: date filtering
# --------------------------------------------------------------------------- #
def test_period_events_excludes_before_open_and_sorts_ascending(store):
    store.conn.execute(
        "INSERT INTO pead_events (id,symbol,published_at,source,headline,url) "
        "VALUES (?,?,?,?,?,?)",
        ("ev1", "GOOG", "2026-06-15T00:00:00+00:00", "news", "before open", ""))
    store.conn.execute(
        "INSERT INTO pead_events (id,symbol,published_at,source,headline,url) "
        "VALUES (?,?,?,?,?,?)",
        ("ev2", "GOOG", "2026-07-10T00:00:00+00:00", "news", "later", ""))
    store.conn.execute(
        "INSERT INTO pead_events (id,symbol,published_at,source,headline,url) "
        "VALUES (?,?,?,?,?,?)",
        ("ev3", "GOOG", "2026-07-02T00:00:00+00:00", "news", "earlier", ""))
    picked = inv._period_events(store, "GOOG", date(2026, 7, 1))
    assert [e["headline"] for e in picked] == ["earlier", "later"]


def test_period_insights_excludes_before_open(store):
    store.conn.execute(
        "INSERT INTO research_insights VALUES (?,?,?,?,?,?,?,?)",
        ("a1", "GOOG", "bullish", "direct", "before open", "", 0.8, "2026-06-01T00:00:00+00:00"))
    store.conn.execute(
        "INSERT INTO research_insights VALUES (?,?,?,?,?,?,?,?)",
        ("a2", "GOOG", "bullish", "direct", "in window", "", 0.8, "2026-07-10T00:00:00+00:00"))
    picked = inv._period_insights(store, "GOOG", date(2026, 7, 1))
    assert [i["summary"] for i in picked] == ["in window"]
