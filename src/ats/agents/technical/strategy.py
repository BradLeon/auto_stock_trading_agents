"""Strategy 甲 — the single source of truth for the timing/exposure rule.

This module is deliberately on the PRODUCTION side even though its first user was
the backtester: `src/ats/research/replay.py` imports from here, so the rule that
was validated over 7.5 years is byte-for-byte the rule that runs live. The
alternative — a copy in each place — is the exact bug class that already bit this
repo once (the macro LLM coercion existed only in agents/pead/outputs.py, so the
macro review silently served last week's data for a full cycle).

Provenance: ported from the user's own backtested LEAPS series V1→V4 (QQQ/SPY),
`signals/momentum.py`. Copied verbatim: the 7-point momentum score, the ladder,
and sigma = min(2, 15/VIX). The only change is the exposure ceiling — 3.0x for a
LEAPS book, 1.0 for unlevered stock.

Everything here is pure: no I/O, no config reads, no clock. Callers supply the
data and the parameters.
"""

from __future__ import annotations

import statistics

# ── defaults (fallback only) ─────────────────────────────────────────────────
# The authoritative values live in config/technical.yaml. Per the convention
# documented at config.py:396-400, a value must have exactly ONE authoritative
# default home — so `load_technical_config` does NOT setdefault these, it merges
# against this dict key-by-key.
LADDER: dict[int, float] = {0: 0.0, 1: 0.0, 2: 0.5, 3: 1.0,
                            4: 1.5, 5: 2.0, 6: 2.5, 7: 3.0}
VIX_ANCHOR = 15.0            # sigma = min(SIGMA_CAP, VIX_ANCHOR / VIX)
SIGMA_CAP = 2.0
PANIC_TERM_RATIO = 1.15      # VIX/VIX3M at or above this -> full exit (Tier 1)
BEAR_PRICE_CAP = 0.5         # close < SMA200 -> at most half (Tier 2)
BEAR_SLOPE_LOOKBACK = 20     # sessions back for the SMA200-declining check (Tier 2, gated variant)
REBAL_BAND = 0.25            # |target - held| must exceed this to act
REBAL_COOLDOWN = 5           # ...and this many sessions since the last change

MODES = ("jia", "yi", "bing")
DEFAULT_MODE = "jia"


