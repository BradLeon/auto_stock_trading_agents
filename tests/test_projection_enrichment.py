"""ARCHITECTURE §7.3 projection enrichment (2026-10-17): additive fields, hash stability, and what each role now carries."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ats.agent import task_projection as tp
from ats.schemas.chain import ClaimAssessment, EntityReading
from ats.schemas.sector import BasketRow, LayerBasket, LayerNameCall, LayerVerdict

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Additive and hash-stable
# --------------------------------------------------------------------------- #
def test_a_payload_that_does_not_use_the_new_fields_hashes_as_before():
    old = {"entity": "NVDA", "levels": {"close": 1.0}, "schema_name": "TechnicalReview",
           "schema_version": "v1", "signal": "bullish", "summary": "s"}
    new = tp.TechnicalReviewPayload(entity="NVDA", signal="bullish", summary="s", levels={"close": 1.0})
    assert new.payload_json() == json.dumps(old, ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize("payload", [
    tp.LayerAnalysisPayload(layer="L1", status="steady", summary="s", findings=["f"], confidence=0.5),
    tp.SectorAllocationPayload(sector="x", stance="neutral", target_weight=0.1, rationale="r"),
    tp.MacroReviewPayload(regime="unclear", summary="s", indicators=["i"]),
])
def test_old_payloads_dump_only_their_original_fields(payload):
    new_fields = set(type(payload)._OMIT_WHEN_EMPTY)
    assert new_fields and not (new_fields & set(json.loads(payload.payload_json())))


def test_false_and_zero_are_values_not_emptiness():
    body = json.loads(tp.TechnicalReviewPayload(
        entity="X", signal="neutral", summary="s", score=0, panic_fired=False, bear_fired=False,
        suggested_exposure=0.0).payload_json())
    assert body["score"] == 0 and body["panic_fired"] is False and body["suggested_exposure"] == 0.0


def test_a_filled_new_field_changes_the_content_hash():
    base = dict(role="technical_review", scope=tp.ProjectionScope(kind="entity", id="X"), as_of="2026-10-17")
    a = tp.build_envelope(payload={"entity": "X", "signal": "neutral", "summary": "s"}, **base)
    b = tp.build_envelope(payload={"entity": "X", "signal": "neutral", "summary": "s", "invalidation": ["y"]}, **base)
    assert a.content_hash != b.content_hash


def test_unknown_item_fields_are_still_rejected():
    with pytest.raises(Exception):
        tp.LayerAnalysisPayload(layer="L1", status="steady", summary="s", findings=["f"], confidence=0.5,
                                cross_section=[{"symbol": "A", "typo_field": 1}])


# --------------------------------------------------------------------------- #
# Layer
# --------------------------------------------------------------------------- #
def _layer():
    claim = lambda cid, st, witnesses: SimpleNamespace(  # noqa: E731
        id=cid, statement=st, witnesses=[SimpleNamespace(entity=w) for w in witnesses])
    return SimpleNamespace(
        key="L6", tickers=[SimpleNamespace(symbol=s) for s in ("MU", "SKHY", "WDC")],
        claims=[claim("share", "HBM share holds", ["MU", "SKHY"]), claim("tight", "supply stays tight", ["MU", "TSM"])])


def _assessment(cid, verdict, readings=(), layer="L6", **kw):
    return ClaimAssessment(claim_id=cid, layer=layer, as_of=NOW, verdict=verdict,
                           entity_readings=list(readings), **kw)


def test_layer_extras_rank_by_evidence_and_split_common_from_divergent():
    from ats.agents.layer.layer_review import projection_extras

    verdict = LayerVerdict(layer_key="L6", as_of=NOW, layer_status="expanding", confidence=0.7,
                           cycle_position="中期", reversal_triggers=["DRAM 现货价连续两周下跌"])
    assessments = [
        _assessment("share", "resolved", [
            EntityReading(entity="SKHY", standing="strong", basis="corroborated", evidence_clusters=4, stance_classes=2),
            EntityReading(entity="MU", standing="neutral", basis="self_reported", evidence_clusters=2)]),
        _assessment("tight", "supportive", basis="corroborated", witnesses_expected=2, witnesses_reported=2,
                    evidence_clusters=3, observation_ids=["o1", "o2"]),
        _assessment("other", "mixed", dissenters=["TSM"], silent_witnesses=["WDC"], unresolved_reason="dissent"),
    ]
    out = projection_extras(_layer(), verdict, assessments)
    assert [r["symbol"] for r in out["cross_section"]] == ["SKHY", "MU", "WDC"]
    assert out["cross_section"][0]["rank"] == 1 and out["cross_section"][2]["rank"] is None   # WDC: not read
    assert out["cross_section_basis"] == "evidence_readings" and out["cross_section_coverage"] == "2/3 members read"
    assert [e["claim_id"] for e in out["common_evidence"]] == ["tight"]
    assert out["common_evidence"][0]["statement"] == "supply stays tight"
    assert out["divergent_evidence"][0]["dissenters"] == ["TSM"]
    assert {(e["symbol"], e["role"]) for e in out["entities"]} >= {("MU", "member"), ("TSM", "witness")}
    assert out["reversal_triggers"] == ["DRAM 现货价连续两周下跌"]
    tp.LayerAnalysisPayload(layer="L6", status="expanding", summary="s", findings=["f"], confidence=0.7, **out)


def test_layer_without_claims_reports_the_gap_and_no_ranking_basis():
    from ats.agents.layer.layer_review import projection_extras

    layer = SimpleNamespace(key="L1", tickers=[SimpleNamespace(symbol="GOOG")], claims=[])
    verdict = LayerVerdict(layer_key="L1", as_of=NOW, layer_status="steady", has_claims=False)
    out = projection_extras(layer, verdict, [])
    assert out["gaps"] == ["本层没有声明命题（配置缺口）"] and "cross_section_basis" not in out
    assert out["cross_section"][0]["rank"] is None


def test_layer_lists_are_capped():
    from ats.agents.layer.layer_review import projection_extras

    layer = SimpleNamespace(key="L", tickers=[SimpleNamespace(symbol=f"T{i}") for i in range(30)],
                            claims=[SimpleNamespace(id=f"c{i}", statement="s", witnesses=[]) for i in range(20)])
    verdict = LayerVerdict(layer_key="L", as_of=NOW, layer_status="steady")
    assessments = [_assessment(f"c{i}", "supportive", layer="L", basis="corroborated") for i in range(20)]
    out = projection_extras(layer, verdict, assessments)
    assert len(out["common_evidence"]) == 8 and len(out["cross_section"]) <= 12 and len(out["entities"]) <= 24


# --------------------------------------------------------------------------- #
# Sector
# --------------------------------------------------------------------------- #
def test_sector_extras_carry_layers_names_and_references():
    from ats.agents.sector.review import allocation_extras

    verdicts = [
        LayerVerdict(layer_key="L7", as_of=NOW, layer_status="expanding", confidence=0.75, cycle_position="早",
                     name_calls=[LayerNameCall(symbol="TSM", stance="增持", rationale="CoWoS")]),
        LayerVerdict(layer_key="L3", as_of=NOW, layer_status="steady", has_claims=False)]
    baskets = [LayerBasket(layer_key="L7", as_of=NOW, layer_cap=0.12,
                           rows=[BasketRow(symbol="TSM", rank=1, weight=0.1)])]
    review = SimpleNamespace(layer_verdicts=verdicts, baskets=baskets, rotation_advice="加 L7 减 L3")
    out = allocation_extras(review, {"L7": 0.12, "L3": 0.05}, {"L7": "p7", "L3": "p3"},
                            missing_layers=["L3"], rotation_available=False)
    l7, l3 = out["layers"]
    assert (l7["layer"], l7["allocation"], l7["budget"], l7["projection_ref"]) == ("L7", "超配", 0.12, "p7")
    assert "本层无命题" in l3["gaps"] and any("缺失" in g for g in l3["gaps"])
    assert out["names"] == [{"symbol": "TSM", "layer": "L7", "subgroup": "", "view": "增持", "weight": 0.1,
                             "rationale": "CoWoS", "self_reported_only": False}]
    assert out["fact_refs"] == ["p3", "p7"] and out["rotation_available"] is False
    tp.SectorAllocationPayload(sector="x", stance="overweight", target_weight=0.17, rationale="r", **out)


def test_sector_names_are_capped_per_layer():
    from ats.agents.sector.review import allocation_extras

    calls = [LayerNameCall(symbol=f"S{i}") for i in range(9)]
    review = SimpleNamespace(layer_verdicts=[LayerVerdict(layer_key="L", as_of=NOW, layer_status="steady", name_calls=calls)],
                             baskets=[], rotation_advice="")
    assert len(allocation_extras(review, {}, {})["names"]) == 5


# --------------------------------------------------------------------------- #
# Macro
# --------------------------------------------------------------------------- #
def test_macro_extras_keep_the_code_regime_and_the_narrative_apart():
    from ats.agents.macro.review import projection_extras
    from ats.schemas.macro_strategy import MacroReview, MacroScenario, SectorTilt, ThemeAssess

    review = MacroReview(
        name="macro", as_of=NOW, quadrant="transition", quadrant_state="provisional",
        regime="risk-off｜后周期", rate_path="年内持平", falsifier="初请4周均值高于26万",
        top_risks=["关税"], horizon="未来4-8周",
        themes=[ThemeAssess(key="fed_policy", label="货币政策", direction="偏鹰", transmission="利率上行压估值", signal="risk-off")],
        scenarios=[MacroScenario(name="基准", description="d", trigger="t", sector_impact="i")],
        sector_tilts=[SectorTilt(sector="半导体", stance="中性", rationale="r")])
    out = projection_extras(review)
    assert out["narrative_regime"].startswith("risk-off") and out["quadrant"] == "transition"
    assert out["transmission"][0]["theme"] == "fed_policy" and out["scenarios"][0]["name"] == "基准"
    assert out["risks"] == ["关税"] and out["horizon"] == "未来4-8周" and out["sector_tilts"][0]["sector"] == "半导体"
    tp.MacroReviewPayload(regime="transition", summary="s", indicators=["i"], **out)


def test_macro_review_rows_written_before_the_new_fields_still_load():
    from ats.schemas.macro_strategy import MacroReview

    r = MacroReview.model_validate({"name": "macro", "as_of": NOW.isoformat()})
    assert r.scenarios == [] and r.horizon == ""


def test_the_model_view_accepts_scenarios_as_a_string_like_the_other_lists():
    from ats.agents.macro.outputs import MacroReviewLLMView

    v = MacroReviewLLMView(regime="r", scenarios='[{"name": "基准", "trigger": "t"}]')
    assert v.scenarios[0].name == "基准"


# --------------------------------------------------------------------------- #
# Technical
# --------------------------------------------------------------------------- #
def _reading(**kw):
    from ats.schemas.technical import TechnicalReading

    base = dict(symbol="NVDA", score=6, close=100.0, sma20=95.0, sma50=90.0, sma200=70.0, target_exposure=1.0,
                prev_exposure=0.5, score_detail={"above_sma20": True, "above_sma50": True, "above_sma200": True, "close": 100.0})
    base.update(kw)
    return TechnicalReading(**base)


def _review(**kw):
    from ats.schemas.technical import TechnicalReview

    return TechnicalReview(as_of=NOW, vix=16.0, vix3m=19.0, **kw)


def test_technical_invalidation_lines_use_the_numbers_beside_them():
    from ats.agents.technical.review import projection_extras

    out = projection_extras(_reading(), _review())
    text = "\n".join(out["invalidation"])
    assert "200 日线 70.00" in text and "50日线 90.00" in text and "信号不再为看多" in text
    assert out["suggested_exposure"] == 1.0 and out["previous_exposure"] == 0.5
    assert "score_detail" in out and all(isinstance(v, bool) for v in out["score_detail"].values())
    tp.TechnicalReviewPayload(entity="NVDA", signal="bullish", summary="s", **out)


def test_technical_bear_and_panic_lines_describe_how_the_caps_lift():
    from ats.agents.technical.review import projection_extras

    out = projection_extras(_reading(score=2, bear_fired=True, panic_fired=True, target_exposure=0.0), _review())
    text = "\n".join(out["invalidation"])
    assert "站回 200 日线" in text and "解除恐慌层" in text and "不再为看空" in text
    assert "熊市层已触发" in out["trend_state"]


# --------------------------------------------------------------------------- #
# Chief's one-line view
# --------------------------------------------------------------------------- #
def test_chiefs_summary_line_mentions_the_new_key_fields():
    from ats.agents.chief.assemble import projection_context_block

    def row(task_id, role, payload):
        return {"task_id": task_id, "role": role, "scope": "x:y", "status": "fresh", "projection_id": "p" * 32,
                "content_hash": "h", "as_of": "2026-10-17", "payload": payload, "reason": ""}

    block = projection_context_block([
        row("macro_review", "macro_review", {"regime": "transition", "summary": "s", "quadrant": "transition",
                                             "quadrant_state": "provisional", "narrative_regime": "risk-off"}),
        row("technical_review", "technical_review", {"signal": "bullish", "summary": "s", "suggested_exposure": 0.5}),
        row("sector_allocation", "sector_allocation", {"stance": "neutral", "target_weight": 0.1,
                                                       "layers": [{}], "names": [{}, {}], "rotation_available": False})])
    assert "叙事 regime=risk-off" in block and "建议敞口上限=50%" in block and "1 层 / 2 个标的" in block
    assert "轮动合成不可用" in block


# --------------------------------------------------------------------------- #
# Forced recompute (manual re-run on unchanged data)
# --------------------------------------------------------------------------- #
def test_force_recompute_makes_the_data_vintage_unique_only_when_asked(monkeypatch):
    from ats.workflow import dispatcher

    monkeypatch.setattr(dispatcher, "_data_vintage_refs", lambda plan, task, inputs: ["series@1"])
    plan = SimpleNamespace(run_id="run-9")
    assert dispatcher.default_data_vintage(plan, None, {}) == ["series@1"]
    assert dispatcher.default_data_vintage(plan, None, {"force_recompute": True}) == [
        "series@1", "forced-recompute:run-9"]
