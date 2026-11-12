"""Each sector tilt names one representative ETF; the config, not the model, picks the ETF."""
from __future__ import annotations

from ats.agents.macro import report
from ats.agents.macro import review as macro_review
from ats.agents.macro.outputs import MacroReviewLLMView, SectorTiltView
from ats.schemas.macro_strategy import MacroConfig

CFG = MacroConfig(
    name="macro", label="宏观", output_dir="",
    sector_universe=[
        {"key": "utilities", "label": "公用事业", "etf": "XLU", "etf_name": "Utilities Select Sector SPDR"},
        {"key": "semiconductors", "label": "半导体", "etf": "SMH", "etf_name": "VanEck Semiconductor ETF"},
    ])


def _review(*tilts):
    view = MacroReviewLLMView(regime="risk-off，晚周期", sector_tilts=list(tilts))
    return macro_review._to_review("macro", CFG, view)


def test_a_known_key_gets_the_configured_etf():
    review = _review(SectorTiltView(sector="公用事业", sector_key="utilities", stance="超配"))
    tilt = review.sector_tilts[0]
    assert (tilt.sector_key, tilt.etf, tilt.etf_name) == ("utilities", "XLU", "Utilities Select Sector SPDR")


def test_an_unknown_or_missing_key_gets_no_etf_rather_than_a_guess():
    review = _review(SectorTiltView(sector="航空", sector_key="airlines", stance="低配"),
                     SectorTiltView(sector="成长股", stance="中性"))
    assert [(t.sector_key, t.etf) for t in review.sector_tilts] == [("", ""), ("", "")]


def test_the_report_table_shows_ticker_and_name_or_a_dash():
    review = _review(SectorTiltView(sector="半导体（AI 硬件链）", sector_key="semiconductors", stance="中性"),
                     SectorTiltView(sector="成长股", stance="低配"))
    text = report.render(review, CFG)
    assert "| 板块/行业 | 观点 | 代表 ETF | 理由 |" in text
    assert "| 半导体（AI 硬件链） | **中性** | SMH VanEck Semiconductor ETF |" in text
    assert "| 成长股 | **低配** | — |" in text


def test_the_prompt_lists_the_selectable_sectors_and_forbids_writing_an_etf():
    block = macro_review._sector_universe_block(CFG)
    assert "`utilities`" in block and "`semiconductors`" in block and "ETF 由系统按 key 配" in block
    assert macro_review._sector_universe_block(MacroConfig(name="m", label="m", output_dir="")) == ""


def test_projection_carries_the_etf_and_the_shipped_config_universe_is_consistent():
    from ats.agent.task_projection import MacroTilt
    from ats.config import load_macro_config

    review = _review(SectorTiltView(sector="公用事业", sector_key="utilities", stance="超配"))
    row = macro_review.projection_extras(review)["sector_tilts"][0]
    assert MacroTilt.model_validate(row).etf == "XLU"
    universe = load_macro_config("macro").sector_universe
    assert len({x.key for x in universe}) == len(universe)
    assert all(x.etf and x.etf_name for x in universe)
