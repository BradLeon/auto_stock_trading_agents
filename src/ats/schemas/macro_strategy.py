"""Macro-strategist contracts — theme config + the persisted weekly review.

Equity-strategist paradigm: the primary deliverable is sector_tilts (over/under
weight) + regime + rate_path, NOT per-topic summaries.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

STANCES = ("超配", "中性", "低配")
SIGNALS = ("risk-on", "neutral", "risk-off", "bullish", "bearish")

# growth × inflation 2x2, plus a mandatory fifth state. `transition` is not a
# rounding error — a classifier forced to pick one of four will hand you a
# confident wrong answer exactly at the turning points, which is where being
# wrong costs the most. See docs/MACRO_ANALYST.md §6.3.
Quadrant = Literal[
    "goldilocks",    # 温和降温：增长稳定/改善 + 通胀下行
    "reflation",     # 经济过热：增长改善 + 通胀上行
    "stagflation",   # 滞胀：增长恶化 + 通胀上行
    "deflation",     # 普通衰退：增长恶化 + 通胀下行
    "transition",    # 过渡/证据不足 —— 不强行四选一
]

# Warsh ended forward guidance, so policy-expectation pricing lost its anchor and
# the front end whipsaws on every print. A quadrant flip is therefore provisional
# until a second consecutive review agrees. Downstream only tilts on `confirmed`.
QuadrantState = Literal["confirmed", "provisional", "insufficient"]

_STATE_CN = {"confirmed": "确认", "provisional": "暂定", "insufficient": "证据不足"}

# How to read a change: yields/spreads in basis points, prices/indices in percent,
# and `level` in raw absolute difference.
#
# `level` exists because a percent change is meaningless on a series that sits
# near zero and changes sign — CFNAI at -0.02 produced a "-89.5%" monthly change,
# which is division noise, not information. Anything centred on zero must use it.
IndicatorUnit = Literal["pct", "price", "index", "level"]


# --------------------------------------------------------------------------- #
# Config (config/macro.yaml)
# --------------------------------------------------------------------------- #
class MacroTheme(BaseModel):
    key: str
    label: str
    kind: str = "quant"                # quant | qual
    quant: list[str] = Field(default_factory=list)   # MacroData field names
    queries: list[str] = Field(default_factory=list)  # Tavily search queries


class MacroConfig(BaseModel):
    name: str = "macro"
    label: str = "宏观"
    output_dir: str = ""
    themes: list[MacroTheme] = Field(default_factory=list)
    search: dict = Field(default_factory=dict)
    review: dict = Field(default_factory=dict)
    factset: dict = Field(default_factory=dict)
    regime: dict = Field(default_factory=dict)   # 四象限阈值覆盖，见 agents/macro/regime.py

    def theme_keys(self) -> set[str]:
        return {t.key for t in self.themes}


# --------------------------------------------------------------------------- #
# Persisted weekly review
# --------------------------------------------------------------------------- #
class ThemeAssess(BaseModel):
    key: str
    label: str = ""
    direction: str = ""                # 该 theme 的方向/变化
    transmission: str = ""             # 对权益市场的传导
    signal: str = "neutral"            # risk-on | neutral | risk-off
    note: str = ""


class SectorTilt(BaseModel):
    sector: str                        # 板块/行业名（自由文本，如 半导体/能源/公用事业）
    stance: str = "中性"               # 超配 | 中性 | 低配
    rationale: str = ""


# --------------------------------------------------------------------------- #
# Deterministic layer — every field below is filled by code, never by the LLM.
# (Same author-separation discipline as CriticFinding; docs/DESIGN.md §9.3.)
# --------------------------------------------------------------------------- #
class IndicatorReading(BaseModel):
    """One indicator as level + change + distributional position.

    A 10y real yield of 2.0% is by itself neither high nor low. What carries
    information is where it moved from and how unusual it is — so the level is
    for display only; the axes and alerts read `d_*` and `z_3y`.
    """

    key: str                            # MacroData/series field name
    label: str = ""                     # 中文标签，报告用
    unit: IndicatorUnit = "pct"         # 决定 d_* 的读法：pct→基点，price/index→百分比
    level: float | None = None          # 最新值（原始单位）
    d_1w: float | None = None           # 变化：pct 计为 bp，price/index 计为 %
    d_1m: float | None = None
    d_3m: float | None = None
    z_3y: float | None = None           # 水平相对 3 年分布的 z-score
    pct_10y: float | None = None        # 水平在 10 年分布中的百分位 0..100
    as_of: date | None = None           # 该序列最后一个观测的日期（非运行时间）
    source: str = ""                    # fred:DGS10 / yfinance:^VIX / macromicro …
    stale: bool = False                 # 观测过旧（按频率各自的容忍度判定）
    # Small vintage snapshot used on the next run to detect revisions to an
    # already-published observation.  Raw histories still are not persisted.
    recent_observations: dict[str, float] = Field(default_factory=dict)


class MacroDataDelta(BaseModel):
    """One macro observation discovered since the comparison review.

    FRED dates monthly observations to their reference month, not their release
    day.  Keep both dates so an 8-Aug review can correctly identify the 7-Aug
    employment release even though its observation is dated 1-Jul.
    """

    key: str
    label: str = ""
    change_kind: Literal["new_release", "revision", "newly_tracked"] = "new_release"
    release_date: date | None = None
    observation_date: date | None = None
    previous_observation_date: date | None = None
    previous_level: float | None = None
    current_level: float | None = None
    level_change: float | None = None
    period_change: float | None = None
    unit: IndicatorUnit = "pct"
    source: str = ""


class AxisInput(BaseModel):
    """One vote feeding a growth/inflation axis, kept so a human can audit it.

    Storing the raw value and the threshold it was compared against is what makes
    a quadrant call checkable after the fact instead of an unexplained number.
    """

    key: str
    label: str = ""
    value: float | None = None          # 该输入的实际取值
    threshold: str = ""                 # 人读的判据，如 "≥ +0.50pp"
    score: float = 0.0                  # 该票的得分 [-1, +1]（负=恶化/下行）
    note: str = ""


class EarningsBackdrop(BaseModel):
    """S&P 500 **aggregate** earnings/valuation, parsed from FactSet Earnings Insight.

    Index level only, never single companies: the aggregate profit cycle is a macro
    variable, one company's EPS is the fundamental analyst's job (MACRO_ANALYST.md §2).

    Every field is optional and independently validated. FactSet's wording shifts
    with the earnings season ("estimated" before the quarter reports, "blended"
    after; guidance counts reset at the quarter roll), so a missing field is
    normal and must not invalidate the ones that did parse.
    """

    source: str = ""                       # EarningsInsight_072426.pdf
    report_date: date | None = None
    quarter: str = ""                      # "Q2 2026"

    growth_pct: float | None = None        # 本季 YoY 盈利增速
    growth_basis: str = ""                 # estimated（未开始披露）| blended（已部分披露）
    prior_growth_pct: float | None = None  # 季初时的预估增速
    prior_as_of: str = ""                  # "June 30"
    sectors_higher: int | None = None      # 几个板块较季初上修
    revision_direction: str = ""           # upward | downward

    guidance_quarter: str = ""             # 指引对应的季度（与 quarter 可能不同）
    guidance_negative: int | None = None
    guidance_positive: int | None = None

    pct_reported: float | None = None      # 已披露公司占比
    pct_eps_beat: float | None = None
    pct_revenue_beat: float | None = None

    fwd_pe: float | None = None            # 前瞻 12 个月 P/E
    fwd_pe_5y_avg: float | None = None
    fwd_pe_10y_avg: float | None = None

    degraded: bool = False                 # 结构化抽取失败，只剩散文
    notes: list[str] = Field(default_factory=list)

    @property
    def revision_pp(self) -> float | None:
        """本季增速相对季初预估的变化（百分点）—— 盈利修正的方向与幅度。"""
        if self.growth_pct is None or self.prior_growth_pct is None:
            return None
        return round(self.growth_pct - self.prior_growth_pct, 1)

    @property
    def fwd_pe_vs_5y_pct(self) -> float | None:
        """前瞻 P/E 相对自身 5 年均值的偏离（%）—— FactSet 自带的分布参照。"""
        if not self.fwd_pe or not self.fwd_pe_5y_avg:
            return None
        return round((self.fwd_pe / self.fwd_pe_5y_avg - 1) * 100, 1)

    def to_context(self) -> str:
        if self.degraded or self.fwd_pe is None:
            return ""
        bits = [f"S&P500 前瞻P/E {self.fwd_pe}"]
        if self.fwd_pe_5y_avg:
            bits.append(f"（5年均值 {self.fwd_pe_5y_avg}，偏离 {self.fwd_pe_vs_5y_pct:+.1f}%）")
        if self.growth_pct is not None:
            bits.append(f"| {self.quarter} 盈利增速 {self.growth_pct:+.1f}%（{self.growth_basis}）")
        if self.revision_pp is not None:
            bits.append(f"，较季初 {self.prior_as_of} 修正 {self.revision_pp:+.1f}pp")
        if self.sectors_higher is not None:
            way = {"upward": "上修", "downward": "下修"}.get(self.revision_direction, "变动")
            bits.append(f"，{self.sectors_higher} 个板块{way}")
        return "".join(bits)


class RateDecomposition(BaseModel):
    """名义 10y = 实际 10y + 通胀补偿，逐项拆开。

    FRED 的 T10YIE 本就定义为 DGS10 − DFII10，所以这个恒等式精确成立，分解是免费的。
    同一个方向的名义利率变化，成因不同则对股票的含义相反 —— 这是算术，不该交给 LLM 叙事。
    """

    d_nominal_bp: float | None = None
    d_real_bp: float | None = None
    d_breakeven_bp: float | None = None
    window_days: int = 30
    classification: str = ""            # §5.1 四种组合之一的人读结论
    equity_read: str = ""               # 对股票的含义（确定性查表，非 LLM）
    real_yield_cause: str = ""          # §5.2 实际收益率下降的成因：良性反通胀/衰退驱动/未定


class MacroReview(BaseModel):
    """The persisted weekly review.

    Two layers with different authors, deliberately kept apart so a reader can tell
    fact from narrative at a glance (docs/MACRO_ANALYST.md §4.4):
      · deterministic — quadrant / axes / indicators / decomposition / alerts:
        computed by code, the LLM is forbidden from rewriting them.
      · narrative — regime / summary / rate_path / sector_tilts / … / falsifier.

    Every deterministic field carries a default: rows written before this layer
    existed still revalidate out of `macro_reviews.payload`.
    """

    name: str
    as_of: datetime

    # ── 确定性层（代码填）──────────────────────────────
    quadrant: Quadrant = "transition"
    quadrant_state: QuadrantState = "insufficient"
    quadrant_weeks: int = 0            # 当前象限已连续成立几期（迟滞计数）
    # WHY there is no call — "信号中性" (data present, genuinely unclear),
    # "输入互相矛盾" and "输入不足" are three different situations and the
    # response to each differs; collapsing them into `insufficient` loses that.
    quadrant_reason: str = ""
    growth_axis: float = 0.0           # [-1, +1]，负=恶化
    inflation_axis: float = 0.0        # [-1, +1]，负=下行
    axis_inputs: list[AxisInput] = Field(default_factory=list)
    indicators: list[IndicatorReading] = Field(default_factory=list)
    comparison_as_of: datetime | None = None
    data_deltas: list[MacroDataDelta] = Field(default_factory=list)
    decomposition: RateDecomposition | None = None
    earnings_backdrop: EarningsBackdrop | None = None   # 总量盈利周期（优先级 8）
    shock_vs_trend: list[str] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    focus_keys: list[str] = Field(default_factory=list)   # 当期该重点看的指标（象限决定）

    # ── 叙事层（LLM 填）────────────────────────────────
    regime: str = ""                   # risk-on/off + 周期位置，一句话自包含（注入用）
    summary: str = ""
    conclusion_delta: str = ""         # 相对上一份正式周报，结论为何/如何变化
    rate_path: str = ""                # 利率路径判断（降/持/加息与时点）
    sector_tilts: list[SectorTilt] = Field(default_factory=list)   # 核心交付物
    asset_implications: str = ""       # 股/债/美元/黄金/原油
    themes: list[ThemeAssess] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)
    falsifier: str = ""                # 什么观察会推翻这次判断（必须具体可观测）

    def quadrant_brief(self) -> str:
        """Shortest useful form, for callers on a tight character budget.

        Omits the alerts that `quadrant_line` carries — in a 280-char monitor
        hint a long alert would crowd out the regime itself.
        """
        if self.quadrant == "transition" and not self.axis_inputs:
            return ""                   # nothing was computed; say nothing
        state = _STATE_CN.get(self.quadrant_state, self.quadrant_state)
        return f"象限 {self.quadrant}（{state}）"

    def quadrant_line(self) -> str:
        """One compact line summarising the deterministic call, for injection."""
        state = _STATE_CN.get(self.quadrant_state, self.quadrant_state)
        line = (f"[象限] {self.quadrant}（{state}"
                f"{f'，第 {self.quadrant_weeks} 期' if self.quadrant_weeks else ''}）"
                f" | 增长 {self.growth_axis:+.2f} / 通胀 {self.inflation_axis:+.2f}")
        if self.alerts:
            line += " | 告警: " + "; ".join(self.alerts)
        return line

    def regime_block(self, max_chars: int = 1200) -> str:
        # The quadrant goes FIRST: downstream readers (PEAD/sector/chief) see only
        # this block, never the full report, and the deterministic call is the part
        # that must survive truncation.
        parts = [f"[宏观评审 {self.as_of:%Y-%m-%d}] {self.regime}", self.quadrant_line()]
        if self.conclusion_delta:
            parts.append(f"相对上期: {self.conclusion_delta}")
        if self.rate_path:
            parts.append(f"利率路径: {self.rate_path}")
        if self.asset_implications:
            parts.append(f"资产含义: {self.asset_implications}")
        if self.sector_tilts:
            tilts = "; ".join(f"{t.sector}={t.stance}" for t in self.sector_tilts)
            parts.append(f"板块倾斜: {tilts}")
        return "\n".join(parts)[:max_chars]
