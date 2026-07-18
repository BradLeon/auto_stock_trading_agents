"""Risk guardrail contract produced by the risk manager."""

from __future__ import annotations

from datetime import datetime

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


class OptionHolding(BaseModel):
    """An option position (secType=OPT). Currently EXEMPT from the equity 6-layer rules
    (stop-loss / drawdown / margin / single-name / beta / chain-layer) — option premium is
    non-linear and needs its own greeks-based rules (TODO). Surfaced for visibility only."""
    symbol: str                       # underlying symbol (IBKR reports the underlying)
    sec_type: str = "OPT"
    market_value: float = 0.0
    weight: float = 0.0
    unrealized_pnl: float = 0.0


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


class RiskReview(BaseModel):
    as_of: datetime
    net_liquidation: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    cash_pct: float = 0.0                         # raw account cash / net_liq
    effective_cash_pct: float = 0.0              # (cash + Σ cash_credit) / net_liq
    effective_leverage: float | None = None      # (gross − Σ cash_credit) / net_liq
    cash_equivalents: list[CashEquivalent] = Field(default_factory=list)
    options: list[OptionHolding] = Field(default_factory=list)     # exempt from equity rules
    symbol_layers: list[SymbolLayer] = Field(default_factory=list)  # explicit symbol→layer map
    portfolio_beta: float | None = None
    chain_layers: list[LayerExposure] = Field(default_factory=list)
    clusters: list[Cluster] = Field(default_factory=list)
    drawdown_pct: float | None = None
    daily_pnl_pct: float | None = None
    stress: list[StressResult] = Field(default_factory=list)
    event_risks: list[EventRisk] = Field(default_factory=list)
    breaches: list[Breach] = Field(default_factory=list)
    risk_state: str = "normal"        # normal | caution | derisk
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
        if self.cash_equivalents:
            parts.append("现金等价物: " + "; ".join(
                f"{c.symbol} 市值${c.market_value:,.0f} haircut={c.haircut:.0%} 现金信用${c.cash_credit:,.0f}"
                for c in self.cash_equivalents))
        if self.options:
            parts.append("期权持仓(暂豁免风控): " + "; ".join(
                f"{o.symbol} w={o.weight:.0%} uPnL=${o.unrealized_pnl:,.0f}" for o in self.options))
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
        return "\n".join(parts)[:max_chars]

    def regime_block(self, max_chars: int = 800) -> str:
        parts = [f"[风控 {self.as_of:%Y-%m-%d}] 状态={self.risk_state} beta={self.portfolio_beta} "
                 f"回撤={self.drawdown_pct}% 现金={self.cash_pct:.0%}(有效{self.effective_cash_pct:.0%})"]
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
