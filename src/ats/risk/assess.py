"""Compute the full 6-layer RiskReview from a live portfolio (deterministic).

Options are folded into every equity layer via the institutional paradigm: delta-adjusted
exposure (delta-notional joins single-name / chain-layer / beta / cluster) plus BSM full-
revaluation for stress. Margin is IBKR-authoritative when available, else a Reg-T estimate.
Greeks are IBKR-authoritative (set in broker/ibkr.py) when available, else a BSM fallback
computed in `enrich_options`. See risk/options_math.py for the quant core.
"""

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
    MarginSummary,
    OptionRisk,
    PortfolioGreeks,
    RiskReview,
    StressResult,
    SymbolLayer,
    UnderlyingExposure,
)
from . import correlation, options_math, stress

log = logging.getLogger("ats.risk.assess")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_sym(s: str) -> str:
    """Normalize a ticker for matching across IBKR/Yahoo/config spellings:
    'BRK B' / 'BRK.B' / 'brk-b' all collapse to 'BRK-B'."""
    return s.upper().replace(" ", "-").replace(".", "-")


def _is_opt(p) -> bool:
    return (p.sec_type or "STK") == "OPT"


def enrich_beta(portfolio: PortfolioSnapshot) -> None:
    """Fill Position.beta for held equities (once, paced) — not done in get_portfolio.
    Option underlyings get their beta in enrich_options (keyed on the underlying symbol)."""
    from ..data import fundamentals

    for p in portfolio.positions:
        if _is_opt(p):
            continue
        if p.beta is None:
            p.beta = fundamentals.fetch_light(p.symbol).get("beta")
            time.sleep(0.5)


def enrich_options(portfolio: PortfolioSnapshot) -> None:
    """Fill option greeks (BSM fallback when IBKR didn't supply them) + underlying beta on each
    OPT Position. Mirrors enrich_beta: fetches/derives data, mutates positions in place, never
    raises. Positions already carrying IBKR greeks (greeks_source=='ibkr') are left as-is."""
    from ..config import get_config
    from ..data import fundamentals

    opts = [p for p in portfolio.positions if _is_opt(p)]
    if not opts:
        return
    rc = get_config().app.risk
    r = rc.option_risk_free_rate

    # Batch spot for underlyings IBKR didn't already price (undPrice).
    need_spot = sorted({(p.underlying or p.symbol) for p in opts if not p.underlying_price})
    spot_map: dict[str, float] = {}
    if need_spot:
        try:
            from ..data import sector_snapshot
            prices = sector_snapshot.fetch_prices(need_spot)
            spot_map = {s: (v[-1] if v else None) for s, v in prices.items()}
        except Exception as exc:  # noqa: BLE001
            log.warning("option spot fetch skipped: %s", exc)

    for p in opts:
        under = p.underlying or p.symbol
        S = p.underlying_price or spot_map.get(under)
        # underlying beta (for L3) — key on the underlying, cache on the option position
        if p.beta is None:
            try:
                p.beta = fundamentals.fetch_light(under).get("beta")
                time.sleep(0.5)
            except Exception as exc:  # noqa: BLE001
                log.warning("option beta skipped for %s: %s", under, exc)
        # IBKR already gave greeks → only backfill spot, keep them
        if p.greeks_source == "ibkr" and p.delta is not None:
            if p.underlying_price is None and S:
                p.underlying_price = S
            continue
        # BSM fallback: need S, K, T
        is_call = (p.right or "C").upper().startswith("C")
        T = options_math.years_to_expiry(p.expiry or "")
        K = p.strike
        if not S or not K or T <= 0:
            continue                       # unpriceable → OptionRisk.priced=False later
        premium = p.market_price           # option price per share
        sigma = options_math._implied_vol(premium, S, K, T, r, is_call)
        if not sigma:                      # fall back to ATM IV from the chain
            try:
                from ..data import options as opt_data
                atm = opt_data.fetch(under).get("atm_iv")
                sigma = (atm / 100.0) if atm else None
            except Exception as exc:  # noqa: BLE001
                log.warning("ATM IV fallback skipped for %s: %s", under, exc)
        if not sigma:
            continue
        g = options_math.greeks(S, K, T, r, sigma, is_call)
        p.delta, p.gamma, p.vega, p.theta = g["delta"], g["gamma"], g["vega"], g["theta"]
        p.iv = sigma
        p.underlying_price = S
        p.greeks_source = "bsm"


