"""One quarter, one dossier: the calendar's `FY2027Q1` and the prep path's `Q1 FY2027` are the same quarter.

Found 2026-10-26 (G0 E02): LRCX, TSM and ASML each had a prep dossier under `Q1 FY2027` / `Q3 FY2026` and an empty
score dossier under `FY2027Q1` / `FY2026Q3`. The event review ran on the empty one, so its report sections one and two
were blank and its actuals were keyed to made-up dimensions.
"""

from datetime import datetime, timezone

import pytest

from ats.agents.fundamental.entry import event_request
from ats.data import period
from ats.memory import get_store
from ats.schemas.pead import ExpectationSet, PeadDossier

CASES = [("LRCX", "FY2027Q1", "Q1 FY2027"), ("TSM", "FY2026Q3", "Q3 FY2026"), ("ASML", "FY2026Q3", "Q3 FY2026")]
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def _dossier(symbol, label, phase, *, narrative=""):
    es = ExpectationSet(symbol=symbol, as_of=NOW, narrative=narrative) if narrative else None
    return PeadDossier(symbol=symbol, fiscal_label=label, phase=phase, updated_at=NOW, expectation_set=es)


@pytest.mark.parametrize("symbol,calendar_label,stored", CASES)
def test_the_calendar_spelling_resolves_to_the_stored_dossier(symbol, calendar_label, stored):
    store = get_store()
    store.save_dossier(_dossier(symbol, stored, "prep", narrative="财报前叙事"))
    assert period.dossier_label(symbol, calendar_label, store=store) == stored
    assert period.dossier_label(symbol, stored, store=store) == stored


@pytest.mark.parametrize("symbol,calendar_label,stored", CASES)
def test_an_event_request_starts_from_the_prep_dossier(symbol, calendar_label, stored):
    get_store().save_dossier(_dossier(symbol, stored, "prep", narrative="财报前叙事"))
    request = event_request(symbol, trigger="earnings_release", fiscal_label=calendar_label)
    assert request.fiscal_label == stored
    assert get_store().get_dossier(symbol, request.fiscal_label).expectation_set.narrative == "财报前叙事"


@pytest.mark.parametrize("symbol,calendar_label,stored", CASES)
def test_a_new_quarter_gets_the_canonical_spelling(symbol, calendar_label, stored):
    assert period.dossier_label(symbol, calendar_label, store=get_store()) == stored


@pytest.mark.parametrize("symbol,calendar_label,stored", CASES)
def test_merge_keeps_one_dossier_and_every_score_run(symbol, calendar_label, stored):
    store = get_store()
    store.save_dossier(_dossier(symbol, stored, "prep", narrative="财报前叙事"))
    store.save_dossier(_dossier(symbol, calendar_label, "score"))
    store.record_score_run(symbol=symbol, fiscal_label=stored, version=1, earnings_date="2026-10-21",
                           has_transcript=True, transcript_source="x", total=0.5, band="b", decision_summary="s")
    store.record_score_run(symbol=symbol, fiscal_label=calendar_label, version=1, earnings_date="2026-10-21",
                           has_transcript=True, transcript_source="x", total=0.0, band="b", decision_summary="s")

    assert period.merge_dossier_aliases(store, symbol) == [(calendar_label, stored)]

    labels = [row["fiscal_label"] for row in store.recent_dossiers(symbol, limit=20)]
    assert labels == [stored]
    assert store.get_dossier(symbol, stored).expectation_set.narrative == "财报前叙事"   # the prep content survives
    assert store.latest_score_run(symbol, stored)["version"] == 2                      # both runs kept
    assert store.latest_score_run(symbol, calendar_label) is None
    assert period.merge_dossier_aliases(store, symbol) == []                           # idempotent


def test_a_stray_alone_is_renamed_not_dropped():
    store = get_store()
    store.save_dossier(_dossier("LRCX", "FY2027Q1", "score", narrative="只有这一份"))
    period.merge_dossier_aliases(store, "LRCX")
    assert store.get_dossier("LRCX", "Q1 FY2027").fiscal_label == "Q1 FY2027"
    assert store.get_dossier("LRCX", "Q1 FY2027").expectation_set.narrative == "只有这一份"
