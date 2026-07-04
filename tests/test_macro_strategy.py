"""Macro strategist — config, assembly, synthesis clamps, store, report, cascade
injection (hermetic, no network/LLM)."""

from datetime import datetime, timezone

import pytest

from ats.agents.macro import assemble, context as macro_context, report, review as macro_review
from ats.agents.macro.outputs import (
    MacroReviewLLMView,
    SectorTiltView,
    ThemeAssessView,
)
from ats.memory import get_store
from ats.schemas.macro import MacroData
from ats.schemas.macro_strategy import MacroConfig, MacroReview, SectorTilt

NOW = datetime.now(timezone.utc)

CFG = MacroConfig(
    name="macro", label="宏观", output_dir="",
    themes=[
        {"key": "fed_policy", "label": "货币政策", "kind": "quant",
         "quant": ["fed_funds", "ust_10y"], "queries": ["FOMC"]},
        {"key": "financial_conditions", "label": "金融条件", "kind": "quant",
         "quant": ["hy_oas", "ig_oas"], "queries": []},
        {"key": "geopolitics", "label": "地缘政治", "kind": "qual",
         "quant": [], "queries": ["Iran conflict"]},
    ],
    search={"max_results_per_query": 2, "recency_days": 14, "max_chars_per_result": 500},
    review={"max_context_chars": 48000},
)


def _view():
    return MacroReviewLLMView(
        regime="risk-off，晚周期，信用利差走阔",
        summary="总评",
        rate_path="美联储 2026 维持，年底或降息一次",
        sector_tilts=[
            SectorTiltView(sector="半导体", stance="低配", rationale="估值透支+高beta"),
            SectorTiltView(sector="公用事业", stance="超配", rationale="防御"),
            SectorTiltView(sector="", stance="超配"),  # empty sector -> dropped
        ],
        asset_implications="股票承压、黄金受益",
        themes=[
            ThemeAssessView(key="fed_policy", direction="持稳", signal="neutral"),
            ThemeAssessView(key="BOGUS", direction="x"),  # unknown -> dropped
        ],
        top_risks=["信用事件"],
    )


def test_config_helpers():
    assert CFG.theme_keys() == {"fed_policy", "financial_conditions", "geopolitics"}


def test_load_macro_config_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    from ats.config import load_macro_config

    with pytest.raises(FileNotFoundError):
        load_macro_config("nope")


def test_assemble_offline_and_live(monkeypatch):
    data = MacroData(as_of=NOW, fed_funds=3.63, ust_10y=4.48, hy_oas=2.75, ig_oas=0.75)
    monkeypatch.setattr("ats.data.macro.fetch", lambda: data)
    monkeypatch.setattr("ats.data.websearch.search_news",
                        lambda q, **k: [{"title": "Iran headline", "url": "u",
                                         "content": "conflict escalates", "published": "2026-07-01"}])
    mc = assemble.build(CFG, live_data=True)
    ctx = mc.as_context()
    assert "fed_funds=3.63" in ctx and "hy_oas=2.75" in ctx        # theme quant fields
    assert "Iran headline" in ctx and "conflict escalates" in ctx  # tavily news
    assert mc.stats()["themes"] == 3

    mc2 = assemble.build(CFG, live_data=False)
    assert "offline" in mc2.as_context()                            # no network


def test_review_clamps_and_persists(monkeypatch):
    monkeypatch.setattr("ats.config.load_macro_config", lambda name="macro": CFG)
    monkeypatch.setattr(macro_review, "run_structured", lambda *a, **k: _view())
    monkeypatch.setattr(assemble, "build", lambda cfg, live_data=True: assemble.MacroContext(cfg=cfg))

    r = macro_review.run("macro", use_llm=True, live_data=False)
    assert [t.key for t in r.themes] == ["fed_policy"]              # bogus theme dropped
    assert {t.sector for t in r.sector_tilts} == {"半导体", "公用事业"}  # empty dropped
    assert r.sector_tilts[0].stance == "低配"
    assert get_store().latest_macro_review("macro").regime == r.regime


