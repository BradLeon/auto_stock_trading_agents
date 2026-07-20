"""Black-Scholes greeks + Reg-T margin — the fallback quant engine for option risk.

Greeks and margin are IBKR-authoritative when available (see broker/ibkr.py); this
module is the estimation fallback when the API can't supply them, plus the always-on
BSM repricer used for scenario stress (F). No scipy — `math.erf`, mirroring the
existing BSM inversion in `data/options.py` (whose `_bs_price`/`_implied_vol` we reuse).

Conventions:
- `qty` is signed (long > 0, short < 0, in contracts); `mult` = shares/contract (100).
- greeks() returns per-option-share terms (delta unitless, vega per 1% vol, theta per day).
- position_greeks() scales to the whole position (× qty × mult) and gives delta_notional.
"""

from __future__ import annotations

import math
from datetime import date, datetime

# Reuse the BSM core already battle-tested for IV inversion (avoids two copies).
from ..data.options import _bs_price, _implied_vol, _norm_cdf  # noqa: F401


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def greeks(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> dict:
    """Per-option-share greeks. delta (unitless), gamma (per $1 spot), vega (per 1% vol),
    theta (per day), rho (per 1.00 rate). Degenerate (expired / σ≤0) → intrinsic-only delta."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # At/after expiry: delta is a step, all other greeks collapse to 0.
        if is_call:
            d = 1.0 if S > K else 0.0
        else:
            d = -1.0 if S < K else 0.0
        return {"delta": d, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    pdf = _norm_pdf(d1)
    disc = math.exp(-r * T)
    if is_call:
        delta = _norm_cdf(d1)
        theta_yr = (-S * pdf * sigma / (2 * sqrtT) - r * K * disc * _norm_cdf(d2))
        rho = K * T * disc * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_yr = (-S * pdf * sigma / (2 * sqrtT) + r * K * disc * _norm_cdf(-d2))
        rho = -K * T * disc * _norm_cdf(-d2)
    gamma = pdf / (S * sigma * sqrtT)
    vega = S * pdf * sqrtT / 100.0    # per 1% vol point (matches IBKR modelGreeks convention)
    return {"delta": delta, "gamma": gamma, "vega": vega,
            "theta": theta_yr / 365.0, "rho": rho}


def position_greeks(g: dict, qty: float, mult: float, spot: float) -> dict:
    """Scale per-share greeks to a signed position. delta_notional = delta×qty×mult×spot."""
    return {
        "delta": g["delta"], "gamma": g["gamma"], "vega": g["vega"], "theta": g["theta"],
        "delta_notional": g["delta"] * qty * mult * spot,
        "pos_gamma": g["gamma"] * qty * mult,
        "pos_vega": g["vega"] * qty * mult,      # per 1% vol point ($)
        "pos_theta": g["theta"] * qty * mult,    # per day ($)
    }


def years_to_expiry(expiry: str, today: date | None = None) -> float:
    """(expiry − today)/365 in years. Accepts YYYYMMDD or YYYY-MM-DD; floors at ~intraday."""
    today = today or datetime.utcnow().date()
    e = expiry.replace("-", "")
    try:
        ed = datetime.strptime(e[:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        return 0.0
    return max((ed - today).days, 0) / 365.0


def regt_margin(strategy: str, S: float, K: float, premium: float, contracts: float,
                mult: float = 100.0) -> float:
    """Reg-T initial margin estimate for a single-leg option position ($). Fallback for when
    IBKR margin is unavailable. `premium` is per-share option price; `contracts` = |qty|.

    - long (buy_call/buy_put): margin = premium paid (fully-owned, no borrow).
    - short put (sell_put): max(0.2·S − OTM, 0.1·K)·mult·n + premium; OTM = max(K−S, 0).
    - covered_call: 0 (the long stock collateralises the short call).
    - naked_call: max(0.2·S − OTM, 0.1·S)·mult·n + premium; OTM = max(S−K, 0).
    """
    n = abs(contracts)
    prem_total = abs(premium) * mult * n
    if strategy in ("buy_call", "buy_put"):
        return prem_total
    if strategy == "covered_call":
        return 0.0
    if strategy == "sell_put":
        otm = max(K - S, 0.0)
        return max(0.2 * S - otm, 0.1 * K) * mult * n + prem_total
    if strategy == "naked_call":
        otm = max(S - K, 0.0)
        return max(0.2 * S - otm, 0.1 * S) * mult * n + prem_total
    # unknown/unclassified short — be conservative, treat like naked
    otm = max(S - K, 0.0) if S > K else max(K - S, 0.0)
    return max(0.2 * S - otm, 0.1 * K) * mult * n + prem_total


def reprice(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """BSM option value per share under (possibly shocked) spot/vol — used by stress reval."""
    return _bs_price(S, K, T, r, sigma, is_call)
