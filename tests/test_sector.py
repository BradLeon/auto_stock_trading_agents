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


def test_coverage_is_declared_not_inferred_from_files(monkeypatch, tmp_path):
    """Coverage must come from an explicit list, never from a leftover config file.

    Inferring it from the filesystem is what made MSFT/AMZN *look* tracked — tagged
    [PEAD] in the sector review, consensus fetched — while no scheduler loop ever
    read their earnings. A stale file is not a decision to cover something.
    """
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "pead").mkdir()
    # A per-ticker file with NO membership anywhere: pure leftover.
    (tmp_path / "pead" / "AXT.yaml").write_text("symbol: AXT\n", encoding="utf-8")
    (tmp_path / "pead.yaml").write_text(
        "targets: [COHR]\nobserve: [MU]\n", encoding="utf-8")
    from ats.config import is_pead_covered, is_pead_target, pead_observe_list

    # tradable target
    assert is_pead_target("COHR") and is_pead_target("cohr")
    assert is_pead_covered("COHR")
    # evidence-only: covered, but NOT tradable
    assert not is_pead_target("MU")
    assert is_pead_covered("MU") and is_pead_covered("mu")
    assert pead_observe_list() == ["MU"]
    # leftover file: neither
    assert not is_pead_target("AXT") and not is_pead_covered("AXT")
    assert not is_pead_target("NVDA") and not is_pead_covered("NVDA")


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


def test_sector_analyst_gets_the_criteria_before_the_evidence(monkeypatch, tmp_path):
    """The curated notes used to reach only the cross-section's structure analyst, while
    the sector analyst re-derived the same criteria from 36k of raw research every week.

    Order is load-bearing and mirrors structure.assess: criteria first (how to weigh a
    reading), ledger after (what this quarter's reading was). When they disagree the
    later block is the one still in view — so the evidence must not be pushed above it.
    """
    from ats.data import industry

    note = tmp_path / "kb.md"
    note.write_text("护城河判据：拥有瓶颈环节 > 客户分散度 > 组装规模", encoding="utf-8")
    cfg = CFG.model_copy(deep=True)
    cfg.layers[1].structure_notes = {"云": str(note)}
    monkeypatch.setattr(industry, "fetch_notes", lambda: [("map.md", "RAW RESEARCH")])

    sc = assemble.build(cfg, live_data=False)
    ctx = sc.as_context()
    assert "拥有瓶颈环节" in ctx
    assert "不是**谁排第几**" in ctx                    # states what it is NOT for
    sc.evidence_block = "## 产业链证据\nEVIDENCE MARKER"
    ctx = sc.as_context()
    assert ctx.index("拥有瓶颈环节") < ctx.index("EVIDENCE MARKER")


def test_review_clamps_and_persists(monkeypatch):
    monkeypatch.setattr("ats.config.load_sector_config", lambda name="x": CFG)
    monkeypatch.setattr(sector_review, "run_structured", lambda *a, **k: _view())
    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.SectorContext(cfg=cfg))

    r = sector_review.run("test_sector", use_llm=True, live_data=False, layers=False)
    assert [a.key for a in r.layers] == ["L3"]                    # bogus key dropped
    assert r.layers[0].boom_score == 100.0                        # clamped
    cohr = r.call_for("COHR")
    assert cohr.conviction == 1.0                                 # clamped
    assert r.call_for("ZZZZ") is None                             # non-universe dropped
    assert r.call_for("MSFT").stance == "持有"                    # bad stance normalized
    assert r.call_for("MSFT").layer == "L2"                       # layer inferred
    assert get_store().latest_sector_review("test_sector").regime == r.regime


def test_sector_view_coerces_item_tagged_top_risks():
    """Reproduces the live 2026-08-01 failure: the model emitted `top_risks` as
    `<item>...</item>` pseudo-XML instead of a JSON array. Validation rejected the
    whole review and run() fell back to the PRIOR week's report (see this file's
    docstring) — same failure class already fixed for macro's `themes`/`top_risks`,
    just never wired up for sector. Coerce rather than discard."""
    tagged = ("\n<item>实际利率趋势性抬升压制高估值成长股</item>\n"
             "<item>高收益利差走阔，信用质量边际转差</item>\n")
    v = SectorReviewLLMView(regime="r", top_risks=tagged)
    assert v.top_risks == ["实际利率趋势性抬升压制高估值成长股", "高收益利差走阔，信用质量边际转差"]

    # A JSON-array string (the other known shape) must still coerce correctly.
    v2 = SectorReviewLLMView(regime="r", top_risks='["能源二次上涨", "信用利差走阔"]')
    assert v2.top_risks == ["能源二次上涨", "信用利差走阔"]

    # Real arrays must still pass through untouched.
    plain = SectorReviewLLMView(regime="r", top_risks=["a"])
    assert plain.top_risks == ["a"]


