"""Sector analyst — config, assembly, synthesis clamps, store, report, injection
(hermetic, no network/LLM)."""

from datetime import datetime, timezone

import pytest

from ats.agents.sector import assemble, context as sector_context, report, review as sector_review
from ats.agents.sector.outputs import (
    CompanyCallView,
    LayerAssessView,
    SectorReviewLLMView,
)
from ats.memory import get_store
from ats.schemas.sector import CompanyCall, LayerAssessment, SectorConfig, SectorReview

NOW = datetime.now(timezone.utc)

CFG = SectorConfig(
    name="test_sector", label="测试行业", output_dir="",
    layers=[
        {"key": "L1", "label": "L1 应用", "question": "q1",
         "tickers": [{"symbol": "GOOGL"}]},
        {"key": "L2", "label": "L2 云", "question": "q2",
         "tickers": [{"symbol": "GOOGL"}, {"symbol": "MSFT"}]},
        {"key": "L3", "label": "L3 光互联",
         "tickers": [{"symbol": "COHR", "note": "光模块"}]},
    ],
    snapshot={"momentum_days": [20, 60], "consensus_for": "pead_targets",
              "sleep_between_tickers": 0},
    review={"static_notes_chars": 100, "insights_per_ticker": 3,
            "events_lookback_days": 14, "events_min_triage": 0.6,
            "dossier_excerpt_chars": 50},
)


def _view():
    return SectorReviewLLMView(
        regime="L3 光互联是当前瓶颈",
        summary="总体景气",
        layers=[
            LayerAssessView(key="L3", boom_score=120, signal="bullish",
                            supply_demand="紧张"),          # clamp to 100
            LayerAssessView(key="BOGUS", boom_score=50),     # unknown key -> dropped
        ],
        company_calls=[
            CompanyCallView(symbol="COHR", layer="L3", stance="增持",
                            conviction=1.7, rationale="瓶颈受益"),  # clamp to 1
            CompanyCallView(symbol="ZZZZ", stance="增持", conviction=0.9),  # non-universe drop
            CompanyCallView(symbol="MSFT", stance="爆买", conviction=0.5),  # bad stance -> 持有
        ],
        rotation_advice="加 L3 减 L2",
        top_risks=["周期见顶"],
    )


def test_sector_config_helpers():
    assert CFG.all_symbols() == ["GOOGL", "MSFT", "COHR"]   # dedup, layer order
    assert CFG.layer_of("COHR") == "L3"
    assert CFG.layer_of("GOOGL") == "L1"                     # first layer wins
    assert CFG.layer_of("XXXX") is None


def test_load_sector_config_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    from ats.config import load_sector_config

    with pytest.raises(FileNotFoundError):
        load_sector_config("nope")


def test_is_pead_target(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "pead").mkdir()
    (tmp_path / "pead" / "COHR.yaml").write_text("symbol: COHR\n", encoding="utf-8")
    from ats.config import is_pead_target

    assert is_pead_target("COHR") and is_pead_target("cohr")
    assert not is_pead_target("NVDA")


def test_assemble_offline_reads_store(monkeypatch):
    from ats.config import load_pead_config
    from ats.data import industry
    from ats.schemas.news import NewsItem
    from ats.schemas.pead import ExpectationSet, PeadDossier

    store = get_store()
    # Seed a COHR dossier whose narrative tail is distinctive.
    pc = load_pead_config("COHR")
    narrative = "OLD HEAD " + "x" * 100 + " FRESH TAIL MARKER"
    store.save_dossier(PeadDossier(
        symbol="COHR", fiscal_label=pc.fiscal_label, phase="prep", updated_at=NOW,
        expectation_set=ExpectationSet(symbol="COHR", fiscal_label=pc.fiscal_label,
                                       as_of=NOW, narrative=narrative)))
    # High- and low-triage events.
    store.append_events("COHR", [
        NewsItem(id="hot1", source="finnhub", headline="InP supply deal", published_at=NOW),
        NewsItem(id="cold1", source="finnhub", headline="listicle noise", published_at=NOW)])
    store.set_triage({"hot1": (0.9, "capex"), "cold1": (0.1, "noise")})
    monkeypatch.setattr(industry, "fetch_notes",
                        lambda: [("map.md", "STATIC INDUSTRY KNOWLEDGE " * 20)])
    monkeypatch.setattr("ats.config.is_pead_target", lambda s: s == "COHR")

    sc = assemble.build(CFG, live_data=False)
    ctx = sc.as_context()
    assert "FRESH TAIL MARKER" in ctx and "OLD HEAD" not in ctx   # tail excerpt
    assert "InP supply deal" in ctx and "listicle noise" not in ctx  # triage filter
    assert "COHR [PEAD]" in ctx
    assert len(sc.static_notes) == 100                             # static cap
    assert "(offline)" in ctx                                      # no yfinance