def _classify_strategy(right: str, qty: float, shares_held: float, mult: float) -> str:
    """Map a single-leg option to one of the 4 supported strategies (+covered/naked split)."""
    is_call = (right or "C").upper().startswith("C")
    if qty < 0:                                    # short
        if is_call:
            return "covered_call" if shares_held >= mult * abs(qty) else "naked_call"
        return "sell_put"
    return "buy_call" if is_call else "buy_put"    # long


def _build_option_risks(option_positions, equities, rc) -> list[OptionRisk]:
    """Construct OptionRisk (strategy, greeks, delta-notional, Reg-T margin estimate) per OPT."""
    shares_by_under: dict[str, float] = {}
    for p in equities:
        if p.qty > 0:
            shares_by_under[_norm_sym(p.symbol)] = shares_by_under.get(_norm_sym(p.symbol), 0.0) + p.qty
    out: list[OptionRisk] = []
    for p in option_positions:
        under = p.underlying or p.symbol
        mult = p.multiplier or 100.0
        shares_held = shares_by_under.get(_norm_sym(under), 0.0)
        strat = _classify_strategy(p.right or "C", p.qty, shares_held, mult)
        S = p.underlying_price
        priced = p.delta is not None and S is not None and bool(p.strike)
        dn = (p.delta * p.qty * mult * S) if priced else 0.0
        margin = None
        if p.strike and S is not None:
            margin = options_math.regt_margin(strat, S, p.strike, p.market_price or 0.0,
                                              abs(p.qty), mult)
        out.append(OptionRisk(
            symbol=p.symbol, underlying=under, sec_type=p.sec_type or "OPT",
            right=(p.right or ""), strike=p.strike or 0.0, expiry=p.expiry or "",
            qty=p.qty, multiplier=mult, strategy=strat, spot=S, iv=p.iv,
            delta=p.delta, gamma=p.gamma, vega=p.vega, theta=p.theta,
            delta_notional=dn, margin=margin, premium_mv=p.market_value,
            unrealized_pnl=p.unrealized_pnl, priced=priced, greeks_source=p.greeks_source))
    return out


