"""Chain evidence — stage 2: the three corroboration gates (hermetic, no LLM).

Covers the acceptance table in docs/CHAIN_EVIDENCE.md §7 阶段二.
"""

from datetime import datetime, timedelta, timezone

from ats.chain.corroborate import corroborate
from ats.schemas.chain import ClaimDef, Horizon, Witness

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
CFG = {"min_clusters": 2, "min_stance_classes": 2, "min_confidence": 0.5}


def _row(entity, metric, *, direction="up", stance="supplier", otype="guidance",
         doc="d1", period="FY26Q3", at=None, conf=1.0, discovery=False, rid=None):
    return {
        "id": rid or f"{doc}:{entity}:{metric}:{period}",
        "document_id": doc, "entity": entity.upper(), "metric": metric,
        "period": period, "observation_type": otype, "stance": stance,
        "direction": direction, "evidence_span": "…", "extraction_confidence": conf,
        "discovery_evidence": 1 if discovery else 0,
        "observed_at": (at or NOW).isoformat(),
    }


def _common(**kw):
    base = dict(id="hbm_supply_tight", kind="common", layer="L5_fab",
                statement="HBM 供给持续紧张", supporting_direction="up",
                metrics=["hbm_capex", "sold_out_ratio", "hbm_capacity"],
                witnesses=[Witness(entity="NVDA", stance="customer"),
                           Witness(entity="MSFT", stance="customer"),
                           Witness(entity="SKHY", stance="supplier"),
                           Witness(entity="MU", stance="supplier")])
    return ClaimDef(**{**base, **kw})


def _relative(**kw):
    base = dict(id="sk_hbm_share", kind="relative", subject="SKHY", layer="L5_fab",
                statement="SK Hynix 维持领先份额", supporting_direction="up",
                direct_metrics=["hbm_share", "customer_qualification", "hbm_asp"],
                witnesses=[Witness(entity="SKHY", stance="incumbent"),
                           Witness(entity="MU", stance="competitor"),
                           Witness(entity="005930.KS", stance="competitor")])
    return ClaimDef(**{**base, **kw})


# --- Gate 1: dedup --------------------------------------------------------- #
def test_ten_reprints_of_one_report_are_one_cluster():
    """Volume is not independence. Ten outlets relaying one number is one witness."""
    rows = [_row("MU", "hbm_capacity", otype="media", stance="supplier",
                 doc=f"news-{i}", rid=f"r{i}") for i in range(10)]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 1
    assert a.verdict == "unknown"          # one cluster cannot clear min_clusters


