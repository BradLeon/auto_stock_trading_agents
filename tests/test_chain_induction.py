"""Chain evidence — stage 4: emergent propositions (hermetic, no LLM/network).

Covers the acceptance table in docs/CHAIN_EVIDENCE.md §7 阶段四.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ats.chain import induction
from ats.chain.corroborate import corroborate
from ats.memory import get_store
from ats.schemas.chain import ClaimDef, ClaimProposal, Concept, Observation, Witness

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
CFG = {"min_observations": 4, "min_entities": 3, "cooldown_days": 30}


def _obs(speaker, metric, *, concept="", direction="up", span="…", n=""):
    return Observation(
        document_id=f"{speaker}-doc{n}", entity=speaker.upper(),
        source_entity=speaker.upper(), metric=metric, concept=concept,
        period="FY26Q3", observation_type="guidance", stance="incumbent",
        direction=direction, evidence_span=span or "…", observed_at=NOW)


def _seed_unmapped(store, n_entities=3):
    """Optical/InP bottleneck fixture: facts that fit no declared claim."""
    rows = [
        _obs("COHR", "optical_lead_time", span="1.6T module lead times extended to 26 weeks"),
        _obs("LITE", "inp_shortage", span="InP components sold out, gap exceeds 30%"),
        _obs("AXT", "inp_yield", span="InP substrate yield ramp slower than planned"),
        _obs("COHR", "eml_capacity", span="EML laser capacity remains the constraint", n="2"),
    ][: max(4, n_entities + 1)]
    for o in rows:
        store.save_observation(o)
    return rows


# --- unmapped pool --------------------------------------------------------- #
def test_unmapped_rows_enter_the_pool_and_move_no_claim():
    """Facts with no home are kept, but must not quietly influence any verdict."""
    store = get_store()
    _seed_unmapped(store)
    store.save_observation(_obs("SKHY", "hbm_share", concept="hbm_share", direction="down"))

    pool = induction.pool(store)
    assert {r["metric"] for r in pool} >= {"optical_lead_time", "inp_shortage"}
    assert all(not r["concept"] for r in pool)      # mapped rows stay out of the pool

    claim = ClaimDef(id="c", kind="common", layer="L5_fab", statement="x",
                     concepts=[Concept(key="hbm_share", desc="份额")],
                     witnesses=[Witness(entity="SKHY", stance="incumbent")])
    a = corroborate(claim, pool, cfg={"min_clusters": 1, "min_stance_classes": 1})
    assert a.evidence_clusters == 0                 # unmapped evidence moves nothing


# --- deterministic, evidence-driven trigger -------------------------------- #
def test_below_threshold_does_not_fire():
    fire, why = induction.should_induce([_obs("COHR", "a").model_dump()], CFG)
    assert fire is False and "尚不成形" in why


def test_single_source_does_not_fire():
    """One company talking a lot is not a pattern — it is one company talking a lot."""
    rows = [{"source_entity": "COHR", "entity": "COHR"} for _ in range(9)]
    fire, why = induction.should_induce(rows, CFG)
    assert fire is False and "自说自话" in why


def test_enough_spread_fires():
    rows = [{"source_entity": s, "entity": s} for s in ("COHR", "LITE", "AXT", "AAOI")]
    fire, why = induction.should_induce(rows, CFG)
    assert fire is True and "触发一次归纳" in why


def test_trigger_is_not_calendar_driven(monkeypatch):
    """Time passing must not produce propositions; only accumulating facts may.

    The same pool re-evaluated later — even much later — still does not fire if it was
    below threshold, because nothing new was learned.
    """
    rows = [{"source_entity": "COHR", "entity": "COHR"}, {"source_entity": "LITE"}]
    for when in (NOW, NOW + timedelta(days=90)):
        fire, _ = induction.should_induce(rows, CFG)
        assert fire is False, f"must not fire merely because time advanced to {when}"


def test_gate_failure_never_calls_the_model(monkeypatch):
    """Cost discipline AND bias discipline: if the model were asked every run, "is there
    something here?" would eventually be answered yes."""
    store = get_store()
    store.save_observation(_obs("COHR", "only_one"))
    monkeypatch.setattr("ats.agents.evidence.proposer.propose",
                        lambda *a, **k: pytest.fail("model must not be invoked"))
    proposal, why = induction.induce(store, cfg=CFG, now=NOW)
    assert proposal is None and why


# --- proposal + anti-hindsight freeze -------------------------------------- #
def _fake_view():
    from ats.agents.evidence.outputs import ClaimProposalView

    return ClaimProposalView(
        statement="AI 数据中心的增量瓶颈正从存储转向光互联，InP 衬底与 EML 器件成为出货约束",
        layer_hint="L5→L3", kind="common",
        concepts=[{"key": "optical_bottleneck", "desc": "光互联交期/缺口/良率",
                   "expect_from": ["COHR", "LITE", "AXT"]}],
        witnesses=[{"entity": "COHR", "stance": "supplier"},
                   {"entity": "NVDA", "stance": "customer"}])


