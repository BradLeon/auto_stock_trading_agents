"""LLM-facing views for PEAD agents (no min/max; numerics clamped in code)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return v


class NarrativeView(BaseModel):
    narrative: str = Field(description="core thesis: business drivers, key bottleneck, margin story")
    focus_ranking: list[str] = Field(default_factory=list, description="what matters most this quarter, ordered")
    valuation: str = Field(default="", description="PE / forward PE / ceiling-floor read")

    @field_validator("focus_ranking", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_list(v)


class ExpectationRowView(BaseModel):
    dim_key: str
    metric: str = ""
    conservative: str = ""
    neutral: str = Field(default="", description="the base-case expectation")
    optimistic: str = ""
    source: str = ""


class ExpectationsView(BaseModel):
    rows: list[ExpectationRowView] = Field(default_factory=list)


class SignalItemView(BaseModel):
    symbol: str
    signal: str = Field(default="", description="one-line implication for the target")


class SignalChainView(BaseModel):
    items: list[SignalItemView] = Field(default_factory=list)
    summary: str = ""


class ActualMetricView(BaseModel):
    dim_key: str
    metric: str = ""
    actual: str = ""
    vs_expected: str = Field(default="", description="远超/中性/低于 + 方向标记")
    note: str = ""


class ActualsView(BaseModel):
    reported_eps: float | None = None
    reported_revenue: float | None = None
    metrics: list[ActualMetricView] = Field(default_factory=list)
    guidance: str = Field(default="", description="forward guidance extracted from the text")
    transcript_signals: list[str] = Field(default_factory=list,
                                           description="key qualitative call signals")

    @field_validator("transcript_signals", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_list(v)


class ScoreItemView(BaseModel):
    dim_key: str
    score: float = Field(description="-2 (far below) .. 0 (in line) .. +2 (far above expectations)")
    note: str = ""


class ScoresView(BaseModel):
    items: list[ScoreItemView] = Field(default_factory=list)
