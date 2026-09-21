"""LLM-facing views for the macro review (no min/max — clamped in code)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..coerce import _as_objlist, _as_strlist
from ...schemas.macro_strategy import FactSetEarningsAssessment


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
    conclusion_delta: str = Field(
        default="",
        description=("相对上一份正式宏观评审，结论发生了什么变化及为什么；必须优先解释"
                     "新增/修订数据。若结论不变，明确写‘结论不变’并说明新数据为何不足以改变它。"))
    rate_path: str = Field(default="", description="美联储利率路径判断：降/持/加息与时点")
    sector_tilts: list[SectorTiltView] = Field(default_factory=list,
                                               description="核心交付物：超配/低配哪些板块行业")
    asset_implications: str = Field(default="", description="股/债/美元/黄金/原油含义")
    themes: list[ThemeAssessView] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)
    factset_earnings_assessment: FactSetEarningsAssessment | None = Field(
        default=None,
        description=("FactSet 盈利周期的八项判断。每项必须给出结论、实际使用的 metric_id、"
                     "正文页码及必要的谨慎说明；数据不可用时可为 null。"))

    # Observed live 2026-07-31: sonnet returned `themes` as a JSON *string*, the
    # whole review failed validation, and run() fell back to the PRIOR week's
    # review — stale macro background feeding five downstream agents with nothing
    # on screen to say so. Coerce instead of discarding the call.
    @field_validator("sector_tilts", "themes", mode="before")
    @classmethod
    def _objlists(cls, v):
        return _as_objlist(v)

    @field_validator("top_risks", mode="before")
    @classmethod
    def _strlists(cls, v):
        return _as_strlist(v)

    falsifier: str = Field(
        default="",
        description=("什么**具体可观测**的事件会推翻这次判断。"
                     "'如果经济恶化' 不合格；'初请 4 周均值连续两周高于 26 万' 合格。"))
