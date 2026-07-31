"""Replay candidate drawdown-control rules over real price history.

Why this exists: the question "would a trend/vol rule have helped in the drawdowns
I actually lived through" is answerable with data, and answering it BEFORE wiring
anything into the live path is the whole point. See docs/MACRO_ANALYST.md's staging
philosophy — deterministic first, verified, then connected.

Three hard rules this module must obey, because a backtest is the easiest kind of
code to fool yourself with:

  1. NO LOOKAHEAD. An exposure for day i is computed from closes[0..i] only and is
     applied to the return realised from i to i+1. `assert_no_lookahead` proves it
     by mutating the future and checking the past signal is unchanged.
  2. NO PARAMETER SEARCH. Every constant below is a textbook default, fixed before
     the first run. Three drawdown episodes cannot support a grid search — it would
     produce a beautiful, overfit, useless answer.
  3. COSTS ARE MANDATORY. Whipsaw is the specific risk being tested; without a
     round-trip cost the rule that trades most looks best for free.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# ── fixed parameters (pre-registered; do not tune) ────────────────────────────
VOL_WINDOW = 20            # trading days for realised vol
VOL_PCTL_TRIGGER = 80.0    # only throttle above this percentile of own vol history
DD_LOOKBACK = 252          # trailing window for "peak" in drawdown
PEER_EXCESS_TRIGGER = -0.15   # own DD minus peer-median DD, in fraction (-15pp)
SMA_WINDOW = 200
COST_BPS = 10.0            # one-way, in basis points of traded notional
TRADING_DAYS = 252

# ── strategy D, pre-registered 2026-07-31 (variant #2) ───────────────────────
# Registered BEFORE its first run, per the no-search discipline above. Spec:
#   trend    = share of {close>SMA20, close>SMA50, close>SMA200} that hold  -> {0,⅓,⅔,1}
#   vix_mult = min(1, VIX_ANCHOR / VIX)   (VIX<=15 -> 1.0; VIX 30 -> 0.5)
#   exposure = trend * vix_mult
# The MA leg grades trend quality per name; the VIX leg is a market-wide throttle,
# so it scales every name together. Only the anchor (15) was chosen by the user;
# nothing here was fitted to the test period.
D_SMAS = (20, 50, 200)
VIX_ANCHOR = 15.0

# ── strategy E constants (see the ported-SmartRisk note further down) ────────
LADDER = {0: 0.0, 1: 0.0, 2: 0.5, 3: 1.0, 4: 1.5, 5: 2.0, 6: 2.5, 7: 3.0}
PANIC_TERM_RATIO = 1.15      # VIX/VIX3M at or above this -> full exit
BEAR_PRICE_CAP = 0.5         # close < SMA200 -> at most half
REBAL_BAND = 0.25            # |target - actual| must exceed this to trade
REBAL_COOLDOWN = 5           # ...and this many days since the last trade


# ── primitives ───────────────────────────────────────────────────────────────
def sma(closes: list[float], n: int) -> float | None:
    return statistics.fmean(closes[-n:]) if len(closes) >= n else None


def realized_vol(closes: list[float], n: int = VOL_WINDOW) -> float | None:
    """Annualised stdev of daily log-ish returns over the last n days."""
    if len(closes) < n + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - n, len(closes))]
    if len(rets) < 2:
        return None
    sd = statistics.stdev(rets)
    return sd * (TRADING_DAYS ** 0.5)


def drawdown(closes: list[float], lookback: int = DD_LOOKBACK) -> float:
    """Current price vs the trailing peak, as a negative fraction."""
    window = closes[-lookback:] if len(closes) > lookback else closes
    peak = max(window)
    return closes[-1] / peak - 1 if peak > 0 else 0.0


def max_drawdown(curve: list[float]) -> float:
    peak, worst = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


# ── rules: closes-so-far -> target exposure in [0, 1] ────────────────────────
def rule_hold(_closes, **_kw) -> float:
    return 1.0


def rule_vol_throttle(closes: list[float], **_kw) -> float:
    """Conditional volatility targeting.

    Conventional (always-on) vol targeting does not reliably help in equities;
    the version that does is conditional — it only cuts in genuinely extreme
    volatility states. So below the trigger percentile this returns full exposure.
    """
    rv = realized_vol(closes)
    if rv is None or rv <= 0:
        return 1.0
    hist = [realized_vol(closes[: i + 1]) for i in range(VOL_WINDOW, len(closes))]
    hist = [h for h in hist if h]
    if len(hist) < 60:
        return 1.0
    trigger = _percentile(hist, VOL_PCTL_TRIGGER)
    if rv <= trigger:
        return 1.0
    target = statistics.median(hist)
    return max(0.0, min(1.0, target / rv))


def rule_peer_relative(closes: list[float], *, peers: list[list[float]] = (), **_kw) -> float:
    """Cut only when THIS name is falling harder than its cohort.

    An indiscriminate sector-wide selloff leaves the excess near zero, so this
    stays fully invested through it — which is the intended behaviour if such
    selloffs mean-revert. It fires when the damage is idiosyncratic.
    """
    if not peers:
        return 1.0
    own = drawdown(closes)
    peer_dds = [drawdown(p) for p in peers if len(p) > 1]
    if not peer_dds:
        return 1.0
    return 0.0 if (own - statistics.median(peer_dds)) < PEER_EXCESS_TRIGGER else 1.0


def rule_trend(closes: list[float], **_kw) -> float:
    m = sma(closes, SMA_WINDOW)
    return 1.0 if m is None or closes[-1] > m else 0.0


def rule_triple_ma_vix(closes: list[float], *, vix: list[float] = (), **_kw) -> float:
    """Strategy D — three-MA trend quality, throttled by market-wide VIX."""
    held = [1.0 for n in D_SMAS if (m := sma(closes, n)) is not None and closes[-1] > m]
    known = [n for n in D_SMAS if sma(closes, n) is not None]
    trend = (len(held) / len(known)) if known else 1.0
    mult = 1.0
    if len(vix) and vix[-1] and vix[-1] > 0:
        mult = min(1.0, VIX_ANCHOR / vix[-1])
    return max(0.0, min(1.0, trend * mult))


RULES = {"BH": rule_hold, "A_vol_throttle": rule_vol_throttle,
         "B_peer_relative": rule_peer_relative, "C_trend_sma200": rule_trend,
         "D_3ma_vix": rule_triple_ma_vix, "E_smartrisk": None}


# ── causal signal series ─────────────────────────────────────────────────────
# The scalar rules above are the readable reference implementation, but calling
# them once per day re-derives the whole history every time (O(n^2)). These build
# the same series in one causal pass; `test_series_match_scalar_rules` pins them
# to the reference so the fast path can never silently drift from it.
def _rolling_vol_series(closes: list[float], n: int = VOL_WINDOW) -> list[float | None]:
    rets = [0.0] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    out: list[float | None] = [None] * len(closes)
    for i in range(n, len(closes)):
        out[i] = statistics.stdev(rets[i - n + 1: i + 1]) * (TRADING_DAYS ** 0.5)
    return out


def series_vol_throttle(closes: list[float]) -> list[float]:
    rv = _rolling_vol_series(closes)
    seen: list[float] = []
    out = []
    for i in range(len(closes)):
        if rv[i] is not None:
            seen.append(rv[i])
        cur = rv[i]
        if cur is None or cur <= 0 or len(seen) < 60:
            out.append(1.0)
            continue
        hist = seen          # inclusive of today, matching the scalar reference
        trigger = _percentile(hist, VOL_PCTL_TRIGGER)
        out.append(1.0 if cur <= trigger
                   else max(0.0, min(1.0, statistics.median(hist) / cur)))
    return out


def series_triple_ma_vix(closes: list[float], vix: list[float]) -> list[float]:
    mas = {n: [statistics.fmean(closes[i - n + 1: i + 1]) if i >= n - 1 else None
               for i in range(len(closes))] for n in D_SMAS}
    out = []
    for i, c in enumerate(closes):
        known = [n for n in D_SMAS if mas[n][i] is not None]
        held = [n for n in known if c > mas[n][i]]
        trend = (len(held) / len(known)) if known else 1.0
        v = vix[i] if i < len(vix) else None
        mult = min(1.0, VIX_ANCHOR / v) if v and v > 0 else 1.0
        out.append(max(0.0, min(1.0, trend * mult)))
    return out


def series_trend(closes: list[float]) -> list[float]:
    out, run = [], 0.0
    for i, c in enumerate(closes):
        if i >= SMA_WINDOW - 1:
            run = statistics.fmean(closes[i - SMA_WINDOW + 1: i + 1])
            out.append(1.0 if c > run else 0.0)
        else:
            out.append(1.0)
    return out


def _dd_series(closes: list[float], lookback: int = DD_LOOKBACK) -> list[float]:
    out = []
    for i in range(len(closes)):
        w = closes[max(0, i - lookback + 1): i + 1]
        pk = max(w)
        out.append(closes[i] / pk - 1 if pk > 0 else 0.0)
    return out


def build_series(prices: dict[str, list[float]], syms: list[str], rule_name: str,
                 *, market: dict[str, list[float]] | None = None
                 ) -> dict[str, list[float]]:
    """Per-symbol exposure series, causal by construction."""
    if rule_name == "BH":
        return {s: [1.0] * len(prices[s]) for s in syms}
    if rule_name == "A_vol_throttle":
        return {s: series_vol_throttle(prices[s]) for s in syms}
    if rule_name == "C_trend_sma200":
        return {s: series_trend(prices[s]) for s in syms}
    if rule_name == "D_3ma_vix":
        vix = (market or {}).get("VIX", [])
        return {s: series_triple_ma_vix(prices[s], vix) for s in syms}
    if rule_name == "E_smartrisk":
        m = market or {}
        return {s: series_smartrisk_equity(prices[s], m.get("VIX", []),
                                           m.get("VIX3M", [])) for s in syms}
    if rule_name == "B_peer_relative":
        dds = {s: _dd_series(prices[s]) for s in syms}
        out = {}
        for s in syms:
            others = [o for o in syms if o != s]
            out[s] = [0.0 if (dds[s][i] - statistics.median([dds[o][i] for o in others]))
                      < PEER_EXCESS_TRIGGER else 1.0 for i in range(len(prices[s]))]
        return out
    raise KeyError(rule_name)


def _percentile(values: list[float], pct: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * pct / 100.0
    lo = int(k)
    return s[lo] if lo + 1 >= len(s) else s[lo] + (s[lo + 1] - s[lo]) * (k - lo)


# ── engine ───────────────────────────────────────────────────────────────────
@dataclass
class Result:
    name: str
    dates: list = field(default_factory=list)
    curve: list[float] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)   # avg equity exposure
    rets: list[float] = field(default_factory=list)       # daily portfolio returns
    rf: list[float] = field(default_factory=list)         # daily reserve (risk-free) returns
    trades: int = 0
    cost_paid: float = 0.0


def run(dates, prices: dict[str, list[float]], *, equity: dict[str, float],
        reserve: tuple[str, float], rule_name: str, start_idx: int,
        cost_bps: float = COST_BPS,
        market: dict[str, list[float]] | None = None,
        rebalance_band: float = 0.0, cooldown: int = REBAL_COOLDOWN) -> Result:
    """Replay one rule.

    `equity` maps symbol -> target weight; `reserve` is (symbol, weight) for the
    cash-like sleeve that freed capital flows into. Exposure for day i is decided
    on closes[..i] and earns the day i->i+1 return.
    """
    res_sym, res_w = reserve
    syms = list(equity)
    sig = build_series(prices, syms, rule_name, market=market)
    if rebalance_band:
        sig = {s: apply_rebalance(v, rebalance_band, cooldown) for s, v in sig.items()}
    nav, prev_exp = 1.0, {s: 1.0 for s in syms}
    res = Result(name=rule_name)

    for i in range(start_idx, len(dates) - 1):
        exp = {s: sig[s][i] for s in syms}      # decided on closes[..i]

        turnover = sum(abs(exp[s] - prev_exp[s]) * equity[s] for s in syms)
        cost = turnover * cost_bps / 10_000.0
        res.cost_paid += cost * nav
        if turnover > 1e-9:
            res.trades += sum(1 for s in syms if abs(exp[s] - prev_exp[s]) > 1e-9)

        ret = 0.0
        idle = 0.0
        for s in syms:
            r = prices[s][i + 1] / prices[s][i] - 1
            ret += equity[s] * exp[s] * r
            idle += equity[s] * (1 - exp[s])
        res_r = prices[res_sym][i + 1] / prices[res_sym][i] - 1
        ret += (res_w + idle) * res_r          # freed capital parks in the reserve

        nav *= (1 + ret - cost)
        res.dates.append(dates[i + 1])
        res.curve.append(nav)
        res.rets.append(ret - cost)
        res.rf.append(res_r)
        res.exposure.append(sum(equity[s] * exp[s] for s in syms) / sum(equity.values()))
        prev_exp = exp
    return res


def sharpe(rets: list[float], rf: list[float]) -> float | None:
    """Annualised Sharpe over the reserve asset's own return.

    Using SGOV (the sleeve the strategy actually parks in) as the risk-free leg
    keeps the ratio internally consistent — the alternative the portfolio really
    had, not an external T-bill series that never touched this book.
    """
    if len(rets) < 3:
        return None
    ex = [r - f for r, f in zip(rets, rf)]
    sd = statistics.stdev(ex)
    if sd == 0:
        return None
    return statistics.fmean(ex) / sd * (TRADING_DAYS ** 0.5)


# ── strategy E: SmartRisk ported to an unlevered equity book ─────────────────
# Pre-registered 2026-07-31 (variant #3), BEFORE its first run.
#
# Source: the user's own backtested LEAPS series V1->V4 (QQQ/SPY). What survived
# all four versions unchanged is the core, so that is what gets ported:
#   · the 7-point momentum score (V1, never modified through V4)
#   · the score->exposure ladder
#   · Vol Target  min(2, 15/VIX)
#   · rebalance only when |target-actual| > 25% AND >= 5 days since last trade
# V4 then adds Tier 1 Panic (VIX term structure) and Tier 2 Bear (SMA200).
#
# TWO DELIBERATE DEPARTURES, both because this book is unlevered stock, not LEAPS:
#   1. Vega Guard is NOT ported. Its rationale is long-call positive vega and the
#      vol crush that follows a spike; a share of stock has no vega, so the
#      mechanism that made it V3's biggest profit source simply does not exist.
#   2. Every "cap 1.0x" rule becomes a NO-OP, because 1.0x IS the ceiling without
#      leverage. Only two of V4's five tier rules still bite: panic->0 and
#      price<SMA200 -> cap 0.5. This is reported, not hidden — it is the honest
#      answer to "does it still apply to single stocks".


def momentum_score_7(closes: list[float], i: int) -> int:
    """V1's 7-point score, unchanged through V4.

    The last two terms (vs 20d/60d ago) were added by the user specifically to
    offset SMA lag — which is exactly the weakness that made a plain SMA200 rule
    useless in the fast July-2026 selloff.
    """
    c = closes[i]
    s20 = statistics.fmean(closes[i - 19: i + 1]) if i >= 19 else None
    s50 = statistics.fmean(closes[i - 49: i + 1]) if i >= 49 else None
    s200 = statistics.fmean(closes[i - 199: i + 1]) if i >= 199 else None
    score = 0
    for m in (s20, s50, s200):
        if m is not None and c > m:
            score += 1
    if s20 is not None and s50 is not None and s20 > s50:
        score += 1
    if s50 is not None and s200 is not None and s50 > s200:
        score += 1
    if i >= 20 and c > closes[i - 20]:
        score += 1
    if i >= 60 and c > closes[i - 60]:
        score += 1
    return score


def series_smartrisk_equity(closes: list[float], vix: list[float],
                            vix3m: list[float]) -> list[float]:
    out = []
    for i in range(len(closes)):
        tgt = min(1.0, LADDER[momentum_score_7(closes, i)])       # unlevered cap
        v = vix[i] if i < len(vix) else None
        if v and v > 0:                                            # Tier 3
            tgt *= min(1.0, VIX_ANCHOR / v)
        v3 = vix3m[i] if i < len(vix3m) else None
        if v and v3 and v3 > 0 and (v / v3) >= PANIC_TERM_RATIO:   # Tier 1
            tgt = 0.0
        s200 = statistics.fmean(closes[i - 199: i + 1]) if i >= 199 else None
        if s200 is not None and closes[i] < s200:                  # Tier 2
            tgt = min(tgt, BEAR_PRICE_CAP)
        out.append(max(0.0, min(1.0, tgt)))
    return out


def apply_rebalance(target: list[float], band: float = REBAL_BAND,
                    cooldown: int = REBAL_COOLDOWN) -> list[float]:
    """Turn a continuously-varying target into what you would actually hold.

    This is the piece strategy D lacked, and why it churned 2320 times: without a
    deadband every daily VIX wiggle became a trade. Causal by construction —
    actual[i] depends only on actual[i-1] and target[i].
    """
    actual, held, last = [], target[0] if target else 0.0, -10**9
    for i, t in enumerate(target):
        if abs(t - held) > band and (i - last) >= cooldown:
            held, last = t, i
        actual.append(held)
    return actual
