"""Compute the full 6-layer RiskReview from a live portfolio (deterministic)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import (
    Breach,
    Cluster,
    EventRisk,
    LayerExposure,
    RiskReview,
    StressResult,
)
from . import correlation, stress

log = logging.getLogger("ats.risk.assess")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enrich_beta(portfolio: PortfolioSnapshot) -> None:
    """Fill Position.beta for held names (once, paced) — not done in get_portfolio."""
    from ..data import fundamentals

    for p in portfolio.positions:
        if p.beta is None:
            p.beta = fundamentals.fetch_light(p.symbol).get("beta")
            time.sleep(0.5)


def assess(portfolio: PortfolioSnapshot, *, sector: str = "ai_hardware",
           event_data: dict[str, dict] | None = None) -> RiskReview:
    """event_data: {symbol: {expected_move_pct, ...}} for held names near earnings."""
    from ..config import get_config, load_sector_config
    from ..memory import get_store

    rc = get_config().app.risk
    net_liq = portfolio.net_liquidation
    weights = {p.symbol: p.weight for p in portfolio.positions}
    r = RiskReview(
        as_of=_now(), net_liquidation=net_liq, gross_exposure=portfolio.gross_exposure,
        net_exposure=portfolio.net_exposure,
        cash_pct=(portfolio.cash / net_liq) if net_liq else 0.0)

    # L3 — portfolio beta
    if any(p.beta is not None for p in portfolio.positions):
        r.portfolio_beta = round(sum(p.weight * (p.beta or 1.0) for p in portfolio.positions), 2)
        if r.portfolio_beta > rc.beta_cap:
            r.breaches.append(Breach(layer="L3-组合beta", limit=f"≤{rc.beta_cap}",
                                     actual=str(r.portfolio_beta), action="block 加 beta 的新买单"))

    # L1 — per-chain-layer concentration
    try:
        scfg = load_sector_config(sector)
        layer_w: dict[str, float] = {}
        labels = {ly.key: ly.label for ly in scfg.layers}
        caps = {ly.key: ly.weight_cap for ly in scfg.layers}
        for p in portfolio.positions:
            lk = scfg.layer_of(p.symbol)
            if lk:
                layer_w[lk] = layer_w.get(lk, 0.0) + p.weight
        for lk, w in layer_w.items():
            cap = caps.get(lk)
            breached = cap is not None and w > cap
            r.chain_layers.append(LayerExposure(key=lk, label=labels.get(lk, lk),
                                                weight=round(w, 4), cap=cap, breached=breached))
            if breached:
                r.breaches.append(Breach(layer=f"L1-{lk}", limit=f"≤{cap:.0%}",
                                         actual=f"{w:.0%}", action="缩/block 入该层的新买单"))
    except Exception as exc:  # noqa: BLE001
        log.warning("chain-layer concentration skipped: %s", exc)

    # L3 — correlation clusters
    top_cluster_members: list[str] | None = None
    if len(weights) >= 2:
        prices = _prices(list(weights))
        for c in correlation.clusters(weights, prices, rc.cluster_corr_threshold):
            if len(c["members"]) > 1:
                r.clusters.append(Cluster(**c))
        if r.clusters:
            top = r.clusters[0]
            top_cluster_members = top.members
            if top.weight > rc.cluster_weight_cap:
                r.breaches.append(Breach(layer="L3-相关簇", limit=f"≤{rc.cluster_weight_cap:.0%}",
                                         actual=f"{top.weight:.0%} ({','.join(top.members[:5])}…)",
                                         action="block 入该相关簇的新买单"))

    # L1 — per-position stop-loss
    for p in portfolio.positions:
        cost = p.avg_cost * abs(p.qty)
        if cost > 0 and p.unrealized_pnl / cost <= -rc.stop_loss_pct:
            r.breaches.append(Breach(layer="L1-止损", limit=f"≥-{rc.stop_loss_pct:.0%}",
                                     actual=f"{p.symbol} {p.unrealized_pnl/cost:.0%}",
                                     action="强制 trim"))

    # L4 — drawdown / daily loss
    hist = get_store().performance_history(limit=250)
    if hist:
        from ..trader import analytics
        dd = analytics.max_drawdown_pct(hist + [_perf_stub(net_liq)])
        r.drawdown_pct = dd
        if dd is not None and dd <= -rc.max_drawdown_pct * 100:
            r.breaches.append(Breach(layer="L4-回撤", limit=f"≥-{rc.max_drawdown_pct:.0%}",
                                     actual=f"{dd}%", action="de-risk：block 所有新买"))
    if net_liq:
        r.daily_pnl_pct = round(portfolio.daily_pnl / net_liq * 100, 2)
        if r.daily_pnl_pct <= -rc.daily_loss_limit_pct * 100:
            r.breaches.append(Breach(layer="L4-日亏", limit=f"≥-{rc.daily_loss_limit_pct:.0%}",
                                     actual=f"{r.daily_pnl_pct}%", action="停新仓"))

    # L5 — stress
    r.stress = [StressResult(**s) for s in stress.run(
        portfolio, market_shocks=rc.stress_market_shocks, top_cluster=top_cluster_members,
        ai_bubble_shock=rc.ai_bubble_cluster_shock)]
    worst = min((s.loss_pct for s in r.stress), default=0.0)
    if worst <= -rc.max_stress_loss_pct * 100:
        r.breaches.append(Breach(layer="L5-压测", limit=f"≥-{rc.max_stress_loss_pct:.0%}",
                                 actual=f"{worst}%", action="block 加重受冲击敞口的新买单"))

    # L6 — earnings-event risk
    for p in portfolio.positions:
        em = (event_data or {}).get(p.symbol, {}).get("expected_move_pct")
        if em:
            loss = round(p.weight * em, 2)   # em already in %
            breached = loss > rc.max_event_loss_pct * 100
            r.event_risks.append(EventRisk(symbol=p.symbol, weight=p.weight,
                                           expected_move_pct=em, event_loss_pct=loss))
            if breached:
                r.breaches.append(Breach(layer="L6-事件", limit=f"≤{rc.max_event_loss_pct:.0%}",
                                         actual=f"{p.symbol} {loss:.1f}%", action="削仓使事件损失≤限"))

    # risk_state
    derisk = any(b.layer.startswith(("L4-回撤",)) for b in r.breaches)
    r.risk_state = "derisk" if derisk else ("caution" if r.breaches else "normal")
    r.notes = f"{len(r.breaches)} breach(es); beta {r.portfolio_beta}; 相关簇 {len(r.clusters)}"
    return r


def _prices(symbols: list[str]) -> dict[str, list[float]]:
    from ..data import sector_snapshot

    return sector_snapshot.fetch_prices(symbols)


def _perf_stub(net_liq: float):
    from ..schemas.memory import PerformanceRecord

    return PerformanceRecord(cycle_id="now", as_of=_now(), net_liquidation=net_liq)
