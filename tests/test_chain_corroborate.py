"""Chain evidence — stage 2: the three corroboration gates (hermetic, no LLM).

Covers the acceptance table in docs/CHAIN_EVIDENCE.md §7 阶段二.
"""

from datetime import datetime, timedelta, timezone

from ats.chain.corroborate import corroborate
from ats.schemas.chain import ClaimDef, Concept, Horizon, Witness

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
CFG = {"min_clusters": 2, "min_stance_classes": 2, "min_confidence": 0.5}


def _row(entity, concept, *, direction="up", speaker=None, otype="guidance",
         doc="d1", period="FY26Q3", at=None, conf=1.0, discovery=False, rid=None,
         metric="", stance="incumbent", polarity="support"):
    """`entity` = who the fact is ABOUT; `speaker` = whose filing disclosed it.

    `stance` is the EXTRACTED value and the engine must IGNORE it — stance comes from
    the claim's declared witness table, keyed on the speaker (invariant 8).
    """
    return {
        "id": rid or f"{doc}:{entity}:{concept}:{period}",
        "document_id": doc, "entity": entity.upper(),
        "source_entity": (speaker or entity).upper(),
        "metric": metric or concept, "concept": concept,
        "period": period, "observation_type": otype, "stance": stance,
        # Polarity is adjudicated from the evidence now, not declared in config, so
        # the fixture says what the evidence MEANS by marking the span (see the
        # conftest adjudicator stub).
        "direction": direction, "extraction_confidence": conf,
        "evidence_span": "…" + ("" if polarity == "support" else f"[[{polarity.upper()}]]"),
        "discovery_evidence": 1 if discovery else 0,
        "observed_at": (at or NOW).isoformat(),
    }


def _common(**kw):
    base = dict(id="hbm_supply_tight", kind="common", layer="L5_fab",
                statement="HBM 供给持续紧张",
                concepts=[Concept(key="supply_tightness", desc="售罄/交期"),
                          Concept(key="capacity_addition", desc="扩产/capex"),
                          Concept(key="hbm_demand", desc="下游需求")],
                witnesses=[Witness(entity="NVDA", stance="customer"),
                           Witness(entity="MSFT", stance="customer"),
                           Witness(entity="SKHY", stance="supplier"),
                           Witness(entity="MU", stance="supplier")])
    return ClaimDef(**{**base, **kw})


def _relative(**kw):
    base = dict(id="hbm_share_and_pricing_power", kind="relative", entities=["SKHY", "MU", "005930.KS"], layer="L5_fab",
                statement="HBM 份额与定价权在三家之间如何分布",
                concepts=[Concept(key="hbm_share", desc="份额及其指引", direct=True),
                          Concept(key="customer_qualification", desc="客户认证进展", direct=True),
                          Concept(key="hbm_pricing", desc="ASP/毛利率", direct=True),
                          Concept(key="capacity_addition", desc="扩产", direct=False)],
                witnesses=[Witness(entity="SKHY", stance="incumbent"),
                           Witness(entity="MU", stance="competitor"),
                           Witness(entity="005930.KS", stance="competitor"),
                           Witness(entity="NVDA", stance="customer")])
    return ClaimDef(**{**base, **kw})


# --- Gate 1: dedup --------------------------------------------------------- #
def test_ten_reprints_of_one_report_are_one_cluster():
    """Volume is not independence. Ten outlets relaying one number is one witness."""
    rows = [_row("MU", "supply_tightness", otype="media", speaker="MU",
                 doc=f"news-{i}", rid=f"r{i}") for i in range(10)]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 1
    assert a.verdict == "unknown"          # one cluster cannot clear min_clusters


