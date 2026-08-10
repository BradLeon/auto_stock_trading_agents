"""Knowledge-base review — six detectors over data we already store (hermetic).

The knowledge base is annual-tenor, which is exactly why it rots unnoticed: nothing
in a weekly cycle ever asks whether it is still true. These detectors are the thing
that asks. They may only ever produce a TODO list — none of them edits config.
"""

from datetime import datetime, timedelta, timezone

from ats.chain import kb_review
from ats.config import load_sector_config
from ats.memory import get_store
from ats.schemas.chain import ClaimAssessment, ClusterJudgement, Observation
from ats.schemas.sector import BasketRow, LayerBasket, SectorReview

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _obs(speaker, *, about=None, metric="misc", concept="", period="FY26Q3", span="…"):
    return Observation(
        document_id=f"{speaker}-{metric}-{period}", entity=(about or speaker).upper(),
        source_entity=speaker.upper(), metric=metric, concept=concept, period=period,
        observation_type="reported_actual", stance="incumbent", direction="up",
        evidence_span=span, observed_at=NOW)


def _review(sector, layer_key, rows, *, as_of=NOW):
    return SectorReview(sector=sector, as_of=as_of, baskets=[
        LayerBasket(layer_key=layer_key, as_of=as_of, rows=rows)])


def test_blind_spots_come_from_the_analyst_own_words():
    """The structure analyst is instructed to say "KB 未覆盖" when it scores a name
    blind. It has been saying so all along and nobody collected it."""
    store = get_store()
    store.save_sector_review(_review("ai_hardware", "L3_dc_infra", [
        BasketRow(symbol="MRVL", rationale="KB 未覆盖该标的，给中性分"),
        BasketRow(symbol="COHR", rationale="自有 InP 衬底，垂直整合深")]))

    found = kb_review.blind_spots(load_sector_config("ai_hardware"), store)
    assert len(found) == 1
    assert "MRVL" in found[0].subject and "COHR" not in found[0].subject


def test_blind_spots_survive_a_week_that_scored_a_different_layer():
    """A weekly run usually scores ONE layer. Reading only the newest review would
    report "no blind spots" for every layer that simply did not run this week —
    silence about L3 is not the same as L3 being covered."""
    store = get_store()
    store.save_sector_review(_review("ai_hardware", "L3_dc_infra",
                                     [BasketRow(symbol="VRT", rationale="KB 未覆盖")],
                                     as_of=NOW - timedelta(days=14)))
    store.save_sector_review(_review("ai_hardware", "L5_fab",
                                     [BasketRow(symbol="SKHY", rationale="认证领先")]))

    found = kb_review.blind_spots(load_sector_config("ai_hardware"), store)
    assert [f.subject for f in found] and "VRT" in found[0].subject


def test_unmapped_clusters_ignore_the_income_statement():
    """The unmapped pool is overwhelmingly GAAP line items lifted off earnings
    releases. Clustering them reports "margin, cash, income" every week forever —
    true, useless, and it buries the one row that is actually a new theme."""
    store = get_store()
    for i, sym in enumerate(["AMD", "NVDA", "MU"]):
        store.save_observation(_obs(sym, metric="gross_margin", period=f"FY26Q{i + 1}"))
        store.save_observation(_obs(sym, metric="free_cash_flow", period=f"FY26Q{i + 1}"))
        store.save_observation(_obs(sym, metric="cowos_capacity", period=f"FY26Q{i + 1}",
                                    span="CoWoS remains the binding constraint"))

    subjects = {f.subject for f in kb_review.unmapped_clusters(store)}
    assert subjects & {"cowos", "capacity"}     # either token of the surviving theme
    assert not subjects & {"margin", "gross", "cash", "flow", "free"}


def test_one_theme_is_one_finding_not_one_per_word():
    """`data_center_revenue` yields the tokens `data` and `center` over the same rows.
    Reported separately they read as two independent themes."""
    store = get_store()
    for i, sym in enumerate(["AMD", "NVDA", "MU"]):
        store.save_observation(_obs(sym, metric="data_center_revenue",
                                    period=f"FY26Q{i + 1}"))
    found = kb_review.unmapped_clusters(store)
    assert len(found) == 1
    assert found[0].subject in {"data", "center"}


def test_one_chatty_filing_is_not_a_theme():
    """Cross-company AND cross-quarter, or it is one company's talking point. The
    knowledge base records how the industry works, not what one CFO said in one call."""
    store = get_store()
    for i in range(6):
        store.save_observation(_obs("NVDA", metric="cowos_capacity", period="FY26Q3",
                                    span=f"span {i}"))
    assert kb_review.unmapped_clusters(store) == []


def test_undeclared_relation_is_how_new_topology_arrives():
    """`AMD → ANTHROPIC` was found exactly this way: an earnings release named a
    partner that sat in no chain at all. Topology is annual-tenor knowledge — the most
    durable thing these detectors can produce."""
    store = get_store()
    store.save_observation(_obs("NVDA", about="ANTHROPIC", metric="partnership",
                                span="strategic partnership to deploy 2GW of MI450"))
    store.save_observation(_obs("NVDA", about="TSM", metric="foundry_capacity"))

    found = kb_review.undeclared_relations(load_sector_config("ai_hardware"), store)
    subjects = {f.subject for f in found}
    assert "NVDA → ANTHROPIC" in subjects
    assert "NVDA → TSM" not in subjects         # declared in NVDA's signal_chain


