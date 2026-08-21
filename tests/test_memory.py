"""Context Memory store + performance tracking (no network)."""

from datetime import datetime, timezone

from ats.memory import compute_performance
from ats.memory.store import TradingMemory
from ats.schemas.memory import PerformanceRecord, TradeLogEntry

NOW = datetime.now(timezone.utc)


def _obs(entity, metric, span, *, document_id="DOC1", concept=""):
    from ats.schemas.chain import Observation

    return Observation(document_id=document_id, entity=entity, metric=metric,
                       concept=concept, observation_type="reported_actual", stance="incumbent",
                       evidence_span=span, observed_at=NOW)


def test_save_and_read_roundtrip():
    mem = TradingMemory(":memory:")
    mem.save_trades([TradeLogEntry(order_id="o1", cycle_id="cycle-1", symbol="NVDA",
                                   action="buy", qty=50, status="filled", submitted_at=NOW)],
                    cycle_id="cycle-1", source="chief", context="{}")
    mem.save_performance(PerformanceRecord(cycle_id="cycle-1", as_of=NOW,
                                           net_liquidation=100000))

    assert mem.last_performance().net_liquidation == 100000
    trades = mem.recent_trades("NVDA")
    assert trades and trades[0]["status"] == "filled" and trades[0]["source"] == "chief"


def test_performance_carries_cumulative_forward():
    prev = PerformanceRecord(cycle_id="c1", as_of=NOW, net_liquidation=100000,
                             cumulative_pnl=0.0)
    # No portfolio -> net liq carried forward, zero daily PnL.
    p = compute_performance(cycle_id="c2", as_of=NOW, portfolio=None, previous=prev,
                            order_results=[], fallback_net_liq=100000)
    assert p.net_liquidation == 100000
    assert p.daily_pnl == 0.0
    assert p.cumulative_pnl == 0.0


def test_pead_events_dedup_is_per_symbol():
    """One peer headline must reach EVERY target whose signal_chain lists that peer.

    Regression: pead_events had a global `id` PRIMARY KEY and append_events deduped on
    id alone, so whichever target fetched an item first owned it and the rest silently
    saw nothing. AMZN sits in 6 targets' signal chains — 5 of them lost every AMZN item.
    """
    from ats.schemas.news import NewsItem

    mem = TradingMemory(":memory:")
    item = NewsItem(id="finnhub:12345", published_at=NOW, source="finnhub",
                    headline="AMZN raises 2026 capex guide", url="http://x")
    targets = ["COHR", "VRT", "LITE", "CRDO", "NVDA", "MSFT"]
    for t in targets:
        assert len(mem.append_events(t, [item])) == 1, f"{t} should receive the item"
        assert mem.count_events(t) == 1
    # Same symbol twice is still idempotent.
    assert mem.append_events("COHR", [item]) == []


def test_observations_by_id_fetches_exactly_the_cited_rows_not_the_whole_entity():
    """The viz bundle knows exactly which observations a claim cited
    (`ClaimAssessment.observation_ids`) and must not over-fetch — a review that cited
    ~2k observations should not drag the other ~4.5k the entity ever produced along
    with it. This is the batch counterpart to `observations()`, which answers a
    different question ("what do we have on this entity")."""
    mem = TradingMemory(":memory:")
    a = _obs("SKHY", "hbm_supply", "cited span")
    b = _obs("SKHY", "hbm_pricing", "also cited")
    c = _obs("SKHY", "unrelated", "never cited")
    for o in (a, b, c):
        mem.save_observation(o)

    fetched = mem.observations_by_id([a.id, b.id])
    assert set(fetched) == {a.id, b.id}
    assert fetched[a.id]["evidence_span"] == "cited span"
    assert c.id not in fetched


def test_observations_by_id_empty_input_returns_empty_without_querying():
    assert TradingMemory(":memory:").observations_by_id([]) == {}


def test_observations_by_id_batches_past_the_sqlite_variable_limit():
    """SQLite's default IN(...) limit is 999 — a review's cited set can plausibly
    exceed that (~2k observations per the design doc), so this must not just work
    for a handful of ids."""
    mem = TradingMemory(":memory:")
    ids = []
    for i in range(1200):
        o = _obs("MU", f"metric{i}", f"span {i}", document_id=f"DOC{i}")
        mem.save_observation(o)
        ids.append(o.id)
    fetched = mem.observations_by_id(ids)
    assert len(fetched) == 1200
    assert all(oid in fetched for oid in ids)


def test_documents_by_id_resolves_exactly_the_referenced_provenance():
    """Counterpart to `observations_by_id`: once the bundle knows which observations
    it cited, it resolves each one's `document_id` to exactly that provenance row —
    not every document ever fetched for the entity."""
    from pathlib import Path

    from ats.data.source_cache import CachedDoc

    mem = TradingMemory(":memory:")
    cited = CachedDoc(symbol="SKHY", period="FY2026Q2", doc_type="transcript",
                      text="…", path=Path("/tmp/skhy-q2.md"), source="defeatbeta",
                      sha256="abc123")
    other = CachedDoc(symbol="SKHY", period="FY2026Q1", doc_type="transcript",
                      text="…", path=Path("/tmp/skhy-q1.md"), source="defeatbeta",
                      sha256="def456")
    mem.save_document(cited)
    mem.save_document(other)

    fetched = mem.documents_by_id([cited.document_id])
    assert set(fetched) == {cited.document_id}
    assert fetched[cited.document_id]["source"] == "defeatbeta"
    assert fetched[cited.document_id]["sha256"] == "abc123"
    assert other.document_id not in fetched


def test_documents_by_id_empty_input_returns_empty_without_querying():
    assert TradingMemory(":memory:").documents_by_id([]) == {}


def test_claim_assessments_on_groups_by_layer_for_the_offline_rebuild():
    """The offline path (`ats sector html <sector> --date`) has no in-memory review to
    read from — this is the query it uses instead. Layers with no rows that day (e.g. a
    layer added after this snapshot) must be absent from the result, not an empty list,
    so the bundle builder can tell 'nothing happened' apart from 'never asked'."""
    from ats.schemas.chain import ClaimAssessment

    mem = TradingMemory(":memory:")
    a1 = ClaimAssessment(claim_id="hbm_supply_tight", layer="L6_memory", as_of=NOW,
                         verdict="supportive")
    a2 = ClaimAssessment(claim_id="hbm_pricing_expand", layer="L6_memory", as_of=NOW,
                         verdict="supportive")
    a3 = ClaimAssessment(claim_id="cloud_capex", layer="L2_cloud", as_of=NOW,
                         verdict="mixed")
    for a in (a1, a2, a3):
        mem.save_claim_assessment(a)

    out = mem.claim_assessments_on(["L6_memory", "L2_cloud", "L1_app"],
                                   NOW.date().isoformat())
    assert [x.claim_id for x in out["L6_memory"]] == ["hbm_supply_tight", "hbm_pricing_expand"]
    assert [x.claim_id for x in out["L2_cloud"]] == ["cloud_capex"]
    assert "L1_app" not in out


def test_claim_assessments_on_empty_layer_keys_returns_empty_without_querying():
    assert TradingMemory(":memory:").claim_assessments_on([], NOW.date().isoformat()) == {}
