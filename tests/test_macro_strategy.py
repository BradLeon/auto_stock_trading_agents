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
from ats.schemas.macro_strategy import (
    FactSetDiagnosticSummary,
    FactSetEarningsAssessment,
    FactSetJudgment,
    FactSetMaterialSummary,
    FactSetObservationSummary,
    MacroConfig,
    MacroReview,
    SectorTilt,
)

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


def test_macro_runtime_config_has_no_factset_download_or_folder_settings():
    """FactSet acquisition belongs only to the registered ingest pipeline."""
    from pathlib import Path

    from ats.config import load_macro_config
    from ats.data import factset

    config_text = (Path(__file__).resolve().parents[1] / "config" / "macro.yaml").read_text(
        encoding="utf-8")
    assert "factset:" not in config_text
    assert "factset" not in type(load_macro_config("macro")).model_fields
    assert not hasattr(factset, "fetch_earnings_insight")
    assert not hasattr(factset, "parse_key_metrics")


def test_assemble_offline_and_live(monkeypatch):
    data = MacroData(as_of=NOW, fed_funds=3.63, ust_10y=4.48, hy_oas=2.75, ig_oas=0.75)
    monkeypatch.setattr("ats.data.runtime.macro.fetch", lambda: data)
    monkeypatch.setattr("ats.data.websearch.search_news",
                        lambda q, **k: [{"title": "Iran headline", "url": "u",
                                         "content": "conflict escalates", "published": "2026-07-01"}])
    monkeypatch.setattr(
        "ats.data.factset.fetch_macro_material",
        lambda: ("", "disabled", None, None))
    consumers = []

    class Snapshot:
        def render(self):
            return "REGIONAL GOVERNED OUTPUT"

    def regional_snapshot(*, consumer):
        consumers.append(consumer)
        return Snapshot()

    monkeypatch.setattr("ats.data.regional.fetch", regional_snapshot)
    mc = assemble.build(CFG, live_data=True)
    ctx = mc.as_context()
    assert "fed_funds=3.63" in ctx and "hy_oas=2.75" in ctx        # theme quant fields
    assert "Iran headline" in ctx and "conflict escalates" in ctx  # tavily news
    assert "REGIONAL GOVERNED OUTPUT" in ctx
    assert consumers == ["macro_agent"]
    assert mc.stats()["regional_chars"] > 0
    assert mc.stats()["themes"] == 3

    mc2 = assemble.build(CFG, live_data=False)
    assert "offline" in mc2.as_context()                            # no network
    assert consumers == ["macro_agent", "macro_agent"]             # local product still read