def momentum_score_7(closes: list[float], i: int) -> int:
    """The 7-point score, unchanged from V1 through V4.

    Five "three moving average" terms plus two raw-momentum terms. The user added
    the last two (vs 20d/60d ago) specifically to offset SMA lag — which is what
    made a plain SMA200 rule useless in the fast July-2026 selloff.
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


def score_detail(closes: list[float], i: int) -> dict:
    """The seven component flags plus the moving averages, for auditability.

    A bare 0-7 score is not checkable after the fact; the Chief (and a human
    reading the report) needs to see WHICH conditions held.
    """
    c = closes[i]
    s20 = statistics.fmean(closes[i - 19: i + 1]) if i >= 19 else None
    s50 = statistics.fmean(closes[i - 49: i + 1]) if i >= 49 else None
    s200 = statistics.fmean(closes[i - 199: i + 1]) if i >= 199 else None
    return {
        "close": c, "sma20": s20, "sma50": s50, "sma200": s200,
        "above_sma20": bool(s20 is not None and c > s20),
        "above_sma50": bool(s50 is not None and c > s50),
        "above_sma200": bool(s200 is not None and c > s200),
        "sma20_gt_sma50": bool(s20 is not None and s50 is not None and s20 > s50),
        "sma50_gt_sma200": bool(s50 is not None and s200 is not None and s50 > s200),
        "above_20d_ago": bool(i >= 20 and c > closes[i - 20]),
        "above_60d_ago": bool(i >= 60 and c > closes[i - 60]),
    }


# ── the three unlevered readings ─────────────────────────────────────────────
#   jia  faithful   E = min(1.0, L*sigma)        ceiling only  ← CHOSEN
#   yi   rescaled   E = min(1.0, (L/3)*sigma)    3.0x means "fully invested"
#   bing separated  E = min(1,L) * min(1,sigma)  score sets base, VIX discounts
#
# 甲 was selected on a 7.5-year replay (2019-01→2026-07): the only one of the
# three whose TIMING contribution was positive on return, drawdown, Sharpe and
# Calmar against a static same-average-exposure control. See docs/TECHNICAL_ANALYST.md.
def exposure_from(score: int, vix: float | None, mode: str = DEFAULT_MODE, *,
                  ladder: dict[int, float] | None = None,
                  vix_anchor: float = VIX_ANCHOR,
                  sigma_cap: float = SIGMA_CAP) -> float:
    """Base exposure in [0, 1], before the Tier overlays."""
    lad = ladder or LADDER
    raw = lad[score]
    if raw == 0.0:          # short-circuit, mirroring the original momentum.py
        return 0.0
    sigma = min(sigma_cap, vix_anchor / vix) if vix and vix > 0 else 1.0
    if mode == "jia":
        e = min(1.0, raw * sigma)
    elif mode == "yi":
        e = min(1.0, (raw / 3.0) * sigma)
    elif mode == "bing":
        e = min(1.0, raw) * min(1.0, sigma)
    else:
        raise KeyError(mode)
    return max(0.0, min(1.0, e))


def sma200_prior(closes: list[float], i: int, lookback: int = BEAR_SLOPE_LOOKBACK
                 ) -> float | None:
    """SMA200 as evaluated `lookback` sessions before i. None if not enough history.

    Only used by the `bear_requires_declining` variant of Tier 2 below.
    """
    j = i - lookback
    return statistics.fmean(closes[j - 199: j + 1]) if j >= 199 else None


def apply_tiers(exposure: float, *, close: float, sma200: float | None,
                vix: float | None, vix3m: float | None,
                panic_ratio: float = PANIC_TERM_RATIO,
                bear_cap: float = BEAR_PRICE_CAP,
                sma200_prior_value: float | None = None,
                bear_requires_declining: bool = False) -> tuple[float, bool, bool]:
    """Tier 1 panic + Tier 2 bear. Returns (exposure, panic_fired, bear_fired).

    Tier 1 is SKIPPED when VIX3M is missing rather than approximated: a stale
    VIX3M divided into a spiking VIX manufactures an inverted term structure on
    exactly the days the signal matters. Yahoo's ^VIX3M has real gaps.

    Tier 2 has two readings, both traceable to the user's own LEAPS series:
      · default (bear_requires_declining=False): price < SMA200 alone caps
        exposure. This is what 甲 has run since inception.
      · bear_requires_declining=True: matches `leaps_smartrisk`'s Tier 2 — the
        cap only fires when SMA200 ITSELF is declining vs BEAR_SLOPE_LOOKBACK
        sessions ago AND price < SMA200. A brief dip below a still-rising
        SMA200 (a pullback inside an uptrend) does not trigger it; only a
        genuinely rolling-over 200-day trend does. Needs `sma200_prior_value`
        (see `sma200_prior`); if that history isn't available yet this
        reading does not fire — conservative, matching the source strategy
        returning cap=None on missing history.
    """
    panic = bool(vix and vix3m and vix3m > 0 and (vix / vix3m) >= panic_ratio)
    if panic:
        exposure = 0.0
    if bear_requires_declining:
        bear = bool(sma200 is not None and close < sma200
                    and sma200_prior_value is not None and sma200 < sma200_prior_value)
    else:
        bear = bool(sma200 is not None and close < sma200)
    if bear:
        exposure = min(exposure, bear_cap)
    return max(0.0, min(1.0, exposure)), panic, bear


def series_momentum_vol(closes: list[float], vix: list[float],
                        vix3m: list[float], mode: str = DEFAULT_MODE, *,
                        bear_requires_declining: bool = False) -> list[float]:
    """Full per-name exposure series — used by the backtester.

    `bear_requires_declining` selects the `leaps_smartrisk`-style Tier 2 (see
    `apply_tiers`); default False reproduces 甲's deployed behaviour unchanged.
    """
    out = []
    for i in range(len(closes)):
        v = vix[i] if i < len(vix) else None
        e = exposure_from(momentum_score_7(closes, i), v, mode)
        s200 = statistics.fmean(closes[i - 199: i + 1]) if i >= 199 else None
        s200_prior = (sma200_prior(closes, i) if bear_requires_declining else None)
        v3 = vix3m[i] if i < len(vix3m) else None
        e, _, _ = apply_tiers(e, close=closes[i], sma200=s200, vix=v, vix3m=v3,
                              sma200_prior_value=s200_prior,
                              bear_requires_declining=bear_requires_declining)
        out.append(e)
    return out


def apply_rebalance(target: list[float], band: float = REBAL_BAND,
                    cooldown: int = REBAL_COOLDOWN) -> list[float]:
    """Turn a continuously-varying target into what you would actually hold.

    Without this deadband every daily VIX wiggle becomes a trade — an earlier
    variant churned 2320 times over 1.6 years. Causal by construction: actual[i]
    depends only on actual[i-1] and target[i].
    """
    actual, held, last = [], target[0] if target else 0.0, -10**9
    for i, t in enumerate(target):
        if abs(t - held) > band and (i - last) >= cooldown:
            held, last = t, i
        actual.append(held)
    return actual


def params_fingerprint(mode: str, ladder: dict[int, float], vix_anchor: float,
                       panic_ratio: float, bear_cap: float) -> str:
    """Short stamp recorded on every reading, so a stored number can always be
    traced back to the exact rule version that produced it."""
    lad = ",".join(f"{k}:{v:g}" for k, v in sorted(ladder.items()))
    return f"{mode}|L[{lad}]|vix{vix_anchor:g}|panic{panic_ratio:g}|bear{bear_cap:g}"
