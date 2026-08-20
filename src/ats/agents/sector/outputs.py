"""LLM-facing views for the sector review (no min/max — clamped in code)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..coerce import _as_objlist, _as_strlist


class LayerAssessView(BaseModel):
    key: str = Field(description="必须原样回填上下文里给出的 [layer key = ...]，"
                                 "例如 L1_app / L5_fab；禁止自造描述性 key，否则该层被丢弃")
    boom_score: float = Field(default=50.0, description="景气度 0-100")
    supply_demand: str = Field(default="", description="供需: 紧张/平衡/过剩 + 一句依据")
    pricing_power: str = Field(
        default="",
        description="本层的定价权归属：谁在瓶颈环节、谁在被替代、份额与 ASP 的方向。"
                    "**若上下文给了「定价权（截面比较命题）」的逐家读数，以它为准**，"
                    "静态行业笔记让位——笔记是稳定背景，读数是本期实际发生的事")
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

    # Same class of failure macro/pead already hit (see agents/coerce.py): a model
    # occasionally serializes a list field as a string instead of a real array,
    # which fails validation and (per review.py's docstring) makes the whole run
    # silently fall back to last week's stored review. Coerce rather than discard.
    @field_validator("layers", "company_calls", mode="before")
    @classmethod
    def _coerce_objlists(cls, v):
        return _as_objlist(v)

    @field_validator("top_risks", mode="before")
    @classmethod
    def _coerce_top_risks(cls, v):
        return _as_strlist(v)


# --------------------------------------------------------------------------- #
# Structure analyst — KB-grounded qualitative overlay for the cross-section
# --------------------------------------------------------------------------- #
class StructureNameView(BaseModel):
    symbol: str
    tech_tenor: float = Field(default=0.0, description=(
        "技术时间朝向 -2..+2：该标的的产品/技术在 secular 曲线上的位置与久期。"
        "+2=处上升技术的右侧、长久期；0=中性/过渡；-2=处被替代技术的尾侧（如光进铜退中的纯铜连接）。"))
    moat_pricing: float = Field(default=0.0, description=(
        "护城河/定价权 -2..+2：垂直整合、市占率、瓶颈环节定价权、客户集中度综合。"
        "+2=强护城河+高定价权+客户分散；-2=弱差异化+客户高度集中+薄定价权。"))
    rationale: str = Field(default="", description="1-2 句依据，必须锚定 KB 笔记与量化事实，不得臆造")


class SubgroupNoteView(BaseModel):
    subgroup: str = Field(description="子层名，如 光互联/铜连接")
    tech_curve_note: str = Field(default="", description="该子层的技术演进阶段判断（如光进铜退当前所处段）")


class StructureView(BaseModel):
    names: list[StructureNameView] = Field(default_factory=list)
    subgroups: list[SubgroupNoteView] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Layer analyst — one chain layer: HOW MUCH, and WHO within it
# --------------------------------------------------------------------------- #
class LayerNameCallView(BaseModel):
    symbol: str
    subgroup: str = Field(default="", description="若本层设了 subgroup，回填该票所属的组；否则留空")
    stance: str = Field(default="持有", description="增持 | 持有 | 减持")
    rationale: str = Field(default="", description=(
        "取舍理由。证据优先级：① relative 命题的逐家读数 → ② 截面排名与结构因子 → "
        "③ 判据笔记。上位与下位冲突时以上位为准并说明分歧"))
    self_reported_only: bool = Field(default=False, description=(
        "该理由是否只依赖「仅自述」的读数（没有客户或第三方交叉印证）"))


class LayerVerdictView(BaseModel):
    layer_key: str = Field(description="必须原样回填上下文里给出的 [layer key = ...]，禁止自造")
    allocation: str = Field(default="标配", description=(
        "本层配置结论：超配 | 标配 | 低配 | 清仓。"
        "**依据必须来自 common 命题的结论**——relative 读数只用于层内选谁，"
        "不得单独作为配置结论的依据"))
    confidence: float = Field(default=0.0, description=(
        "0-1。证据缺失或本层无命题时 ≤0.3；只有多条 common 议题同向才上调"))
    cycle_position: str = Field(default="", description=(
        "早/中/晚周期 + 依据。**依据必须是产业证据**：资本开支指引、订单与交期、库存、"
        "产能投放。**不得**使用利率、风险偏好或大盘走向——那是宏观分析师的工作，"
        "在这里重复判断会让同一个因素被计两次"))
    claim_attributions: list[str] = Field(default_factory=list, description=(
        "每条 common 议题一行：该命题的当期结论 + 它对本层配置的含义。"
        "议题互相矛盾时两侧都要保留，不要压成单一分数"))
    reversal_triggers: list[str] = Field(default_factory=list, description=(
        "会让本结论反向的**具体观察项**，下一轮要能直接核对。"
        "「基本面恶化」这类无法判定的表述不算"))
    name_calls: list[LayerNameCallView] = Field(default_factory=list)
    rationale: str = Field(default="", description="3-6 行本层总评")

    # Same failure macro/pead already hit: a model sometimes serializes a list field as
    # a string. Validation would fail and the whole layer would silently fall back to
    # last week's verdict — coerce instead of discarding a real answer.
    @field_validator("name_calls", mode="before")
    @classmethod
    def _coerce_name_calls(cls, v):
        return _as_objlist(v)

    @field_validator("claim_attributions", "reversal_triggers", mode="before")
    @classmethod
    def _coerce_strlists(cls, v):
        return _as_strlist(v)


class LayerRotationView(BaseModel):
    """Cross-layer pass — consumes layer verdicts, never re-litigates them."""
    regime: str = Field(description="一句话行业状态（自包含，会被注入其他 agent 的上下文）")
    summary: str = Field(default="", description="5-10 行总评，面向下周的 prep/monitor 读者")
    rotation_advice: str = Field(default="", description=(
        "利润池正从哪层迁移到哪层 + 一条可执行的层间加减建议。"
        "**引用具体层的配置结论与周期位置作为依据**，不得推翻任何一层的结论"))
    conflicts: list[str] = Field(default_factory=list, description=(
        "相邻层结论互相矛盾之处，标注为待人工裁决；没有就留空"))
    missing_layers: list[str] = Field(default_factory=list, description=(
        "结论缺失的层键；涉及它们的加减建议必须标注证据不足"))
    top_risks: list[str] = Field(default_factory=list)

    @field_validator("conflicts", "missing_layers", "top_risks", mode="before")
    @classmethod
    def _coerce_strlists(cls, v):
        return _as_strlist(v)
