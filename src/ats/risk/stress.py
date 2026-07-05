"""Scenario stress — deterministic loss estimates for an individual book.

No VaR model; beta × market-shock for broad scenarios, plus a named 'AI 泡沫'
scenario that hits the top correlated cluster harder. Loss as % of NAV (negative).
"""

from __future__ import annotations

from ..schemas.portfolio import PortfolioSnapshot


def _beta(p) -> float:
    return p.beta if p.beta is not None else 1.0


def market_shock(portfolio: PortfolioSnapshot, shock: float) -> float:
    """Beta-weighted equity-market shock: Σ weight_i · beta_i · shock. (% NAV)"""
    invested = sum(p.weight for p in portfolio.positions)
    beta_wsum = sum(p.weight * _beta(p) for p in portfolio.positions)
    # cash is unaffected; loss scales with invested beta-exposure
    return round(beta_wsum * shock * 100, 2)


def cluster_shock(portfolio: PortfolioSnapshot, cluster_members: list[str],
                  shock: float) -> float:
    """Extra shock concentrated on a correlated cluster (e.g. AI-semi -35%). (% NAV)"""
    members = set(cluster_members)
    w = sum(p.weight for p in portfolio.positions if p.symbol in members)
    return round(w * shock * 100, 2)


def run(portfolio: PortfolioSnapshot, *, market_shocks: list[float],
        top_cluster: list[str] | None, ai_bubble_shock: float) -> list[dict]:
    out = [{"scenario": f"市场 {int(s*100)}% (beta加权)", "loss_pct": market_shock(portfolio, s)}
           for s in market_shocks]
    if top_cluster:
        out.append({"scenario": f"AI泡沫破裂 {int(ai_bubble_shock*100)}% (打相关簇)",
                    "loss_pct": cluster_shock(portfolio, top_cluster, ai_bubble_shock)})
    return out
