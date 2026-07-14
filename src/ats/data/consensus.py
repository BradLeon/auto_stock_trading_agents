"""Analyst consensus for the upcoming quarter — yfinance, free.

Returns a dict, None/[] for anything unavailable. Never raises.
  Estimates: eps, revenue, eps_low, eps_high, revenue_low, revenue_high
  Price targets: target_mean, target_median, target_low, target_high, target_current
  Ratings (current month): rating_strong_buy, rating_buy, rating_hold,
    rating_sell, rating_strong_sell
  rating_trend: [{period, strong_buy, buy, hold, sell, strong_sell}, ...]  (0m/-1m/-2m/-3m)
  upgrades_downgrades: [{date, firm, to_grade, from_grade, action}, ...]  (last 120d, max 8)
"""

from __future__ import annotations

from .base import safe_fetch

name = "consensus"

_UD_DAYS = 120
_UD_MAX = 8


def _num(x):
    try:
        v = float(x)
        return v if v == v else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _int(x):
    v = _num(x)
    return int(v) if v is not None else None


def _str(x):
    if x is None or x != x:  # None / NaN
        return None
    s = str(x).strip()
    return s or None


_ANALYST_DEFAULTS = {
    "target_mean": None, "target_median": None, "target_low": None,
    "target_high": None, "target_current": None,
    "rating_strong_buy": None, "rating_buy": None, "rating_hold": None,
    "rating_sell": None, "rating_strong_sell": None,
    "rating_trend": [],          # [{period, strong_buy, buy, hold, sell, strong_sell}]
    "upgrades_downgrades": [],   # [{date, firm, to_grade, from_grade, action}]
}


def _yf_consensus(symbol: str) -> dict:
    import yfinance as yf
    from .base import yf_symbol

    t = yf.Ticker(yf_symbol(symbol))
    out: dict = {"eps": None, "revenue": None, "eps_low": None, "eps_high": None,
                 "revenue_low": None, "revenue_high": None}

    # earnings_estimate / revenue_estimate: rows '0q' (current quarter) etc., col 'avg'.
    ee = getattr(t, "earnings_estimate", None)
    if ee is not None and hasattr(ee, "index") and "0q" in ee.index and "avg" in ee.columns:
        out["eps"] = _num(ee.loc["0q", "avg"])
        if "low" in ee.columns:
            out["eps_low"] = _num(ee.loc["0q", "low"])
        if "high" in ee.columns:
            out["eps_high"] = _num(ee.loc["0q", "high"])

    re = getattr(t, "revenue_estimate", None)
    if re is not None and hasattr(re, "index") and "0q" in re.index and "avg" in re.columns:
        out["revenue"] = _num(re.loc["0q", "avg"])
        if "low" in re.columns:
            out["revenue_low"] = _num(re.loc["0q", "low"])
        if "high" in re.columns:
            out["revenue_high"] = _num(re.loc["0q", "high"])

    # Fallback to the calendar dict for EPS/revenue averages.
    cal = getattr(t, "calendar", None)
    if isinstance(cal, dict):
        if out["eps"] is None:
            out["eps"] = _num(cal.get("EPS Estimate"))
        if out["revenue"] is None:
            out["revenue"] = _num(cal.get("Revenue Estimate"))

    if out["eps"] is None and out["revenue"] is None:
        raise ValueError(f"no consensus for {symbol}")
    return out


def _yf_analyst(symbol: str) -> dict:
    import yfinance as yf
    from .base import yf_symbol

    t = yf.Ticker(yf_symbol(symbol))
    out: dict = {**_ANALYST_DEFAULTS, "rating_trend": [], "upgrades_downgrades": []}

    # analyst_price_targets: {'current': ..., 'low': ..., 'high': ..., 'mean': ..., 'median': ...}
    pt = getattr(t, "analyst_price_targets", None)
    if isinstance(pt, dict):
        for key in ("mean", "median", "low", "high", "current"):
            out[f"target_{key}"] = _num(pt.get(key))

    # recommendations_summary: DataFrame period 0m/-1m/-2m/-3m, cols strongBuy..strongSell.
    rs = getattr(t, "recommendations_summary", None)
    if rs is not None and hasattr(rs, "empty") and not rs.empty and "period" in rs.columns:
        for _, row in rs.iterrows():
            entry = {
                "period": _str(row.get("period")),
                "strong_buy": _int(row.get("strongBuy")),
                "buy": _int(row.get("buy")),
                "hold": _int(row.get("hold")),
                "sell": _int(row.get("sell")),
                "strong_sell": _int(row.get("strongSell")),
            }
            out["rating_trend"].append(entry)
            if entry["period"] == "0m":
                out["rating_strong_buy"] = entry["strong_buy"]
                out["rating_buy"] = entry["buy"]
                out["rating_hold"] = entry["hold"]
                out["rating_sell"] = entry["sell"]
                out["rating_strong_sell"] = entry["strong_sell"]

    # upgrades_downgrades: DataFrame indexed by GradeDate, cols Firm/ToGrade/FromGrade/Action.
    ud = getattr(t, "upgrades_downgrades", None)
    if ud is not None and hasattr(ud, "empty") and not ud.empty:
        from datetime import date, timedelta

        cutoff = date.today() - timedelta(days=_UD_DAYS)
        for idx, row in ud.sort_index(ascending=False).iterrows():
            d = idx.date() if hasattr(idx, "date") else None
            if d is None or d < cutoff:
                continue
            out["upgrades_downgrades"].append({
                "date": d.isoformat(),
                "firm": _str(row.get("Firm")),
                "to_grade": _str(row.get("ToGrade")),
                "from_grade": _str(row.get("FromGrade")),
                "action": _str(row.get("Action")),  # up/down/init/main/reit
            })
            if len(out["upgrades_downgrades"]) >= _UD_MAX:
                break

    if (out["target_mean"] is None and out["rating_strong_buy"] is None
            and out["rating_buy"] is None and not out["upgrades_downgrades"]):
        raise ValueError(f"no analyst data for {symbol}")
    return out


def fetch(symbol: str) -> dict:
    out: dict = {"eps": None, "revenue": None, "eps_low": None, "eps_high": None,
                 "revenue_low": None, "revenue_high": None, **_ANALYST_DEFAULTS}
    est = safe_fetch(lambda: _yf_consensus(symbol), source=f"{name}:{symbol}")
    if est:
        out.update(est)
    an = safe_fetch(lambda: _yf_analyst(symbol), source=f"{name}-analyst:{symbol}")
    if an:
        out.update(an)
    return out
