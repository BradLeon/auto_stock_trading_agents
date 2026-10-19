"""Parent 7.5: the Fundamental projections carry what the legacy PEAD report carried (2026-10-19)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from ats.agent.task_projection import (FundamentalEventReviewPayload, FundamentalExpectationUpdatePayload,
                                       build_envelope, ProjectionScope)
from ats.agents.fundamental import event as fundamental_event
from ats.agents.fundamental import routine
from ats.agents.fundamental.baseline_view import baseline_detail
from ats.schemas.pead import (ActualMetric, Actuals, Expectation, ExpectationSet, FundamentalBackground,
                              MarketSetup, PeadDossier, SignalChainItem)

NOW = datetime(2026, 10, 14, 14, 30, tzinfo=timezone.utc)


def _dossier() -> PeadDossier:
    es = ExpectationSet(
        symbol="TSM", fiscal_label="Q3 FY2026", as_of=NOW, narrative="AI demand keeps pricing power" * 3,
        focus_ranking=["gross margin", "CoWoS capacity"],
        expectations=[Expectation(dim_key="gross_margin", metric="GM", conservative="<65%", neutral="~67%",
                                  optimistic=">68.5%", source="mgmt guide"),
                      Expectation(dim_key="revenue", metric="Rev", neutral="NT$1.45T")],
        consensus_eps=4.45, consensus_revenue=1.4545e12, consensus_target_price=552.4,
        consensus_rating_summary="强买5/买12", consensus_recent_actions=["2026-10-10 MS → Overweight"],
        valuation="fwd PE 22")
    return PeadDossier(
        symbol="TSM", fiscal_label="Q3 FY2026", updated_at=NOW, expectation_set=es,
        fundamental_background=FundamentalBackground(
            background="1. moat", peer_comparison="| a | b |", watch_metrics="GM, capex",
            catalysts=["10-16 print"], key_risks=["N2 yield slips", "export controls"], valuation="ceiling 600"),
        market_setup=MarketSetup(symbol="TSM", as_of=NOW, pre_earnings_close=300.0, expected_move_pct=3.83,
                                 atm_iv=0.41, iv_skew=-2.0, dist_to_ath_pct=-4.0, notes=["opt src yf"]),
        signal_chain=[SignalChainItem(symbol="ASML", role="upstream", reported=True, price_chg_pct=2.1,
                                      signal="orders strong")],
        signal_chain_summary="net supportive", scorecard_weights={"gross_margin": 0.2, "revenue": 0.2})


def test_the_baseline_detail_keeps_every_section_the_legacy_report_had():
    detail = baseline_detail(_dossier(), provenance="relabelled from Q2 FY2026")
    assert detail["built_at"] == "2026-10-14T14:30:00+00:00"
    assert detail["core_narrative"] == "1. moat" and detail["peer_comparison"] == "| a | b |"
    assert detail["focus_ranking"] == ["gross margin", "CoWoS capacity"]
    assert detail["catalysts"] == ["10-16 print"] and detail["key_risks"] == ["N2 yield slips", "export controls"]
    assert detail["falsifiable_conditions"] == detail["key_risks"]
    rows = {r["dim_key"]: r for r in detail["expectation_table"]}
    assert rows["gross_margin"]["weight"] == 0.2 and rows["gross_margin"]["optimistic"] == ">68.5%"
    assert detail["consensus"]["eps"] == 4.45 and detail["consensus"]["target_price"] == 552.4
    assert detail["market_implied"]["expected_move_pct"] == 3.83 and detail["market_implied"]["atm_iv"] == 0.41
    assert detail["signal_chain"][0]["symbol"] == "ASML" and detail["signal_chain_summary"] == "net supportive"
    assert detail["valuation"] == "fwd PE 22" and detail["provenance"] == "relabelled from Q2 FY2026"


def test_long_text_is_clipped_so_a_baseline_stays_a_card():
    dossier = _dossier()
    dossier.fundamental_background.peer_comparison = "x" * 9000
    assert len(baseline_detail(dossier)["peer_comparison"]) <= 1800


def test_the_detail_validates_as_a_payload_and_an_old_payload_hashes_as_before():
    plain = {"entity": "TSM", "metric": "narrative", "period": "Q3 FY2026", "previous_value": None,
             "new_value": None, "driver": "[基线 v1] x"}
    before = FundamentalExpectationUpdatePayload(**plain).payload_json()
    assert "baseline" not in before and "change_class" not in before
    rich = FundamentalExpectationUpdatePayload(**plain, baseline=baseline_detail(_dossier()))
    assert json.loads(rich.payload_json())["baseline"]["consensus"]["eps"] == 4.45
    assert before != rich.payload_json()


def test_a_classified_fact_keeps_its_class_kpi_reason_and_evidence():
    baseline = {"symbol": "TSM", "fiscal_label": "Q3 FY2026", "metrics": {"gross_margin": 67.0},
                "kpi_words": {"gross margin"}}
    drafts = routine.build_expectation_updates(
        ["gross_margin 67.4% in the quarter"], ["确认"], baseline, reasons=["within 1% of base"],
        evidence_refs=["brief-1"])
    draft = drafts[0]
    assert draft["change_class"] == "确认" and draft["kpi"] == "gross_margin"
    assert draft["explanation"] == "within 1% of base" and draft["evidence_refs"] == ["brief-1"]
    FundamentalExpectationUpdatePayload(**draft)          # and it is publishable


def test_the_event_review_carries_what_was_reported_and_what_it_was_compared_with():
    actuals = Actuals(symbol="ASML", fiscal_label="Q3 FY2026", as_of=NOW, reported_eps=12.95,
                      reported_revenue=11.7e9, guidance="Q4 €11-12B",
                      metrics=[ActualMetric(dim_key="revenue", metric="Net sales", actual="EUR 11.7B",
                                            vs_expected="🔴", note="record")],
                      transcript_signals=["tone confident"], transcript_source="fool.com")
    payload = fundamental_event.build_event_review_payload(
        symbol="ASML", period="Q3 FY2026", event="earnings", scorecard=None,
        event_view={"direction": 1, "magnitude": 0.5, "confidence": 0.7, "rationale": "beat"},
        tri_diffs={"vs_baseline": [{"dim": "revenue"}], "vs_consensus": []}, actuals=actuals,
        baseline_ref="proj-1", baseline_as_of="2026-10-13T15:18:00+00:00")
    parsed = FundamentalEventReviewPayload(**payload)
    assert parsed.comparison_basis == "frozen_baseline" and parsed.baseline_ref == "proj-1"
    assert parsed.actuals.reported_eps == 12.95 and parsed.actuals.metrics[0].actual == "EUR 11.7B"
    assert parsed.actuals.transcript_signals == ["tone confident"]


def test_with_nothing_to_compare_the_basis_is_none():
    payload = fundamental_event.build_event_review_payload(
        symbol="TSM", period="Q3 FY2026", event="earnings", scorecard=None,
        event_view={"direction": 0, "magnitude": 0.0}, tri_diffs={}, actuals=None)
    assert payload["comparison_basis"] == "none" and payload["actuals"] is None
    assert "baseline_ref" not in payload
