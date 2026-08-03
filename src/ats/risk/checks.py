"""Pre-trade gate based on projected post-trade risk, not action labels."""

from __future__ import annotations

from ..schemas.decision import TradeDecision
from ..schemas.instruments import normalize_symbol
from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import RiskDirective, RiskReview
from . import assess as risk_assess
from . import marginal


def pre_trade(decisions: list[TradeDecision], portfolio: PortfolioSnapshot | None, *,
              sector: str = "ai_hardware", event_data: dict[str, dict] | None = None,
              review: RiskReview | None = None, apply_base: bool = True
              ) -> tuple[list[TradeDecision], list[str], RiskReview | None]:
    """Project each order sequentially and allow it only when its risk delta is valid.

    `apply_base=False` means the caller already applied operational order sizing caps.
    Portfolio limits are always enforced here from the post-trade snapshot.
    """
    from ..config import get_config, load_risk_policy

    if portfolio is None:
        return decisions, ["(no live portfolio — risk checks skipped)"], None
    rc = get_config().app.risk
    policy = load_risk_policy()
    notes: list[str] = []
    candidates = _apply_order_caps(decisions, portfolio, rc, notes) if apply_base else [
        d.model_copy(deep=True) for d in decisions]

    current_pf = portfolio.model_copy(deep=True)
    supplied_state = _supplied_state(review)
    if review is None or not review.economic_exposures:
        risk_assess.enrich_beta(current_pf)
        risk_assess.enrich_options(current_pf)
        current_review = risk_assess.assess(
            current_pf, sector=sector, event_data=event_data)
        if supplied_state in ("REPAIR_ONLY", "DATA_INVALID", "EMERGENCY"):
            current_review.directive = _override_directive(current_review, supplied_state)
    else:
        current_review = review
    initial_review = current_review
    effective_state = _supplied_state(current_review)
    if effective_state in ("REPAIR_ONLY", "DATA_INVALID", "EMERGENCY"):
        notes.append(
            f"STATE {effective_state}: de-risk/repair-only，"
            "订单必须净改善且不得恶化其他风险指标")

    approved: list[TradeDecision] = []
    for decision in candidates:
        decision = _clip_event_notional(
            decision, current_pf, rc, event_data or {}, notes)
        post_pf = marginal.project_trade(current_pf, decision)
        if post_pf.model_dump() == current_pf.model_dump():
            notes.append(f"BLOCK {decision.symbol}: 无法从订单字段推导交易后仓位")
            continue
        risk_assess.enrich_beta(post_pf)
        risk_assess.enrich_options(post_pf)
        post_review = risk_assess.assess(
            post_pf, sector=sector, event_data=event_data)
        verdict = marginal.compare(
            decision.symbol, current_review, post_review, rc, policy)
        changed = [
            f"{d.metric} {d.before_utilization:.2f}→{d.after_utilization:.2f}"
            for d in verdict.deltas if d.improved or d.worsened]
        detail = ", ".join(changed[:4]) or "风险向量无变化"
        if not verdict.allowed:
            notes.append(
                f"BLOCK {decision.symbol}: {'; '.join(verdict.reasons)} [{detail}]")
            continue
        notes.append(
            f"ALLOW {decision.symbol}: marginal={verdict.classification} [{detail}]")
        approved.append(decision)
        current_pf = post_pf
        current_review = post_review
    return approved, notes, initial_review


def _apply_order_caps(decisions, portfolio, rc, notes) -> list[TradeDecision]:
    """Operational sizing only; portfolio risk is left to post-trade simulation."""
    out: list[TradeDecision] = []
    nav = portfolio.net_liquidation
    held = {normalize_symbol(p.symbol): p for p in portfolio.positions if p.sec_type != "OPT"}
    for raw in decisions:
        d = raw.model_copy(deep=True)
        if d.notional_usd and d.notional_usd > rc.max_single_order_usd:
            notes.append(
                f"CLIP {d.symbol}: order ${d.notional_usd:,.0f}→${rc.max_single_order_usd:,.0f}")
            d.notional_usd = rc.max_single_order_usd
        if d.notional_usd is None and d.target_weight is not None and nav:
            current = held.get(normalize_symbol(d.symbol))
            current_mv = current.market_value if current else 0.0
            d.notional_usd = abs(d.target_weight * nav - current_mv)
            if d.notional_usd > rc.max_single_order_usd:
                notes.append(
                    f"CLIP {d.symbol}: target delta ${d.notional_usd:,.0f}"
                    f"→${rc.max_single_order_usd:,.0f}")
                d.notional_usd = rc.max_single_order_usd
        if d.notional_usd is None and d.action == "sell":
            current = held.get(normalize_symbol(d.symbol))
            if current:
                d.notional_usd = abs(current.market_value)
        out.append(d)
    return out


def _clip_event_notional(decision, portfolio, rc, event_data, notes):
    if decision.notional_usd is None:
        return decision
    from ..config import load_instrument_risk_registry

    meta = load_instrument_risk_registry().resolve(decision.symbol)
    em = (event_data.get(decision.symbol, {}).get("expected_move_pct")
          or event_data.get(meta.risk_symbol, {}).get("expected_move_pct"))
    if not em or decision.action not in ("buy", "add") or not portfolio.net_liquidation:
        return decision
    max_notional = (
        rc.max_event_loss_pct * 100.0 / (em * meta.exposure_multiplier)
        * portfolio.net_liquidation)
    if decision.notional_usd > max_notional:
        notes.append(
            f"CLIP {decision.symbol}: 边际事件风险 ${decision.notional_usd:,.0f}"
            f"→${max_notional:,.0f}")
        return decision.model_copy(update={"notional_usd": round(max_notional, 2)})
    return decision


def _supplied_state(review: RiskReview | None) -> str:
    if review is None:
        return "NORMAL"
    if review.directive:
        return review.directive.state
    return "EMERGENCY" if review.risk_state == "derisk" else "NORMAL"


def _override_directive(review: RiskReview, state: str) -> RiskDirective:
    base = review.directive or RiskDirective()
    return base.model_copy(update={
        "state": state,
        "can_increase_risk": False,
        "allowed_actions": ["reduce", "hedge_if_verified_improving"],
    })
