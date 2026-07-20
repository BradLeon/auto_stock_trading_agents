"""Scenario stress — deterministic loss estimates for the full book (equities + options).

Equities: beta × market-shock (linear). Options: BSM full-revaluation under the shocked
spot AND a paired vol bump (crashes raise IV) — this captures gamma/vega non-linearity that
a linear delta approximation misses (short puts show real tail loss, long puts show their
hedge gain). A named 'AI 泡沫' scenario hits the top correlated cluster harder. Loss as %
of NAV (negative).
"""

from __future__ import annotations

from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import OptionRisk
from .options_math import reprice, years_to_expiry


def _beta(p) -> float:
    return p.beta if p.beta is not None else 1.0


def _risk_weight(p, cash_equivalents: dict[str, float] | None) -> float:
    """Effective shockable weight: cash equivalents only expose their haircut portion
    (haircut=0 near-cash contributes nothing to a market/cluster shock)."""
    hc = (cash_equivalents or {}).get(p.symbol)
    return p.weight * hc if hc is not None else p.weight


def market_shock(portfolio: PortfolioSnapshot, shock: float,
                 cash_equivalents: dict[str, float] | None = None) -> float:
    """Beta-weighted equity-market shock: Σ risk_weight_i · beta_i · shock. (% NAV)"""
    beta_wsum = sum(_risk_weight(p, cash_equivalents) * _beta(p) for p in portfolio.positions)
    # cash and near-cash equivalents are unaffected; loss scales with invested beta-exposure
    return round(beta_wsum * shock * 100, 2)


def cluster_shock(portfolio: PortfolioSnapshot, cluster_members: list[str],
                  shock: float, cash_equivalents: dict[str, float] | None = None) -> float:
    """Extra shock concentrated on a correlated cluster (e.g. AI-semi -35%). (% NAV)"""
    members = set(cluster_members)
    w = sum(_risk_weight(p, cash_equivalents)
            for p in portfolio.positions if p.symbol in members)
    return round(w * shock * 100, 2)


def _option_reval_loss(options: list[OptionRisk], spot_shock: float, vol_shock: float,
                       net_liq: float, r: float,
                       only_underlyings: set[str] | None = None) -> float:
    """BSM full-revaluation P&L of the option book under (spot·(1+shock), σ·(1+vol_shock)),
    as % NAV (signed). Options that can't be priced (priced=False / missing inputs) contribute 0."""
    if not options or not net_liq:
        return 0.0
    pnl = 0.0
    for o in options:
        if not o.priced or o.spot is None or o.iv is None or not o.strike:
            continue
        if only_underlyings is not None and o.underlying not in only_underlyings:
            continue
        T = years_to_expiry(o.expiry) if o.expiry else 0.0
        is_call = (o.right or "C").upper().startswith("C")
        S0, sigma0 = o.spot, o.iv
        v0 = reprice(S0, o.strike, T, r, sigma0, is_call)                       # per share now
        v1 = reprice(S0 * (1 + spot_shock), o.strike, T, r,
                     max(sigma0 * (1 + vol_shock), 1e-4), is_call)              # shocked
        pnl += (v1 - v0) * o.qty * o.multiplier          # qty signed (short < 0)
    return round(pnl / net_liq * 100, 2)


def run(portfolio: PortfolioSnapshot, *, market_shocks: list[float],
        top_cluster: list[str] | None, ai_bubble_shock: float,
        cash_equivalents: dict[str, float] | None = None,
        options: list[OptionRisk] | None = None, vol_shocks: list[float] | None = None,
        ai_bubble_vol_shock: float = 0.0, r: float = 0.045) -> list[dict]:
    """`portfolio` is the equity-only book (beta-shocked); `options` are revalued via BSM.
    vol_shocks pairs 1:1 with market_shocks (missing → 0). Regression-safe: with no options
    and no vol_shocks, output equals the pre-upgrade equity-only stress."""
    vol_shocks = vol_shocks or []
    net_liq = portfolio.net_liquidation
    out = []
    for i, s in enumerate(market_shocks):
        vs = vol_shocks[i] if i < len(vol_shocks) else 0.0
        eq_loss = market_shock(portfolio, s, cash_equivalents)
        opt_loss = _option_reval_loss(options, s, vs, net_liq, r) if options else 0.0
        tag = f"市场 {int(s*100)}%" + (f"+vol{int(vs*100)}%" if vs else "") + " (beta加权+期权重估)"
        out.append({"scenario": tag, "loss_pct": round(eq_loss + opt_loss, 2)})
    if top_cluster:
        members = set(top_cluster)
        eq_loss = cluster_shock(portfolio, top_cluster, ai_bubble_shock, cash_equivalents)
        opt_loss = (_option_reval_loss(options, ai_bubble_shock, ai_bubble_vol_shock, net_liq, r,
                                       only_underlyings=members) if options else 0.0)
        vtag = f"+vol{int(ai_bubble_vol_shock*100)}%" if ai_bubble_vol_shock else ""
        out.append({"scenario": f"AI泡沫破裂 {int(ai_bubble_shock*100)}%{vtag} (打相关簇)",
                    "loss_pct": round(eq_loss + opt_loss, 2)})
    return out
