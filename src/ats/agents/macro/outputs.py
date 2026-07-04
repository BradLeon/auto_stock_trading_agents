"""LLM-facing views for the macro review (no min/max — clamped in code)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ThemeAssessView(BaseModel):
    key: str = Field(description="echo the theme key exactly, e.g. financial_conditions")
    direction: str = Field(default="", description="该主题当前方向/边际变化")
    transmission: str = Field(default="", description="对权益市场的传导（如利率↑→估值压缩）")
    signal: str = Field(default="neutral", description="risk-on | neutral | risk-off")
    note: str = ""


class SectorTiltView(BaseModel):
    sector: str = Field(description="板块/行业名，如 半导体/能源/公用事业/成长股")
    stance: str = Field(default="中性", description="超配 | 中性 | 低配")
    rationale: str = ""


class MacroReviewLLMView(BaseModel):
    regime: str = Field(description="risk-on/neutral/risk-off + 周期位置，一句话自包含（会被注入其他 agent）")
    summary: str = Field(default="", description="5-10 行总评，面向 prep/monitor/行业分析师读者")
    rate_path: str = Field(default="", description="美联储利率路径判断：降/持/加息与时点")
    sector_tilts: list[SectorTiltView] = Field(default_factory=list,
                                               description="核心交付物：超配/低配哪些板块行业")
    asset_implications: str = Field(default="", description="股/债/美元/黄金/原油含义")
    themes: list[ThemeAssessView] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)
