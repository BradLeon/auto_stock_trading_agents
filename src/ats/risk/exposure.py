"""Holdings seen two ways: what they are worth, and what they actually expose the book to.

Market value is the wrong size for three kinds of position, and a report that shows only market
value understates all of them:

* a 2x leveraged ETF (NVDX, SKUU, 7709) moves like twice its market value in its underlying;
* a short put's market value is the small premium, while its directional exposure is
  `-delta x qty x multiplier x spot` — large and POSITIVE (short put = long the underlying);
* a long call (QQQ) costs little and exposes the delta-notional.

This module is a read-only view over the numbers the risk review already computed
(`RiskReview.symbol_layers` carries each holding's exposure weight with the instrument
multiplier, the cash-equivalent haircut and the option delta applied) joined to the portfolio
for quantity and market value. It never recomputes exposure on its own.
"""

from __future__ import annotations


def _option_key(underlying: str, right: str, strike, expiry: str) -> tuple:
    return (str(underlying), str(right or "")[:1].upper(), round(float(strike or 0), 4), str(expiry or ""))


def option_key_of_position(p) -> tuple:
    """Key shared by a stored/live position (dict or model) and the review's OptionRisk."""
    get = p.get if isinstance(p, dict) else lambda k, d=None: getattr(p, k, d)
    return _option_key(get("underlying") or get("symbol"), get("right"), get("strike"), get("expiry"))


def exposure_rows(review, portfolio=None) -> list[dict]:
    """One row per holding, largest true exposure first.

    `portfolio` (a PortfolioSnapshot) supplies quantity and market value; without it those cells
    are None and only the exposure columns are filled — a report built from a saved review still
    shows true exposure.
    """
    from . import inputs

    registry = inputs.rules().instruments
    nav = review.net_liquidation or 0.0
    cash_eq = {k.upper().replace(" ", "-") for k in (review.cash_equivalents and
                                                   [c.symbol for c in review.cash_equivalents] or [])}
    # Stocks only: an option carries its underlying's symbol, so keying on the symbol alone would
    # hand a stock row the option's quantity and market value.
    positions = {p.symbol: p for p in (portfolio.positions if portfolio else [])
                 if getattr(p, "sec_type", "STK") != "OPT"}
    layer_rows = list(review.symbol_layers)
    option_layers = [s for s in layer_rows if s.sec_type == "OPT"]
    rows: list[dict] = []

    for sl in layer_rows:
        if sl.sec_type == "OPT":
            continue
        p = positions.get(sl.symbol)
        meta = registry.resolve(sl.symbol)
        norm = sl.symbol.upper().replace(" ", "-")
        if norm in cash_eq:
            factor, value = "现金等价物（不计敞口）", 0.0
        elif meta.exposure_multiplier != 1:
            factor, value = f"{meta.exposure_multiplier:g}× 每日目标", meta.exposure_multiplier
        else:
            factor, value = "1", 1.0
        rows.append({"name": sl.symbol, "kind": "STK", "key": ("STK", sl.symbol),
                     "qty": p.qty if p else None, "market_value": p.market_value if p else None,
                     "factor": factor, "factor_value": value, "exposure": sl.weight * nav,
                     "nav_pct": sl.weight, "layer": sl.label or "未分层", "mapped": bool(sl.layer)})

    for o, sl in zip(review.option_risks, option_layers):
        strike = f"{o.strike:g}"
        right = "P" if o.right == "P" else "C"
        rows.append({"name": f"{o.underlying} {strike}{right}", "kind": "OPT",
                     "key": ("OPT",) + _option_key(o.underlying, o.right, o.strike, o.expiry),
                     "qty": o.qty, "market_value": o.premium_mv,
                     "factor": "—" if o.delta is None else f"{o.delta:+.4f}", "factor_value": o.delta,
                     "exposure": sl.weight * nav, "nav_pct": sl.weight,
                     "layer": sl.label or "未分层", "mapped": bool(sl.layer)})
    return sorted(rows, key=lambda r: -abs(r["exposure"] or 0.0))


def unmapped(rows: list[dict]) -> list[dict]:
    """Holdings whose exposure sits in no chain layer (an index option is legitimately one)."""
    return [r for r in rows if not r["mapped"] and abs(r["exposure"] or 0.0) > 0]


def by_key(rows: list[dict]) -> dict:
    return {r["key"]: r for r in rows}