def assess(portfolio: PortfolioSnapshot, *, sector: str = "ai_hardware",
           event_data: dict[str, dict] | None = None) -> RiskReview:
    """event_data: {symbol: {expected_move_pct, ...}} for held names near earnings."""
    from ..config import get_config, load_sector_config
    from ..memory import get_store

    rc = get_config().app.risk
    net_liq = portfolio.net_liquidation

    # Options are now FOLDED INTO the 6 layers via delta-notional + BSM reval (no longer exempt).
    equities = [p for p in portfolio.positions if not _is_opt(p)]
    option_positions = [p for p in portfolio.positions if _is_opt(p)]
    option_mv_total = sum(p.market_value for p in option_positions)

    # --- cash-equivalent lens (unified haircut model) -------------------------
    ce_norm = {_norm_sym(k): v for k, v in (rc.cash_equivalents or {}).items()}
    ce_map: dict[str, float] = {p.symbol: ce_norm[_norm_sym(p.symbol)]
                                for p in equities if _norm_sym(p.symbol) in ce_norm}
    risk_wt: dict[str, float] = {}          # equity symbol -> risk weight (fraction of net_liq)
    cash_credit_total = 0.0
    r = RiskReview(
        as_of=_now(), net_liquidation=net_liq, gross_exposure=portfolio.gross_exposure,
        net_exposure=portfolio.net_exposure,
        cash_pct=(portfolio.cash / net_liq) if net_liq else 0.0)

    # --- option risk decomposition (greeks / strategy / delta-notional / margin) ---
    r.option_risks = _build_option_risks(option_positions, equities, rc)

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
    effective_cash = portfolio.cash + cash_credit_total
    r.effective_cash_pct = (effective_cash / net_liq) if net_liq else 0.0
    if net_liq:
        # option premium MV is excluded from equity gross leverage (economic leverage is
        # captured separately by delta_adj_leverage in portfolio greeks)
        r.effective_leverage = round(
            (portfolio.gross_exposure - cash_credit_total - option_mv_total) / net_liq, 2)

    # --- per-underlying NET delta exposure (equity risk weight + option delta-notional) ---
    beta_map: dict[str, float | None] = {}
    equity_w: dict[str, float] = {}
    option_w: dict[str, float] = {}
    for p in equities:
        s = _norm_sym(p.symbol)
        equity_w[s] = equity_w.get(s, 0.0) + risk_wt[p.symbol]
        if p.beta is not None:
            beta_map[s] = p.beta
    for o in r.option_risks:
        s = _norm_sym(o.underlying)
        if net_liq:
            option_w[s] = option_w.get(s, 0.0) + o.delta_notional / net_liq
    for p in option_positions:            # underlying betas for option-only names
        s = _norm_sym(p.underlying or p.symbol)
        if s not in beta_map and p.beta is not None:
            beta_map[s] = p.beta
    # display symbol per normalized key (prefer the equity/underlying spelling)
    disp: dict[str, str] = {}
    for p in equities:
        disp.setdefault(_norm_sym(p.symbol), p.symbol)
    for o in r.option_risks:
        disp.setdefault(_norm_sym(o.underlying), o.underlying)

    net_w: dict[str, float] = {}
    for s in set(equity_w) | set(option_w):
        net_w[s] = equity_w.get(s, 0.0) + option_w.get(s, 0.0)

    # L1 — single-name cap on NET delta weight (option delta-notional now counts; hedges net off)
    for s, w in net_w.items():
        if abs(w) > rc.max_position_pct:
            r.breaches.append(Breach(layer="L1-单票", limit=f"≤{rc.max_position_pct:.0%}",
                                     actual=f"{disp[s]} 净Δ {w:.0%}", action="削到上限"))

    # L2 — leverage + cash floor (effective: net of cash-equivalent credit)
    eff_lev = r.effective_leverage if r.effective_leverage is not None else portfolio.leverage
    if eff_lev > rc.max_gross_leverage:
        r.breaches.append(Breach(layer="L2-杠杆", limit=f"≤{rc.max_gross_leverage}",
                                 actual=f"{eff_lev:.2f}x", action="缩买单/禁新买"))
    if r.effective_cash_pct < rc.cash_floor_pct:
        r.breaches.append(Breach(layer="L2-现金", limit=f"≥{rc.cash_floor_pct:.0%}",
                                 actual=f"有效现金 {r.effective_cash_pct:.0%}",
                                 action="禁新买（现金低于安全线）"))

    # L2 — margin (IBKR authoritative; Reg-T estimate fallback → caution, never hard-block)
    _assess_margin(r, portfolio, equities, rc, net_liq)

    # L3 — portfolio beta over NET delta weight (options摊 beta via delta-notional)
    if beta_map:
        r.portfolio_beta = round(sum(net_w[s] * (beta_map.get(s) or 1.0) for s in net_w), 2)
        if r.portfolio_beta > rc.beta_cap:
            r.breaches.append(Breach(layer="L3-组合beta", limit=f"≤{rc.beta_cap}",
                                     actual=str(r.portfolio_beta), action="block 加 beta 的新买单"))

    # L1 — per-chain-layer concentration + explicit symbol→layer map (equities + option Δ名义)
    top_cluster_members: list[str] | None = None
    try:
        scfg = load_sector_config(sector)
        labels = {ly.key: ly.label for ly in scfg.layers}
        caps = {ly.key: ly.weight_cap for ly in scfg.layers}
        layer_w: dict[str, float] = {}
        for p in equities:
            lk = scfg.layer_of(p.symbol)
            r.symbol_layers.append(SymbolLayer(
                symbol=p.symbol, layer=lk or "", label=labels.get(lk, "未分层") if lk else "未分层",
                weight=round(risk_wt[p.symbol], 4), sec_type=p.sec_type or "STK"))
        for o in r.option_risks:
            lk = scfg.layer_of(o.underlying)
            dw = (o.delta_notional / net_liq) if net_liq else 0.0
            r.symbol_layers.append(SymbolLayer(
                symbol=f"{o.underlying}[{o.strategy or o.right}]", layer=lk or "",
                label=labels.get(lk, "未分层") if lk else "未分层",
                weight=round(dw, 4), sec_type="OPT"))
        # per-underlying NET delta weight drives the layer cap (hedges net off within a layer)
        for s, w in net_w.items():
            lk = scfg.layer_of(disp[s])
            if lk:
                layer_w[lk] = layer_w.get(lk, 0.0) + w
            r.underlying_exposures.append(UnderlyingExposure(
                symbol=disp[s], equity_weight=round(equity_w.get(s, 0.0), 4),
                option_delta_weight=round(option_w.get(s, 0.0), 4),
                net_delta_weight=round(w, 4), layer=lk or ""))
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

    # L3 — correlation clusters (weight = |net delta weight|; option underlyings included)
    cluster_weights = {disp[s]: abs(w) for s, w in net_w.items() if abs(w) > 0}
    if len(cluster_weights) >= 2:
        prices = _prices(list(cluster_weights))
        for c in correlation.clusters(cluster_weights, prices, rc.cluster_corr_threshold):
            if len(c["members"]) > 1:
                r.clusters.append(Cluster(**c))
        if r.clusters:
            top = r.clusters[0]
            top_cluster_members = top.members
            if top.weight > rc.cluster_weight_cap:
                r.breaches.append(Breach(layer="L3-相关簇", limit=f"≤{rc.cluster_weight_cap:.0%}",
                                         actual=f"{top.weight:.0%} ({','.join(top.members[:5])}…)",
                                         action="block 入该相关簇的新买单"))

    # L1 — per-position stop-loss: equities (unchanged) + strategy-aware options (caution)
    for p in equities:
        if p.symbol in ce_map:
            continue
        cost = p.avg_cost * abs(p.qty)
        if cost > 0 and p.unrealized_pnl / cost <= -rc.stop_loss_pct:
            r.breaches.append(Breach(layer="L1-止损", limit=f"≥-{rc.stop_loss_pct:.0%}",
                                     actual=f"{p.symbol} {p.unrealized_pnl/cost:.0%}",
                                     action="强制 trim"))
    _assess_option_stops(r, option_positions, rc)

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

    # L5 — stress: equity beta shock + option BSM full-revaluation with paired vol shocks
    eq_portfolio = portfolio.model_copy(update={"positions": equities})
    r.stress = [StressResult(**s) for s in stress.run(
        eq_portfolio, market_shocks=rc.stress_market_shocks, top_cluster=top_cluster_members,
        ai_bubble_shock=rc.ai_bubble_cluster_shock, cash_equivalents=ce_map,
        options=r.option_risks, vol_shocks=rc.stress_vol_shocks,
        ai_bubble_vol_shock=rc.ai_bubble_vol_shock, r=rc.option_risk_free_rate)]
    worst = min((s.loss_pct for s in r.stress), default=0.0)
    if worst <= -rc.max_stress_loss_pct * 100:
        r.breaches.append(Breach(layer="L5-压测", limit=f"≥-{rc.max_stress_loss_pct:.0%}",
                                 actual=f"{worst}%", action="block 加重受冲击敞口的新买单"))

    # L6 — earnings-event risk: equities (linear) + options (BSM ±EM reval, worse side)
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
    _assess_option_events(r, event_data, rc, net_liq)

    # --- portfolio greeks aggregate + caution-level option limits (disclose, don't hard-block) ---
    _assess_portfolio_greeks(r, net_w, net_liq, rc)

    # risk_state
    derisk = any(b.layer.startswith(("L4-回撤",)) for b in r.breaches)
    r.risk_state = "derisk" if derisk else ("caution" if (r.breaches or r.cautions) else "normal")
    r.notes = (f"{len(r.breaches)} breach(es), {len(r.cautions)} caution(s); "
               f"beta {r.portfolio_beta}; 相关簇 {len(r.clusters)}; 期权 {len(r.option_risks)}")
    return r