def test_assemble_keeps_macro_workflow_available_when_regional_product_fails(monkeypatch):
    data = MacroData(as_of=NOW, fed_funds=3.63)
    monkeypatch.setattr("ats.data.runtime.macro.fetch", lambda: data)
    monkeypatch.setattr("ats.data.websearch.search_news", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("ats.data.regional.fetch",
                        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")))
    context = assemble.build(CFG, live_data=True)

    assert context.regional_block == "(区域月度数据不可用)"
    assert context.quant_block


def test_assemble_includes_factset(monkeypatch):
    data = MacroData(as_of=NOW, fed_funds=3.63)
    monkeypatch.setattr("ats.data.runtime.macro.fetch", lambda: data)
    monkeypatch.setattr("ats.data.websearch.search_news", lambda q, **k: [])
    monkeypatch.setattr(
        "ats.data.factset.fetch_macro_material",
        lambda: ("S&P500 EPS 增速 23.3%, 前瞻 P/E 20.4",
                     "factset:x.pdf", None, None))
    mc = assemble.build(CFG, live_data=True)
    ctx = mc.as_context()
    assert "FactSet 完整分析材料" in ctx and "前瞻 P/E 20.4" in ctx
    assert mc.stats()["earnings_source"] == "factset:x.pdf"


def test_factset_assessment_filters_citations_and_report_separates_facts():
    material = FactSetMaterialSummary(
        report_date=datetime(2026, 8, 28).date(), version_id="factset@082826",
        freshness="fresh",
        observations=[FactSetObservationSummary(
            observation_id="obs-growth", metric_id="earnings.eps.yoy_growth",
            period="2026Q2", value=0.52, unit="ratio", estimate_state="blended",
            page_numbers=[1])],
        diagnostics=[FactSetDiagnosticSummary(
            diagnostic_id="eps_minus_revenue_growth",
            label="盈利增长减营收增长", value=36.5, unit="percentage_point",
            input_observation_ids=["obs-growth", "obs-revenue"])],
        narrative_pages={"earnings_concentration": [3]})
    assessment = FactSetEarningsAssessment(
        growth_quality=FactSetJudgment(
            conclusion="盈利增长强，但明显快于营收。",
            metric_ids=["earnings.eps.yoy_growth", "invented.metric"],
            page_numbers=[3, 99]))
    view = MacroReviewLLMView(
        regime="risk-on", factset_earnings_assessment=assessment)

    review = macro_review._to_review(
        "macro", CFG, view, {"factset_material": material}, as_of=NOW)

    judgment = review.factset_earnings_assessment.growth_quality
    assert judgment.metric_ids == ["earnings.eps.yoy_growth"]
    assert judgment.page_numbers == [3]
    markdown = report.render(review, CFG)
    assert "FactSet 盈利周期判断" in markdown
    assert "数据事实（程序读取，非模型解释）" in markdown
    assert "earnings.eps.yoy_growth" in markdown and "52.0%" in markdown
    assert "盈利增长强，但明显快于营收" in markdown
    assert "第 3 页" in markdown and "invented.metric" not in markdown
    get_store().save_macro_review(review)
    loaded = get_store().latest_macro_review("macro")
    assert loaded.factset_material.version_id == "factset@082826"
    assert loaded.factset_earnings_assessment.growth_quality.page_numbers == [3]


def test_review_clamps_and_persists(monkeypatch):
    monkeypatch.setattr("ats.config.load_macro_config", lambda name="macro": CFG)
    monkeypatch.setattr(macro_review, "run_structured", lambda *a, **k: _view())
    monkeypatch.setattr(assemble, "build", lambda cfg, live_data=True: assemble.MacroContext(cfg=cfg))

    r = macro_review.run("macro", use_llm=True, live_data=False)
    assert [t.key for t in r.themes] == ["fed_policy"]              # bogus theme dropped
    assert {t.sector for t in r.sector_tilts} == {"半导体", "公用事业"}  # empty dropped
    assert r.sector_tilts[0].stance == "低配"
    assert get_store().latest_macro_review("macro").regime == r.regime


def test_review_accepts_an_exact_configured_theme_label_but_not_unknown_text():
    view = MacroReviewLLMView(
        regime="neutral",
        themes=[
            ThemeAssessView(key="货币政策", direction="维持"),
            ThemeAssessView(key="geopolitics", direction="紧张"),
            ThemeAssessView(key="不是配置主题", direction="未知"),
        ])
    review = macro_review._to_review("macro", CFG, view)

    assert [item.key for item in review.themes] == ["fed_policy", "geopolitics"]


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


def test_same_day_rerun_uses_previous_formal_day_as_comparison():
    store = get_store()
    store.save_macro_review(MacroReview(name="m", as_of=datetime(2026, 8, 1), regime="weekly"))
    store.save_macro_review(MacroReview(name="m", as_of=datetime(2026, 8, 8, 1),
                                        regime="bad-rerun"))
    prior = store.latest_macro_review_before("m", datetime(2026, 8, 8).date())
    assert prior is not None and prior.regime == "weekly"


def test_report_render_and_write(tmp_path):
    r = MacroReview(name="macro", as_of=NOW, regime="risk-off", summary="S",
                    rate_path="维持", asset_implications="黄金受益",
                    sector_tilts=[SectorTilt(sector="半导体", stance="低配", rationale="贵")],
                    top_risks=["信用事件"])
    md = report.render(r, CFG)
    assert "半导体" in md and "低配" in md and "risk-off" in md and "黄金受益" in md
    assert "本次模型未返回可识别的逐主题结构化条目" in md
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


def test_sector_assemble_does_not_ingest_macro(monkeypatch):
    """宏观**不进**行业链路（2026-08-20，design D16）。

    这个测试从「断言注入」反转成「断言不注入」，因为宏观在 Chief 已经有落点
    （chief/assemble.py 读宏观评审的 sector_tilts）。行业这边再吃一遍有两个后果：
    同一个利率/风险偏好判断被计两次；层级结论变差时分不清是产业景气变差还是宏观变差——
    而那两件事对仓位的含义相反（减这一层 vs 减总仓位）。
    """
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
    assert sc.macro_block == ""                                    # 不再填充
    assert "MACRO REGIME MARKER" not in sc.as_context()            # 也不出现在上下文里


def test_scheduler_macro_before_sector(monkeypatch):
    from datetime import date

    from ats.runtime import scheduler

    order = []
    monkeypatch.setattr("ats.runtime.cli.run_macro_review", lambda name, **k: order.append("macro"))
    monkeypatch.setattr("ats.runtime.cli.run_sector_review", lambda name, **k: order.append("sector"))
    # weekday gate now tracks weekly_review_tz, not ET — see _today_weekly().
    monkeypatch.setattr(scheduler, "_today_weekly", lambda: date(2026, 7, 4))   # Saturday
    # The weekly job now also ranks the cross-section (network) and writes a report.
    monkeypatch.setattr(scheduler, "_cross_section_weekly", lambda name: None)
    scheduler._macro_weekly()
    scheduler._sector_weekly()
    assert order == ["macro", "sector"]

    order.clear()
    monkeypatch.setattr(scheduler, "_today_weekly", lambda: date(2026, 7, 6))   # Monday — no longer fires
    scheduler._macro_weekly()
    assert order == []
