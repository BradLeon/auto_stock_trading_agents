"""Consensus period/freshness alignment for PEAD prep (found 2026-10-16: TSM Q2 baseline carried the
Q3 consensus and Q2 revenue scored as a 12% miss)."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ats.data import consensus as ca

AS_OF = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)
FULL = {"eps": 4.45, "eps_low": 4.3, "eps_high": 4.6, "revenue": 1.454e12, "revenue_low": 1.4e12,
        "revenue_high": 1.5e12, "target_mean": 410.0, "rating_buy": 20}


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    monkeypatch.setattr(ca, "alignment_settings", lambda: (14, 10))


def test_usable_consensus_passes_through_unchanged():
    out, why = ca.align(FULL, {"target_event_date": "2026-11-17", "known_at": "2026-10-10T00:00:00+00:00"},
                        as_of=AS_OF)
    assert why == "" and out == FULL


def test_event_already_passed_is_the_tsm_case():
    out, why = ca.align(FULL, {"target_event_date": "2026-10-15", "known_at": "2026-10-15T20:00:00+00:00"},
                        as_of=AS_OF)
    assert why.startswith("event_already_passed")
    assert all(out[k] is None for k in ("eps", "eps_low", "eps_high", "revenue", "revenue_low", "revenue_high"))
    assert out["unavailable_reason"] == why
    assert out["target_mean"] == 410.0 and out["rating_buy"] == 20      # not period-specific


def test_same_day_print_is_still_usable():
    out, why = ca.align(FULL, {"target_event_date": "2026-10-16", "known_at": "2026-10-16T01:00:00+00:00"},
                        as_of=AS_OF)
    assert why == "" and out["revenue"] == 1.454e12


def test_stale_snapshot_is_ignored_even_with_a_future_event():
    out, why = ca.align(FULL, {"target_event_date": "2026-11-18", "known_at": "2026-09-04T00:35:15+00:00"},
                        as_of=AS_OF)
    assert why.startswith("stale") and out["eps"] is None


def test_expected_event_date_mismatch_is_caught_when_the_caller_knows_it():
    out, why = ca.align(FULL, {"target_event_date": "2026-10-29", "known_at": "2026-10-15T00:00:00+00:00"},
                        as_of=AS_OF, expected_event_date="2027-01-13")
    assert why.startswith("event_date_mismatch") and out["revenue"] is None
    out, why = ca.align(FULL, {"target_event_date": "2026-10-29", "known_at": "2026-10-15T00:00:00+00:00"},
                        as_of=AS_OF, expected_event_date="2026-11-02")
    assert why == ""                                                    # within tolerance


def test_missing_metadata_cannot_be_verified_so_it_is_kept():
    out, why = ca.align(FULL, {}, as_of=AS_OF)
    assert why == "" and out == FULL


def test_nothing_to_blank_returns_empty_reason():
    out, why = ca.align({"eps": None, "revenue": None, "target_mean": 1.0},
                        {"target_event_date": "2026-01-01"}, as_of=AS_OF)
    assert why == "" and "unavailable_reason" not in out


def test_naive_as_of_is_treated_as_utc():
    _, why = ca.align(FULL, {"target_event_date": "2026-10-15"}, as_of=datetime(2026, 10, 16, 3, 0))
    assert why.startswith("event_already_passed")


def test_prompt_tells_the_model_not_to_invent_missing_consensus():
    from ats.agents.pead.prep import _consensus_text

    blanked, _ = ca.align(FULL, {"target_event_date": "2026-10-15"}, as_of=AS_OF)
    text = _consensus_text(blanked)
    assert "UNAVAILABLE" in text and "event_already_passed" in text
    assert "UNAVAILABLE" not in _consensus_text(FULL)


def test_governed_prep_ignores_the_wrong_quarter_consensus(monkeypatch):
    from ats.graph import pead

    governed = {"fundamentals_text": "{}", "industry_context": "", "input_refs": ["r"],
                "consensus": dict(FULL), "consensus_meta": {"target_event_date": "2026-10-15",
                                                            "known_at": "2026-10-15T20:00:00+00:00"}}
    monkeypatch.setattr("ats.agents.fundamental.inputs.governed_company_inputs", lambda symbol: governed)
    state = SimpleNamespace(symbol="TSM", fiscal_label="Q4 FY2026", as_of=AS_OF, live_data=True,
                            config=SimpleNamespace(), information_facts=[])
    out = pead.prep_fetch(state)
    assert out["consensus"]["revenue"] is None and out["consensus_note"].startswith("event_already_passed")
    ok = dict(governed, consensus_meta={"target_event_date": "2027-01-13",
                                        "known_at": "2026-10-15T20:00:00+00:00"})
    monkeypatch.setattr("ats.agents.fundamental.inputs.governed_company_inputs", lambda symbol: ok)
    out = pead.prep_fetch(state)
    assert out["consensus"]["revenue"] == 1.454e12 and out["consensus_note"] == ""


def test_expectation_set_records_why_consensus_is_blank():
    from ats.schemas.pead import ExpectationSet

    es = ExpectationSet(symbol="TSM", as_of=AS_OF, consensus_note="event_already_passed: x")
    assert es.consensus_note and es.consensus_revenue is None
    assert ExpectationSet(symbol="TSM", as_of=AS_OF).consensus_note == ""


def test_event_review_skips_baseline_dimensions_without_numbers():
    """Without consensus the baseline matrix carries no numeric neutral, so no false miss is computed."""
    from ats.agents.fundamental.event import compute_tri_diffs

    baseline = {"expectations": [{"dim_key": "revenue", "neutral": "营收与指引一致，无共识可比", "metric": "revenue"}]}
    actuals = SimpleNamespace(metrics=[SimpleNamespace(dim_key="revenue", actual="1.270")],
                              reported_eps=None, reported_revenue=1.27e12)
    es = SimpleNamespace(consensus_eps=None, consensus_revenue=None)
    tri = compute_tri_diffs(frozen_baseline=baseline, expectation_set=es, actuals=actuals, market_setup=None)
    assert tri["vs_baseline"] == [] and tri["vs_consensus"] == {} and tri["directions_agree"]