def _assess_margin(r: RiskReview, portfolio, equities, rc, net_liq: float) -> None:
    """L2 margin: prefer IBKR authoritative figures; else sum a Reg-T estimate. IBKR breaches
    hard-block; estimated breaches degrade to caution (annotated 估算) to avoid false blocks."""
    if not net_liq:
        return
    if portfolio.margin_source == "ibkr" and portfolio.init_margin:
        m = MarginSummary(
            init_margin=portfolio.init_margin, maint_margin=portfolio.maint_margin,
            excess_liquidity=portfolio.excess_liquidity, buying_power=portfolio.buying_power,
            margin_util=round(portfolio.init_margin / net_liq, 4),
            excess_liq_pct=(round(portfolio.excess_liquidity / net_liq, 4)
                            if portfolio.excess_liquidity is not None else None),
            source="ibkr")
    else:
        # Reg-T estimate: long equity 50% of MV + per-option Reg-T; excess ≈ net_liq − init.
        eq_init = sum(0.5 * abs(p.market_value) for p in equities)
        opt_init = sum((o.margin or 0.0) for o in r.option_risks)
        init_est = eq_init + opt_init
        excess_est = net_liq - init_est
        m = MarginSummary(
            init_margin=round(init_est, 0), maint_margin=None,
            excess_liquidity=round(excess_est, 0),
            margin_util=round(init_est / net_liq, 4),
            excess_liq_pct=round(excess_est / net_liq, 4), source="regt_est")
    r.margin = m
    hard = m.source == "ibkr"
    bucket = r.breaches if hard else r.cautions
    tag = "" if hard else "（估算）"
    if m.margin_util is not None and m.margin_util > rc.max_margin_util_pct:
        bucket.append(Breach(layer="L2-保证金利用率", limit=f"≤{rc.max_margin_util_pct:.0%}",
                             actual=f"{m.margin_util:.0%}{tag}",
                             action="缩仓/禁新开保证金仓" if hard else "关注：保证金利用率偏高"))
    if m.excess_liq_pct is not None and m.excess_liq_pct < rc.min_excess_liquidity_pct:
        bucket.append(Breach(layer="L2-剩余流动性", limit=f"≥{rc.min_excess_liquidity_pct:.0%}",
                             actual=f"{m.excess_liq_pct:.0%}{tag}",
                             action="补现金/减仓" if hard else "关注：保证金垫偏薄"))


