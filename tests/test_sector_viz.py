"""build_bundle() — the data assembly behind the HTML dashboard (viz.py).

A small self-contained SectorConfig (not the real ai_hardware config) so these tests
stay fast and independent of that config's contents changing. Mirrors the same shapes
`_run_layered` / `ats sector html` will actually pass: a `SectorConfig`, a
`SectorReview`, a `{layer_key: [ClaimAssessment]}` map, and a real `TradingMemory`.
"""

from datetime import datetime, timezone
from pathlib import Path

from ats.agents.sector import viz
from ats.data.source_cache import CachedDoc
from ats.memory.store import TradingMemory
from ats.schemas.chain import ClaimAssessment, ClaimDef, ClusterJudgement, EntityReading, \
    Observation, Witness
from ats.schemas.sector import BasketRow, LayerBasket, LayerNameCall, LayerTicker, \
    LayerVerdict, SectorConfig, SectorLayer, SectorReview

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)


def _cfg(layers=None):
    layer = SectorLayer(
        key="L1_test", label="测试层", weight_cap=0.10,
        tickers=[LayerTicker(symbol="AAA"), LayerTicker(symbol="BBB")],
        claims=[
            ClaimDef(id="c_common", kind="common", statement="测试共同命题", layer="L1_test",
                     witnesses=[Witness(entity="AAA", stance="supplier"),
                               Witness(entity="CCC", stance="customer")]),
            ClaimDef(id="c_rel", kind="relative", statement="测试相对命题", layer="L1_test",
                     entities=["AAA", "BBB"],
                     witnesses=[Witness(entity="AAA", stance="incumbent"),
                               Witness(entity="BBB", stance="competitor")]),
        ])
    return SectorConfig(name="test_sector", label="测试行业", layers=(layers or [layer]))


def _doc(store, *, symbol="AAA", source="defeatbeta", doc_id_seed="1"):
    doc = CachedDoc(symbol=symbol, period="FY2026Q2", doc_type="transcript", text="…",
                    path=Path(f"/tmp/{symbol}-{doc_id_seed}.md"), source=source,
                    sha256=f"sha{doc_id_seed}", fetched_at=NOW.isoformat())
    store.save_document(doc)
    return doc


def _obs(store, doc, *, entity="AAA", metric="m1", span="cited span", concept="k1"):
    o = Observation(document_id=doc.document_id, entity=entity, source_entity=entity,
                    metric=metric, concept=concept, observation_type="reported_actual",
                    stance="supplier", direction="up", evidence_span=span, observed_at=NOW,
                    extraction_confidence=0.9)
    store.save_observation(o)
    return o


def _review(layer_key="L1_test", *, basket=None, verdict=None):
    return SectorReview(sector="test_sector", as_of=NOW, regime="测试 regime",
                        rotation_advice="测试轮动",
                        baskets=[basket] if basket else [],
                        layer_verdicts=[verdict] if verdict else [])


def _verdict(layer_key="L1_test", **kw):
    defaults = dict(layer_key=layer_key, as_of=NOW, allocation="标配", confidence=0.5,
                    cycle_position="中周期", claim_attributions=["测试依据"],
                    rationale="测试综合")
    defaults.update(kw)
    return LayerVerdict(**defaults)


def _basket(layer_key="L1_test", **kw):
    defaults = dict(layer_key=layer_key, as_of=NOW, layer_cap=0.06,
                    rows=[BasketRow(symbol="AAA", composite=0.3, rank=1, weight=0.04,
                                    factors={"growth": 0.5}, metrics={"rev_growth": 0.1})])
    defaults.update(kw)
    return LayerBasket(**defaults)


def test_every_configured_layer_appears_even_with_no_verdict_this_round():
    """A layer that produced nothing this round must still show up — silently dropping
    it would look like the layer never existed rather than 'no verdict this time'."""
    cfg = _cfg()
    review = _review()
    store = TradingMemory(":memory:")
    bundle = viz.build_bundle(cfg, review, assessments_by_layer={}, store=store)
    assert len(bundle["layers"]) == 1
    ly = bundle["layers"][0]
    assert ly["key"] == "L1_test"
    assert ly["die"]["allocation"] == "—"
    assert "本轮未产出结论" in ly["die"]["flags"]