def test_company_press_release_call_and_deck_are_one_cluster():
    """A company repeating its own figure across channels is not corroboration."""
    rows = [_row("MU", "supply_tightness", doc=d, rid=f"r{d}")
            for d in ("press-release", "earnings-call", "investor-deck")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 1


def test_same_fact_from_company_and_customer_stays_two_clusters():
    """The inverse guard: collapsing these would destroy real cross-witness evidence."""
    rows = [_row("MU", "supply_tightness", speaker="MU", doc="mu-call", rid="a"),
            _row("MU", "supply_tightness", speaker="NVDA", otype="counterparty",
                 doc="nvda-call", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 2
    assert a.verdict == "supportive"


# --- Gate 2: stance -------------------------------------------------------- #
def test_three_sell_side_notes_cannot_confirm():
    """Three sell-side notes are ONE witness class — secondary material never
    contributes a stance, so it cannot carry a claim to a verdict on its own."""
    # Three DIFFERENT houses, so gate 1 legitimately keeps them apart and the test
    # exercises gate 2 rather than tripping the cluster-count floor first.
    rows = [_row("MU", "supply_tightness", otype="research", speaker=f"HOUSE{i}",
                 doc=f"note-{i}", rid=f"n{i}") for i in range(3)]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 3
    assert a.verdict == "mixed"            # capped: not confirmed
    assert a.stance_classes == 0
    assert "立场" in a.note


def test_customer_plus_supplier_can_confirm():
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", doc="sk", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", otype="counterparty",
                 doc="nvda", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "supportive"
    assert a.stance_classes == 2


def test_single_stance_however_many_names_stays_capped():
    """Two different suppliers still agree from the same economic position."""
    rows = [_row("MU", "supply_tightness", speaker="MU", doc="mu", rid="a"),
            _row("SKHY", "supply_tightness", speaker="SKHY", doc="sk", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "mixed" and a.stance_classes == 1


# --- Gate 3: common / relative isolation (the reason this design exists) ---- #
def test_competitor_expansion_does_not_move_our_share_claim():
    """THE case. Micron raising HBM capacity/capex proves industry supply grew.

    It does NOT prove SK Hynix lost share — that inference needs share, qualification
    or ASP evidence about SK Hynix itself. Deriving "trim SK" from "Micron expands"
    is exactly the unsupported step this gate exists to block.
    """
    rows = [_row("MU", "capacity_addition", speaker="MU", doc="mu-call", rid="a"),
            _row("MU", "supply_tightness", speaker="MU", doc="mu-call", rid="b"),
            _row("005930.KS", "capacity_addition", speaker="005930.KS",
                 doc="ss", rid="c")]

    common = corroborate(_common(), rows, cfg=CFG)
    relative = corroborate(_relative(), rows, cfg=CFG)

    assert common.verdict != "unknown"          # industry demand/supply DID move
    assert relative.verdict == "unknown"        # our share claim did NOT
    assert relative.evidence_clusters == 0
    assert relative.refute_score == 0.0


def test_a_rivals_own_share_reading_now_counts_for_the_rival():
    """The point of the cross-section. Under the old subject-shaped claim, Micron's own
    share disclosure was discarded — only evidence ABOUT SK Hynix could enter, and
    earnings calls do not discuss competitors, so the claim rested on SK's self-report
    alone. Now each company's disclosure lands in its own row and they can be compared."""
    rows = [_row("SKHY", "hbm_share", speaker="SKHY", doc="sk-call", rid="a"),
            _row("SKHY", "hbm_pricing", speaker="SKHY", doc="sk-call", rid="a2"),
            _row("MU", "hbm_share", speaker="MU", doc="mu-call", rid="b",
                 polarity="weak"),
            _row("MU", "customer_qualification", speaker="MU", doc="mu-call", rid="b2",
                 polarity="weak")]
    a = corroborate(_relative(), rows, cfg=CFG)
    assert a.verdict == "resolved"
    by = {r.entity: r for r in a.entity_readings}
    assert by["SKHY"].standing == "strong" and by["MU"].standing == "weak"
    assert by["005930.KS"].standing == "unknown"        # silent, not weak
    assert all(r.reason for r in a.entity_readings)


def test_a_reading_that_is_only_self_reported_says_so():
    """Basis travels with the reading. A cohort where every name self-reports is still
    comparable — the bias is common-mode — but a self-reported `strong` must never be
    presented as if a customer had confirmed it."""
    rows = [_row("SKHY", "hbm_share", speaker="SKHY", doc="sk", rid="a"),
            _row("SKHY", "hbm_pricing", speaker="SKHY", doc="sk", rid="a2"),
            _row("MU", "hbm_share", speaker="MU", doc="mu", rid="b"),
            _row("MU", "customer_qualification", speaker="NVDA", otype="counterparty",
                 doc="nv", rid="b2")]
    a = corroborate(_relative(), rows, cfg=CFG)
    by = {r.entity: r for r in a.entity_readings}
    assert by["SKHY"].basis == "self_reported"     # only SK spoke about SK
    assert by["MU"].basis == "corroborated"        # MU plus its customer
    assert "NVDA" in by["MU"].speakers


def test_relative_claim_ignores_non_direct_metrics_about_the_subject():
    """Even about the subject, only the declared direct metrics may move share."""
    rows = [_row("SKHY", "capacity_addition", speaker="SKHY", doc="sk", rid="a"),
            _row("SKHY", "", speaker="SKHY", metric="revenue", doc="sk", rid="b")]
    a = corroborate(_relative(), rows, cfg=CFG)
    assert a.verdict == "unknown" and a.evidence_clusters == 0


# --- horizon / coverage / reachability ------------------------------------- #
def test_observations_outside_horizon_decay_to_unknown():
    claim = _common(horizon=Horizon(**{"from": datetime(2026, 8, 1).date(),
                                       "to": datetime(2027, 12, 31).date()}))
    stale = NOW - timedelta(days=400)
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", at=stale, doc="a", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", otype="counterparty",
                 at=stale, doc="b", rid="b")]
    a = corroborate(claim, rows, cfg=CFG)
    assert a.verdict == "unknown"


def test_coverage_travels_with_the_verdict():
    """"supportive 4/5 reported" and "supportive 1/5" must be distinguishable."""
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", doc="sk", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", otype="counterparty",
                 doc="nv", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.witnesses_expected == 4 and a.witnesses_reported == 2
    assert a.coverage == "2/4"


def test_no_data_is_unknown_and_explicitly_not_negative():
    """An unreachable metric must never read as counter-evidence."""
    a = corroborate(_common(), [], cfg=CFG)
    assert a.verdict == "unknown"
    assert a.support_score == 0.0 and a.refute_score == 0.0
    assert "不等于负面" in a.note


def test_low_confidence_rows_are_dropped():
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", conf=0.2, doc="a", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", conf=0.3, doc="b", rid="b")]
    assert corroborate(_common(), rows, cfg=CFG).verdict == "unknown"


def test_discovery_evidence_cannot_confirm_the_claim_it_discovered():
    """Anti-hindsight: material that MADE us notice a proposition explains "why look",
    it may never also serve as "it is true"."""
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", discovery=True,
                 doc="a", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", otype="counterparty",
                 discovery=True, doc="b", rid="b")]
    assert corroborate(_common(), rows, cfg=CFG).verdict == "unknown"


