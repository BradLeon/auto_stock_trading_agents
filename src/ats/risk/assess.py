"""Compute the full 6-layer RiskReview from a live portfolio (deterministic)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import (
    Breach,
    CashEquivalent,
    Cluster,
    EventRisk,
    LayerExposure,
    OptionHolding,
    RiskReview,
    StressResult,
    SymbolLayer,
)
from . import correlation, stress

log = logging.getLogger("ats.risk.assess")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_sym(s: str) -> str:
    """Normalize a ticker for matching across IBKR/Yahoo/config spellings:
    'BRK B' / 'BRK.B' / 'brk-b' all collapse to 'BRK-B'."""
    return s.upper().replace(" ", "-").replace(".", "-")


def enrich_beta(portfolio: PortfolioSnapshot) -> None:
    """Fill Position.beta for held names (once, paced) — not done in get_portfolio."""
    from ..data import fundamentals

    for p in portfolio.positions:
        if (p.sec_type or "STK") == "OPT":     # options exempt from beta — don't fetch
            continue
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

    # --- options are EXEMPT from the equity 6-layer rules (stop-loss / drawdown / margin /
    # single-name / beta / chain-layer / stress / event). Option premium is non-linear and
    # needs its own greeks-based rules (TODO: 长期单独期权风控). For now they are surfaced
    # for visibility only; all per-name equity checks below iterate `equities`.
    equities = [p for p in portfolio.positions if (p.sec_type or "STK") != "OPT"]
    option_positions = [p for p in portfolio.positions if (p.sec_type or "STK") == "OPT"]
    option_mv_total = sum(p.market_value for p in option_positions)

    # --- cash-equivalent lens (unified haircut model) -------------------------
    # SGOV/SHV/BRK-B etc. are (partly) cash: cash_credit = mv×(1−haircut) counts as
    # effective cash; risk_weight = weight×haircut is the residual exposure that alone
    # occupies single-name / leverage / beta / concentration limits. Snapshot stays raw.
    # match config keys to actual holdings tolerant of symbol format (BRK-B / BRK B / BRK.B)
    ce_norm = {_norm_sym(k): v for k, v in (rc.cash_equivalents or {}).items()}
    ce_map: dict[str, float] = {p.symbol: ce_norm[_norm_sym(p.symbol)]
                                for p in equities if _norm_sym(p.symbol) in ce_norm}
    risk_wt: dict[str, float] = {}          # symbol -> risk weight (fraction of net_liq)
    cash_credit_total = 0.0
    r = RiskReview(
        as_of=_now(), net_liquidation=net_liq, gross_exposure=portfolio.gross_exposure,
        net_exposure=portfolio.net_exposure,
        cash_pct=(portfolio.cash / net_liq) if net_liq else 0.0)
    for p in option_positions:
        r.options.append(OptionHolding(
            symbol=p.symbol, sec_type=p.sec_type or "OPT", market_value=p.market_value,
            weight=p.weight, unrealized_pnl=p.unrealized_pnl))
    for p in equities:
        hc = ce_map.get(p.symbol)
        if hc is not None:
            risk_wt[p.symbol] = p.weight * hc
            cc = p.market_value * (1.0 - hc)
            cash_credit_total += cc
            r.cash_equivalents.append(CashEquivalent(
                symbol=p.symbol, market_value=p.market_value, haircut=hc, cash_credit=cc))
        else:
            risk_wt[p.symbol] = p.weight
    weights = risk_wt                        # cluster/correlation weighting uses risk weight
    effective_cash = portfolio.cash + cash_credit_total
    r.effective_cash_pct = (effective_cash / net_liq) if net_liq else 0.0
    if net_liq:
        # options exempt from margin: subtract their premium MV from effective gross too
        r.effective_leverage = round(
            (portfolio.gross_exposure - cash_credit_total - option_mv_total) / net_liq, 2)

    # L1 — single-name cap (on risk weight, so full-credit cash equivalents never breach)
    for p in equities:
        rw = risk_wt[p.symbol]
        if rw > rc.max_position_pct:
            r.breaches.append(Breach(layer="L1-单票", limit=f"≤{rc.max_position_pct:.0%}",
                                     actual=f"{p.symbol} {rw:.0%}", action="削到上限"))
    # L2 — leverage + cash floor (effective: net of cash-equivalent credit)
    eff_lev = r.effective_leverage if r.effective_leverage is not None else portfolio.leverage
    if eff_lev > rc.max_gross_leverage:
        r.breaches.append(Breach(layer="L2-杠杆", limit=f"≤{rc.max_gross_leverage}",
                                 actual=f"{eff_lev:.2f}x", action="缩买单/禁新买"))
    if r.effective_cash_pct < rc.cash_floor_pct:
        r.breaches.append(Breach(layer="L2-现金", limit=f"≥{rc.cash_floor_pct:.0%}",
                                 actual=f"有效现金 {r.effective_cash_pct:.0%}",
                                 action="禁新买（现金低于安全线）"))

    # L3 — portfolio beta (cash equivalents contribute only their risk-weight share)
    if any(p.beta is not None for p in equities):
        r.portfolio_beta = round(sum(risk_wt[p.symbol] * (p.beta or 1.0)
                                     for p in equities), 2)
        if r.portfolio_beta > rc.beta_cap:
            r.breaches.append(Breach(layer="L3-组合beta", limit=f"≤{rc.beta_cap}",
                                     actual=str(r.portfolio_beta), action="block 加 beta 的新买单"))

    # L1 — per-chain-layer concentration + explicit symbol→layer map (equities only)
    try:
        scfg = load_sector_config(sector)
        layer_w: dict[str, float] = {}
        labels = {ly.key: ly.label for ly in scfg.layers}
        caps = {ly.key: ly.weight_cap for ly in scfg.layers}
        for p in equities:
            lk = scfg.layer_of(p.symbol)
            r.symbol_layers.append(SymbolLayer(
                symbol=p.symbol, layer=lk or "", label=labels.get(lk, "未分层") if lk else "未分层",
                weight=round(risk_wt[p.symbol], 4), sec_type=p.sec_type or "STK"))
            if lk:
                layer_w[lk] = layer_w.get(lk, 0.0) + risk_wt[p.symbol]
        # options: record their underlying's layer too, but they don't occupy the cap
        for p in option_positions:
            lk = scfg.layer_of(p.symbol)
            r.symbol_layers.append(SymbolLayer(
                symbol=p.symbol, layer=lk or "", label=labels.get(lk, "未分层") if lk else "未分层",
                weight=round(p.weight, 4), sec_type=p.sec_type or "OPT"))
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

    # L1 — per-position stop-loss (equities only; skip cash equivalents: a SGOV tick
    # shouldn't force a trim; options exempt — premium is non-linear, needs own rules)
    for p in equities:
        if p.symbol in ce_map:
            continue
        cost = p.avg_cost * abs(p.qty)
        if cost > 0 and p.unrealized_pnl / cost <= -rc.stop_loss_pct:
            r.breaches.append(Breach(layer="L1-止损", limit=f"≥-{rc.stop_loss_pct:.0%}",
                                     actual=f"{p.symbol} {p.unrealized_pnl/cost:.0%}",
                                     action="强制 trim"))

    # L4 — drawdown / daily loss (scoped to THIS account — never mix paper vs live)
    hist = [h for h in get_store().performance_history(limit=250)
            if h.account_id == portfolio.account_id]
    if hist:
        from ..trader import analytics
        dd = analytics.max_drawdown_pct(hist + [_perf_stub(net_liq, portfolio.account_id)])
        r.drawdown_pct = dd
        if dd is not None and dd <= -rc.max_drawdown_pct * 100:
            r.breaches.append(Breach(layer="L4-回撤", limit=f"≥-{rc.max_drawdown_pct:.0%}",
                                     actual=f"{dd}%", action="de-risk：block 所有新买"))
    if net_liq:
        r.daily_pnl_pct = round(portfolio.daily_pnl / net_liq * 100, 2)
        if r.daily_pnl_pct <= -rc.daily_loss_limit_pct * 100:
            r.breaches.append(Breach(layer="L4-日亏", limit=f"≥-{rc.daily_loss_limit_pct:.0%}",
                                     actual=f"{r.daily_pnl_pct}%", action="停新仓"))

    # L5 — stress (options exempt: shock only the equity book)
    eq_portfolio = portfolio.model_copy(update={"positions": equities})
    r.stress = [StressResult(**s) for s in stress.run(
        eq_portfolio, market_shocks=rc.stress_market_shocks, top_cluster=top_cluster_members,
        ai_bubble_shock=rc.ai_bubble_cluster_shock, cash_equivalents=ce_map)]
    worst = min((s.loss_pct for s in r.stress), default=0.0)
    if worst <= -rc.max_stress_loss_pct * 100:
        r.breaches.append(Breach(layer="L5-压测", limit=f"≥-{rc.max_stress_loss_pct:.0%}",
                                 actual=f"{worst}%", action="block 加重受冲击敞口的新买单"))

    # L6 — earnings-event risk (equities only; option event risk is priced into premium)
    for p in equities:
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


def _perf_stub(net_liq: float, account_id: str = ""):
    from ..schemas.memory import PerformanceRecord

    return PerformanceRecord(cycle_id="now", as_of=_now(), account_id=account_id,
                             net_liquidation=net_liq)