def test_a_speaker_with_no_chain_is_one_finding_not_one_per_counterparty():
    """AMD has no pead config, so every company it names looks "undeclared". Six
    findings would bury the single thing to fix — and while the chain is missing,
    `relation_hint` is empty, so descriptive references resolve to nobody."""
    store = get_store()
    for other in ["ANTHROPIC", "TSM", "MSFT"]:
        store.save_observation(_obs("AMD", about=other, metric=f"rel_{other}"))

    found = [f for f in kb_review.undeclared_relations(
        load_sector_config("ai_hardware"), store) if f.subject.startswith("AMD")]
    assert len(found) == 1
    assert "无 signal_chain" in found[0].subject
    assert all(o in found[0].detail for o in ("ANTHROPIC", "TSM", "MSFT"))


def _assessment(claim_id, judgements, *, layer="L5_fab"):
    return ClaimAssessment(claim_id=claim_id, layer=layer, as_of=NOW,
                           judgements=judgements)


def test_a_dimension_that_never_moves_a_verdict_is_reported():
    """`capacity_addition` collected a full round of evidence and was judged neutral
    every single time. That is not a claim being unproven — it is a dimension asking a
    question the evidence cannot settle, and only a person can reframe it."""
    store = get_store()
    store.save_claim_assessment(_assessment("c1", [
        ClusterJudgement(cluster_key=f"k{i}", polarity="neutral",
                         speaker=s, concept="capacity_addition",
                         reason="扩产是对需求的回应，不构成否定")
        for i, s in enumerate(["AMD", "NVDA", "SKHY", "TSM", "MU", "MSFT"])]))

    found = kb_review.attribution_failures(store)
    assert any("capacity_addition" in f.subject for f in found)


def test_a_dimension_that_discriminates_is_not_reported():
    """The complement, or the detector would fire on every claim that has not yet
    resolved — turning a signal about broken bindings into weekly noise."""
    store = get_store()
    store.save_claim_assessment(_assessment("c1", [
        ClusterJudgement(cluster_key=f"k{i}", polarity=p, speaker=s,
                         concept="supply_tightness", reason="…")
        for i, (s, p) in enumerate([("AMD", "support"), ("NVDA", "neutral"),
                                    ("SKHY", "support"), ("TSM", "refute"),
                                    ("MU", "support"), ("MSFT", "neutral")])]))

    assert not [f for f in kb_review.attribution_failures(store)
                if "supply_tightness" in f.subject]


def test_declared_third_party_sources_are_not_strangers():
    """TW_IC_EXPORT is a customs bureau declared in config/sources.yaml. It appears in
    no ticker list by design, and flagging it every week would train the reader to skip
    the one section whose whole job is to be read."""
    store = get_store()
    store.save_observation(_obs("TW_IC_EXPORT", metric="ic_exports_yoy"))
    store.save_observation(_obs("ASML", about="INTC", metric="highna_adoption",
                                span="Intel Foundry is using High-NA on 18A"))

    found = kb_review.unfamiliar_entities(load_sector_config("ai_hardware"), store)
    subjects = {f.subject for f in found}
    assert "INTC" in subjects
    assert "TW_IC_EXPORT" not in subjects


def test_a_fresh_note_is_not_stale_however_much_evidence_arrived(tmp_path):
    """Age AND accumulation, both. A file edited last week is not due for review no
    matter how busy the quarter was, and an untouched file with no new evidence has
    nothing to be checked against — this signal says "nobody has compared these",
    which is only interesting when there is something to compare against."""
    import os

    cfg = load_sector_config("ai_hardware")
    layer = next(ly for ly in cfg.layers if ly.structure_notes)
    note = tmp_path / "kb.md"
    note.write_text("# 判据", encoding="utf-8")
    layer.structure_notes = {"test": str(note)}
    store = get_store()

    def touch(days_before):
        stamp = (NOW - timedelta(days=days_before)).timestamp()
        os.utime(note, (stamp, stamp))

    touch(200)                                   # old, but no evidence has arrived
    assert kb_review.stale_notes(cfg, store, now=NOW) == []

    for i in range(40):
        store.save_observation(_obs(layer.tickers[0].symbol, metric=f"m{i}"))
    touch(10)                                    # evidence arrived, note is recent
    assert kb_review.stale_notes(cfg, store, now=NOW) == []

    touch(200)                                   # both conditions
    aged = kb_review.stale_notes(cfg, store, now=NOW)
    assert aged and "天未改动" in aged[0].detail and "40 条观测" in aged[0].detail


def test_review_reports_an_empty_result_rather_than_inventing_one():
    """No findings is a real answer. A section that always has something in it is a
    section nobody believes."""
    store = get_store()
    md = "\n".join(kb_review.as_section(
        kb_review.review(load_sector_config("ai_hardware"), store, now=NOW)))
    assert "知识库复核" in md and "本期无待复核项" in md


def test_one_broken_detector_does_not_silence_the_others(monkeypatch):
    """Six independent readings of the same store. A schema drift in one must not take
    the other five off the report — that failure mode is invisible, because a detector
    that reports nothing looks exactly like a detector that found nothing."""
    store = get_store()
    store.save_observation(_obs("NVDA", about="ANTHROPIC", metric="partnership"))
    monkeypatch.setattr(kb_review, "unmapped_clusters",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    found = kb_review.review(load_sector_config("ai_hardware"), store, now=NOW)
    assert any("ANTHROPIC" in f.subject for f in found)