def test_review_clamps_and_persists(monkeypatch):
    monkeypatch.setattr("ats.config.load_sector_config", lambda name="x": CFG)
    monkeypatch.setattr(sector_review, "run_structured", lambda *a, **k: _view())
    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.SectorContext(cfg=cfg))

    r = sector_review.run("test_sector", use_llm=True, live_data=False)
    assert [a.key for a in r.layers] == ["L3"]                    # bogus key dropped
    assert r.layers[0].boom_score == 100.0                        # clamped
    cohr = r.call_for("COHR")
    assert cohr.conviction == 1.0                                 # clamped
    assert r.call_for("ZZZZ") is None                             # non-universe dropped
    assert r.call_for("MSFT").stance == "持有"                    # bad stance normalized
    assert r.call_for("MSFT").layer == "L2"                       # layer inferred
    assert get_store().latest_sector_review("test_sector").regime == r.regime


def test_review_llm_failure_keeps_prior(monkeypatch):
    monkeypatch.setattr("ats.config.load_sector_config", lambda name="x": CFG)
    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.SectorContext(cfg=cfg))
    prior = SectorReview(sector="test_sector", as_of=NOW, regime="PRIOR REGIME")
    get_store().save_sector_review(prior)

    def boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(sector_review, "run_structured", boom)
    r = sector_review.run("test_sector", use_llm=True, live_data=False)
    assert r.regime == "PRIOR REGIME"                             # prior returned
    assert get_store().latest_sector_review("test_sector").regime == "PRIOR REGIME"


def test_store_roundtrip_and_history():
    store = get_store()
    store.save_sector_review(SectorReview(sector="s", as_of=datetime(2026, 1, 1), regime="old"))
    store.save_sector_review(SectorReview(sector="s", as_of=datetime(2026, 2, 1), regime="new"))
    store.save_sector_review(SectorReview(sector="s", as_of=datetime(2026, 2, 1), regime="new2"))
    assert store.latest_sector_review("s").regime == "new2"       # replace same as_of
    assert len(store.recent_sector_reviews("s")) == 2


def test_report_render_and_write(tmp_path, monkeypatch):
    monkeypatch.setattr("ats.config.is_pead_target", lambda s: s == "COHR")
    r = SectorReview(
        sector="test_sector", as_of=NOW, regime="R", summary="S",
        layers=[LayerAssessment(key="L3", label="L3 光互联", boom_score=80,
                                supply_demand="紧张", signal="bullish")],
        company_calls=[CompanyCall(symbol="COHR", layer="L3", stance="增持",
                                   conviction=0.6, rationale="瓶颈受益")],
        rotation_advice="加 L3", top_risks=["周期"])
    md = report.render(r, CFG)
    assert "L3 光互联" in md and "**COHR**" in md and "增持" in md and "加 L3" in md

    cfg2 = CFG.model_copy(update={"output_dir": str(tmp_path)})
    path = report.write(r, cfg2)
    assert path is not None and path.exists() and "行业分析-测试行业" in path.name
    assert report.write(r, CFG) is None                           # unset dir degrades


