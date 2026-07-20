"""Pre-trade hard enforcement — the 6-layer gate applied before ANY order.

Reuses the existing L1-2 clip (risk_manager + risk_validator), then layers the new
hard constraints (beta / correlation cluster / chain-layer / earnings-event /
drawdown / stress). Only ever TIGHTENS: risk-reducing sells always pass; buys are
dropped or their notional clipped. Returns (approved_decisions, notes, RiskReview).
"""

from __future__ import annotations

import logging

from ..schemas.decision import TradeDecision
from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import RiskGuardrails, RiskReview
from . import assess as risk_assess

log = logging.getLogger("ats.risk.checks")

_BUY = {"buy", "add"}


def pre_trade(decisions: list[TradeDecision], portfolio: PortfolioSnapshot | None, *,
              sector: str = "ai_hardware", event_data: dict[str, dict] | None = None,
              review: RiskReview | None = None, apply_base: bool = True
              ) -> tuple[list[TradeDecision], list[str], RiskReview | None]:
    """apply_base=False skips the L1-2 clip (caller already ran a scoped version, e.g. PEAD)."""
    from ..agents import risk_manager, risk_validator
    from ..config import get_config

    rc = get_config().app.risk
    if portfolio is None:
        return decisions, ["(no live portfolio — risk checks skipped)"], None

    net_liq = portfolio.net_liquidation or get_config().app.account.net_liquidation_usd
    sector_by_symbol = {p.symbol: p.sector for p in portfolio.positions}

    if apply_base:
        # L1-2 base: existing deterministic clip (position/order/sector/cash/leverage).
        gr = risk_manager.assess(as_of=_now(), risk_cfg=rc, portfolio=portfolio,
                                 sector_by_symbol=sector_by_symbol)
        clipped, notes = risk_validator.apply_guardrails(
            decisions, gr, sector_by_symbol=sector_by_symbol, net_liquidation=net_liq,
            portfolio=portfolio)
    else:
        clipped, notes = list(decisions), []

    # New layers: assess current risk (beta enriched) unless a review was passed in.
    if review is None:
        risk_assess.enrich_beta(portfolio)
        risk_assess.enrich_options(portfolio)
        review = risk_assess.assess(portfolio, sector=sector, event_data=event_data)

    breach_layers = {b.layer for b in review.breaches}
    derisk = review.risk_state == "derisk" or any(b.startswith("L4-") for b in breach_layers)
    beta_over = any(b.startswith("L3-组合beta") for b in breach_layers)
    cluster_over = any(b == "L3-相关簇" for b in breach_layers)
    stress_over = any(b == "L5-压测" for b in breach_layers)
    top_cluster = set(review.clusters[0].members) if review.clusters else set()
    over_layers = {le.key for le in review.chain_layers if le.breached}

    from ..config import load_sector_config
    try:
        scfg = load_sector_config(sector)
    except Exception:  # noqa: BLE001
        scfg = None

    out: list[TradeDecision] = []
    for d in clipped:
        if d.action not in _BUY:
            out.append(d)                       # risk-reducing always passes
            continue
        if derisk:
            notes.append(f"BLOCK {d.symbol}: de-risk 态（回撤/日亏破限）只许减仓")
            continue
        if beta_over and (_beta(portfolio, d.symbol) or 0) > rc.beta_cap:
            notes.append(f"BLOCK {d.symbol}: 组合 beta 超限，禁加高 beta 名")
            continue
        if (cluster_over or stress_over) and d.symbol in top_cluster:
            notes.append(f"BLOCK {d.symbol}: 相关簇/压测超限，禁加该簇")
            continue
        if scfg and scfg.layer_of(d.symbol) in over_layers:
            notes.append(f"BLOCK {d.symbol}: 产业链层 {scfg.layer_of(d.symbol)} 权重超限")
            continue
        # L6 event: clip notional so weight*EM <= max_event_loss
        em = (event_data or {}).get(d.symbol, {}).get("expected_move_pct")
        if em and d.notional_usd and net_liq:
            max_notional = (rc.max_event_loss_pct * 100 / em) * net_liq
            if d.notional_usd > max_notional:
                notes.append(f"CLIP {d.symbol}: 事件风险，notional {d.notional_usd:.0f}→{max_notional:.0f}")
                d = d.model_copy(update={"notional_usd": round(max_notional, 0)})
        out.append(d)
    return out, notes, review


def _beta(portfolio: PortfolioSnapshot, symbol: str) -> float | None:
    for p in portfolio.positions:
        if p.symbol == symbol:
            return p.beta
    return None


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