def test_silent_witnesses_render_as_a_named_gap_not_left_blank():
    cfg = _cfg()
    store = TradingMemory(":memory:")
    a = ClaimAssessment(claim_id="c_common", layer="L1_test", as_of=NOW, verdict="supportive",
                        support_score=3, silent_witnesses=["CCC"])
    review = _review(basket=_basket(), verdict=_verdict())
    bundle = viz.build_bundle(cfg, review, assessments_by_layer={"L1_test": [a]}, store=store)
    ly = bundle["layers"][0]
    matrix_row = ly["witness_matrix"]["rows"][0]
    assert matrix_row["cells"]["CCC"] == "silent"
    assert {"speaker": "CCC", "claim_id": "c_common", "statement": "测试共同命题"} \
        in ly["evidence"]["silent"]


def test_source_tier_is_surfaced_on_every_quote():
    cfg = _cfg()
    store = TradingMemory(":memory:")
    doc = _doc(store, source="defeatbeta")
    obs = _obs(store, doc)
    j = ClusterJudgement(cluster_key="AAA|AAA|k1|up", polarity="support", speaker="AAA",
                         concept="k1", stance="supplier", reason="r", observation_ids=[obs.id])
    a = ClaimAssessment(claim_id="c_common", layer="L1_test", as_of=NOW, verdict="supportive",
                        judgements=[j])
    review = _review(basket=_basket(), verdict=_verdict())
    bundle = viz.build_bundle(cfg, review, assessments_by_layer={"L1_test": [a]}, store=store)
    quotes = bundle["layers"][0]["trace"]["clusters"][0]["quotes"]
    assert quotes[0]["tier"] == "keyed"          # defeatbeta -> RANK_KEYED
    assert quotes[0]["text"] == "cited span"


def test_only_the_cited_observation_is_loaded_not_the_whole_entity():
    """The claim cited ONE of the entity's observations — the other must never surface,
    the same discipline `observations_by_id` exists to enforce at the store layer."""
    cfg = _cfg()
    store = TradingMemory(":memory:")
    doc = _doc(store)
    cited = _obs(store, doc, span="cited span")
    _obs(store, doc, metric="m2", span="never cited")
    j = ClusterJudgement(cluster_key="AAA|AAA|k1|up", polarity="support", speaker="AAA",
                         concept="k1", stance="supplier", observation_ids=[cited.id])
    a = ClaimAssessment(claim_id="c_common", layer="L1_test", as_of=NOW, verdict="supportive",
                        judgements=[j])
    review = _review(basket=_basket(), verdict=_verdict())
    bundle = viz.build_bundle(cfg, review, assessments_by_layer={"L1_test": [a]}, store=store)
    all_text = [q["text"] for c in bundle["layers"][0]["trace"]["clusters"] for q in c["quotes"]]
    assert all_text == ["cited span"]


def test_cluster_key_is_the_stable_id_linking_evidence_to_trace():
    cfg = _cfg()
    store = TradingMemory(":memory:")
    doc = _doc(store)
    obs = _obs(store, doc)
    j = ClusterJudgement(cluster_key="AAA|AAA|k1|up", polarity="support", speaker="AAA",
                         concept="k1", stance="supplier", observation_ids=[obs.id])
    a = ClaimAssessment(claim_id="c_common", layer="L1_test", as_of=NOW, verdict="supportive",
                        judgements=[j])
    review = _review(basket=_basket(), verdict=_verdict())
    bundle = viz.build_bundle(cfg, review, assessments_by_layer={"L1_test": [a]}, store=store)
    ly = bundle["layers"][0]
    evi_key = ly["evidence"]["stances"][0]["clusters"][0]["cluster_key"]
    trace_key = ly["trace"]["clusters"][0]["cluster_key"]
    assert evi_key == trace_key == "AAA|AAA|k1|up"


