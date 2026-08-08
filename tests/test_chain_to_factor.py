"""Chain evidence — stage 3: verdicts -> moat_pricing -> cross-section -> Chief.

Covers the acceptance table in docs/CHAIN_EVIDENCE.md §7 阶段三 (hermetic: no network,
no LLM, no Obsidian, no IBKR).
"""

from datetime import datetime, timezone

from ats.agents.sector import cross_section
from ats.chain import factor_evidence
from ats.chain.corroborate import corroborate
from ats.schemas.chain import ClaimDef, Concept, Witness
from ats.schemas.sector import BasketRow, LayerBasket, SectorReview

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
CFG = {"min_clusters": 2, "min_stance_classes": 2, "min_confidence": 0.5}


def _row(entity, concept, *, direction="up", speaker=None, otype="guidance",
         doc="d1", rid=None, polarity="support", span="…"):
    return {"id": rid or f"{doc}:{entity}:{concept}", "document_id": doc,
            "entity": entity.upper(), "source_entity": (speaker or entity).upper(),
            "metric": concept, "concept": concept, "period": "FY26Q3",
            "observation_type": otype, "stance": "incumbent", "direction": direction,
            "evidence_span": span + ("" if polarity == "support" else f"[[{polarity.upper()}]]"), "extraction_confidence": 1.0,
            "discovery_evidence": 0, "observed_at": NOW.isoformat()}


def _share_claim(**kw):
    return ClaimDef(**{**dict(
        id="hbm_share_and_pricing_power", kind="relative", entities=["SKHY", "MU", "005930.KS"], layer="L5_fab",
        feeds_factor="moat_pricing",
        statement="HBM 份额与定价权在三家之间如何分布",
        concepts=[Concept(key="hbm_share", desc="份额", direct=True),
                  Concept(key="customer_qualification", desc="认证", direct=True),
                  Concept(key="hbm_pricing", desc="ASP/毛利率", direct=True),
                  Concept(key="capacity_addition", desc="扩产")],
        witnesses=[Witness(entity="SKHY", stance="incumbent"),
                   Witness(entity="MU", stance="competitor"),
                   Witness(entity="005930.KS", stance="competitor"),
                   Witness(entity="NVDA", stance="customer")]), **kw})


def _common_claim():
    return ClaimDef(
        id="hbm_supply_tight", kind="common", layer="L5_fab",
        statement="HBM 供给持续紧张",
        concepts=[Concept(key="supply_tightness", desc="紧张"),
                  Concept(key="capacity_addition", desc="扩产")],
        witnesses=[Witness(entity="SKHY", stance="supplier"),
                   Witness(entity="NVDA", stance="customer")])


# --- evidence packs -------------------------------------------------------- #
def test_one_claim_renders_as_one_comparison_table():
    """A cross-section pack is per-CLAIM, not per-name. The structure analyst has to
    produce differentiated -2..+2 scores across a cohort, and N independent per-name
    assertions cannot express an ordering — "SK strong, Samsung strong" says nothing
    about which to overweight."""
    rows = [_row("SKHY", "hbm_share", speaker="SKHY", doc="sk", rid="a",
                 span="ASP rose approximately 30% on continued price strength"),
            _row("SKHY", "hbm_pricing", speaker="SKHY", doc="sk", rid="a2",
                 span="pricing reflects our higher technology complexity"),
            _row("MU", "hbm_share", speaker="MU", doc="mu", rid="b", polarity="weak",
                 span="we expect our HBM share to decline modestly"),
            _row("MU", "customer_qualification", speaker="MU", doc="mu", rid="b2",
                 polarity="weak", span="qualification at the lead customer slipped")]
    claim = _share_claim()
    a = corroborate(claim, rows, cfg=CFG)
    assert a.verdict == "resolved"

    packs = factor_evidence.build_packs([a], [claim], {r["id"]: r for r in rows})
    assert len(packs) == 1                       # one claim -> one table
    pack = packs[0]
    assert pack.factor == "moat_pricing"
    assert [r.entity for r in pack.readings] == ["SKHY", "MU", "005930.KS"]

    text = pack.as_text()
    assert "| **SKHY** | 强" in text and "| **MU** | 弱" in text
    assert "005930.KS" in text and "无读数" in text   # silence is shown, not omitted
    ctx = factor_evidence.as_context(packs)
    assert "moat_pricing" in ctx and "不要凭印象打分" in ctx