def test_review_llm_failure_keeps_prior(monkeypatch):
    monkeypatch.setattr("ats.config.load_macro_config", lambda name="macro": CFG)
    monkeypatch.setattr(assemble, "build", lambda cfg, live_data=True: assemble.MacroContext(cfg=cfg))
    get_store().save_macro_review(MacroReview(name="macro", as_of=NOW, regime="PRIOR"))

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(macro_review, "run_structured", boom)
    assert macro_review.run("macro", use_llm=True, live_data=False).regime == "PRIOR"


def test_store_roundtrip():
    store = get_store()
    store.save_macro_review(MacroReview(name="m", as_of=datetime(2026, 1, 1), regime="old"))
    store.save_macro_review(MacroReview(name="m", as_of=datetime(2026, 2, 1), regime="new"))
    assert store.latest_macro_review("m").regime == "new"
    assert len(store.recent_macro_reviews("m")) == 2


def test_report_render_and_write(tmp_path):
    r = MacroReview(name="macro", as_of=NOW, regime="risk-off", summary="S",
                    rate_path="维持", asset_implications="黄金受益",
                    sector_tilts=[SectorTilt(sector="半导体", stance="低配", rationale="贵")],
                    top_risks=["信用事件"])
    md = report.render(r, CFG)
    assert "半导体" in md and "低配" in md and "risk-off" in md and "黄金受益" in md
    cfg2 = CFG.model_copy(update={"output_dir": str(tmp_path)})
    path = report.write(r, cfg2)
    assert path is not None and "宏观分析-宏观" in path.name
    assert report.write(r, CFG) is None                            # unset dir degrades


def test_injection_and_cascade(monkeypatch):
    store = get_store()
    assert macro_context.prep_block("COHR") == ""                  # no review yet
    assert macro_context.sector_block() == ""

    store.save_macro_review(MacroReview(
        name="macro", as_of=NOW, regime="risk-off 晚周期", rate_path="维持",
        asset_implications="黄金受益",
        sector_tilts=[SectorTilt(sector="半导体", stance="低配", rationale="RA")]))
    assert "risk-off 晚周期" in macro_context.prep_block("COHR")
    assert "利率: 维持" in macro_context.monitor_hint()
    sb = macro_context.sector_block()
    assert "半导体: 低配" in sb and "黄金受益" in sb

    # Stub reviews are not injected.
    store.save_macro_review(MacroReview(name="macro",
                                        as_of=datetime(2027, 1, 1, tzinfo=timezone.utc),
                                        regime="(no-llm)"))
    assert macro_context.prep_block("COHR") == ""


def test_sector_assemble_ingests_macro(monkeypatch):
    """Sector review's assemble picks up the macro background block (cascade)."""
    from ats.agents.sector import assemble as sector_assemble
    from ats.schemas.sector import SectorConfig

    get_store().save_macro_review(MacroReview(
        name="macro", as_of=NOW, regime="MACRO REGIME MARKER",
        sector_tilts=[SectorTilt(sector="半导体", stance="低配")]))
    monkeypatch.setattr("ats.data.industry.fetch_notes", lambda: [])
    scfg = SectorConfig(name="t", label="t", layers=[
        {"key": "L1", "label": "L1", "tickers": [{"symbol": "NVDA"}]}],
        snapshot={"momentum_days": [20], "consensus_for": "none", "sleep_between_tickers": 0},
        review={"static_notes_chars": 100, "insights_per_ticker": 1, "events_lookback_days": 14,
                "events_min_triage": 0.6, "dossier_excerpt_chars": 50})
    sc = sector_assemble.build(scfg, live_data=False)
    assert "MACRO REGIME MARKER" in sc.as_context()                # macro fed into sector


def test_scheduler_macro_before_sector(monkeypatch):
    from datetime import date

    from ats.runtime import scheduler

    order = []
    monkeypatch.setattr("ats.runtime.cli.run_macro_review", lambda name, **k: order.append("macro"))
    monkeypatch.setattr("ats.runtime.cli.run_sector_review", lambda name, **k: order.append("sector"))
    monkeypatch.setattr(scheduler, "_today", lambda: date(2026, 7, 6))   # Monday
    scheduler._macro_weekly()
    scheduler._sector_weekly()
    assert order == ["macro", "sector"]

    order.clear()
    monkeypatch.setattr(scheduler, "_today", lambda: date(2026, 7, 7))   # Tuesday
    scheduler._macro_weekly()
    assert order == []
