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


class CandidateClaimView(BaseModel):
    statement: str = Field(description=(
        "一条**尚未预设、但值得追踪**的命题。写成可证伪的多头/空头假设，不要写成问题"))
    witnesses: list[str] = Field(default_factory=list, description=(
        "谁能就它作证（公司代码或第三方源）。**说不出证人就不要提这条候选**"))
    falsifier: str = Field(default="", description=(
        "什么读数会证伪它。必须具体到下一轮能核对；"
        "「基本面恶化」这类无法判定的表述不算"))
    why_now: str = Field(default="", description="本轮素材里的什么让你想到它")


class LayerVerdictView(BaseModel):
    layer_key: str = Field(description="必须原样回填上下文里给出的 [layer key = ...]，禁止自造")
    layer_status: str = Field(default="steady", description=(
        "本层状态判断：expanding | steady | contracting | unclear。"
        "**依据必须来自 common 命题的结论**——relative 读数只用于层内选谁与结构因子，"
        "不得单独作为状态判断的依据。给不出 expanding/contracting 时如实写 steady，"
        "并说明是证据缺失"))
    confidence: float = Field(default=0.0, description=(
        "0-1。证据缺失或本层无命题时 ≤0.3；只有多条 common 议题同向才上调"))
    cycle_position: str = Field(default="", description=(
        "早/中/晚周期 + 依据。**依据必须是产业证据**：资本开支指引、订单与交期、库存、"
        "产能投放。**不得**使用利率、风险偏好或大盘走向——那是宏观分析师的工作，"
        "在这里重复判断会让同一个因素被计两次"))
    claim_attributions: list[str] = Field(default_factory=list, description=(
        "每条 common 议题一行：该命题的当期结论 + 它对本层状态的含义。"
        "议题互相矛盾时两侧都要保留，不要压成单一分数"))
    reversal_triggers: list[str] = Field(default_factory=list, description=(
        "会让本结论反向的**具体观察项**，下一轮要能直接核对。"
        "「基本面恶化」这类无法判定的表述不算"))
    name_calls: list[LayerNameCallView] = Field(default_factory=list)
    rationale: str = Field(default="", description="3-6 行本层总评")
    candidate_claims: list[CandidateClaimView] = Field(default_factory=list, description=(
        "读完本层素材后自发提出的候选追踪议题。**只进报告，不参与本期任何计算**"
        "——不影响状态判断、不影响截面排序、不影响权重。宁缺勿滥：说不出证人或证伪条件"
        "就不要提"))

    # Same failure macro/pead already hit: a model sometimes serializes a list field as
    # a string. Validation would fail and the whole layer would silently fall back to
    # last week's verdict — coerce instead of discarding a real answer.
    @field_validator("name_calls", "candidate_claims", mode="before")
    @classmethod
    def _coerce_name_calls(cls, v):
        return _as_objlist(v)

    @field_validator("claim_attributions", "reversal_triggers", mode="before")
    @classmethod
    def _coerce_strlists(cls, v):
        return _as_strlist(v)

    @field_validator("layer_status", mode="before")
    @classmethod
    def _normalize_status(cls, v):
        if isinstance(v, str):
            v = v.strip().lower()
            # 容忍中文回填：模型偶尔用中文回答枚举
            table = {"扩张": "expanding", "平稳": "steady", "收缩": "contracting",
                     "不明": "unclear", "上行": "expanding", "下行": "contracting"}
            v = table.get(v, v)
        return v


# --- event-impact-routing (2026-11-16): only used when config/workflow/event_impact.yaml layer.enabled ---
class RiskClaimStateView(BaseModel):
    claim_id: str = Field(description="上下文「风险命题」里列出的命题 id，原样回填")
    status: str = Field(default="dormant", description=(
        "dormant 休眠 | emerging 出现 | pressured 承压 | realized 兑现 | resolved 解除。"
        "承压/兑现须有至少 2 个独立来源且其中至少 1 个一手或权威——代码会按引用的成分与台账核对，不达标降为出现"))
    component_refs: list[str] = Field(default_factory=list, description="支撑状态判断的事件成分编号（ref 原样回填）")
    materiality_review: str = Field(default="", description=(
        "对所引成分重要性评级的复核：agree / higher / lower + 一句理由"))
    rationale: str = ""

    @field_validator("component_refs", mode="before")
    @classmethod
    def _refs(cls, v):
        return _as_strlist(v)


