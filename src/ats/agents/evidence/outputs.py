"""LLM-facing views for the evidence observer (validated/normalized in observer.py)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..coerce import _as_objlist


class ObservationView(BaseModel):
    entity: str = Field(description="这条事实说的是哪个公司/实体（代码或名称）")
    metric: str = Field(description="指标 key，如 hbm_capacity / capex_guide / lead_time / asp")
    period: str = Field(default="", description="该事实覆盖的期间，如 FY26Q3 / 2027")
    observation_type: str = Field(
        default="reported_actual",
        description="reported_actual(已实现) | guidance(前瞻主张) | counterparty(对手方谈别的环节) "
                    "| regulatory | research | media | market。已实现与指引不得互相冒充")
    stance: str = Field(
        default="incumbent",
        description="说话人的经济位置：incumbent(主体自己) | competitor | customer(需求方) "
                    "| supplier(供给方) | regulator")
    direction: str = Field(default="flat",
                           description="该指标本身的方向 up|flat|down（不是对股价的判断）")
    value: float | None = Field(default=None, description="有明确数值时填，否则留空")
    unit: str = ""
    evidence_span: str = Field(
        description="从原文逐字摘录的最短必要片段，禁止改写/翻译/摘要。无法摘录则不要输出这条观测")


class EvidenceExtractionView(BaseModel):
    observations: list[ObservationView] = Field(default_factory=list)
    # A model that cannot read the document must say so rather than invent rows:
    # "could not read" and "says nothing" are different states downstream.
    failure_reason: str = Field(
        default="",
        description="整份文档无法抽取时填原因（指标未披露/口径无法解析/实体歧义）；"
                    "能抽出任何一条就留空")

    @field_validator("observations", mode="before")
    @classmethod
    def _coerce(cls, v):
        return _as_objlist(v)
