"""Context Memory store + performance tracking (no network)."""

from datetime import datetime, timezone

from ats.memory import compute_performance
from ats.memory.store import TradingMemory
from ats.schemas.memory import PerformanceRecord, TradeLogEntry

NOW = datetime.now(timezone.utc)


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


def test_save_claim_assessment_same_day_rerun_overwrites_not_duplicates():
    """A verdict history exists to answer 'when did this turn from mixed to
    contradicted' — the unit of version is the DAY, not the instant. Re-running the
    same review three times on one day (as happened in production on 2026-08-07,
    see save_claim_assessment's docstring) must leave one row per claim, not three."""
    from ats.schemas.chain import ClaimAssessment

    mem = TradingMemory(":memory:")
    for verdict in ("unknown", "mixed", "supportive"):
        mem.save_claim_assessment(ClaimAssessment(claim_id="hbm_supply_tight",
                                                   layer="L6_memory", as_of=NOW,
                                                   verdict=verdict))
    rows = mem.claim_assessments_on(["L6_memory"], NOW.date().isoformat())["L6_memory"]
    assert len(rows) == 1
    assert rows[0].verdict == "supportive"       # last write wins, not the first
