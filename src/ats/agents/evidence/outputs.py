"""LLM-facing views for the evidence observer (validated/normalized in observer.py)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..coerce import _as_objlist


class ObservationView(BaseModel):
    entity: str = Field(description="这条事实说的是哪个公司/实体（代码或名称）")
    metric: str = Field(description="你对该指标的命名，如 hbm_capacity / lead_time（仅供展示）")
    # Semantic link, not a string match: the caller supplies a closed menu of claim
    # dimensions with descriptions. Naming variants (hbm_market_share / hbm_share /
    # 份额指引下修) must land on the same dimension instead of being lost.
    concept: str = Field(
        default="",
        description="这条事实归属于上下文所给「可归属维度」清单里的哪一个 key。"
                    "按语义判断，不要按字面匹配。都不属于就留空——留空是允许的，"
                    "**不要硬套**一个不相干的维度")
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


# --------------------------------------------------------------------------- #
# Claim proposer — induce ONE candidate proposition from unmapped observations
# --------------------------------------------------------------------------- #
class ProposedConceptView(BaseModel):
    key: str = Field(description="维度 key，蛇形小写，如 optical_lead_time")
    desc: str = Field(description="这个维度覆盖什么表述（供后续语义归属用）")
    # No `supports_when`: polarity is adjudicated per cluster with a recorded reason
    # (chain/adjudicate), not declared. A proposed claim must not reintroduce the
    # context-free scalar the design just removed.
    expect_from: list[str] = Field(default_factory=list,
                                   description="该维度应该由哪些公司来说（交叉验证对象）")
    direct: bool = Field(default=False, description="relative 命题里，此维度能否改变份额判断")


class ProposedWitnessView(BaseModel):
    entity: str
    stance: str = Field(description="customer | supplier | competitor | incumbent | regulator")


class ClaimProposalView(BaseModel):
    statement: str = Field(
        default="",
        description="一条可证伪的经济命题，写清机制。若这些观测并未共同指向一件事，留空")
    layer_hint: str = Field(default="", description="涉及的产业链层，可跨层如 L5→L3")
    kind: str = Field(default="common", description="common(行业共同) | relative(层内相对份额)")
    subject: str = Field(default="", description="kind=relative 时必填：谁的相对位置")
    concepts: list[ProposedConceptView] = Field(default_factory=list)
    witnesses: list[ProposedWitnessView] = Field(default_factory=list)

    @field_validator("concepts", "witnesses", mode="before")
    @classmethod
    def _coerce(cls, v):
        return _as_objlist(v)

    def as_concepts(self):
        from .proposer import to_concepts

        return to_concepts(self)

    def as_witnesses(self):
        from .proposer import to_witnesses

        return to_witnesses(self)


# --------------------------------------------------------------------------- #
# Cluster adjudication — what does this evidence MEAN for this claim?
# --------------------------------------------------------------------------- #
class ClusterJudgementView(BaseModel):
    cluster_key: str = Field(description="原样回填该组的 id=，不要改写")
    polarity: str = Field(default="neutral", description="support | refute | neutral")
    reason: str = Field(
        default="",
        description="一句话讲清为什么，要引到具体内容。这句会入库并展示给人看，"
                    "不写理由等于没判断")


class AdjudicationView(BaseModel):
    judgements: list[ClusterJudgementView] = Field(default_factory=list)

    @field_validator("judgements", mode="before")
    @classmethod
    def _coerce(cls, v):
        return _as_objlist(v)


class EntityReadingView(BaseModel):
    entity: str = Field(description="原样回填上面给出的公司代码")
    standing: str = Field(default="unknown",
                          description="strong | neutral | weak | unknown")
    reason: str = Field(default="", description="一句话依据，要引到具体内容")


class CrossSectionView(BaseModel):
    readings: list[EntityReadingView] = Field(default_factory=list)

    @field_validator("readings", mode="before")
    @classmethod
    def _coerce(cls, v):
        return _as_objlist(v)
