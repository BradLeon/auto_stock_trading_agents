"""Option-implied earnings setup: Expected Move, ATM IV, 25Δ skew.

Primary: local ThetaData EOD API (127.0.0.1:25503) — matches the user's workflow.
Fallback: yfinance option chain (free, in-process). Returns a dict; missing
fields are None. Never raises.

NOTE: the ThetaData v3 response parser is best-effort and should be validated
against a live ThetaData instance — if it doesn't match, it raises and we fall
back to yfinance, so the cycle still works.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from .base import safe_fetch

log = logging.getLogger("ats.data.options")
name = "options"

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
# ThetaData (best-effort; validate parser against live instance)
# --------------------------------------------------------------------------- #
def _thetadata(symbol: str, earnings_date: date | None) -> dict | None:
    import os

    import httpx

    host = os.environ.get("THETADATA_URL", "http://127.0.0.1:25503")
    today = datetime.now(timezone.utc).date()
    d = (earnings_date or today).strftime("%Y%m%d")
    url = f"{host}/v3/option/history/eod"
    r = httpx.get(url, params={"symbol": symbol, "expiration": "*",
                               "start_date": d, "end_date": d}, timeout=10)
    r.raise_for_status()
    payload = r.json()
    # Defensive parse: expect rows of option EOD records. Shape varies by version,
    # so bail (->fallback) if we can't find strike/IV/price-like fields.
    rows = payload.get("response") if isinstance(payload, dict) else payload
    if not rows:
        raise ValueError("thetadata: empty/unexpected response")
    # Parsing of straddle/skew from ThetaData rows is left as an integration point;
    # raise so we fall back to yfinance until validated against a live feed.
    raise NotImplementedError("thetadata parser pending validation against live feed")


# --------------------------------------------------------------------------- #
# yfinance option chain (validated path)
# --------------------------------------------------------------------------- #
def _spot(t) -> float:
    fi = getattr(t, "fast_info", None)
    if fi:
        for k in ("last_price", "lastPrice"):
            v = fi.get(k) if hasattr(fi, "get") else getattr(fi, k, None)
            if v:
                return float(v)
    hist = t.history(period="1d")
    return float(hist["Close"].iloc[-1])


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


def _yfinance(symbol: str, earnings_date: date | None) -> dict | None:
    import yfinance as yf

    t = yf.Ticker(symbol)
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

    # Approx 25Δ skew: put IV ~0.95*spot minus call IV ~1.05*spot (delta not in yf).
    otm_put = _nearest_row(puts, spot * 0.95)
    otm_call = _nearest_row(calls, spot * 1.05)
    skew = None
    if otm_put.get("impliedVolatility") and otm_call.get("impliedVolatility"):
        skew = float((otm_put["impliedVolatility"] - otm_call["impliedVolatility"]) * 100)

    return {"expected_move_pct": round(em, 2) if em else None,
            "atm_iv": round(atm_iv, 1) if atm_iv else None,
            "iv_skew": round(skew, 2) if skew is not None else None,
            "expiration": exp}
