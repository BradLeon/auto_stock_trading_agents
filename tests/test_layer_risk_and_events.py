"""Risk claims, event components and down-only advice in the layer analysis (event-impact-routing 5.1–5.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from ats.agents.layer import event_inputs as ei
from ats.agents.sector.outputs import LayerVerdictEventView
from ats.chain import risk_sources as rs
from ats.chain.corroborate import corroborate
from ats.schemas.chain import ClaimAssessment, ClaimDef, Concept, Witness

NOW = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)
CFG = {"min_clusters": 2, "min_stance_classes": 2, "min_confidence": 0.5}
CONF = {"enabled": True, "max_components": 30, "safety_min": "major", "validity_days": 14}


# --------------------------------------------------------------------------- 5.1 source counting

def test_relays_of_one_original_report_are_one_source():
    actors = ["The Wall Street Journal（经IBD转述）", "Nicole Nguyen / WSJ", "《华尔街日报》/Jinjoo Lee",
              "华尔街日报（Amrith Ramkumar）", "Kelly Cloonan / 华尔街日报"]
    counted = rs.gate([rs.origin_of(a) for a in actors])
    assert counted["independent"] == ["WSJ"] and not counted["ok"]


def test_two_independent_sources_with_one_authoritative_pass():
    counted = rs.gate([rs.origin_of("Dow Jones Newswires（作者 Joe Stonor）"), rs.origin_of("Barron's（Al Root）")])
    assert counted["independent"] == ["BARRONS", "DOW_JONES_NEWSWIRES"]
    assert counted["authoritative"] == ["DOW_JONES_NEWSWIRES"] and counted["ok"]


def test_first_hand_markers_and_word_boundaries():
    assert rs.origin_of("NVIDIA董事会（道琼斯通讯社发布的新闻稿）").authoritative
    assert rs.origin_of("英伟达发言人").authoritative
    assert not rs.origin_of("sector strategist at Baird").authoritative      # "sec" is not inside "sector"
    assert not rs.gate([rs.origin_of("Barron's"), rs.origin_of("IBD（Ed Carson）")])["ok"]   # nothing first-hand


def _risk(**kw):
    base = dict(id="l5_export_market_access_risk", kind="risk", layer="L5_chip_design",
                statement="出口管制与市场准入限制正在收紧",
                concepts=[Concept(key="xpu_export_market_access", desc="出口许可与立法")],
                witnesses=[Witness(entity="NVDA", stance="incumbent"), Witness(entity="AMD", stance="incumbent"),
                           Witness(entity="DOWJONES", stance="regulator")])
    return ClaimDef(**{**base, **kw})


def _row(entity, *, speaker, otype="media", doc="d1", rid=None):
    return {"id": rid or f"{doc}:{speaker}", "document_id": doc, "entity": entity, "source_entity": speaker,
            "metric": "m", "concept": "xpu_export_market_access", "period": "", "observation_type": otype,
            "direction": "up", "extraction_confidence": 1.0, "evidence_span": "…", "discovery_evidence": 0,
            "observed_at": NOW.isoformat()}


def test_a_risk_claim_from_one_publisher_stays_unknown():
    rows = [_row("NVDA", speaker="DOWJONES", doc=f"a{i}", rid=f"r{i}") for i in range(5)]
    rows += [_row("AMD", speaker="DOWJONES", doc="b", rid="rb")]
    a = corroborate(_risk(), rows, cfg=CFG, as_of=NOW)
    assert a.verdict == "unknown" and a.unresolved_reason == "source_gate"
    assert a.independent_sources == ["DOWJONES"]


def test_a_risk_claim_with_a_company_filing_and_a_publisher_is_supportive():
    rows = [_row("NVDA", speaker="NVDA", otype="regulatory", doc="10q"), _row("NVDA", speaker="DOWJONES", doc="dj")]
    a = corroborate(_risk(), rows, cfg=CFG, as_of=NOW)
    assert a.verdict == "supportive" and a.authoritative_sources == ["NVDA"]


def test_operating_claims_keep_the_company_speaker_rule():
    common = _risk(kind="common", id="c1")
    rows = [_row("NVDA", speaker="NVDA", otype="guidance", doc="x"), _row("AMD", speaker="AMD", otype="guidance",
                                                                          doc="y")]
    a = corroborate(common, rows, cfg=CFG, as_of=NOW)
    assert a.verdict == "unknown" and a.unresolved_reason == "single_stance"   # 2 speakers < 3, one stance


# --------------------------------------------------------------------------- 5.2 config switch

def test_risk_claims_are_invisible_until_the_layer_switch_is_on(monkeypatch, tmp_path):
    """The L5 claim waits in the change folder until go-live (the running dispatcher's schema predates `risk`)."""
    import shutil

    import yaml

    from ats import config
    from ats.schemas.chain import ClaimDef

    pending = Path(__file__).resolve().parents[1] / "openspec/changes/event-impact-routing/pending" \
        / "l5_export_market_access_risk.yaml"
    claim = yaml.safe_load(pending.read_text(encoding="utf-8"))[0]
    assert ClaimDef(**claim).kind == "risk" and {"DOWJONES", "NVDA"} <= {w["entity"] for w in claim["witnesses"]}

    root = tmp_path / "config"
    shutil.copytree(Path(config.__file__).resolve().parents[2] / "config", root)
    doc = yaml.safe_load((root / "sectors/ai_hardware.yaml").read_text(encoding="utf-8"))
    next(x for x in doc["layers"] if x["key"] == "L5_chip_design")["claims"].append(claim)
    (root / "sectors/ai_hardware.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(config, "_config_dir", lambda: root)
    monkeypatch.setattr(config, "risk_claims_enabled", lambda: False)
    l5 = next(x for x in config.load_sector_config().layers if x.key == "L5_chip_design")
    assert not [c for c in l5.claims if c.kind == "risk"]
    monkeypatch.setattr(config, "risk_claims_enabled", lambda: True)
    l5 = next(x for x in config.load_sector_config().layers if x.key == "L5_chip_design")
    assert [c.id for c in l5.claims if c.kind == "risk"] == ["l5_export_market_access_risk"]


# --------------------------------------------------------------------------- 5.3 inputs

LAYER = SimpleNamespace(key="L5_chip_design", tickers=[SimpleNamespace(symbol=s) for s in ("NVDA", "AMD")],
                        claims=[_risk()])


def _brief(pid, entity, comps, *, days_ago=1, actors=("Dow Jones Newswires", "Barron's")):
    evidence = [{"fact_index": i, "source_actor": a} for i, a in enumerate(actors)]
    return {"projection_id": pid, "scope_id": entity, "as_of": (NOW - timedelta(days=days_ago)).isoformat(),
            "payload": {"entity": entity, "event_components": comps, "source_evidence": evidence}}


def _comp(cid, scope, materiality, *, layers=(), direction="negative", cluster=None):
    return {"component_id": cid, "cluster_key": cluster or cid, "content": f"内容 {cid}", "scope": scope,
            "layers": list(layers), "materiality": materiality, "direction": direction, "fact_indices": [0, 1]}


class Store:
    def __init__(self, briefs, prior=None):
        self.briefs, self.prior = briefs, prior

    def task_projection_envelopes(self, *, agent_role=None, scope_id=None, limit=500, **_):
        if agent_role == "information_brief":
            return self.briefs
        return [{"payload": {"off_thesis_shocks": self.prior}}] if self.prior else []


def test_components_reach_the_layer_by_scope_or_through_the_member_safety_net():
    store = Store([
        _brief("b1", "NVDA", [_comp("c-layer", "layer", "major", layers=["L5_chip_design"]),
                              _comp("c-own-major", "entity", "major"),
                              _comp("c-own-minor", "entity", "moderate")]),
        _brief("b2", "TSM", [_comp("c-tsm-own", "entity", "critical"),
                             _comp("c-cross", "cross_layer", "major", layers=["L5_chip_design", "L7_foundry_pkg"])]),
        _brief("b0", "NVDA", [_comp("c-layer", "layer", "major", layers=["L5_chip_design"])], days_ago=2),
        _brief("b-old", "AMD", [_comp("c-old", "layer", "critical", layers=["L5_chip_design"])], days_ago=20),
    ])
    comps = ei.read_components(store, LAYER, now=NOW, conf=CONF)
    routes = {c["ref"]: c["route"] for c in comps}
    assert routes == {"b1#c-layer": "layer", "b1#c-own-major": "safety_net", "b2#c-cross": "layer"}
    assert {s["key"] for s in comps[0]["sources"]} == {"DOW_JONES_NEWSWIRES", "BARRONS"}


# --------------------------------------------------------------------------- 5.3 output checks

def _components():
    store = Store([_brief("b1", "NVDA", [_comp("c-layer", "layer", "major", layers=["L5_chip_design"]),
                                         _comp("c-own", "entity", "major")]),
                   _brief("b2", "AMD", [_comp("c-weak", "layer", "major", layers=["L5_chip_design"])],
                          actors=("Barron's", "Barron's（Al Root）"))])
    return ei.read_components(store, LAYER, now=NOW, conf=CONF)


def _view(**kw):
    return LayerVerdictEventView.model_validate({"layer_key": "L5_chip_design", "risk_claims": [], **kw})


def test_pressured_needs_the_source_gate_and_is_kept_when_it_passes():
    comps = _components()
    view = _view(risk_claims=[{"claim_id": "l5_export_market_access_risk", "status": "pressured",
                               "component_refs": ["b1#c-layer"], "materiality_review": "agree"}])
    fields, _ = ei.apply(LAYER, view, assessments=[], components=comps, now=NOW, conf=CONF)
    row = fields["risk_claims"][0]
    assert row["status"] == "pressured" and row["independent_sources"] == 2 and row["authoritative_sources"] == 1

    view = _view(risk_claims=[{"claim_id": "l5_export_market_access_risk", "status": "pressured",
                               "component_refs": ["b2#c-weak"]}])
    fields, _ = ei.apply(LAYER, view, assessments=[], components=comps, now=NOW, conf=CONF)
    row = fields["risk_claims"][0]
    assert row["status"] == "emerging" and "未达门槛" in row["rationale"]


def test_an_omitted_risk_claim_is_filled_in_from_the_ledger():
    a = ClaimAssessment(claim_id="l5_export_market_access_risk", as_of=NOW, verdict="supportive")
    fields, _ = ei.apply(LAYER, _view(), assessments=[a], components=[], now=NOW, conf=CONF)
    assert fields["risk_claims"][0]["status"] == "emerging"


def test_shocks_cross_scope_claims_and_down_only_advice():
    comps = _components()
    view = _view(
        off_thesis_shocks=[{"shock_id": "L5-shock-1", "component_refs": ["b1#c-layer"], "summary": "无法归入的冲击",
                            "materiality": "major", "direction": "negative"},
                           {"shock_id": "L5-shock-2", "component_refs": ["b9#missing"], "summary": "无来源"},
                           {"shock_id": "L5-shock-3", "component_refs": ["b1#c-layer"], "summary": "太轻",
                            "materiality": "minor"}],
        cross_scope_claims=[{"component_ref": "b1#c-own", "transmission": "董事会动荡推迟买方采购"}],
        utilization_advice={"direction": "down", "notches": 1, "reasons": ["L5-shock-1"]})
    fields, gaps = ei.apply(LAYER, view, assessments=[], components=comps, now=NOW, conf=CONF)
    assert [s["shock_id"] for s in fields["off_thesis_shocks"]] == ["L5-shock-1"]
    assert fields["off_thesis_shocks"][0]["expires_at"].startswith("2026-11-29")
    assert fields["cross_scope_claims"][0]["from_entity"] == "NVDA"
    assert fields["utilization_advice"] == {"direction": "down", "notches": 1, "reasons": ["L5-shock-1"],
                                            "rationale": ""}
    assert any("没有可读的成分引用" in g for g in gaps)


def test_no_risk_signal_means_no_downgrade():
    comps = _components()
    view = _view(risk_claims=[{"claim_id": "l5_export_market_access_risk", "status": "dormant"}],
                 utilization_advice={"direction": "down", "notches": 2, "reasons": ["l5_export_market_access_risk"]})
    fields, gaps = ei.apply(LAYER, view, assessments=[], components=comps, now=NOW, conf=CONF)
    assert fields["utilization_advice"]["direction"] == "none" and any("已清除" in g for g in gaps)
    up = _view(utilization_advice={"direction": "up", "notches": 1, "reasons": []})
    fields, _ = ei.apply(LAYER, up, assessments=[], components=comps, now=NOW, conf=CONF)
    assert fields["utilization_advice"]["direction"] == "none"


def test_an_expired_shock_is_background_and_cannot_carry_advice():
    store = Store([_brief("b1", "NVDA", [_comp("c-layer", "layer", "major", layers=["L5_chip_design"])],
                          days_ago=13)])
    comps = ei.read_components(store, LAYER, now=NOW, conf=CONF)
    later = NOW + timedelta(days=2)          # the component is now 15 days old
    view = _view(off_thesis_shocks=[{"shock_id": "L5-shock-1", "component_refs": ["b1#c-layer"],
                                     "summary": "旧冲击", "materiality": "major", "direction": "negative"}],
                 utilization_advice={"direction": "down", "notches": 1, "reasons": ["L5-shock-1"]})
    fields, gaps = ei.apply(LAYER, view, assessments=[], components=comps, now=later, conf=CONF)
    assert "off_thesis_shocks" not in fields and fields["utilization_advice"]["direction"] == "none"
    assert any("降为背景" in g for g in gaps)
    block = ei.render_block(LAYER, assessments=[], components=[], now=later,
                            prior=[{"shock_id": "L5-shock-1", "summary": "旧冲击",
                                    "expires_at": (NOW + timedelta(days=1)).isoformat()}])
    assert "已过期的冲击" in block and "不得**作为下调依据" in block


def test_a_live_risk_claim_does_not_anchor_an_expanding_or_contracting_status():
    from ats.agents.layer.layer_review import _to_verdict
    from ats.agents.sector.outputs import LayerVerdictView

    risk = ClaimAssessment(claim_id="l5_export_market_access_risk", layer="L5_chip_design", as_of=NOW,
                           verdict="contradicted")
    view = LayerVerdictView(layer_key="L5_chip_design", layer_status="contracting", confidence=0.7)
    layer = SimpleNamespace(key="L5_chip_design", claims=[_risk()], tickers=[], cohort_extra=[])
    verdict = _to_verdict(layer, view, True, False, assessments=[risk])
    assert verdict.layer_status == "steady" and verdict.confidence <= 0.3     # evidence gap, not "contracting"


def test_an_unlaunched_risk_claim_does_not_move_the_sector_config_lineage(monkeypatch, tmp_path):
    """Committing a risk claim while the switch is off must not make Layer/sector projections look stale."""
    import yaml

    from ats import config
    from ats.workflow.planning import _file_hash

    base = {"layers": [{"key": "L5_chip_design", "claims": [{"id": "c1", "kind": "common"}]}]}
    with_risk = {"layers": [{"key": "L5_chip_design",
                             "claims": [{"id": "c1", "kind": "common"}, {"id": "r1", "kind": "risk"}]}]}
    sectors = tmp_path / "sectors"
    sectors.mkdir()
    a, b = sectors / "a.yaml", tmp_path / "b"
    b.mkdir()
    b = b / "sectors"
    b.mkdir()
    b = b / "a.yaml"
    a.write_text(yaml.safe_dump(base), encoding="utf-8")
    b.write_text(yaml.safe_dump(with_risk), encoding="utf-8")
    monkeypatch.setattr(config, "risk_claims_enabled", lambda: False)
    assert _file_hash(a) == _file_hash(b)
    monkeypatch.setattr(config, "risk_claims_enabled", lambda: True)
    assert _file_hash(a) != _file_hash(b)


def test_risk_claims_must_be_answered_when_the_event_view_is_used():
    """2026-11-16 replay: the L5 run left risk_claims out entirely; the key is now required (an empty list is fine)."""
    import pydantic

    with __import__("pytest").raises(pydantic.ValidationError):
        LayerVerdictEventView.model_validate({"layer_key": "L5_chip_design"})


def test_a_layer_component_without_a_destination_is_named_in_gaps():
    comps = _components()
    view = _view(claim_attributions=["订单命题：成分 b2#c-weak 归入，选举后的采购担忧"])
    _, gaps = ei.apply(LAYER, view, assessments=[], components=comps, now=NOW, conf=CONF)
    assert any("b1#c-layer" in g and "未归宿" in g for g in gaps)          # neither claimed nor attributed
    assert not any("b2#c-weak" in g for g in gaps)                           # attributed by ref
    assert not any("b1#c-own" in g for g in gaps)                            # safety net: no destination required


def test_the_event_block_comes_before_the_evidence(monkeypatch):
    from ats.agents.layer import layer_review
    from ats.agents.sector import assemble

    monkeypatch.setattr(assemble, "layer_evidence_blocks", lambda cfg, layer, a: ("## 共同需求议题\n长证据", ""))
    layer = SimpleNamespace(key="L5_chip_design", label="L5", question="Q", claims=[_risk()], structure_notes={})
    ctx = layer_review.build_context(None, layer, event_block="## 事件成分与风险命题")
    assert ctx.index("## 事件成分与风险命题") < ctx.index("## 共同需求议题")
