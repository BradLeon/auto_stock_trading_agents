"""Option-implied earnings setup: Expected Move, ATM IV, 25Δ-proxy skew.

Primary: local ThetaData v3 terminal (127.0.0.1:25503). Its REST API returns CSV;
the FREE tier exposes option + stock EOD (bid/ask), but NOT implied vol / greeks —
so we compute Expected Move from the ATM straddle and back out IV via a
Black-Scholes inversion from the option mids.
Fallback: yfinance option chain. Returns a dict; missing fields are None; never raises.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import os
from datetime import date, datetime, timedelta, timezone

from .base import safe_fetch

log = logging.getLogger("ats.data.options")
name = "options"
_RISK_FREE = 0.045

_EMPTY = {"expected_move_pct": None, "atm_iv": None, "iv_skew": None, "expiration": None,
          "source": None}


def fetch(symbol: str, earnings_date: date | None = None) -> dict:
    """Try ThetaData, then yfinance, then return an empty setup with a note."""
    td = safe_fetch(lambda: _thetadata(symbol, earnings_date), source=f"thetadata:{symbol}",
                    attempts=1)
    if td:
        td["source"] = "thetadata"
        return td
    yf = safe_fetch(lambda: _yfinance(symbol, earnings_date), source=f"yf-options:{symbol}")
    if yf:
        yf["source"] = "yfinance"
        return yf
    return dict(_EMPTY)


# --------------------------------------------------------------------------- #
# ThetaData v3 (CSV; free-tier EOD + BS-inverted IV)
# --------------------------------------------------------------------------- #
def _td_get(path: str, **params) -> list[dict]:
    """GET a ThetaData v3 endpoint and parse its CSV body into row dicts."""
    import httpx

    host = os.environ.get("THETADATA_URL", "http://127.0.0.1:25503")
    with httpx.Client(trust_env=False, timeout=30) as c:  # bypass any SOCKS proxy for localhost
        r = c.get(f"{host}{path}", params=params)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def thetadata_raw(symbol: str, on_date: date | None = None) -> list[dict]:
    """Nearest-future-expiration option EOD rows — used by `ats thetadata` to inspect."""
    spot, qdate = _stock_eod(symbol)
    exps = _future_expirations(symbol, qdate)
    if not exps:
        return []
    return _td_get("/v3/option/history/eod", symbol=symbol, expiration=exps[0],
                   start_date=qdate, end_date=qdate)


def _stock_eod(symbol: str) -> tuple[float, str]:
    """Latest available stock EOD close (spot) + its date (yyyymmdd)."""
    today = datetime.now(timezone.utc).date()
    rows = _td_get("/v3/stock/history/eod", symbol=symbol,
                   start_date=(today - timedelta(days=7)).strftime("%Y%m%d"),
                   end_date=today.strftime("%Y%m%d"))
    rows = [r for r in rows if r.get("close")]
    if not rows:
        raise ValueError("thetadata: no stock EOD")
    last = rows[-1]
    qdate = (last.get("created") or "")[:10].replace("-", "") or today.strftime("%Y%m%d")
    return float(last["close"]), qdate


def _future_expirations(symbol: str, qdate: str) -> list[str]:
    rows = _td_get("/v3/option/list/expirations", symbol=symbol)
    exps = sorted({r["expiration"] for r in rows if r.get("expiration")})
    return [e for e in exps if e.replace("-", "") >= qdate]


def _thetadata(symbol: str, earnings_date: date | None) -> dict:
    spot, qdate = _stock_eod(symbol)
    exps = _future_expirations(symbol, qdate)
    chosen = _pick_exp(exps, earnings_date)
    if not chosen:
        raise ValueError("thetadata: no future expiration")
    rows = _td_get("/v3/option/history/eod", symbol=symbol, expiration=chosen,
                   start_date=qdate, end_date=qdate)
    calls = {float(r["strike"]): r for r in rows if r.get("right", "").upper().startswith("C")}
    puts = {float(r["strike"]): r for r in rows if r.get("right", "").upper().startswith("P")}
    if not calls or not puts:
        raise ValueError("thetadata: missing call/put side")

    t_years = max((datetime.strptime(chosen, "%Y-%m-%d").date()
                   - datetime.strptime(qdate, "%Y%m%d").date()).days, 1) / 365.0

    k_atm = min(calls, key=lambda k: abs(k - spot))
    p_atm = min(puts, key=lambda k: abs(k - spot))
    cm, pm = _mid_csv(calls[k_atm]), _mid_csv(puts[p_atm])
    em = ((cm + pm) / spot * 100) if (cm and pm) else None

    iv_c = _implied_vol(cm, spot, k_atm, t_years, _RISK_FREE, True) if cm else None
    iv_p = _implied_vol(pm, spot, p_atm, t_years, _RISK_FREE, False) if pm else None
    atm_iv = ((iv_c + iv_p) / 2 * 100) if (iv_c and iv_p) else None

    # 25Δ-proxy skew: IV at ~0.95*S put minus ~1.05*S call.
    kp = min(puts, key=lambda k: abs(k - spot * 0.95))
    kc = min(calls, key=lambda k: abs(k - spot * 1.05))
    ivp = _implied_vol(_mid_csv(puts[kp]), spot, kp, t_years, _RISK_FREE, False)
    ivc = _implied_vol(_mid_csv(calls[kc]), spot, kc, t_years, _RISK_FREE, True)
    skew = ((ivp - ivc) * 100) if (ivp and ivc) else None

    return {"expected_move_pct": round(em, 2) if em else None,
            "atm_iv": round(atm_iv, 1) if atm_iv else None,
            "iv_skew": round(skew, 2) if skew is not None else None, "expiration": chosen}


def _mid_csv(row: dict) -> float | None:
    try:
        b, a = float(row.get("bid") or 0), float(row.get("ask") or 0)
    except (TypeError, ValueError):
        return None
    if b > 0 and a > 0:
        return (b + a) / 2
    try:
        last = float(row.get("close") or 0)
    except (TypeError, ValueError):
        return None
    return last if last > 0 else None


def _pick_exp(exps: list[str], earnings_date: date | None) -> str | None:
    if not exps:
        return None
    if not earnings_date:
        return exps[0]
    ed = earnings_date.strftime("%Y%m%d")
    after = [e for e in exps if e.replace("-", "") >= ed]
    return after[0] if after else exps[-1]


# --------------------------------------------------------------------------- #
# Black-Scholes implied-vol inversion (bisection)
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bs_price(s: float, k: float, t: float, r: float, sigma: float, is_call: bool) -> float:
    if t <= 0 or sigma <= 0:
        return max(0.0, (s - k) if is_call else (k - s))
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if is_call:
        return s * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)
    return k * math.exp(-r * t) * _norm_cdf(-d2) - s * _norm_cdf(-d1)


def _implied_vol(price, s, k, t, r, is_call) -> float | None:
    if not price or price <= max(0.0, (s - k) if is_call else (k - s)) or t <= 0:
        return None
    lo, hi = 1e-3, 5.0
    for _ in range(64):
        mid = (lo + hi) / 2
        if _bs_price(s, k, t, r, mid, is_call) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------- #
# yfinance option chain (fallback)
# --------------------------------------------------------------------------- #
def _spot(t) -> float:
    fi = getattr(t, "fast_info", None)
    if fi:
        for k in ("last_price", "lastPrice"):
            v = fi.get(k) if hasattr(fi, "get") else getattr(fi, k, None)
            if v:
                return float(v)
    return float(t.history(period="1d")["Close"].iloc[-1])


def _nearest_row(df, spot: float):
    return df.iloc[(df["strike"] - spot).abs().argmin()]


def _mid(row) -> float | None:
    bid, ask, last = row.get("bid"), row.get("ask"), row.get("lastPrice")
    if bid and ask and bid > 0 and ask > 0:
        return float((bid + ask) / 2)
    return float(last) if last and last > 0 else None


def _pick_expiration(expirations: tuple[str, ...], earnings_date: date | None) -> str | None:
    if not expirations:
        return None
    exps = sorted(datetime.strptime(e, "%Y-%m-%d").date() for e in expirations)
    if earnings_date:
        after = [e for e in exps if e >= earnings_date]
        chosen = after[0] if after else exps[-1]
    else:
        chosen = exps[0]
    return chosen.strftime("%Y-%m-%d")


def _yfinance(symbol: str, earnings_date: date | None) -> dict:
    import yfinance as yf
    from .base import yf_symbol

    t = yf.Ticker(yf_symbol(symbol))
    exp = _pick_expiration(t.options, earnings_date)
    if not exp:
        raise ValueError(f"no option expirations for {symbol}")
    spot = _spot(t)
    chain = t.option_chain(exp)
    calls, puts = chain.calls, chain.puts
    if calls.empty or puts.empty:
        raise ValueError(f"empty option chain for {symbol} {exp}")

    atm_call, atm_put = _nearest_row(calls, spot), _nearest_row(puts, spot)
    c_mid, p_mid = _mid(atm_call), _mid(atm_put)
    em = ((c_mid + p_mid) / spot * 100) if (c_mid and p_mid and spot) else None

    iv_c, iv_p = atm_call.get("impliedVolatility"), atm_put.get("impliedVolatility")
    atm_iv = float((iv_c + iv_p) / 2 * 100) if (iv_c and iv_p) else None

    otm_put = _nearest_row(puts, spot * 0.95)
    otm_call = _nearest_row(calls, spot * 1.05)
    skew = None
    if otm_put.get("impliedVolatility") and otm_call.get("impliedVolatility"):
        skew = float((otm_put["impliedVolatility"] - otm_call["impliedVolatility"]) * 100)

    return {"expected_move_pct": round(em, 2) if em else None,
            "atm_iv": round(atm_iv, 1) if atm_iv else None,
            "iv_skew": round(skew, 2) if skew is not None else None,
            "expiration": exp}
