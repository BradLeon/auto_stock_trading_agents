"""Found on TSM/ASML Q3 (2026-10-17): the Dispatcher's event review was `complete` but empty."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ats.agents.fundamental import event
from ats.schemas.pead import Scorecard, ScorecardLine


def _scorecard():
    from datetime import datetime, timezone

    return Scorecard(symbol="TSM", as_of=datetime.now(timezone.utc),
                     lines=[ScorecardLine(dim_key="revenue", label="rev", weight=1.0, score=0.0, weighted=0.0, note="无信息")],
                     total=0.0, threshold=1.2, band="未达门槛")


def _payload(tri, confidence=0.6):
    return event.build_event_review_payload(
        symbol="TSM", period="Q3 FY2026", event="earnings", scorecard=_scorecard(),
        event_view={"direction": 0, "magnitude": 0.0, "rationale": "预期差中性", "confidence": confidence,
                    "falsifiable_conditions": []},
        tri_diffs=tri, actuals=SimpleNamespace(guidance="g"))


def test_no_baseline_and_no_consensus_is_published_as_a_gap_not_as_in_line_with_expectations():
    payload = _payload({"vs_baseline": [], "vs_consensus": {}, "vs_market": {}})
    assert payload["notes"].startswith(event.NO_COMPARISON_BASIS)
    assert payload["confidence"] <= event.NO_BASIS_CONFIDENCE
    assert payload["guidance"] == "g"                       # the extracted guidance is still delivered


def test_a_review_with_a_comparison_basis_is_untouched():
    payload = _payload({"vs_baseline": [{"dim_key": "revenue", "delta": 0.02}], "vs_consensus": {}})
    assert payload["notes"] == "预期差中性" and payload["confidence"] == 0.6
    payload = _payload({"vs_baseline": [], "vs_consensus": {"eps": {"delta_pct": 0.03}}})
    assert payload["confidence"] == 0.6


def test_a_missing_confidence_still_gets_the_low_cap():
    assert _payload({}, confidence=None)["confidence"] == event.NO_BASIS_CONFIDENCE


@pytest.mark.parametrize("use_llm,expected", [(True, True), (False, False)])
def test_a_model_backed_event_review_reads_live_documents(monkeypatch, use_llm, expected):
    """live_data used to default to False, which left the graph offline: no transcript, no release."""
    from ats.agents.fundamental.entry import event_request
    import ats.graph.pead as pead_graph

    seen = {}

    class App:
        def invoke(self, state, config=None):
            seen["live_data"] = state.live_data
            return {}

    monkeypatch.setattr(pead_graph, "build_pead_graph", lambda checkpointer=None: App())
    monkeypatch.setattr(event, "freeze_baseline", lambda *a, **k: {"frozen_at": "t"})
    request = event_request("TSM", trigger="earnings_release", fiscal_label="Q3 FY2026", use_llm=use_llm)
    try:
        event.run_event_pass.__wrapped__(request)
    except Exception:  # noqa: BLE001 - only the state handed to the graph matters here
        pass
    assert seen.get("live_data") is expected


def test_an_explicit_live_data_flag_still_wins():
    from ats.agents.fundamental.entry import event_request

    request = event_request("TSM", trigger="earnings_release", fiscal_label="Q3 FY2026", use_llm=True,
                            extra={"live_data": False})
    assert request.extra["live_data"] is False
