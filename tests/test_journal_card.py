"""EpisodeCard construction: episode joined with its opening plan, its legs' own
intents, and (when requested) the predictions tied to those intents via entry_id.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ats.journal import card as card_mod
from ats.journal import prices
from ats.memory import get_store
from ats.schemas.journal import JournalEntry, TradeEpisode
from ats.schemas.pead import Scorecard

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


@pytest.fixture
def store():
    return get_store()


@pytest.fixture(autouse=True)
def _no_network_prices(monkeypatch):
    """record_pead_prediction fetches price bars on a cache miss — degrades quietly
    to an empty history, which is all these join-by-entry_id tests need."""
    monkeypatch.setattr(prices, "bars", lambda symbol: [])


def _episode(**kw):
    base = dict(episode_id="e1", symbol="GOOG", direction="long", status="open",
               origin="system", basis_source="observed_fills",
               opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
               primary_entry_id="c1:GOOG:open")
    return TradeEpisode(**{**base, **kw})


def _entry(**kw):
    base = dict(entry_id="c1:GOOG:open", cycle_id="c1", as_of=NOW, symbol="GOOG",
               action="buy", setup="pead_event")
    return JournalEntry(**{**base, **kw})


def test_build_card_collects_distinct_legs_in_order(store):
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, episode_id, "
        "entry_id) VALUES (?,?,?,?,?,?,?,?)",
        ("f1", "GOOG", "BOT", 10, 100.0, "2026-07-01T10:00:00+00:00", "e1", "c1:GOOG:open"))
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, episode_id, "
        "entry_id) VALUES (?,?,?,?,?,?,?,?)",
        ("f2", "GOOG", "BOT", 5, 110.0, "2026-07-05T10:00:00+00:00", "e1", "c2:GOOG:add"))
    store.save_journal_entry(_entry(entry_id="c1:GOOG:open", action="buy"))
    store.save_journal_entry(_entry(entry_id="c2:GOOG:add", action="add"))
    card = card_mod.build_card(store, _episode(), with_predictions=False)
    assert [leg.entry_id for leg in card.legs] == ["c1:GOOG:open", "c2:GOOG:add"]


def test_build_card_skips_legs_with_no_linked_entry(store):
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, episode_id, "
        "entry_id) VALUES (?,?,?,?,?,?,?,?)",
        ("f1", "GOOG", "BOT", 10, 100.0, "2026-07-01T10:00:00+00:00", "e1", None))
    card = card_mod.build_card(store, _episode(), with_predictions=False)
    assert card.legs == []


def test_no_primary_entry_id_means_no_plan(store):
    card = card_mod.build_card(store, _episode(primary_entry_id=""), with_predictions=False)
    assert card.plan is None


def test_with_predictions_false_skips_the_lookup_entirely(store, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("predictions_for_entries must not be called")

    monkeypatch.setattr(store, "predictions_for_entries", boom)
    card = card_mod.build_card(store, _episode(), with_predictions=False)
    assert card.predictions == []


def test_with_predictions_true_joins_by_entry_id(store):
    """An episode has no fiscal_label of its own — predictions are found by tracing
    back to the entry_id that actually placed the trade, not by symbol+date guessing."""
    store.save_journal_entry(_entry())
    sc = Scorecard(symbol="GOOG", fiscal_label="Q2 FY2026", as_of=NOW, lines=[],
                   total=1.5, threshold=1.2, band="达到做多门槛")
    from ats.journal import predictions as jp

    ids = jp.record_pead_prediction(store=store, symbol="GOOG", fiscal_label="Q2 FY2026",
                                    scorecard=sc, scored_at=NOW, entry_id="c1:GOOG:open")
    assert ids
    card = card_mod.build_card(store, _episode(), with_predictions=True)
    assert len(card.predictions) == 1
    pred, outcomes = card.predictions[0]
    assert pred.source == "pead_score" and pred.entry_id == "c1:GOOG:open"
    assert outcomes == []   # not yet scored


def test_with_predictions_ignores_other_symbols_entries(store):
    """A prediction whose entry_id isn't one of THIS episode's legs must not leak in —
    entry_id is the only join key, never a bare symbol match."""
    store.save_journal_entry(_entry())
    store.save_journal_entry(_entry(entry_id="c9:GOOG:unrelated"))
    sc = Scorecard(symbol="GOOG", fiscal_label="Q1 FY2026", as_of=NOW, lines=[],
                   total=0.5, threshold=1.2, band="中性观望")
    from ats.journal import predictions as jp

    jp.record_pead_prediction(store=store, symbol="GOOG", fiscal_label="Q1 FY2026",
                              scorecard=sc, scored_at=NOW, entry_id="c9:GOOG:unrelated")
    card = card_mod.build_card(store, _episode(), with_predictions=True)
    assert card.predictions == []
