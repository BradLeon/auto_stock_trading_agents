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


# --- Gate 2: stance, as a GRADE ------------------------------------------- #
def test_three_sell_side_notes_cannot_confirm():
    """Three sell-side notes are ONE witness class — secondary material never
    contributes a stance, so it cannot carry a claim to a verdict on its own.

    This survived gate 2 becoming a grade, and it is the test that nearly did not:
    the speaker floor that now substitutes for stance diversity was first written over
    ALL clusters, which let three research houses clear it and confirm the claim
    through the back door. The floor counts PRIMARY speakers only, for the same reason
    the stance count always has.
    """
    # Three DIFFERENT houses, so gate 1 legitimately keeps them apart and the test
    # exercises gate 2 rather than tripping the cluster-count floor first.
    rows = [_row("MU", "supply_tightness", otype="research", speaker=f"HOUSE{i}",
                 doc=f"note-{i}", rid=f"n{i}") for i in range(3)]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 3
    assert a.verdict == "unknown"          # withheld: no primary testimony at all
    assert a.stance_classes == 0 and a.speakers == []
    assert "立场" in a.note


def test_customer_plus_supplier_can_confirm():
    rows = [_row("SKHY", "supply_tightness", speaker="SKHY", doc="sk", rid="a"),
            _row("SKHY", "hbm_demand", speaker="NVDA", otype="counterparty",
                 doc="nvda", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "supportive"
    assert a.stance_classes == 2
    assert a.basis == "corroborated"       # two vantage points agreed


def test_two_speakers_sharing_a_vantage_are_still_too_few():
    """Two suppliers agreeing from the same economic position is not enough to stand
    on its own — the substitute for vantage diversity is source multiplicity, and two
    is below the floor."""
    rows = [_row("MU", "supply_tightness", speaker="MU", doc="mu", rid="a"),
            _row("SKHY", "supply_tightness", speaker="SKHY", doc="sk", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "unknown" and a.unresolved_reason == "single_stance"
    assert a.stance_classes == 1 and a.basis == "thin"


def test_enough_independent_filers_resolve_on_one_vantage():
    """The change this whole grade exists for.

    Four competing suppliers agreeing is not self-confirmation — they are the parties
    least able to coordinate a story, since any one of them profits by contradicting
    the others. Returning `mixed` over 4 support / 0 refute called that a conflict.
    It resolves now, carrying `self_reported` so the caveat is never lost.
    """
    rows = [_row("MU", "supply_tightness", speaker=s, doc=f"d{s}", rid=f"r{s}")
            for s in ("MU", "SKHY", "005930.KS", "WDC")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "supportive"
    assert a.basis == "self_reported"      # graded, not vetoed
    assert a.stance_classes == 1 and len(a.speakers) == 4
    assert a.unresolved_reason == ""       # nothing is unresolved — it resolved
    assert "仅自述" in a.note                # and the caveat is visible


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

    # Assert on CLUSTERS, not on the verdict word. The industry claim legitimately
    # reaches this evidence; the share claim must never see it at all. (This used to
    # compare verdicts, which worked only by accident: both sides can now read
    # `unknown` for entirely different reasons — too few independent filers on one,
    # nothing admissible whatsoever on the other — and that difference is the point.)
    assert common.evidence_clusters > 0         # industry demand/supply DID move
    assert relative.evidence_clusters == 0      # our share claim saw nothing
    assert relative.verdict == "unknown"
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
    assert "具名例外" in a.note                 # …as an exception, not a failed challenge


def test_a_clear_majority_names_the_exception_instead_of_going_unresolved():
    """The live case this rule was rewritten for.

    `capex_funding_quality` read 3 support : 12 refute and was called 分歧, because the
    minority hit `>= 3 clusters` and `>= 20% share` at exactly both thresholds — while
    a sibling claim passed at 18%. Two percentage points decided between "the evidence
    conflicts" and "the evidence is clear", and the adjudicator's own wobble moved the
    same claim across the line between runs.

    What the minority is actually worth is being NAMED: "every hyperscaler's cash flow
    is tight except Microsoft" is a finding about Microsoft, and averaging it into
    「未消解」 throws away the most informative row in the claim.
    """
    claim = _common(
        concepts=[Concept(key="external_funding_need", desc="为 capex 举债")],
        witnesses=[Witness(entity=e, stance="customer")
                   for e in ("GOOG", "AMZN", "META", "ORCL", "MSFT")])
    rows = [_row("GOOG", "external_funding_need", speaker=s, doc=f"d{s}", rid=f"r{s}",
                 polarity="refute")
            for s in ("GOOG", "AMZN", "META", "ORCL")]
    rows += [_row("GOOG", "external_funding_need", speaker="MSFT", doc="dm", rid="rm",
                  polarity="support")]
    a = corroborate(claim, rows, cfg=CFG)

    assert a.verdict == "contradicted"        # 4:1 — the direction is callable
    assert a.dissenters == ["MSFT"]           # and the exception is named
    assert "具名例外" in a.note and "MSFT" in a.note
    # The dissenting evidence itself survives for downstream agents to read.
    assert any(j.polarity == "support" and j.speaker == "MSFT" for j in a.judgements)


def test_a_split_too_close_to_call_is_still_mixed():
    """Naming the exception must not make dissent toothless. When the majority is not
    even twice the minority the direction genuinely cannot be called, and `mixed` keeps
    exactly that meaning — nothing weaker."""
    rows = [_row("MU", concept, speaker=who, doc=f"{who}-{concept}", rid=f"{who}{concept}")
            for who, concepts in (("SKHY", ("supply_tightness", "capacity_addition")),
                                  ("MU", ("supply_tightness", "capacity_addition")),
                                  ("MSFT", ("hbm_demand",)))
            for concept in concepts]
    rows += [_row("MU", "hbm_demand", speaker=who, otype="counterparty",
                  doc=f"{who}-d", rid=f"{who}x", polarity="refute")
             for who in ("NVDA", "SKHY", "MU")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "mixed" and a.unresolved_reason == "dissent"
    assert "方向无法判定" in a.note              # 5 vs 3 = 1.7:1, below the ratio


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


def test_conflict_and_thinness_are_told_apart():
    """`mixed` may now mean exactly one thing: the evidence conflicts.

    Evidence that is one-sided but rests on one vantage point used to land in `mixed`
    too, and rendering both as 「分歧」 told the reader the filings disagreed when they
    did not. That state is now either a resolved verdict carrying `self_reported`, or —
    when too few independent filers spoke — `unknown` with the reason attached.
    """
    from ats.chain.report import verdict_mark
    from ats.schemas.chain import ClaimAssessment

    dissent = ClaimAssessment(claim_id="c", as_of=NOW, verdict="mixed",
                              unresolved_reason="dissent")
    thin = ClaimAssessment(claim_id="c", as_of=NOW, verdict="unknown",
                           unresolved_reason="single_stance")
    assert "分歧" in verdict_mark(dissent)
    assert "分歧" not in verdict_mark(thin) and "未确认" in verdict_mark(thin)
    # Assessments stored before the fields existed must still render.
    assert verdict_mark(ClaimAssessment(claim_id="c", as_of=NOW, verdict="mixed"))


def test_a_self_reported_verdict_never_reads_like_a_corroborated_one():
    """The caveat replaced a veto, so it has to be visible. If `✅ 印证` and a verdict
    resting on one vantage point rendered identically, this change would have quietly
    made the output worse rather than better."""
    from ats.chain.report import verdict_mark
    from ats.schemas.chain import ClaimAssessment

    corro = ClaimAssessment(claim_id="c", as_of=NOW, verdict="supportive",
                            basis="corroborated")
    self_rep = ClaimAssessment(claim_id="c", as_of=NOW, verdict="supportive",
                               basis="self_reported")
    assert verdict_mark(corro) != verdict_mark(self_rep)
    assert "仅自述" in verdict_mark(self_rep)


def test_withheld_verdict_says_which_way_the_evidence_leans():
    """"Unconfirmed" without a direction reads as "we learned nothing". A claim refuted
    by two independent filings and a claim with no directional evidence must not
    produce the same sentence — the first is a finding, the second is a gap."""
    claim = _common(witnesses=[Witness(entity="NVDA", stance="customer"),
                               Witness(entity="MSFT", stance="customer")])
    rows = [_row("NVDA", "supply_tightness", speaker=s, doc=f"d{i}", rid=f"r{i}",
                 polarity="refute")
            for i, s in enumerate(["NVDA", "MSFT", "NVDA", "MSFT"])]
    a = corroborate(claim, rows, cfg=CFG)

    assert a.verdict == "unknown"                  # 2 speakers: below the floor
    assert a.unresolved_reason == "single_stance"  # NOT a disagreement
    assert a.stance_classes == 1
    assert "一边倒" in a.note and "反驳" in a.note and "独立说话人" in a.note


# --- The single-vantage question, with no per-claim override --------------- #
def _one_vantage(**kw):
    """A claim whose question admits a single vantage point by construction."""
    base = dict(id="capex_funding_quality", kind="common", layer="L2_cloud",
                statement="capex 由内生现金流支撑",
                concepts=[Concept(key="external_funding_need", desc="为 capex 举债")],
                witnesses=[Witness(entity=e, stance="customer")
                           for e in ("GOOG", "MSFT", "AMZN", "META", "ORCL")])
    return ClaimDef(**{**base, **kw})


def test_a_single_vantage_question_resolves_without_being_declared():
    """"Is this company's capex funded internally" can only be answered by the company
    whose capex it is — suppliers and customers cannot see its balance sheet. It used to
    need a hand-written `min_stance_classes: 1` on the claim to escape the veto, which
    cost a judgement call ("is this single-vantage BY CONSTRUCTION?") on every claim
    anyone wrote. Four independent filers now carry it on their own.
    """
    rows = [_row("GOOG", "external_funding_need", speaker=s, doc=f"d{i}", rid=f"r{i}",
                 polarity="refute")
            for i, s in enumerate(["GOOG", "MSFT", "AMZN", "META"])]

    a = corroborate(_one_vantage(), rows, cfg=CFG)
    # Refuted 4:0 — and the claim's statement is the bullish assumption, so resolving
    # it means falsifying it.
    assert a.verdict == "contradicted"
    assert a.basis == "self_reported" and a.stance_classes == 1
    assert len(a.speakers) == 4


def test_one_vantage_still_needs_several_independent_filers():
    """Source multiplicity is what substitutes for vantage diversity, so it has to be a
    real floor. A couple of interested parties may still not confirm a claim about
    themselves.

    Note where the boundary actually bites: ONE speaker never gets this far — gate 1
    collapses that company's sentences into a single cluster and `min_clusters` rejects
    it as `unknown`. The case this floor exists for is two filers, which clears dedup
    but is still too thin to stand without a second vantage point.
    """
    from ats.chain.corroborate import MIN_INDEPENDENT_SPEAKERS

    def rows_from(*speakers):
        return [_row("GOOG", "external_funding_need", speaker=s, doc=f"d{s}",
                     rid=f"r{s}", polarity="refute") for s in speakers]

    thin = rows_from("GOOG", "MSFT")               # 2 clusters: clears gate 1...
    a = corroborate(_one_vantage(), thin, cfg=CFG)
    assert a.evidence_clusters == 2                # ...but below the speaker floor
    assert a.verdict == "unknown" and a.unresolved_reason == "single_stance"

    enough = rows_from(*["GOOG", "MSFT", "AMZN", "META"][:MIN_INDEPENDENT_SPEAKERS])
    b = corroborate(_one_vantage(), enough, cfg=CFG)
    assert b.verdict == "contradicted" and b.basis == "self_reported"


def test_a_single_vantage_verdict_names_the_filers_it_rests_on():
    """The note has to carry both halves of the trade: which single vantage point this
    is, and how many independent filers were accepted in place of a second one. A reader
    who cannot see the count cannot judge whether the substitution was earned."""
    rows = [_row("GOOG", "external_funding_need", speaker=s, doc=f"d{s}", rid=f"r{s}",
                 polarity="refute") for s in ("GOOG", "MSFT", "AMZN", "META")]
    a = corroborate(_one_vantage(), rows, cfg=CFG)
    assert "仅自述" in a.note and "customer" in a.note
    assert "4 个独立说话人" in a.note
    assert all(s in a.note for s in ("GOOG", "MSFT", "AMZN", "META"))