def test_relative_claim_gets_entity_readings_not_a_support_refute_bar_and_stays_out_of_the_matrix():
    cfg = _cfg()
    store = TradingMemory(":memory:")
    er = EntityReading(entity="AAA", standing="strong", reason="领先", basis="self_reported")
    a = ClaimAssessment(claim_id="c_rel", layer="L1_test", as_of=NOW, verdict="resolved",
                        entity_readings=[er])
    review = _review(basket=_basket(), verdict=_verdict())
    bundle = viz.build_bundle(cfg, review, assessments_by_layer={"L1_test": [a]}, store=store)
    claim = bundle["layers"][0]["claims"][0]
    assert claim["kind"] == "relative"
    assert claim["support_score"] == 0.0 and claim["refute_score"] == 0.0
    assert claim["entity_readings"][0]["entity"] == "AAA"
    assert bundle["layers"][0]["witness_matrix"]["rows"] == []


def test_cross_validation_flag_requires_two_distinct_witness_stances_not_two_quotes_from_one():
    cfg = _cfg()
    store = TradingMemory(":memory:")
    doc = _doc(store)
    o1 = _obs(store, doc, span="span one")
    o2 = _obs(store, doc, entity="CCC", metric="m2", span="span two")
    j_single = ClusterJudgement(cluster_key="AAA|AAA|k1|up", polarity="support", speaker="AAA",
                                concept="k1", stance="supplier", observation_ids=[o1.id])
    a_single = ClaimAssessment(claim_id="c_common", layer="L1_test", as_of=NOW,
                               verdict="supportive", judgements=[j_single])
    review = _review(basket=_basket(), verdict=_verdict())
    bundle = viz.build_bundle(cfg, review, assessments_by_layer={"L1_test": [a_single]}, store=store)
    assert bundle["layers"][0]["evidence"]["stances"][0]["clusters"][0]["cross"] is False

    j2 = ClusterJudgement(cluster_key="CCC|CCC|k1|up", polarity="support", speaker="CCC",
                          concept="k1", stance="customer", observation_ids=[o2.id])
    a_two = ClaimAssessment(claim_id="c_common", layer="L1_test", as_of=NOW, verdict="supportive",
                            judgements=[j_single, j2])
    bundle2 = viz.build_bundle(cfg, review, assessments_by_layer={"L1_test": [a_two]}, store=store)
    flags = {c["speaker"]: c["cross"] for s in bundle2["layers"][0]["evidence"]["stances"]
            for c in s["clusters"]}
    assert flags == {"AAA": True, "CCC": True}


def test_budget_uses_the_baskets_actual_post_rescale_cap_not_a_recomputed_formula():
    """Mirrors report.py's `_budget_derivation`: when a group ceiling squeezed the
    basket below the formula value, the die must show what was ACTUALLY granted."""
    cfg = _cfg()
    store = TradingMemory(":memory:")
    squeezed_basket = _basket(layer_cap=0.03)     # formula would say 0.10 * 0.6 = 0.06
    review = _review(basket=squeezed_basket, verdict=_verdict(allocation="标配"))
    bundle = viz.build_bundle(cfg, review, assessments_by_layer={}, store=store)
    die = bundle["layers"][0]["die"]
    assert die["budget"] == 0.03
    assert "被跨层组上限按比例压到 3.0%" in bundle["layers"][0]["budget_formula"]


def test_chainmap_edges_come_from_declared_customer_supplier_witness_stance():
    cfg = _cfg()
    lanes = {lane["key"]: lane for lane in viz._chainmap(cfg)["lanes"]}
    assert {"AAA", "BBB"} == {n["symbol"] for n in lanes["L1_test"]["nodes"]}
    # CCC is a witness (customer stance) but not a layer ticker anywhere -> no node,
    # so it must not appear in any edge either.
    edges = viz._chainmap(cfg)["edges"]
    assert all("CCC" not in nid for pair in edges for nid in pair)
