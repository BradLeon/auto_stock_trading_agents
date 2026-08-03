"""Risk guardrail contract produced by the risk manager."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RiskGuardrails(BaseModel):
    """Hard constraints the Manager's decisions must satisfy.

    A deterministic validator (see agents/manager) enforces these after the
    Manager LLM produces decisions — the LLM is never trusted to self-comply.
    """

    as_of: datetime
    max_position_pct: float = Field(0.20, gt=0, le=1)
    max_sector_pct: float = Field(0.40, gt=0, le=1)
    max_gross_leverage: float = Field(1.0, gt=0)
    max_single_order_usd: float = Field(25000, gt=0)
    cash_floor_pct: float = Field(0.05, ge=0, le=1)
    no_add_list: list[str] = Field(default_factory=list, description="symbols not allowed to add")
    forced_trim: list[str] = Field(default_factory=list, description="symbols that must be reduced")
    notes: str = ""


# --------------------------------------------------------------------------- #
# Risk officer — full 6-layer picture (deterministic)
# --------------------------------------------------------------------------- #
class Cluster(BaseModel):
    members: list[str] = Field(default_factory=list)
    weight: float = 0.0               # total portfolio weight of the cluster
    avg_corr: float = 0.0


class StressResult(BaseModel):
    scenario: str
    loss_pct: float = 0.0             # portfolio loss vs NAV (negative)


class EventRisk(BaseModel):
    symbol: str
    weight: float = 0.0
    expected_move_pct: float | None = None
    event_loss_pct: float = 0.0       # weight * expected_move (as % NAV)


class LayerExposure(BaseModel):
    key: str
    label: str = ""
    weight: float = 0.0
    cap: float | None = None
    breached: bool = False


class CashEquivalent(BaseModel):
    """A holding treated (partly) as cash via a haircut. cash_credit counts toward
    effective cash; risk_weight (= market_value × haircut) is the residual exposure."""
    symbol: str
    market_value: float = 0.0
    haircut: float = 0.0              # 0 = full cash credit .. 1 = full risk exposure
    cash_credit: float = 0.0          # market_value × (1 − haircut)


class OptionRisk(BaseModel):
    """An option position (secType=OPT) decomposed by greeks and folded into the 6-layer
    framework via delta-notional (single-name/factor) + BSM full-revaluation (stress).
    Only the 4 single-leg strategies are classified: sell_put / covered_call / naked_call /
    buy_call / buy_put."""
    symbol: str                       # contract label (underlying + expiry/strike/right)
    underlying: str                   # underlying symbol (drives layer/beta/cluster)
    sec_type: str = "OPT"
    right: str = ""                   # 'C' | 'P'
    strike: float = 0.0
    expiry: str = ""                  # YYYYMMDD
    qty: float = 0.0                  # signed: long > 0, short < 0 (contracts)
    multiplier: float = 100.0
    strategy: str = ""                # sell_put | covered_call | naked_call | buy_call | buy_put | ""
    spot: float | None = None         # underlying spot used for pricing
    iv: float | None = None           # implied vol (decimal)
    delta: float | None = None        # per-contract greeks (option-share terms)
    gamma: float | None = None
    vega: float | None = None         # per 1 vol point (×0.01)
    theta: float | None = None        # per day
    delta_notional: float = 0.0       # signed: delta × qty × mult × spot
    margin: float | None = None       # position margin (IBKR or Reg-T estimate)
    premium_mv: float = 0.0           # market value of the premium (IBKR marketValue)
    unrealized_pnl: float = 0.0
    priced: bool = False              # greeks available (IBKR or BSM); False → list-only fallback
    greeks_source: str | None = None  # 'ibkr' | 'bsm' | None
    economic_entity: str = ""
    risk_symbol: str = ""
    exposure_multiplier: float = 1.0
    fx_rate_to_base: float = 1.0


class PortfolioGreeks(BaseModel):
    """Portfolio-level aggregate greeks (options only; equities contribute delta_notional
    via underlying exposure, not to gamma/vega/theta)."""
    net_delta_notional: float = 0.0   # Σ option delta_notional (signed, $)
    net_gamma: float = 0.0            # Σ gamma × qty × mult
    net_vega: float = 0.0             # Σ vega × qty × mult (per 1 vol point, $)
    net_theta: float = 0.0            # Σ theta × qty × mult (per day, $)
    delta_adj_leverage: float = 0.0   # Σ|net delta_notional (equity+option)| / net_liq


class UnderlyingExposure(BaseModel):
    """Per-underlying net exposure combining equity weight and option delta-notional weight
    (long put / short call net against long stock)."""
    symbol: str
    equity_weight: float = 0.0        # risk-weighted equity weight (fraction of net_liq)
    option_delta_weight: float = 0.0  # Σ option delta_notional / net_liq (signed)
    net_delta_weight: float = 0.0     # equity_weight + option_delta_weight (signed)
    layer: str = ""


class EconomicExposure(BaseModel):
    """One economic risk after aggregating listings, ETFs and options."""
    economic_entity: str
    label: str
    risk_symbol: str
    members: list[str] = Field(default_factory=list)
    capital_weight: float = 0.0
    equity_delta_weight: float = 0.0
    option_delta_weight: float = 0.0
    net_delta_weight: float = 0.0
    beta_contribution: float | None = None
    layer: str = ""


class MarginSummary(BaseModel):
    """Account-level margin picture; projected/Reg-T estimates are disclosed as estimates."""
    init_margin: float | None = None
    maint_margin: float | None = None
    excess_liquidity: float | None = None
    buying_power: float | None = None
    margin_util: float | None = None      # init_margin / net_liq
    excess_liq_pct: float | None = None   # excess_liquidity / net_liq
    source: str | None = None             # 'ibkr' | 'regt_est' | None


class SymbolLayer(BaseModel):
    """Explicit portfolio-symbol → 产业链层 correspondence (so the mapping is auditable)."""
    symbol: str
    layer: str = ""                   # layer key, "" if unmapped
    label: str = "未分层"
    weight: float = 0.0
    sec_type: str = "STK"


class Breach(BaseModel):
    layer: str                        # e.g. "L1-chain-layer", "L3-beta", "L6-event"
    limit: str                        # human-readable limit
    actual: str
    action: str                       # what enforcement did / would do


class AssignmentRisk(BaseModel):
    symbol: str
    underlying: str
    expiry: str
    days_to_expiry: int = 0
    full_assignment_notional: float = 0.0
    assignment_probability: float | None = None
    probability_source: str = "unknown"
    probability_weighted_notional: float = 0.0


class ExpiryFundingBucket(BaseModel):
    label: str
    through_days: int
    expiries: list[str] = Field(default_factory=list)
    full_notional: float = 0.0
    expected_notional: float = 0.0
    p99_notional: float = 0.0


class OptionSurvivalSummary(BaseModel):
    assignments: list[AssignmentRisk] = Field(default_factory=list)
    expiry_buckets: list[ExpiryFundingBucket] = Field(default_factory=list)
    total_full_assignment_notional: float = 0.0
    probability_weighted_notional: float = 0.0
    p99_assignment_notional: float = 0.0
    peak_expiry_full_notional: float = 0.0
    available_liquidity: float = 0.0
    p99_funding_gap: float = 0.0
    has_unknown_probability: bool = False
    notes: str = ""


DirectiveState = Literal["NORMAL", "LIMITED", "REPAIR_ONLY", "DATA_INVALID", "EMERGENCY"]


class RiskDirective(BaseModel):
    """Compact, deterministic instructions consumed by Chief and the order gate."""
    state: DirectiveState = "NORMAL"
    can_increase_risk: bool = True
    allowed_actions: list[str] = Field(default_factory=list)
    blocked_entities: list[str] = Field(default_factory=list)
    blocked_layers: list[str] = Field(default_factory=list)
    risk_budget_remaining: dict[str, float] = Field(default_factory=dict)
    required_repairs: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    def as_chief_instruction(self) -> str:
        actions = ", ".join(self.allowed_actions) or "none"
        lines = [f"RiskDirective={self.state} · 可增加风险={self.can_increase_risk} · 允许={actions}"]
        if self.blocked_entities:
            lines.append("禁止增加实体: " + ", ".join(self.blocked_entities))
        if self.blocked_layers:
            lines.append("禁止增加产业链层: " + ", ".join(self.blocked_layers))
        if self.required_repairs:
            lines.append("必须修复: " + "; ".join(self.required_repairs[:5]))
        if self.reasons:
            lines.append("原因: " + "; ".join(self.reasons[:5]))
        return "\n".join(lines)


class OptionSurvivalPolicy(BaseModel):
    max_full_assignment_nav_pct: float = Field(1.0, gt=0)
    max_probability_weighted_nav_pct: float = Field(0.35, gt=0)
    max_p99_assignment_nav_pct: float = Field(0.75, gt=0)
    max_peak_expiry_nav_pct: float = Field(0.50, gt=0)
    expiry_horizons_days: list[int] = Field(default_factory=lambda: [7, 30, 90, 365])
    p99_z_score: float = Field(2.326, gt=0)


class DirectivePolicy(BaseModel):
    limited_headroom_pct: float = Field(0.10, ge=0, lt=1)


class RiskPolicy(BaseModel):
    option_survival: OptionSurvivalPolicy = Field(default_factory=OptionSurvivalPolicy)
    directive: DirectivePolicy = Field(default_factory=DirectivePolicy)


class RiskMetricDelta(BaseModel):
    metric: str
    before_utilization: float
    after_utilization: float
    limit: float = 1.0
    worsened: bool = False
    improved: bool = False
    new_breach: bool = False


class MarginalRiskAssessment(BaseModel):
    symbol: str
    allowed: bool
    classification: str = "neutral"
    deltas: list[RiskMetricDelta] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class RiskReview(BaseModel):
    as_of: datetime
    net_liquidation: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    cash_pct: float = 0.0                         # raw account cash / net_liq
    effective_cash_pct: float = 0.0              # (cash + Σ cash_credit) / net_liq
    effective_leverage: float | None = None      # (gross − Σ cash_credit) / net_liq
    cash_equivalents: list[CashEquivalent] = Field(default_factory=list)
    option_risks: list[OptionRisk] = Field(default_factory=list)   # options folded into 6 layers
    portfolio_greeks: PortfolioGreeks | None = None
    underlying_exposures: list[UnderlyingExposure] = Field(default_factory=list)
    economic_exposures: list[EconomicExposure] = Field(default_factory=list)
    margin: MarginSummary | None = None
    symbol_layers: list[SymbolLayer] = Field(default_factory=list)  # explicit symbol→layer map
    portfolio_beta: float | None = None
    chain_layers: list[LayerExposure] = Field(default_factory=list)
    clusters: list[Cluster] = Field(default_factory=list)
    drawdown_pct: float | None = None
    daily_pnl_pct: float | None = None
    stress: list[StressResult] = Field(default_factory=list)
    event_risks: list[EventRisk] = Field(default_factory=list)
    option_survival: OptionSurvivalSummary | None = None
    breaches: list[Breach] = Field(default_factory=list)
    cautions: list[Breach] = Field(default_factory=list)   # advisory (期权限额/估算保证金): 披露不硬阻单
    risk_state: str = "normal"        # normal | caution | derisk
    directive: RiskDirective | None = None
    notes: str = ""

    def as_memo_context(self, max_chars: int = 4000) -> str:
        """Full deterministic picture fed to the risk-officer LLM. Numbers come from
        this engine — the LLM narrates, it does not invent figures."""
        el = f"{self.effective_leverage:.2f}x" if self.effective_leverage is not None else "—"
        parts = [
            f"风险状态={self.risk_state} · NetLiq ${self.net_liquidation:,.0f}",
            f"现金(原始)={self.cash_pct:.1%} · 有效现金={self.effective_cash_pct:.1%} · "
            f"杠杆(原始/有效)={self.gross_exposure/self.net_liquidation if self.net_liquidation else 0:.2f}x/{el}",
            f"组合beta={self.portfolio_beta} · 回撤={self.drawdown_pct}% · 日盈亏={self.daily_pnl_pct}%",
        ]
        if self.directive:
            parts.append(self.directive.as_chief_instruction())
        if self.margin:
            m = self.margin
            src = (
                "IBKR" if m.source == "ibkr"
                else "IBKR基线+交易后估算" if m.source == "ibkr_projected_est"
                else "Reg-T估算" if m.source == "regt_est" else "—")
            util = f"{m.margin_util:.0%}" if m.margin_util is not None else "—"
            elp = f"{m.excess_liq_pct:.0%}" if m.excess_liq_pct is not None else "—"
            parts.append(f"保证金({src}): 利用率={util} · 剩余流动性={elp} · "
                         f"初始保证金=${m.init_margin:,.0f}" if m.init_margin is not None
                         else f"保证金({src}): 利用率={util} · 剩余流动性={elp}")
        if self.portfolio_greeks:
            g = self.portfolio_greeks
            parts.append(f"组合Greeks: 净Δ名义=${g.net_delta_notional:,.0f} · 净Vega=${g.net_vega:,.0f}/1vol · "
                         f"净Theta=${g.net_theta:,.0f}/日 · Δ调整杠杆={g.delta_adj_leverage:.2f}x")
        if self.cash_equivalents:
            parts.append("现金等价物: " + "; ".join(
                f"{c.symbol} 市值${c.market_value:,.0f} haircut={c.haircut:.0%} 现金信用${c.cash_credit:,.0f}"
                for c in self.cash_equivalents))
        if self.economic_exposures:
            parts.append("经济风险实体: " + "; ".join(
                f"{e.label}[{','.join(e.members)}] 资本={e.capital_weight:.1%} "
                f"净经济Δ={e.net_delta_weight:+.1%} beta贡献={e.beta_contribution:+.1%}"
                for e in sorted(self.economic_exposures,
                                key=lambda x: abs(x.net_delta_weight), reverse=True)))
        if self.option_risks:
            parts.append("期权持仓(已并入风控): " + "; ".join(
                f"{o.underlying} {o.strategy or o.right} Δ名义=${o.delta_notional:,.0f}"
                + (f" IV={o.iv:.0%}" if o.iv is not None else "")
                + (f" 保证金=${o.margin:,.0f}" if o.margin is not None else "")
                + (f" uPnL=${o.unrealized_pnl:,.0f}") + ("" if o.priced else " [未定价]")
                for o in self.option_risks))
        if self.symbol_layers:
            parts.append("标的→产业链层: " + "; ".join(
                f"{sl.symbol}[{sl.sec_type}]→{sl.label}({sl.weight:.0%})" for sl in self.symbol_layers))
        if self.chain_layers:
            parts.append("产业链层: " + "; ".join(
                f"{le.label}={le.weight:.0%}" + (f"(限{le.cap:.0%}⚠)" if le.breached else "")
                for le in self.chain_layers))
        if self.clusters:
            parts.append("相关簇: " + "; ".join(
                f"{c.weight:.0%} avgρ={c.avg_corr} [{','.join(c.members[:5])}]" for c in self.clusters))
        if self.stress:
            parts.append("压测: " + "; ".join(f"{s.scenario}={s.loss_pct}%" for s in self.stress))
        if self.event_risks:
            parts.append("事件: " + "; ".join(
                f"{e.symbol} w={e.weight:.0%} EM={e.expected_move_pct}% 损失={e.event_loss_pct}%"
                for e in self.event_risks))
        if self.breaches:
            parts.append("破限:\n" + "\n".join(
                f"  ⚠️ {b.layer}: {b.actual} vs {b.limit} → {b.action}" for b in self.breaches))
        else:
            parts.append("破限: 无")
        if self.cautions:
            parts.append("提示(不硬阻单):\n" + "\n".join(
                f"  · {c.layer}: {c.actual} vs {c.limit} → {c.action}" for c in self.cautions))
        return "\n".join(parts)[:max_chars]

    def regime_block(self, max_chars: int = 800) -> str:
        parts = [f"[风控 {self.as_of:%Y-%m-%d}] 状态={self.risk_state} beta={self.portfolio_beta} "
                 f"回撤={self.drawdown_pct}% 现金={self.cash_pct:.0%}(有效{self.effective_cash_pct:.0%})"]
        if self.directive:
            parts.insert(0, self.directive.as_chief_instruction())
        if self.portfolio_greeks or self.margin:
            g, m = self.portfolio_greeks, self.margin
            netv = f"净Vega=${g.net_vega:,.0f}" if g else ""
            util = f"保证金利用率={m.margin_util:.0%}" if (m and m.margin_util is not None) else ""
            extra = " ".join(x for x in (netv, util) if x)
            if extra:
                parts.append(f"  {extra}")
        for b in self.breaches:
            parts.append(f"  ⚠️ {b.layer}: {b.actual} vs {b.limit} → {b.action}")
        return "\n".join(parts)[:max_chars]


# --------------------------------------------------------------------------- #
# Risk officer — narrative memo (LLM analyst role, mirrors macro/sector)
# --------------------------------------------------------------------------- #
class LayerConclusion(BaseModel):
    layer: str                        # e.g. "L1 单票/止损", "L2 杠杆/现金"
    conclusion: str = ""              # 一句结论：是否可放心 / 需关注什么


class RiskMemo(BaseModel):
    """Narrative risk-officer assessment. Numbers live in `review` (deterministic);
    the LLM fields are judgment layered on top and never overwrite the figures."""
    as_of: datetime
    assessment: str = ""              # 总评
    cash_equivalent_read: str = ""    # 现金等价物解读（真实可用弹药）
    layer_conclusions: list[LayerConclusion] = Field(default_factory=list)
    headroom: str = ""                # 距各限额的余量判断
    recommended_actions: list[str] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)
    review: RiskReview | None = None  # deterministic source