def test_the_evidence_shown_is_the_evidence_the_reading_rests_on():
    """The defect this replaced: the pack printed the first four observations the entity
    had made, so SK hynix was labelled `strong` above one `down` reading ("DRAM ASP
    growth fell below market expectations") and two `flat` ones. Evidence that
    contradicts the conclusion it is printed under is worse than no evidence."""
    rows = [_row("SKHY", "hbm_pricing", speaker="SKHY", doc="sk", rid="cited",
                 span="ASP rose approximately 30%"),
            _row("SKHY", "hbm_share", direction="down", speaker="SKHY", doc="sk",
                 rid="notcited", span="ASP growth fell below market expectations"),
            _row("MU", "hbm_share", speaker="MU", doc="mu", rid="m", polarity="weak",
                 span="share to decline")]
    claim = _share_claim()
    a = corroborate(claim, rows, cfg=CFG)
    sk = next(r for r in a.entity_readings if r.entity == "SKHY")
    assert sk.key_observation_ids and "notcited" not in sk.key_observation_ids

    text = factor_evidence.build_packs([a], [claim],
                                       {r["id"]: r for r in rows})[0].as_text()
    assert "ASP rose approximately 30%" in text
    assert "fell below market expectations" not in text


def test_a_claim_that_declares_no_factor_reaches_no_factor():
    """`feeds_factor` omitted is the default and means the claim informs no factor. It
    still reaches the weekly report and the sector analyst — routing and aggregation are
    separate decisions, which is what `kind` used to conflate."""
    rows = [_row("SKHY", "hbm_share", speaker="SKHY", doc="sk", rid="a"),
            _row("MU", "hbm_share", speaker="MU", doc="mu", rid="b", polarity="weak")]
    claim = _share_claim(feeds_factor="")
    a = corroborate(claim, rows, cfg=CFG)
    assert a.verdict == "resolved"                # the comparison still happened
    assert factor_evidence.build_packs([a], [claim], {}) == []


def test_common_verdict_never_produces_moat_evidence():
    """A_class claims describe the industry, not who wins inside it. Routing one here
    would re-introduce the "competitor expands => we lost share" inference that gate 3
    exists to block."""
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", doc="sk", rid="a"),
            _row("SKHY", "supply_tightness", speaker="NVDA", otype="counterparty",
                 doc="nv", rid="b")]
    claim = _common_claim()
    a = corroborate(claim, rows, cfg=CFG)
    assert a.verdict == "supportive"
    assert factor_evidence.build_packs([a], [claim], {r["id"]: r for r in rows}) == []


def test_unknown_and_mixed_produce_nothing_so_moat_stays_null():
    """"not looked at" must stay distinguishable from "looked at, judged neutral" —
    so an unresolved claim yields NO evidence and moat_pricing keeps its null."""
    claim = _share_claim()
    # No evidence at all, and evidence for only ONE company (a comparison needs two).
    for rows in ([], [_row("SKHY", "hbm_share", speaker="SKHY", rid="only")]):
        a = corroborate(claim, rows, cfg=CFG)
        assert a.verdict == "unknown"
        assert factor_evidence.build_packs([a], [claim], {}) == []


def test_competitor_expansion_alone_yields_no_moat_evidence():
    """End-to-end HBM-保持 fixture: industry supply grew, our share claim did not move,
    therefore nothing may nudge SK's moat_pricing."""
    rows = [_row("MU", "capacity_addition", speaker="MU", doc="mu", rid="a"),
            _row("005930.KS", "capacity_addition", speaker="005930.KS", doc="ss", rid="b")]
    claim = _share_claim()
    a = corroborate(claim, rows, cfg=CFG)
    assert a.verdict == "unknown"
    assert factor_evidence.build_packs([a], [claim], {r["id"]: r for r in rows}) == []


