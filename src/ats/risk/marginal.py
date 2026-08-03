"""Post-trade projection and metric-by-metric marginal risk comparison."""

from __future__ import annotations

import math

from ..schemas.decision import TradeDecision
from ..schemas.instruments import normalize_symbol
from ..schemas.portfolio import Position, PortfolioSnapshot
from ..schemas.risk import MarginalRiskAssessment, RiskMetricDelta, RiskReview

_BUY = {"buy", "add"}
_SELL = {"trim", "sell"}


def project_trade(portfolio: PortfolioSnapshot, decision: TradeDecision) -> PortfolioSnapshot:
    """Return a base-currency post-trade snapshot without mutating the live snapshot.

    TradeDecision currently represents stocks only. Options already held remain in the
    projected portfolio and continue to affect every risk metric.
    """
    post = portfolio.model_copy(deep=True)
    if decision.action == "hold":
        return post
    norm = normalize_symbol(decision.symbol)
    matches = [p for p in post.positions
               if p.sec_type != "OPT" and normalize_symbol(p.symbol) == norm]
    current = matches[0] if matches else None
    current_mv = current.market_value if current else 0.0
    delta_mv = _market_value_delta(decision, current, post.net_liquidation)
    if decision.action in _SELL:
        delta_mv = max(delta_mv, -max(current_mv, 0.0))
    if abs(delta_mv) < 1e-9:
        return post

    if current is None:
        current = Position(
            symbol=decision.symbol, sector="unknown", sec_type="STK",
            qty=decision.qty or 0.0, avg_cost=decision.limit_price or 0.0,
            market_price=decision.limit_price or 0.0, market_value=0.0,
            currency=post.base_currency, fx_rate_to_base=1.0)
        post.positions.append(current)
    old_abs = abs(current.market_value)
    new_mv = current.market_value + delta_mv
    if decision.qty:
        qty_delta = abs(decision.qty) * (1 if delta_mv > 0 else -1)
        current.qty = max(current.qty + qty_delta, 0.0)
    elif old_abs > 0:
        current.qty *= abs(new_mv) / old_abs
    current.market_value = new_mv
    current.market_value_local = new_mv
    current.weight = new_mv / post.net_liquidation if post.net_liquidation else 0.0

    post.cash -= delta_mv
    post.gross_exposure += abs(new_mv) - old_abs
    post.net_exposure += delta_mv
    post.leverage = post.gross_exposure / post.net_liquidation if post.net_liquidation else 0.0
    if post.margin_source in ("ibkr", "ibkr_projected_est"):
        # IBKR what-if is not available in this synchronous gate. Preserve the live
        # baseline and apply a disclosed Reg-T long-stock increment for the projection.
        if post.init_margin is not None:
            post.init_margin = max(post.init_margin + 0.50 * delta_mv, 0.0)
        if post.maint_margin is not None:
            post.maint_margin = max(post.maint_margin + 0.25 * delta_mv, 0.0)
        if post.excess_liquidity is not None:
            post.excess_liquidity -= 0.25 * delta_mv
        post.margin_source = "ibkr_projected_est"
    post.positions = [p for p in post.positions
                      if p.sec_type == "OPT" or abs(p.market_value) > 1e-9]
    post.exposure.by_ticker = {
        p.symbol: p.market_value / post.net_liquidation
        for p in post.positions if p.sec_type != "OPT" and post.net_liquidation}
    by_sector: dict[str, float] = {}
    for p in post.positions:
        if p.sec_type != "OPT" and post.net_liquidation:
            by_sector[p.sector] = by_sector.get(p.sector, 0.0) + p.market_value / post.net_liquidation
    post.exposure.by_sector = by_sector
    return post


def _market_value_delta(decision: TradeDecision, current: Position | None,
                        net_liq: float) -> float:
    current_mv = current.market_value if current else 0.0
    sign = 1.0 if decision.action in _BUY else -1.0
    if decision.notional_usd is not None:
        return sign * decision.notional_usd
    if decision.target_weight is not None:
        target_mv = decision.target_weight * net_liq
        raw = target_mv - current_mv
        return max(raw, 0.0) if sign > 0 else min(raw, 0.0)
    if decision.qty is not None:
        price = decision.limit_price or (current.market_price if current else 0.0)
        fx = current.fx_rate_to_base if current else 1.0
        return sign * abs(decision.qty) * price * fx
    if decision.action == "sell":
        return -max(current_mv, 0.0)
    return 0.0