def test_sector_view_coerces_stringified_layers_and_calls():
    import json

    layers = json.dumps([{"key": "L3", "boom_score": 80.0}])
    calls = json.dumps([{"symbol": "MSFT", "stance": "增持"}])
    v = SectorReviewLLMView(regime="r", layers=layers, company_calls=calls)
    assert len(v.layers) == 1 and v.layers[0].key == "L3"
    assert len(v.company_calls) == 1 and v.company_calls[0].symbol == "MSFT"


def test_review_llm_failure_keeps_prior(monkeypatch):
    monkeypatch.setattr("ats.config.load_sector_config", lambda name="x": CFG)
    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.SectorContext(cfg=cfg))
    prior = SectorReview(sector="test_sector", as_of=NOW, regime="PRIOR REGIME")
    get_store().save_sector_review(prior)

    def boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(sector_review, "run_structured", boom)
    r = sector_review.run("test_sector", use_llm=True, live_data=False, layers=False)
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
    # The weekly job now also runs the cross-section, which fetches factors for the
    # whole universe. Stub it: without this the test makes ~25 live yfinance calls
    # (measured: 119s) and its result depends on the network.
    xs = []
    monkeypatch.setattr(scheduler, "_cross_section_weekly", lambda name: xs.append(name))
    # weekday gate now tracks weekly_review_tz, not ET — see scheduler._today_weekly().
    monkeypatch.setattr(scheduler, "_today_weekly", lambda: date(2026, 7, 4))   # a Saturday
    scheduler._sector_weekly()
    assert calls == ["ai_hardware"]
    assert xs == ["ai_hardware"]          # ranking follows the review, same fresh data

    calls.clear()
    xs.clear()
    monkeypatch.setattr(scheduler, "_today_weekly", lambda: date(2026, 7, 6))   # Monday — no longer fires
    scheduler._sector_weekly()
    assert calls == [] and xs == []


def test_cross_section_weekly_respects_the_kill_switch(monkeypatch):
    """`sector_review.cross_section: false` must fully restore pre-stage-3 behaviour."""
    from datetime import date

    from ats import config
    from ats.runtime import scheduler

    real = config.load_pead_global
    monkeypatch.setattr(config, "load_pead_global", lambda: {
        **real(), "sector_review": {**real()["sector_review"], "cross_section": False}})
    monkeypatch.setattr("ats.runtime.cli.run_sector_review", lambda name, **k: None)
    monkeypatch.setattr(scheduler, "_today_weekly", lambda: date(2026, 7, 4))
    monkeypatch.setattr(scheduler, "_cross_section_weekly",
                        lambda name: pytest.fail("kill switch must prevent the run"))
    scheduler._sector_weekly()


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


def test_cross_section_structural_blend_reranks():
    """Structure overlay (tech_tenor/moat_pricing) must be able to override a
    pure-quant winner: a strong-quant / weak-structure name drops below a
    mid-quant / strong-structure peer under the 60/40 blend."""
    from ats.agents.sector.cross_section import (
        BLENDED_WEIGHTS, FactorRow, QUANT_WEIGHTS, rank_cohort)

    strongq = FactorRow(symbol="STRONGQ", market_cap=40e9, beta=3.0, rev_growth=1.5,
                        gross_margin=0.65, op_margin=0.35, fwd_pe=25, mom_60d=15, rating_delta=0.3,
                        tech_tenor=-2.0, moat_pricing=-2.0)
    midq = FactorRow(symbol="MIDQ", market_cap=60e9, beta=1.5, rev_growth=0.3,
                     gross_margin=0.40, op_margin=0.18, fwd_pe=40, mom_60d=-5, rating_delta=0.0,
                     tech_tenor=2.0, moat_pricing=2.0)
    weakq = FactorRow(symbol="WEAKQ", market_cap=8e9, beta=3.5, rev_growth=0.2,
                      gross_margin=0.30, op_margin=-0.05, fwd_pe=38, mom_60d=-30, rating_delta=-0.2,
                      tech_tenor=0.0, moat_pricing=0.0)
    rows = [strongq, midq, weakq]

    rank_cohort(rows, layer_cap=0.14, weights=QUANT_WEIGHTS)   # quant pass
    assert strongq.rank == 1                                    # quant loves STRONGQ
    for r in rows:
        r.quant_rank = r.rank

    rank_cohort(rows, layer_cap=0.14, weights=BLENDED_WEIGHTS)  # blended pass
    # structure flips it: MIDQ (strong structure) overtakes STRONGQ (weak structure)
    assert midq.rank < strongq.rank
    assert midq.quant_rank > strongq.quant_rank                 # and it was behind on quant
    # sizing invariants still hold
    assert abs(sum(r.weight for r in rows) - 0.14) < 1e-9
    assert all(r.weight <= 0.14 * 0.40 + 1e-9 for r in rows)