# --- cross-section rerank -------------------------------------------------- #
def _rows(moats: dict):
    """SKHY leads on the QUANT factors so a rerank can only come from the structural
    evidence — otherwise the test proves nothing.

    PEG is held equal (fwd_pe scaled with growth) so the quant edge comes from ONE
    factor. Otherwise growth and value both favour SK and their combined weight simply
    swamps the 20% moat factor, which would make this test unfalsifiable rather than
    passing for a good reason.
    """
    # The real three-player HBM cohort. Two names is too few to test sizing: the
    # single-name cap (layer_cap x 0.4) binds on both and forces them equal, which is
    # correct behaviour but tells us nothing about whether the evidence moved anything.
    spec = {"SKHY": (0.40, 20.0), "MU": (0.30, 15.0), "SS": (0.25, 12.5)}   # all PEG 0.5
    out = []
    for sym, m in moats.items():
        growth, pe = spec[sym]
        r = cross_section.FactorRow(symbol=sym, rev_growth=growth,
                                    gross_margin=0.40, op_margin=0.20, fwd_pe=pe,
                                    mom_60d=5.0, rating_delta=0.1, beta=1.2,
                                    market_cap=5e10)
        r.moat_pricing, r.tech_tenor = m, 0.0
        out.append(r)
    return out


def test_moat_swing_reorders_the_composite():
    """The payoff: a share reversal must actually move the ranking, not just the note.

    On financials alone SK Hynix wins. Once the evidence says its share is slipping and
    Samsung's is rising, the composite must put Samsung first — that reordering is what
    ultimately turns into a different suggested weight.
    """
    quant = _rows({"SKHY": None, "MU": None, "SS": None})
    cross_section.rank_cohort(quant, layer_cap=0.30)
    assert {r.symbol: r.rank for r in quant}["SKHY"] == 1     # quant favours SK

    blended = _rows({"SKHY": -1.5, "MU": 0.0, "SS": 1.5})
    cross_section.rank_cohort(blended, layer_cap=0.30,
                              weights=cross_section.BLENDED_WEIGHTS)
    after = {r.symbol: r.rank for r in blended}
    assert after["SS"] == 1 and after["SKHY"] > after["SS"]   # evidence flips the order
    w = {r.symbol: r.weight for r in blended}
    assert w["SS"] > w["SKHY"]                                # and the suggested sizing


def test_null_moat_is_cohort_neutral_not_zero_opinion():
    """A null must not be silently scored as a neutral judgement."""
    rows = _rows({"SKHY": None, "MU": None, "SS": None})
    cross_section.rank_cohort(rows, layer_cap=0.30,
                              weights=cross_section.BLENDED_WEIGHTS)
    assert all(r.moat_pricing is None for r in rows)
    assert all(r.z.get("moat_pricing", 0.0) == 0.0 for r in rows)



# --- Chief injection ------------------------------------------------------- #
def _review_with_basket(structural=True):
    return SectorReview(
        sector="ai_hardware", as_of=NOW, regime="L5 是瓶颈",
        baskets=[LayerBasket(
            layer_key="L5_fab", as_of=NOW, layer_cap=0.30, structural=structural,
            rows=[BasketRow(symbol="SS", quant_rank=3, rank=1, weight=0.13,
                            moat_pricing=1.5, rationale="HBM4 通过核心客户认证 · 份额+6pt"),
                  BasketRow(symbol="SKHY", quant_rank=1, rank=2, weight=0.09,
                            moat_pricing=-0.5, rationale="份额指引下修 -5pt")])])


def test_chief_sees_the_divergence_but_never_the_weights():
    """The Chief gets the disagreement and its reason — NOT the suggested sizing.

    Handing over "13% / 9%" would get copied, moving the sizing decision to a model
    nobody is accountable for; the Chief is the only decision-maker.
    """
    from ats.agents.chief import assemble

    lines = "\n".join(assemble._basket_lines(_review_with_basket()))
    assert "量化第3 → 复合第1" in lines and "SS" in lines
    assert "HBM4 通过核心客户认证" in lines                  # the evidence travels
    assert "13%" not in lines and "9%" not in lines         # the sizing does not
    assert "0.13" not in lines and "weight" not in lines


def test_pure_quant_basket_adds_nothing():
    """Without the structural overlay there is no position claim to report."""
    from ats.agents.chief import assemble

    assert assemble._basket_lines(_review_with_basket(structural=False)) == []


def test_feed_chief_basket_flag_off_is_a_clean_kill_switch(monkeypatch):
    """Regression guard: with the flag off the Chief context is byte-identical to the
    pre-stage-3 system."""
    import ats.config as _config
    from ats.agents.chief import assemble

    real = _config.load_pead_global
    monkeypatch.setattr(_config, "load_pead_global", lambda: {
        **real(), "sector_review": {**real()["sector_review"], "feed_chief_basket": False}})
    assert assemble._basket_lines(_review_with_basket()) == []