def _assess_option_stops(r: RiskReview, option_positions, rc) -> None:
    """L1 stop-loss, strategy-aware (caution): long option by |loss|/premium_paid ≥ stop;
    short option by loss/premium_received ≥ short_option_loss_mult."""
    by_sym = {p.symbol: p for p in option_positions}
    for o in r.option_risks:
        p = by_sym.get(o.symbol)
        if p is None:
            continue
        basis = abs(p.avg_cost) * abs(p.qty)     # cost (long) / credit (short), $
        if basis <= 0:
            continue
        if p.qty > 0:                            # long option
            ratio = p.unrealized_pnl / basis
            if ratio <= -rc.stop_loss_pct:
                r.cautions.append(Breach(layer="L1-期权止损", limit=f"≥-{rc.stop_loss_pct:.0%}",
                                         actual=f"{o.underlying} {o.strategy} {ratio:.0%}",
                                         action="关注：长期权浮亏触及止损"))
        else:                                    # short option
            loss_mult = (-p.unrealized_pnl) / basis
            if loss_mult >= rc.short_option_loss_mult:
                r.cautions.append(Breach(layer="L1-期权止损",
                                         limit=f"浮亏<{rc.short_option_loss_mult:.0f}×收权利金",
                                         actual=f"{o.underlying} {o.strategy} {loss_mult:.1f}×",
                                         action="关注：短期权浮亏放大，考虑平仓/展期"))