def risk_utilizations(review: RiskReview, rc, policy) -> dict[str, float]:
    """Normalize heterogeneous limits so >1 means breached and lower is safer."""
    out: dict[str, float] = {}
    for e in review.economic_exposures:
        out[f"entity:{e.economic_entity}"] = _ratio(abs(e.net_delta_weight), rc.max_position_pct)
    for layer in review.chain_layers:
        if layer.cap:
            out[f"layer:{layer.key}"] = _ratio(abs(layer.weight), layer.cap)
    if review.portfolio_beta is not None:
        out["portfolio_beta"] = _ratio(max(review.portfolio_beta, 0.0), rc.beta_cap)
    leverage = review.effective_leverage
    if leverage is not None:
        out["gross_leverage"] = _ratio(max(leverage, 0.0), rc.max_gross_leverage)
    out["cash_floor"] = _lower_bound_utilization(review.effective_cash_pct, rc.cash_floor_pct)
    if review.stress:
        loss = abs(min(min(s.loss_pct for s in review.stress), 0.0)) / 100.0
        out["stress_loss"] = _ratio(loss, rc.max_stress_loss_pct)
    if review.drawdown_pct is not None:
        out["drawdown"] = _ratio(abs(min(review.drawdown_pct, 0.0)) / 100.0,
                                 rc.max_drawdown_pct)
    if review.daily_pnl_pct is not None:
        out["daily_loss"] = _ratio(abs(min(review.daily_pnl_pct, 0.0)) / 100.0,
                                   rc.daily_loss_limit_pct)
    if review.margin:
        if review.margin.margin_util is not None:
            out["margin_util"] = _ratio(review.margin.margin_util, rc.max_margin_util_pct)
        if review.margin.excess_liq_pct is not None:
            out["excess_liquidity"] = _lower_bound_utilization(
                review.margin.excess_liq_pct, rc.min_excess_liquidity_pct)
    if review.event_risks:
        out["event_loss"] = _ratio(
            max(e.event_loss_pct for e in review.event_risks) / 100.0,
            rc.max_event_loss_pct)
    survival = review.option_survival
    if survival and review.net_liquidation:
        sp = policy.option_survival
        nav = review.net_liquidation
        out["assignment_full"] = _ratio(
            survival.total_full_assignment_notional / nav, sp.max_full_assignment_nav_pct)
        out["assignment_expected"] = _ratio(
            survival.probability_weighted_notional / nav,
            sp.max_probability_weighted_nav_pct)
        out["assignment_p99"] = _ratio(
            survival.p99_assignment_notional / nav, sp.max_p99_assignment_nav_pct)
        out["assignment_expiry_peak"] = _ratio(
            survival.peak_expiry_full_notional / nav, sp.max_peak_expiry_nav_pct)
        if survival.p99_assignment_notional:
            out["assignment_liquidity"] = _ratio(
                survival.p99_assignment_notional, survival.available_liquidity)
        if survival.has_unknown_probability:
            out["assignment_data"] = 2.0
    return out


def compare(symbol: str, before: RiskReview, after: RiskReview, rc, policy,
            tolerance: float = 1e-6) -> MarginalRiskAssessment:
    pre = risk_utilizations(before, rc, policy)
    post = risk_utilizations(after, rc, policy)
    deltas: list[RiskMetricDelta] = []
    for metric in sorted(set(pre) | set(post)):
        b = pre.get(metric, 0.0)
        a = post.get(metric, 0.0)
        deltas.append(RiskMetricDelta(
            metric=metric, before_utilization=b, after_utilization=a,
            worsened=a > b + tolerance, improved=a < b - tolerance,
            new_breach=b <= 1.0 + tolerance and a > 1.0 + tolerance))

    new_breaches = [d.metric for d in deltas if d.new_breach]
    worsened = [d.metric for d in deltas if d.worsened]
    improved = [d.metric for d in deltas if d.improved]
    pre_state = before.directive.state if before.directive else (
        "EMERGENCY" if before.risk_state == "derisk" else "NORMAL")
    repair_mode = pre_state in ("REPAIR_ONLY", "DATA_INVALID", "EMERGENCY")
    improved_breach = any(d.improved and d.before_utilization > 1.0 + tolerance for d in deltas)
    reasons: list[str] = []
    allowed = True
    if new_breaches:
        allowed = False
        reasons.append("产生新破限: " + ", ".join(new_breaches))
    if repair_mode and worsened:
        allowed = False
        reasons.append("修复态不得恶化: " + ", ".join(worsened))
    if repair_mode and not improved_breach:
        allowed = False
        reasons.append("修复态必须改善至少一项已破限指标")
    after_state = after.directive.state if after.directive else "NORMAL"
    if after_state == "DATA_INVALID" and pre_state != "DATA_INVALID":
        allowed = False
        reasons.append("交易后关键数据无效")
    classification = "improving" if improved and not worsened else (
        "worsening" if worsened else "neutral")
    if allowed:
        reasons.append("交易后风险在允许边界内" if not repair_mode else "净改善且无其他恶化")
    return MarginalRiskAssessment(
        symbol=symbol, allowed=allowed, classification=classification,
        deltas=deltas, reasons=reasons)


def _ratio(actual: float, limit: float) -> float:
    if limit <= 0:
        return 1e9 if actual > 0 else 0.0
    value = actual / limit
    return min(value, 1e9) if math.isfinite(value) else 1e9


def _lower_bound_utilization(actual: float, minimum: float) -> float:
    if minimum <= 0:
        return 0.0
    if actual <= 0:
        return 1e9
    return min(minimum / actual, 1e9)