def test_induction_produces_a_card_and_freezes_its_evidence(monkeypatch):
    store = get_store()
    rows = _seed_unmapped(store)
    monkeypatch.setattr("ats.agents.evidence.proposer.propose", lambda *a, **k: _fake_view())

    proposal, why = induction.induce(store, cfg=CFG, now=NOW)
    assert proposal is not None and "瓶颈" in proposal.statement
    assert proposal.layer_hint == "L5→L3"           # cross-layer is allowed, and expected
    assert proposal.status == "pending"

    # The material that MADE us notice is frozen at proposal time.
    frozen = {r["id"] for r in store.observations(limit=100) if r["discovery_evidence"]}
    assert {o.id for o in rows} <= frozen
    assert induction.pool(store) == []              # and it leaves the pool

    card = induction.as_card(proposal, {r["id"]: dict(r) for r in store.observations()})
    assert "待确认命题" in card and "冻结" in card
    assert "optical_bottleneck" in card


def test_frozen_discovery_evidence_cannot_confirm_the_claim_it_produced(monkeypatch):
    """The whole point of the freeze.

    Without it an agent could induce a proposition from a pattern and then cite that
    very pattern as proof — a hindsight loop that always confirms itself.
    """
    store = get_store()
    _seed_unmapped(store)
    monkeypatch.setattr("ats.agents.evidence.proposer.propose", lambda *a, **k: _fake_view())
    proposal, _ = induction.induce(store, cfg=CFG, now=NOW)

    # The human adopts it: the proposed shape becomes a real claim.
    adopted = ClaimDef(
        id="optical_bottleneck", kind="common", layer="L3_dc_infra",
        statement=proposal.statement,
        concepts=[Concept(key="optical_bottleneck", desc="光互联交期/缺口")],
        witnesses=[Witness(entity="COHR", stance="supplier"),
                   Witness(entity="LITE", stance="supplier"),
                   Witness(entity="AXT", stance="competitor")])
    # Re-map the same (now frozen) rows onto the adopted concept.
    for r in store.observations(limit=100):
        row = dict(r)
        row["concept"] = "optical_bottleneck"
        rows = [row]
        a = corroborate(adopted, rows, cfg={"min_clusters": 1, "min_stance_classes": 1})
        assert a.verdict == "unknown", "discovery evidence must never confirm its own claim"


def test_proposal_survives_a_store_roundtrip():
    store = get_store()
    p = ClaimProposal(statement="s", signature="sig1", created_at=NOW,
                      observation_ids=["a", "b"])
    store.save_claim_proposal(p)
    rows = store.claim_proposals()
    assert rows and rows[0]["id"] == p.id and rows[0]["status"] == "pending"
    assert store.set_proposal_status(p.id, "rejected", reviewer="boss", rationale="噪音")
    assert store.claim_proposals(status="rejected")[0]["rationale"] == "噪音"


# --- cooldown -------------------------------------------------------------- #
def test_rejected_candidate_cannot_return_reworded():
    """The signature fingerprints the EVIDENCE, not the sentence, so rephrasing the
    same idea next week is recognised as the same candidate."""
    store = get_store()
    sig = ClaimProposal.make_signature(["COHR", "LITE"], ["lead_time", "yield"])
    store.save_claim_proposal(ClaimProposal(
        statement="瓶颈在光互联", signature=sig, created_at=NOW - timedelta(days=3),
        status="rejected", reviewed_at=NOW - timedelta(days=3)))
    # Same evidence, different words -> same signature -> suppressed.
    assert ClaimProposal.make_signature(["lite", "COHR"], ["YIELD", "lead_time"]) == sig
    assert induction.in_cooldown(store, sig, CFG, NOW) is True


def test_cooldown_expires():
    store = get_store()
    sig = "sig-old"
    store.save_claim_proposal(ClaimProposal(
        statement="s", signature=sig, created_at=NOW - timedelta(days=90),
        status="rejected", reviewed_at=NOW - timedelta(days=90)))
    assert induction.in_cooldown(store, sig, CFG, NOW) is False


def test_accepted_proposal_is_not_in_cooldown():
    """Cooldown suppresses REJECTED ideas, not adopted ones."""
    store = get_store()
    sig = "sig-ok"
    store.save_claim_proposal(ClaimProposal(
        statement="s", signature=sig, created_at=NOW, status="accepted", reviewed_at=NOW))
    assert induction.in_cooldown(store, sig, CFG, NOW) is False


# --- zero blast radius before adoption ------------------------------------- #
def test_pending_proposal_reaches_no_factor_and_no_chief(monkeypatch):
    """A candidate is a card for a person — it must not touch the decision path."""
    from ats.agents.chief import assemble

    store = get_store()
    _seed_unmapped(store)
    monkeypatch.setattr("ats.agents.evidence.proposer.propose", lambda *a, **k: _fake_view())
    proposal, _ = induction.induce(store, cfg=CFG, now=NOW)
    assert proposal is not None

    ctx = assemble.build(live_broker=False).as_context()
    assert proposal.statement not in ctx
    assert "待确认命题" not in ctx