# --- cross-section orchestration resilience -------------------------------- #
def test_a_new_review_keeps_the_same_days_baskets(monkeypatch):
    """A review always creates a new row. Re-running it after the cross-section
    stranded that day's baskets on the older row, so `latest_sector_review` returned a
    basket-less review and the Chief saw no cross-section at all."""
    from datetime import datetime, timezone

    from ats.memory import get_store
    from ats.schemas.sector import LayerBasket, SectorReview

    store = get_store()
    today = datetime.now(timezone.utc)
    store.save_sector_review(SectorReview(
        sector="test_sector", as_of=today, regime="prior",
        baskets=[LayerBasket(layer_key="L5_fab", as_of=today, structural=True)]))

    monkeypatch.setattr("ats.config.load_sector_config", lambda name="x": CFG)
    monkeypatch.setattr(sector_review, "run_structured", lambda *a, **k: _view())
    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.SectorContext(cfg=cfg))
    out = sector_review.run("test_sector", use_llm=True, live_data=False, layers=False)
    assert [b.layer_key for b in out.baskets] == ["L5_fab"]


def test_stale_baskets_are_not_carried_across_days(monkeypatch):
    """A basket describes one run's prices and factor values. Re-attaching last week's
    would misstate when it was computed."""
    from datetime import datetime, timedelta, timezone

    from ats.memory import get_store
    from ats.schemas.sector import LayerBasket, SectorReview

    store = get_store()
    old = datetime.now(timezone.utc) - timedelta(days=7)
    store.save_sector_review(SectorReview(
        sector="test_sector", as_of=old, regime="prior",
        baskets=[LayerBasket(layer_key="L5_fab", as_of=old, structural=True)]))

    monkeypatch.setattr("ats.config.load_sector_config", lambda name="x": CFG)
    monkeypatch.setattr(sector_review, "run_structured", lambda *a, **k: _view())
    monkeypatch.setattr(assemble, "build",
                        lambda cfg, live_data=True: assemble.SectorContext(cfg=cfg))
    assert sector_review.run("test_sector", use_llm=True, live_data=False,
                             layers=False).baskets == []


def test_cohort_is_one_row_per_company_not_per_listing(monkeypatch):
    """SK hynix is configured under three codes because the book holds more than one.
    The cohort took all three: it ranked the same company three times and handed it
    20.5% of a 30% layer budget. Listing-level differences are the portfolio's problem;
    selection and relative ranking are about the business."""
    from ats.agents.sector import cross_section
    from ats.schemas.sector import SectorConfig, SectorLayer, LayerTicker

    cfg = SectorConfig(name="t", layers=[SectorLayer(
        key="L5", label="L5", weight_cap=0.30, tickers=[
            LayerTicker(symbol="TSM"), LayerTicker(symbol="MU"),
            LayerTicker(symbol="HY9H"), LayerTicker(symbol="SKHY"),
            LayerTicker(symbol="000660.KS"), LayerTicker(symbol="005930.KS")])])
    seen = {}

    def _fetch(symbols, subgroups=None):
        seen["cohort"] = list(symbols)
        return [cross_section.FactorRow(symbol=s, market_cap=1e11, rev_growth=0.3)
                for s in symbols]

    monkeypatch.setattr("ats.config.load_sector_config", lambda name="t": cfg)
    monkeypatch.setattr(cross_section, "fetch_factors", _fetch)
    cross_section.run_layer("t", "L5", persist=False, structure=False)

    # Three SK hynix listings fold onto the US ADR — which is the canonical id in
    # config/entities.yaml, so "prefer the US ticker" needs no separate rule.
    assert seen["cohort"] == ["TSM", "MU", "SKHY", "005930.KS"]


def test_run_defaults_to_the_layered_path(monkeypatch):
    """`run()` 默认走分层路径，`layers=False` 才回到旧的单次合成。

    这条断言存在的理由是 2026-08-20 的一次真实事故：三个针对旧路径写的测试只 patch 了
    `review.run_structured`，而分层路径调的是 `layer_review` / `rotation` 里的同名函数 ——
    默认值一改，它们就**静默地开始打真实网络**，还照样显示为绿。conftest 现在有
    `_no_live_layer_analyst` 兜底，这里再钉住默认值本身。
    """
    monkeypatch.setattr("ats.config.load_sector_config", lambda name="x": CFG)
    called = {}
    monkeypatch.setattr(sector_review, "_run_layered",
                        lambda *a, **k: called.setdefault("layered", True))
    sector_review.run("test_sector", use_llm=False, live_data=False)
    assert called.get("layered") is True
