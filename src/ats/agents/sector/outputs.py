"""LLM-facing views for the sector review (no min/max — clamped in code)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LayerAssessView(BaseModel):
    key: str = Field(description="必须原样回填上下文里给出的 [layer key = ...]，"
                                 "例如 L1_app / L5_fab；禁止自造描述性 key，否则该层被丢弃")
    boom_score: float = Field(default=50.0, description="景气度 0-100")
    supply_demand: str = Field(default="", description="供需: 紧张/平衡/过剩 + 一句依据")
    pricing_power: str = ""
    capital_flow: str = Field(default="", description="资金流向观察（以相对动量/估值扩张为 proxy）")
    cycle_position: str = Field(default="", description="早/中/晚周期 + 依据")
    signal: str = Field(default="neutral", description="bullish | neutral | bearish")
    note: str = ""


class CompanyCallView(BaseModel):
    symbol: str
    layer: str = Field(default="", description="the layer key this company sits in")
    stance: str = Field(default="持有", description="增持 | 持有 | 减持")
    conviction: float = Field(default=0.0, description="0-1")
    rationale: str = ""


class SectorReviewLLMView(BaseModel):
    regime: str = Field(description="一句话行业状态（自包含，会被注入其他 agent 的上下文）")
    summary: str = Field(default="", description="5-10 行总评，面向下周的 prep/monitor 读者")
    layers: list[LayerAssessView] = Field(default_factory=list)
    company_calls: list[CompanyCallView] = Field(default_factory=list)
    rotation_advice: str = Field(default="", description="层间轮动建议：加/减哪层，为什么")
    top_risks: list[str] = Field(default_factory=list)
