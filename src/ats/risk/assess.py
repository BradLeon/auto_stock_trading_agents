"""Compute the full 6-layer RiskReview from a live portfolio (deterministic).

Options are folded into every equity layer via the institutional paradigm: delta-adjusted
exposure (delta-notional joins single-name / chain-layer / beta / cluster) plus BSM full-
revaluation for stress. Margin is IBKR-authoritative when available, else a Reg-T estimate.
Greeks are IBKR-authoritative (set in broker/ibkr.py) when available, else a BSM fallback
computed in `enrich_options`. See risk/options_math.py for the quant core.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone

from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import (
    Breach,
    AssignmentRisk,
    CashEquivalent,
    Cluster,
    EconomicExposure,
    ExpiryFundingBucket,
    EventRisk,
    LayerExposure,
    MarginSummary,
    OptionRisk,
    OptionSurvivalSummary,
    PortfolioGreeks,
    RiskReview,
    RiskDirective,
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
    from ..config import load_instrument_risk_registry
    from ..data import fundamentals

    registry = load_instrument_risk_registry()
    for p in portfolio.positions:
        if _is_opt(p):
            continue
        if p.beta is None:
            risk_symbol = registry.resolve(p.symbol).risk_symbol
            p.beta = fundamentals.fetch_light(risk_symbol).get("beta")
            time.sleep(0.5)


def enrich_options(portfolio: PortfolioSnapshot) -> None:
    """Fill option greeks (BSM fallback when IBKR didn't supply them) + underlying beta on each
    OPT Position. Mirrors enrich_beta: fetches/derives data, mutates positions in place, never
    raises. Positions already carrying IBKR greeks (greeks_source=='ibkr') are left as-is."""
    from ..config import get_config, load_instrument_risk_registry
    from ..data import fundamentals

    opts = [p for p in portfolio.positions if _is_opt(p)]
    if not opts:
        return
    rc = get_config().app.risk
    registry = load_instrument_risk_registry()
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
                p.beta = fundamentals.fetch_light(registry.resolve(under).risk_symbol).get("beta")
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


def _build_option_risks(option_positions, equities, rc, registry) -> list[OptionRisk]:
    """Construct OptionRisk (strategy, greeks, delta-notional, Reg-T margin estimate) per OPT."""
    shares_by_under: dict[str, float] = {}
    for p in equities:
        if p.qty > 0:
            shares_by_under[_norm_sym(p.symbol)] = shares_by_under.get(_norm_sym(p.symbol), 0.0) + p.qty
    out: list[OptionRisk] = []
    for p in option_positions:
        under = p.underlying or p.symbol
        meta = registry.resolve(under)
        mult = p.multiplier or 100.0
        shares_held = shares_by_under.get(_norm_sym(under), 0.0)
        strat = _classify_strategy(p.right or "C", p.qty, shares_held, mult)
        S = p.underlying_price
        priced = p.delta is not None and S is not None and bool(p.strike)
        dn = (p.delta * p.qty * mult * S * p.fx_rate_to_base) if priced else 0.0
        margin = None
        if p.strike and S is not None:
            margin = (options_math.regt_margin(strat, S, p.strike, p.market_price or 0.0,
                                               abs(p.qty), mult) * p.fx_rate_to_base)
        out.append(OptionRisk(
            symbol=p.symbol, underlying=under, sec_type=p.sec_type or "OPT",
            right=(p.right or ""), strike=p.strike or 0.0, expiry=p.expiry or "",
            qty=p.qty, multiplier=mult, strategy=strat, spot=S, iv=p.iv,
            delta=p.delta, gamma=p.gamma, vega=p.vega, theta=p.theta,
            delta_notional=dn, margin=margin, premium_mv=p.market_value,
            unrealized_pnl=p.unrealized_pnl, priced=priced, greeks_source=p.greeks_source,
            economic_entity=meta.economic_entity, risk_symbol=meta.risk_symbol,
            exposure_multiplier=meta.exposure_multiplier, fx_rate_to_base=p.fx_rate_to_base))
    return out


def _days_to_expiry(expiry: str) -> int:
    try:
        ed = datetime.strptime(expiry.replace("-", "")[:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        return 0
    return max((ed - datetime.now(timezone.utc).date()).days, 0)


def _assess_option_survival(r: RiskReview, portfolio, rc, policy, net_liq: float) -> None:
    """Funding survival for short puts, separated from delta/BSM mark-to-market risk."""
    assignments: list[AssignmentRisk] = []
    by_expiry: dict[str, float] = {}
    for o in r.option_risks:
        if o.strategy != "sell_put" or o.qty >= 0:
            continue
        full = o.strike * abs(o.qty) * o.multiplier * o.fx_rate_to_base
        probability = None
        source = "unknown"
        T = options_math.years_to_expiry(o.expiry) if o.expiry else 0.0
        if o.spot and o.strike and o.iv is not None:
            probability = options_math.itm_probability(
                o.spot, o.strike, T, rc.option_risk_free_rate, o.iv, is_call=False)
            source = "bsm_N(-d2)"
        elif o.delta is not None:
            probability = min(max(abs(o.delta), 0.0), 1.0)
            source = "abs_delta_fallback"
        expected = full * probability if probability is not None else full
        assignments.append(AssignmentRisk(
            symbol=o.symbol, underlying=o.underlying, expiry=o.expiry,
            days_to_expiry=_days_to_expiry(o.expiry), full_assignment_notional=round(full, 2),
            assignment_probability=probability, probability_source=source,
            probability_weighted_notional=round(expected, 2)))
        by_expiry[o.expiry] = by_expiry.get(o.expiry, 0.0) + full

    effective_cash = r.effective_cash_pct * net_liq
    liquidity_candidates = [effective_cash]
    if portfolio.excess_liquidity is not None:
        liquidity_candidates.append(portfolio.excess_liquidity)
    available = max(liquidity_candidates, default=0.0)
    total_full = sum(a.full_assignment_notional for a in assignments)
    total_expected = sum(a.probability_weighted_notional for a in assignments)
    peak_expiry = max(by_expiry.values(), default=0.0)
    unknown = any(a.assignment_probability is None for a in assignments)

    horizons = sorted(set(policy.expiry_horizons_days))
    max_days = max((a.days_to_expiry for a in assignments), default=0)
    if max_days and (not horizons or max_days > horizons[-1]):
        horizons.append(max_days)
    buckets: list[ExpiryFundingBucket] = []
    for horizon in horizons:
        active = [a for a in assignments if a.days_to_expiry <= horizon]
        full = sum(a.full_assignment_notional for a in active)
        expected = sum(a.probability_weighted_notional for a in active)
        variance = sum(
            a.full_assignment_notional ** 2 * (a.assignment_probability or 0.0)
            * (1.0 - (a.assignment_probability or 0.0))
            for a in active if a.assignment_probability is not None)
        p99 = full if any(a.assignment_probability is None for a in active) else min(
            full, expected + policy.p99_z_score * math.sqrt(variance))
        buckets.append(ExpiryFundingBucket(
            label=f"≤{horizon}天", through_days=horizon,
            expiries=sorted({a.expiry for a in active}), full_notional=round(full, 2),
            expected_notional=round(expected, 2), p99_notional=round(p99, 2)))
    p99_total = max((b.p99_notional for b in buckets), default=0.0)
    summary = OptionSurvivalSummary(
        assignments=assignments, expiry_buckets=buckets,
        total_full_assignment_notional=round(total_full, 2),
        probability_weighted_notional=round(total_expected, 2),
        p99_assignment_notional=round(p99_total, 2),
        peak_expiry_full_notional=round(peak_expiry, 2), available_liquidity=round(available, 2),
        p99_funding_gap=round(max(p99_total - available, 0.0), 2),
        has_unknown_probability=unknown,
        notes="到期ITM概率采用BSM N(-d2)；美式期权可能提前指派。P99按仓位级Bernoulli近似。")
    r.option_survival = summary
    if not net_liq or not assignments:
        return

    checks = [
        ("L2-期权全额指派", total_full / net_liq,
         policy.max_full_assignment_nav_pct, "降低灾难性全指派名义"),
        ("L2-期权概率占用", total_expected / net_liq,
         policy.max_probability_weighted_nav_pct, "降低概率加权资金占用"),
        ("L2-期权P99指派", p99_total / net_liq,
         policy.max_p99_assignment_nav_pct, "降低P99联合指派资金"),
        ("L2-期权单日到期峰值", peak_expiry / net_liq,
         policy.max_peak_expiry_nav_pct, "分散同到期日或降低手数"),
    ]
    for layer, actual, limit, action in checks:
        if actual > limit:
            r.breaches.append(Breach(layer=layer, limit=f"≤{limit:.0%} NAV",
                                     actual=f"{actual:.0%} NAV", action=action))
    if summary.p99_funding_gap > 0:
        r.breaches.append(Breach(
            layer="L2-期权生存流动性", limit="P99指派≤可用流动性",
            actual=f"缺口 ${summary.p99_funding_gap:,.0f}", action="补流动性/平仓/错开到期"))
    if unknown:
        r.breaches.append(Breach(
            layer="L2-期权指派数据", limit="短put概率必须可估",
            actual="存在未知指派概率", action="DATA_INVALID：补齐spot/IV/delta"))


def _build_directive(r: RiskReview, rc, policy, net_liq: float) -> RiskDirective:
    worst_stress = min((s.loss_pct for s in r.stress), default=0.0)
    max_entity = max((abs(e.net_delta_weight) for e in r.economic_exposures), default=0.0)
    budgets = {
        "single_entity_delta": round(max(rc.max_position_pct - max_entity, 0.0), 4),
        "portfolio_beta": round(max(rc.beta_cap - (r.portfolio_beta or 0.0), 0.0), 4),
        "stress_loss": round(max(rc.max_stress_loss_pct - abs(min(worst_stress, 0.0)) / 100, 0.0), 4),
        "cash_above_floor": round(max(r.effective_cash_pct - rc.cash_floor_pct, 0.0), 4),
    }
    if r.option_survival and net_liq:
        budgets["p99_assignment"] = round(max(
            policy.option_survival.max_p99_assignment_nav_pct
            - r.option_survival.p99_assignment_notional / net_liq, 0.0), 4)

    blocked_entities = sorted(
        e.economic_entity for e in r.economic_exposures
        if abs(e.net_delta_weight) > rc.max_position_pct)
    # 组越限 → 组内每一层都进 blocked_layers。组上限存在的全部意义就是「两半都没越限
    # 但合计越了」，那时逐层看不出任何问题，只封组是封不住新买单的（下游按层键判断）。
    blocked_layers = sorted(
        {le.key for le in r.chain_layers if le.breached}
        | {m for g in r.chain_layer_groups if g.breached for m in g.members})
    data_invalid = net_liq <= 0 or bool(
        r.option_survival and r.option_survival.has_unknown_probability)
    emergency = any(b.layer.startswith(("L4-回撤", "L4-日亏", "L2-期权全额指派"))
                    for b in r.breaches)
    if r.margin and r.margin.excess_liquidity is not None and r.margin.excess_liquidity <= 0:
        emergency = True

    if data_invalid:
        state = "DATA_INVALID"
        actions = ["reduce", "cancel_pending"]
    elif emergency:
        state = "EMERGENCY"
        actions = ["reduce", "close_short_options", "raise_liquidity"]
    elif r.breaches:
        state = "REPAIR_ONLY"
        actions = ["reduce", "hedge_if_verified_improving"]
    else:
        upper_utils = [
            max_entity / rc.max_position_pct if rc.max_position_pct else 0.0,
            (r.portfolio_beta or 0.0) / rc.beta_cap if rc.beta_cap else 0.0,
            abs(min(worst_stress, 0.0)) / 100 / rc.max_stress_loss_pct
            if rc.max_stress_loss_pct else 0.0,
        ]
        near_limit = max(upper_utils, default=0.0) >= 1.0 - policy.directive.limited_headroom_pct
        state = "LIMITED" if (r.cautions or near_limit) else "NORMAL"
        actions = ["increase_within_budget", "hedge", "reduce"] if state == "LIMITED" else [
            "increase_within_budget", "hedge", "reduce"]
    return RiskDirective(
        state=state, can_increase_risk=state in ("NORMAL", "LIMITED"),
        allowed_actions=actions, blocked_entities=blocked_entities,
        blocked_layers=blocked_layers, risk_budget_remaining=budgets,
        required_repairs=[f"{b.layer}: {b.actual}→{b.limit}" for b in r.breaches],
        reasons=[b.action for b in r.breaches] + [c.action for c in r.cautions])


def assess(portfolio: PortfolioSnapshot, *, sector: str = "ai_hardware",
           event_data: dict[str, dict] | None = None) -> RiskReview:
    """event_data: {symbol: {expected_move_pct, ...}} for held names near earnings."""
    from ..config import (
        get_config,
        load_instrument_risk_registry,
        load_risk_policy,
        load_sector_config,
    )
    from ..memory import get_store

    rc = get_config().app.risk
    registry = load_instrument_risk_registry()
    risk_policy = load_risk_policy()
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
    r.option_risks = _build_option_risks(option_positions, equities, rc, registry)

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

    _assess_option_survival(
        r, portfolio, rc, risk_policy.option_survival, net_liq)

    # --- per-underlying NET delta exposure (equity risk weight + option delta-notional) ---
    beta_map: dict[str, float | None] = {}
    equity_w: dict[str, float] = {}
    capital_w: dict[str, float] = {}
    option_w: dict[str, float] = {}
    risk_symbols: dict[str, str] = {}
    layer_symbols: dict[str, str] = {}
    members: dict[str, set[str]] = {}
    disp: dict[str, str] = {}
    for p in equities:
        meta = registry.resolve(p.symbol)
        s = meta.economic_entity
        capital_w[s] = capital_w.get(s, 0.0) + risk_wt[p.symbol]
        equity_w[s] = equity_w.get(s, 0.0) + risk_wt[p.symbol] * meta.exposure_multiplier
        risk_symbols[s] = meta.risk_symbol
        layer_symbols[s] = meta.layer_symbol
        members.setdefault(s, set()).add(p.symbol)
        disp[s] = meta.label
        if p.beta is not None:
            beta_map[s] = p.beta
    for o in r.option_risks:
        s = o.economic_entity or _norm_sym(o.underlying)
        if net_liq:
            option_w[s] = (option_w.get(s, 0.0)
                           + o.delta_notional / net_liq * o.exposure_multiplier)
        meta = registry.resolve(o.underlying)
        risk_symbols[s] = meta.risk_symbol
        layer_symbols[s] = meta.layer_symbol
        members.setdefault(s, set()).add(o.symbol)
        disp[s] = meta.label
    for p in option_positions:            # underlying betas for option-only names
        meta = registry.resolve(p.underlying or p.symbol)
        s = meta.economic_entity
        if s not in beta_map and p.beta is not None:
            beta_map[s] = p.beta

    net_w: dict[str, float] = {}
    for s in set(equity_w) | set(option_w):
        net_w[s] = equity_w.get(s, 0.0) + option_w.get(s, 0.0)

    economic_by_entity: dict[str, EconomicExposure] = {}
    underlying_by_entity: dict[str, UnderlyingExposure] = {}
    for s, w in net_w.items():
        beta = beta_map.get(s)
        ue = UnderlyingExposure(
            symbol=disp[s], equity_weight=round(equity_w.get(s, 0.0), 4),
            option_delta_weight=round(option_w.get(s, 0.0), 4),
            net_delta_weight=round(w, 4))
        ee = EconomicExposure(
            economic_entity=s, label=disp[s], risk_symbol=risk_symbols[s],
            members=sorted(members.get(s, set())), capital_weight=round(capital_w.get(s, 0.0), 4),
            equity_delta_weight=round(equity_w.get(s, 0.0), 4),
            option_delta_weight=round(option_w.get(s, 0.0), 4), net_delta_weight=round(w, 4),
            beta_contribution=round(w * (beta if beta is not None else 1.0), 4))
        underlying_by_entity[s] = ue
        economic_by_entity[s] = ee
        r.underlying_exposures.append(ue)
        r.economic_exposures.append(ee)

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
            meta = registry.resolve(p.symbol)
            lk = scfg.layer_of(meta.layer_symbol)
            r.symbol_layers.append(SymbolLayer(
                symbol=p.symbol, layer=lk or "", label=labels.get(lk, "未分层") if lk else "未分层",
                weight=round(risk_wt[p.symbol] * meta.exposure_multiplier, 4),
                sec_type=p.sec_type or "STK"))
        for o in r.option_risks:
            meta = registry.resolve(o.underlying)
            lk = scfg.layer_of(meta.layer_symbol)
            dw = ((o.delta_notional / net_liq) * meta.exposure_multiplier) if net_liq else 0.0
            r.symbol_layers.append(SymbolLayer(
                symbol=f"{o.underlying}[{o.strategy or o.right}]", layer=lk or "",
                label=labels.get(lk, "未分层") if lk else "未分层",
                weight=round(dw, 4), sec_type="OPT"))
        # per-underlying NET delta weight drives the layer cap (hedges net off within a layer)
        for s, w in net_w.items():
            lk = scfg.layer_of(layer_symbols[s])
            if lk:
                layer_w[lk] = layer_w.get(lk, 0.0) + w
            underlying_by_entity[s].layer = lk or ""
            economic_by_entity[s].layer = lk or ""
        # ⚠️ 这里读的必须是**静态** `weight_cap`，绝不能是层级分析师的预算使用率调整过的值。
        # 两者回答不同的问题：使用率是「新增资金愿意投多少」，breach 是「已有持仓是否越界」。
        # 共用一个数会让一条「低配」结论把满仓但合规的层瞬间判成超限、触发不必要的减仓
        # —— 那是用建议信号冒充风险事件。使用率只作用在 cross_section 的 basket 预算上。
        for lk, w in layer_w.items():
            cap = caps.get(lk)
            breached = cap is not None and w > cap
            r.chain_layers.append(LayerExposure(key=lk, label=labels.get(lk, lk),
                                                weight=round(w, 4), cap=cap, breached=breached))
            if breached:
                r.breaches.append(Breach(layer=f"L1-{lk}", limit=f"≤{cap:.0%}",
                                         actual=f"{w:.0%}", action="缩/block 入该层的新买单"))

        # 跨层上限。拆层把一个 cap 变成两个独立的 cap，两半同时满仓就能超过拆分前允许的
        # 总量 —— 那等于在重构的掩护下放松护栏。group 上限把子层重新绑成一个天花板，
        # 数值取拆分前的原值，所以**单层都没越限而合计越限**时它仍然会响。
        for g in scfg.layer_groups:
            gw = round(sum(layer_w.get(k, 0.0) for k in g.layers), 4)
            hard = g.weight_cap_hard
            breached = gw > g.weight_cap
            r.chain_layer_groups.append(LayerExposure(
                key=g.key, label=g.label or g.key, weight=gw, cap=g.weight_cap,
                breached=breached, is_group=True, members=list(g.layers)))
            if breached:
                over_hard = hard is not None and gw > hard
                r.breaches.append(Breach(
                    layer=f"L1-组{g.key}", limit=f"≤{g.weight_cap:.0%}" + (
                        f"（硬顶 {hard:.0%}）" if hard is not None else ""),
                    actual=f"{gw:.0%}（{'+'.join(g.layers)}）",
                    action=("block 入该组任一层的新买单" if over_hard
                            else "缩/block 入该组的新买单")))
    except Exception as exc:  # noqa: BLE001
        log.warning("chain-layer concentration skipped: %s", exc)

    # L3 — correlation clusters (weight = |net delta weight|; option underlyings included)
    cluster_weights: dict[str, float] = {}
    for s, w in net_w.items():
        if abs(w) > 0:
            rs = risk_symbols[s]
            cluster_weights[rs] = cluster_weights.get(rs, 0.0) + abs(w)
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
        cost = p.avg_cost * abs(p.qty) * p.fx_rate_to_base
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
    stress_equities = []
    for p in equities:
        meta = registry.resolve(p.symbol)
        stress_equities.append(p.model_copy(update={
            "symbol": meta.risk_symbol,
            "weight": risk_wt[p.symbol] * meta.exposure_multiplier,
        }))
    eq_portfolio = portfolio.model_copy(update={"positions": stress_equities})
    r.stress = [StressResult(**s) for s in stress.run(
        eq_portfolio, market_shocks=rc.stress_market_shocks, top_cluster=top_cluster_members,
        ai_bubble_shock=rc.ai_bubble_cluster_shock, cash_equivalents=None,
        options=r.option_risks, vol_shocks=rc.stress_vol_shocks,
        ai_bubble_vol_shock=rc.ai_bubble_vol_shock, r=rc.option_risk_free_rate)]
    worst = min((s.loss_pct for s in r.stress), default=0.0)
    if worst <= -rc.max_stress_loss_pct * 100:
        r.breaches.append(Breach(layer="L5-压测", limit=f"≥-{rc.max_stress_loss_pct:.0%}",
                                 actual=f"{worst}%", action="block 加重受冲击敞口的新买单"))

    # L6 — earnings-event risk: equities (linear) + options (BSM ±EM reval, worse side)
    for p in equities:
        meta = registry.resolve(p.symbol)
        em = ((event_data or {}).get(p.symbol, {}).get("expected_move_pct")
              or (event_data or {}).get(meta.risk_symbol, {}).get("expected_move_pct"))
        if em:
            loss = round(risk_wt[p.symbol] * meta.exposure_multiplier * em, 2)
            breached = loss > rc.max_event_loss_pct * 100
            r.event_risks.append(EventRisk(symbol=p.symbol, weight=p.weight,
                                           expected_move_pct=em, event_loss_pct=loss))
            if breached:
                r.breaches.append(Breach(layer="L6-事件", limit=f"≤{rc.max_event_loss_pct:.0%}",
                                         actual=f"{p.symbol} {loss:.1f}%", action="削仓使事件损失≤限"))
    _assess_option_events(r, event_data, rc, net_liq)

    # --- portfolio greeks aggregate + caution-level option limits (disclose, don't hard-block) ---
    _assess_portfolio_greeks(r, net_w, net_liq, rc)

    # Chief-facing state machine; risk_state remains as a legacy compatibility projection.
    r.directive = _build_directive(r, rc, risk_policy, net_liq)
    r.risk_state = (
        "normal" if r.directive.state == "NORMAL"
        else "derisk" if r.directive.state in ("DATA_INVALID", "EMERGENCY")
        else "caution")
    r.notes = (f"{len(r.breaches)} breach(es), {len(r.cautions)} caution(s); "
               f"beta {r.portfolio_beta}; 相关簇 {len(r.clusters)}; 期权 {len(r.option_risks)}")
    return r


def _assess_margin(r: RiskReview, portfolio, equities, rc, net_liq: float) -> None:
    """L2 margin: prefer IBKR authoritative figures; else sum a Reg-T estimate. IBKR breaches
    hard-block; estimated breaches degrade to caution (annotated 估算) to avoid false blocks."""
    if not net_liq:
        return
    if portfolio.margin_source in ("ibkr", "ibkr_projected_est") and portfolio.init_margin:
        m = MarginSummary(
            init_margin=portfolio.init_margin, maint_margin=portfolio.maint_margin,
            excess_liquidity=portfolio.excess_liquidity, buying_power=portfolio.buying_power,
            margin_util=round(portfolio.init_margin / net_liq, 4),
            excess_liq_pct=(round(portfolio.excess_liquidity / net_liq, 4)
                            if portfolio.excess_liquidity is not None else None),
            source=portfolio.margin_source)
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
        basis = abs(p.avg_cost) * abs(p.qty) * p.fx_rate_to_base
        # cost (long) / credit (short), account base currency
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
        em = ((event_data or {}).get(o.underlying, {}).get("expected_move_pct")
              or (event_data or {}).get(o.risk_symbol, {}).get("expected_move_pct"))
        if not em:
            continue
        product_em = em * o.exposure_multiplier
        T = options_math.years_to_expiry(o.expiry) if o.expiry else 0.0
        is_call = (o.right or "C").upper().startswith("C")
        v0 = options_math.reprice(o.spot, o.strike, T, rc.option_risk_free_rate, o.iv, is_call)
        worst = 0.0
        for sign in (1.0, -1.0):
            shocked_spot = o.spot * max(1 + sign * product_em / 100.0, 0.01)
            v1 = options_math.reprice(shocked_spot, o.strike, T,
                                     rc.option_risk_free_rate, o.iv, is_call)
            pnl = (v1 - v0) * o.qty * o.multiplier * o.fx_rate_to_base
            worst = min(worst, pnl)
        loss_pct = round(-worst / net_liq * 100, 2)      # positive % NAV
        r.event_risks.append(EventRisk(symbol=f"{o.underlying}[{o.strategy or o.right}]",
                                       weight=round(abs(o.delta_notional) / net_liq, 4),
                                       expected_move_pct=product_em, event_loss_pct=loss_pct))
        if loss_pct > rc.max_event_loss_pct * 100:
            r.breaches.append(Breach(layer="L6-事件", limit=f"≤{rc.max_event_loss_pct:.0%}",
                                     actual=f"{o.underlying}({o.strategy}) {loss_pct:.1f}%",
                                     action="削期权仓使事件损失≤限"))


def _assess_portfolio_greeks(r: RiskReview, net_w: dict, net_liq: float, rc) -> None:
    """Aggregate option greeks + delta-adjusted leverage; caution if |净vega|/NAV over limit."""
    net_dn = sum(o.delta_notional for o in r.option_risks)
    net_gamma = sum((o.gamma or 0.0) * o.qty * o.multiplier for o in r.option_risks)
    net_vega = sum((o.vega or 0.0) * o.qty * o.multiplier * o.fx_rate_to_base
                   for o in r.option_risks)
    net_theta = sum((o.theta or 0.0) * o.qty * o.multiplier * o.fx_rate_to_base
                    for o in r.option_risks)
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