def test_injection_blocks(monkeypatch):
    store = get_store()
    assert sector_context.prep_block("test_sector", "COHR") == ""   # no review yet
    assert sector_context.monitor_hint("COHR", "test_sector") == ""

    monkeypatch.setattr("ats.config.load_sector_config", lambda name="x": CFG)
    store.save_sector_review(SectorReview(
        sector="test_sector", as_of=NOW, regime="L3 是瓶颈", summary="SUM",
        layers=[LayerAssessment(key="L3", label="L3 光互联", boom_score=80, signal="bullish")],
        company_calls=[CompanyCall(symbol="COHR", layer="L3", stance="增持",
                                   conviction=0.6, rationale="RA")],
        rotation_advice="ROT"))
    block = sector_context.prep_block("test_sector", "COHR")
    assert "L3 是瓶颈" in block and "增持" in block and "ROT" in block
    hint = sector_context.monitor_hint("COHR", "test_sector")
    assert "L3 是瓶颈" in hint and "景气 80" in hint and len(hint) <= 280

    # Stub reviews (no-llm) are not injected.
    store.save_sector_review(SectorReview(sector="test_sector",
                                          as_of=datetime(2027, 1, 1, tzinfo=timezone.utc),
                                          regime="(no-llm)"))
    assert sector_context.prep_block("test_sector", "COHR") == ""


def test_scheduler_sector_weekly(monkeypatch):
    from datetime import date

    from ats.runtime import scheduler

    calls = []
    monkeypatch.setattr("ats.runtime.cli.run_sector_review",
                        lambda name, **k: calls.append(name))
    monkeypatch.setattr(scheduler, "_today", lambda: date(2026, 7, 6))   # a Monday
    scheduler._sector_weekly()
    assert calls == ["ai_hardware"]

    calls.clear()
    monkeypatch.setattr(scheduler, "_today", lambda: date(2026, 7, 7))   # Tuesday
    scheduler._sector_weekly()
    assert calls == []


# --------------------------------------------------------------------------- #
# Cross-sectional layer basket (hermetic — no network; drives rank_cohort)
# --------------------------------------------------------------------------- #
def test_cross_section_rank_and_sizing():
    from ats.agents.sector.cross_section import FactorRow, rank_cohort

    rows = [
        # strong: high growth/margins, cheap PEG, positive momentum + revisions
        FactorRow(symbol="AAA", market_cap=40e9, beta=1.5, rev_growth=1.2,
                  gross_margin=0.65, op_margin=0.35, fwd_pe=25, mom_60d=15, rating_delta=0.4),
        FactorRow(symbol="BBB", market_cap=60e9, beta=1.2, rev_growth=0.4,
                  gross_margin=0.45, op_margin=0.20, fwd_pe=40, mom_60d=-5, rating_delta=0.1),
        FactorRow(symbol="CCC", market_cap=8e9, beta=3.5, rev_growth=0.2,
                  gross_margin=0.30, op_margin=-0.05, fwd_pe=38, mom_60d=-30, rating_delta=-0.2),
        FactorRow(symbol="DDD"),   # data desert -> excluded
    ]
    rank_cohort(rows, layer_cap=0.10, single_name_cap_frac=0.40)
    by = {r.symbol: r for r in rows}

    # data desert flagged, ranked last, zero weight
    assert by["DDD"].data_ok is False
    assert by["DDD"].weight == 0.0
    assert by["DDD"].rank == 4

    # ranking monotonic with composite (best name first)
    assert by["AAA"].rank == 1
    ranked = sorted((r for r in rows if r.data_ok), key=lambda r: r.rank)
    comps = [r.composite for r in ranked]
    assert comps == sorted(comps, reverse=True)

    # weights sum to the layer cap and respect the single-name cap
    assert abs(sum(r.weight for r in rows) - 0.10) < 1e-9
    assert all(r.weight <= 0.10 * 0.40 + 1e-9 for r in rows)