def test_company_press_release_call_and_deck_are_one_cluster():
    """A company repeating its own figure across channels is not corroboration."""
    rows = [_row("MU", "hbm_capacity", doc=d, rid=f"r{d}")
            for d in ("press-release", "earnings-call", "investor-deck")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 1


def test_same_fact_from_company_and_customer_stays_two_clusters():
    """The inverse guard: collapsing these would destroy real cross-witness evidence."""
    rows = [_row("MU", "hbm_capacity", stance="supplier", doc="mu-call", rid="a"),
            _row("MU", "hbm_capacity", stance="customer", otype="counterparty",
                 doc="nvda-call", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.evidence_clusters == 2
    assert a.verdict == "supportive"


# --- Gate 2: stance -------------------------------------------------------- #
def test_three_sell_side_notes_cannot_confirm():
    """Three sell-side notes are ONE witness class — secondary material never
    contributes a stance, so it cannot carry a claim to a verdict on its own."""
    rows = [_row("MU", "hbm_capex", otype="research", doc=f"note-{i}",
                 period=f"P{i}", rid=f"n{i}") for i in range(3)]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "mixed"            # capped: not confirmed
    assert a.stance_classes == 0
    assert "立场" in a.note


def test_customer_plus_supplier_can_confirm():
    rows = [_row("SKHY", "sold_out_ratio", stance="supplier", doc="sk", rid="a"),
            _row("NVDA", "hbm_capex", stance="customer", otype="counterparty",
                 doc="nvda", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "supportive"
    assert a.stance_classes == 2


def test_single_stance_however_many_names_stays_capped():
    """Two different suppliers still agree from the same economic position."""
    rows = [_row("MU", "hbm_capex", stance="supplier", doc="mu", rid="a"),
            _row("SKHY", "sold_out_ratio", stance="supplier", doc="sk", rid="b")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "mixed" and a.stance_classes == 1


# --- Gate 3: common / relative isolation (the reason this design exists) ---- #
def test_competitor_expansion_does_not_move_our_share_claim():
    """THE case. Micron raising HBM capacity/capex proves industry supply grew.

    It does NOT prove SK Hynix lost share — that inference needs share, qualification
    or ASP evidence about SK Hynix itself. Deriving "trim SK" from "Micron expands"
    is exactly the unsupported step this gate exists to block.
    """
    rows = [_row("MU", "hbm_capacity", stance="supplier", doc="mu-call", rid="a"),
            _row("MU", "hbm_capex", stance="supplier", doc="mu-call", rid="b"),
            _row("005930.KS", "hbm_capacity", stance="supplier", doc="ss", rid="c")]

    common = corroborate(_common(), rows, cfg=CFG)
    relative = corroborate(_relative(), rows, cfg=CFG)

    assert common.verdict != "unknown"          # industry demand/supply DID move
    assert relative.verdict == "unknown"        # our share claim did NOT
    assert relative.evidence_clusters == 0
    assert relative.refute_score == 0.0


def test_direct_share_evidence_does_move_the_relative_claim():
    """The other half: with real evidence about the subject, the claim must move."""
    rows = [_row("SKHY", "hbm_share", direction="down", stance="incumbent",
                 otype="guidance", doc="sk-call", rid="a"),
            _row("SKHY", "customer_qualification", direction="down", stance="competitor",
                 otype="counterparty", doc="ss-call", rid="b")]
    a = corroborate(_relative(), rows, cfg=CFG)
    assert a.verdict == "contradicted"
    assert a.refute_score == 2.0 and a.support_score == 0.0


def test_relative_claim_ignores_non_direct_metrics_about_the_subject():
    """Even about the subject, only the declared direct metrics may move share."""
    rows = [_row("SKHY", "hbm_capacity", stance="incumbent", doc="sk", rid="a"),
            _row("SKHY", "revenue", stance="incumbent", doc="sk", rid="b")]
    a = corroborate(_relative(), rows, cfg=CFG)
    assert a.verdict == "unknown" and a.evidence_clusters == 0


# --- horizon / coverage / reachability ------------------------------------- #
def test_observations_outside_horizon_decay_to_unknown():
    claim = _common(horizon=Horizon(**{"from": datetime(2026, 8, 1).date(),
                                       "to": datetime(2027, 12, 31).date()}))
    stale = NOW - timedelta(days=400)
    rows = [_row("SKHY", "sold_out_ratio", stance="supplier", at=stale, doc="a", rid="a"),
            _row("NVDA", "hbm_capex", stance="customer", otype="counterparty",
                 at=stale, doc="b", rid="b")]
    a = corroborate(claim, rows, cfg=CFG)
    assert a.verdict == "unknown"


def test_coverage_travels_with_the_verdict():
    """"supportive 4/5 reported" and "supportive 1/5" must be distinguishable."""
    rows = [_row("SKHY", "sold_out_ratio", stance="supplier", doc="sk", rid="a"),
            _row("NVDA", "hbm_capex", stance="customer", otype="counterparty",
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
    rows = [_row("SKHY", "sold_out_ratio", stance="supplier", conf=0.2, doc="a", rid="a"),
            _row("NVDA", "hbm_capex", stance="customer", conf=0.3, doc="b", rid="b")]
    assert corroborate(_common(), rows, cfg=CFG).verdict == "unknown"


def test_discovery_evidence_cannot_confirm_the_claim_it_discovered():
    """Anti-hindsight: material that MADE us notice a proposition explains "why look",
    it may never also serve as "it is true"."""
    rows = [_row("SKHY", "sold_out_ratio", stance="supplier", discovery=True,
                 doc="a", rid="a"),
            _row("NVDA", "hbm_capex", stance="customer", otype="counterparty",
                 discovery=True, doc="b", rid="b")]
    assert corroborate(_common(), rows, cfg=CFG).verdict == "unknown"


# --- per-metric polarity --------------------------------------------------- #
def test_metric_polarity_can_invert_within_one_claim():
    """One claim legitimately mixes polarities.

    Under "HBM supply stays tight": lead_time UP supports it, but capacity UP means
    supply is LOOSENING and must count against it. A claim-level direction alone would
    score supply loosening as evidence FOR tightness — confidently inverted.
    """
    claim = _common(metrics=["hbm_lead_time", "hbm_capacity"],
                    supporting_direction="up",
                    metric_polarity={"hbm_capacity": "down"})
    rows = [_row("SKHY", "hbm_lead_time", direction="up", stance="supplier",
                 doc="sk", rid="a"),
            _row("MU", "hbm_capacity", direction="up", stance="supplier",
                 doc="mu", rid="b")]
    a = corroborate(claim, rows, cfg=CFG)
    assert a.support_score == 1.0        # lead_time up  -> tight
    assert a.refute_score == 1.0         # capacity up   -> loosening
    assert a.verdict == "mixed"


def test_metric_without_override_uses_claim_direction():
    claim = _common(metrics=["hbm_lead_time"], supporting_direction="up",
                    metric_polarity={"hbm_capacity": "down"})
    rows = [_row("SKHY", "hbm_lead_time", direction="up", stance="supplier",
                 doc="sk", rid="a"),
            _row("NVDA", "hbm_lead_time", direction="up", stance="customer",
                 otype="counterparty", doc="nv", rid="b")]
    assert corroborate(claim, rows, cfg=CFG).verdict == "supportive"


# --- support / refute must never be netted --------------------------------- #
def test_counter_evidence_is_not_hidden_by_netting():
    rows = [_row("SKHY", "sold_out_ratio", stance="supplier", doc="a", rid="a"),
            _row("NVDA", "hbm_capex", stance="customer", otype="counterparty",
                 doc="b", rid="b"),
            _row("MSFT", "hbm_capex", direction="down", stance="customer",
                 otype="counterparty", doc="c", rid="c"),
            _row("MU", "sold_out_ratio", direction="down", stance="supplier",
                 doc="d", rid="d")]
    a = corroborate(_common(), rows, cfg=CFG)
    assert a.verdict == "mixed"
    assert a.support_score == 2.0 and a.refute_score == 2.0   # both sides visible
    assert a.dissenters                                        # and named