# --- dissent: veto vs caveat ----------------------------------------------- #
def test_a_lone_dissenting_cluster_does_not_veto_a_large_majority():
    """`mixed` means "the disagreement is unresolved", and one cluster against many is
    not a disagreement. The old any-refute-at-all rule made that indistinguishable, and
    it degraded as coverage grew: with 38 real clusters the same data returned
    `mixed 26/1` and `supportive 28/0` on consecutive runs, because the adjudicator
    wavers on borderline clusters and a single flip decided the verdict."""
    # Nine supporting clusters across two stance classes (suppliers + a customer),
    # against one dissenting cluster.
    rows = [_row("MU", concept, speaker=who, doc=f"{who}-{concept}", rid=f"{who}{concept}")
            for who in ("SKHY", "MU", "MSFT")
            for concept in ("supply_tightness", "capacity_addition", "hbm_demand")]
    rows.append(_row("MU", "hbm_demand", speaker="NVDA", otype="counterparty",
                     doc="nv", rid="x", polarity="refute"))
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "supportive"          # 9 vs 1 — the majority stands
    assert a.dissenters == ["NVDA"]           # …but the dissent is NAMED
    assert "异议" in a.note                    # …and stated, not hidden


def test_material_dissent_still_overturns():
    """Demoting a lone dissent to a caveat must not make dissent toothless: several
    independent dissenting clusters still leave the question open."""
    rows = [_row("MU", concept, speaker=who, doc=f"{who}-{concept}", rid=f"{who}{concept}")
            for who, concepts in (("SKHY", ("supply_tightness", "capacity_addition")),
                                  ("MU", ("supply_tightness", "capacity_addition")),
                                  ("MSFT", ("hbm_demand",)))
            for concept in concepts]
    rows += [_row("MU", "hbm_demand", speaker=who, otype="counterparty",
                  doc=f"{who}-d", rid=f"{who}x", polarity="refute")
             for who in ("NVDA", "SKHY", "MU")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "mixed" and "分歧未消解" in a.note


def test_dissent_share_overturns_even_when_small_in_count():
    """The share test carries the small-evidence case: 2 against 3 is a real split
    even though 2 is below the absolute cluster threshold."""
    rows = [_row("MU", "supply_tightness", speaker=who, doc=f"{who}-s", rid=f"{who}s")
            for who in ("SKHY", "MU", "MSFT")]
    rows += [_row("MU", "hbm_demand", speaker=who, otype="counterparty",
                  doc=f"{who}-d", rid=f"{who}x", polarity="refute")
             for who in ("NVDA", "SKHY")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "mixed"


# --- polarity is adjudicated, not declared --------------------------------- #
def test_same_dimension_and_direction_can_land_on_either_side():
    """The reason `supports_when` had to go.

    Two capacity-UP readings on the same claim: one is a response to demand the
    company cannot meet, the other is supply overtaking demand. Same dimension, same
    direction, opposite meanings — a config scalar could only ever get one of them
    right, and got the real SK hynix call wrong."""
    rows = [_row("SKHY", "capacity_addition", direction="up", speaker="SKHY",
                 doc="sk", rid="a"),
            _row("MU", "capacity_addition", direction="up", speaker="MU",
                 doc="mu", rid="b", polarity="refute")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.support_score == 1.0 and a.refute_score == 1.0
    assert a.verdict == "mixed"


def test_every_polarity_call_carries_a_recorded_reason():
    """A polarity without a reason is unauditable, and an unauditable judgement is no
    better than the inverted config it replaced."""
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", doc="sk", rid="a"),
            _row("MU", "capacity_addition", speaker="MU", doc="mu", rid="b",
                 polarity="refute")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert len(a.judgements) == 2
    assert all(j.reason for j in a.judgements)
    assert {j.polarity for j in a.judgements} == {"support", "refute"}
    assert all(j.speaker and j.observation_ids for j in a.judgements)


def test_one_call_restating_a_plan_across_periods_is_one_piece_of_evidence():
    """Gate 1's actual job. SK hynix narrated one expansion plan across 2026 / FY26 /
    FY26Q2 / FY26Q3 / 2027; with `period` in the cluster key those five sentences
    became five independent refutations and outvoted two genuine supporting clusters.
    One plan with a schedule is one piece of evidence."""
    rows = [_row("SKHY", "capacity_addition", speaker="SKHY", doc="sk-call",
                 period=p, rid=f"p{i}")
            for i, p in enumerate(["2026", "FY26", "FY26Q2", "FY26Q3", "2027"])]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 1
    assert len(a.judgements) == 1
    assert len(a.judgements[0].observation_ids) == 5    # members kept, not discarded


def test_unmapped_observations_are_ignored_not_guessed():
    """A fact that maps to no declared dimension must not be forced into one.

    It is still stored (it feeds the induction pool), but it may not silently move a
    claim it has nothing to do with.
    """
    rows = [_row("MU", "", speaker="MU", metric="gross_margin", doc="mu", rid="a"),
            _row("MU", "", speaker="MU", metric="headcount", doc="mu", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "unknown" and a.evidence_clusters == 0


# --- declared stance / silence --------------------------------------------- #
def test_stance_comes_from_the_claim_not_the_document():
    """Every filing is the company's own call, so the EXTRACTED stance is always
    "incumbent". If the engine trusted it, cross-stance corroboration could never be
    satisfied and no claim would ever be confirmed."""
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", stance="incumbent",
                 doc="sk", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", stance="incumbent",
                 otype="counterparty", doc="nv", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.stance_classes == 2         # supplier + customer, per the claim's table
    assert a.verdict == "supportive"


def test_silent_witnesses_are_named_not_ignored():
    """A declared witness saying nothing is a GAP, not neutrality — that is how
    selective disclosure becomes visible."""
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", doc="sk", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", otype="counterparty",
                 doc="nv", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert set(a.silent_witnesses) == {"MU", "MSFT"}
    assert "未发声" in a.note


# --- support / refute must never be netted --------------------------------- #
def test_counter_evidence_is_not_hidden_by_netting():
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", doc="a", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", otype="counterparty",
                 doc="b", rid="b"),
            _row("MU", "hbm_demand", direction="down", speaker="MSFT",
                 otype="counterparty", doc="c", rid="c", polarity="refute"),
            _row("MU", "supply_tightness", direction="down", speaker="MU",
                 doc="d", rid="d", polarity="refute")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "mixed"
    assert a.support_score == 2.0 and a.refute_score == 2.0   # both sides visible
    assert a.dissenters                                        # and named