def _assess_option_events(r: RiskReview, event_data, rc, net_liq: float) -> None:
    """L6 option event risk: reprice ±expected_move via BSM, take the worse side as event loss."""
    if not net_liq:
        return
    for o in r.option_risks:
        if not o.priced or o.spot is None or o.iv is None or not o.strike:
            continue
        em = (event_data or {}).get(o.underlying, {}).get("expected_move_pct")
        if not em:
            continue
        T = options_math.years_to_expiry(o.expiry) if o.expiry else 0.0
        is_call = (o.right or "C").upper().startswith("C")
        v0 = options_math.reprice(o.spot, o.strike, T, rc.option_risk_free_rate, o.iv, is_call)
        worst = 0.0
        for sign in (1.0, -1.0):
            v1 = options_math.reprice(o.spot * (1 + sign * em / 100.0), o.strike, T,
                                     rc.option_risk_free_rate, o.iv, is_call)
            pnl = (v1 - v0) * o.qty * o.multiplier
            worst = min(worst, pnl)
        loss_pct = round(-worst / net_liq * 100, 2)      # positive % NAV
        r.event_risks.append(EventRisk(symbol=f"{o.underlying}[{o.strategy or o.right}]",
                                       weight=round(abs(o.delta_notional) / net_liq, 4),
                                       expected_move_pct=em, event_loss_pct=loss_pct))
        if loss_pct > rc.max_event_loss_pct * 100:
            r.breaches.append(Breach(layer="L6-事件", limit=f"≤{rc.max_event_loss_pct:.0%}",
                                     actual=f"{o.underlying}({o.strategy}) {loss_pct:.1f}%",
                                     action="削期权仓使事件损失≤限"))


def _assess_portfolio_greeks(r: RiskReview, net_w: dict, net_liq: float, rc) -> None:
    """Aggregate option greeks + delta-adjusted leverage; caution if |净vega|/NAV over limit."""
    net_dn = sum(o.delta_notional for o in r.option_risks)
    net_gamma = sum((o.gamma or 0.0) * o.qty * o.multiplier for o in r.option_risks)
    net_vega = sum((o.vega or 0.0) * o.qty * o.multiplier for o in r.option_risks)
    net_theta = sum((o.theta or 0.0) * o.qty * o.multiplier for o in r.option_risks)
    dal = (sum(abs(w) for w in net_w.values())) if net_w else 0.0
    r.portfolio_greeks = PortfolioGreeks(
        net_delta_notional=round(net_dn, 0), net_gamma=round(net_gamma, 2),
        net_vega=round(net_vega, 2), net_theta=round(net_theta, 2),
        delta_adj_leverage=round(dal, 2))
    if net_liq and rc.max_net_vega_pct and abs(net_vega) / net_liq > rc.max_net_vega_pct:
        r.cautions.append(Breach(layer="L-期权净vega", limit=f"|净vega|/NAV≤{rc.max_net_vega_pct:.0%}",
                                 actual=f"${net_vega:,.0f}/1%vol ({abs(net_vega)/net_liq:.1%}NAV)",
                                 action="关注：波动率敞口偏大"))


def _prices(symbols: list[str]) -> dict[str, list[float]]:
    from ..data import sector_snapshot

    return sector_snapshot.fetch_prices(symbols)


def _perf_stub(net_liq: float, account_id: str = ""):
    from ..schemas.memory import PerformanceRecord

    return PerformanceRecord(cycle_id="now", as_of=_now(), account_id=account_id,
                             net_liquidation=net_liq)