class OffThesisShockView(BaseModel):
    shock_id: str = Field(default="", description="本层内唯一的短编号，如 L5-shock-1；延续上一轮的冲击时沿用原编号")
    component_refs: list[str] = Field(default_factory=list, description="该冲击依据的事件成分编号")
    summary: str = Field(default="", description="一句话：发生了什么、为什么无法归入任何命题")
    materiality: str = Field(default="moderate", description="minor | moderate | major | critical（只列中等及以上）")
    direction: str = Field(default="unclear", description="positive | negative | mixed | unclear")
    status: str = Field(default="ongoing", description="ongoing 持续 | concluded 已了结")
    touched_claims: list[str] = Field(default_factory=list, description="它碰到但不足以归入的命题 id")
    proposed_claim: str = Field(default="", description="若建议新增一条命题，写成可证伪的陈述；否则留空")
    materiality_review: str = Field(default="", description="agree / higher / lower + 一句理由")

    @field_validator("component_refs", "touched_claims", mode="before")
    @classmethod
    def _lists(cls, v):
        return _as_strlist(v)


class CrossScopeClaimView(BaseModel):
    component_ref: str = Field(description="标为「本公司」、但你认为会传导到整层的成分编号")
    transmission: str = Field(default="", description="传导路径与理由，如「董事会动荡推迟买方采购决策」")
    materiality_review: str = Field(default="", description="agree / higher / lower + 一句理由")


class UtilizationAdviceView(BaseModel):
    direction: str = Field(default="none", description="down | none。**不得建议上调**")
    notches: int = Field(default=0, description="建议下调档数（0-2）；临时复盘时板块配置只执行一档")
    reasons: list[str] = Field(default_factory=list, description=(
        "所依据的风险命题 id（须为承压/兑现）或冲击编号（须为重大及以上、负面、持续）"))
    rationale: str = ""

    @field_validator("reasons", mode="before")
    @classmethod
    def _reasons(cls, v):
        return _as_strlist(v)


class LayerVerdictEventView(LayerVerdictView):
    """LayerVerdictView plus the event-impact fields; selected only while the layer switch is on."""

    # Required (no default): the 2026-11-16 replay's L5 run simply left it out. An empty list is a valid answer for a
    # layer with no risk claims; omitting the key is not.
    risk_claims: list[RiskClaimStateView] = Field(description=(
        "**必填**。本层每条风险命题一行（上下文「风险命题」一节列出的全部，含休眠的）；本层没有风险命题时给空列表"))
    off_thesis_shocks: list[OffThesisShockView] = Field(default_factory=list, description=(
        "无法归入任何经营或风险命题、且重要性不低于中等的层面成分"))
    cross_scope_claims: list[CrossScopeClaimView] = Field(default_factory=list, description=(
        "标为「本公司」但存在整层传导的成分；无传导的不要列"))
    utilization_advice: UtilizationAdviceView | None = Field(default=None, description=(
        "只下调的使用率建议；没有承压/兑现的风险命题、也没有重大负面持续冲击时给 none"))

    @field_validator("risk_claims", "off_thesis_shocks", "cross_scope_claims", mode="before")
    @classmethod
    def _objlists(cls, v):
        return _as_objlist(v)


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
    macro_background: str = Field(
        default="", description="行业分析不读取宏观评审，此项留空（旧报告里才有值）")
    factset_background: str = Field(
        default="", description="FactSet 十一行业数据提供的背景；未提供时说明不可用")
    agreements: list[str] = Field(
        default_factory=list, description="FactSet 与八层公司证据一致之处；没有可用数据就留空")
    divergences: list[str] = Field(
        default_factory=list, description="FactSet 与八层公司证据冲突之处，不得掩盖；没有可用数据就留空")
    recommendation_impact: str = Field(
        default="", description="FactSet 是否改变跨层加减建议；不能修改已形成的单层结论，不得引用宏观判断")

    @field_validator("conflicts", "missing_layers", "top_risks",
                     "agreements", "divergences", mode="before")
    @classmethod
    def _coerce_strlists(cls, v):
        return _as_strlist(v)
