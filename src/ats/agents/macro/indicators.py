"""Turn raw macro series into level + change + distributional position.

Pure functions, no I/O: everything here takes an already-fetched series so the
whole layer is testable against synthetic data with no network and no pandas.

Why this module exists at all — a 10y real yield of 2.0% is by itself neither
high nor low. What carries information is where it moved from (Δ) and how
unusual the level is (z-score / percentile). The old macro path kept only the
latest scalar, which is why the LLM could do no better than read numbers back.
See docs/MACRO_ANALYST.md §4.

Lookbacks are by CALENDAR DATE, not by row position: the same code has to work
for a daily yield, a weekly claims print and a monthly PCE release, and only a
date-based window means the same thing across all three.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from ...schemas.macro_strategy import IndicatorReading

# How long an observation may go without an update before we call it stale.
# Monthly tolerances have to be generous: FRED dates a monthly series to the
# FIRST of its reference month and the print lands about a month after the month
# ends, so on 30 Jul the newest legitimate core-PCE observation can still be
# dated 1 May — 90 days old and perfectly current. A tighter bound would mark
# healthy series stale every month, which trains you to ignore the flag.
_STALE_DAYS = {"daily": 7, "weekly": 21, "monthly": 95, "quarterly": 250}

_Z_WINDOW_DAYS = 365 * 3      # z-score vs trailing 3 years
_PCT_WINDOW_DAYS = 365 * 10   # percentile vs trailing 10 years


def as_points(series) -> list[tuple[date, float]]:
    """Normalise a pandas Series (or a list of pairs) to sorted (date, value).

    Accepting both is deliberate: production passes pandas, tests pass literal
    lists — so the tests need neither pandas nor a fixture factory.
    """
    if series is None:
        return []
    items = series.items() if hasattr(series, "items") else series
    out: list[tuple[date, float]] = []
    for idx, val in items:
        if val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        if fval != fval:      # NaN
            continue
        out.append((_to_date(idx), fval))
    out.sort(key=lambda p: p[0])
    return out


def _to_date(idx) -> date:
    if isinstance(idx, date) and not hasattr(idx, "hour"):
        return idx
    for attr in ("date",):                 # datetime / pandas Timestamp
        fn = getattr(idx, attr, None)
        if callable(fn):
            return fn()
    return date.fromisoformat(str(idx)[:10])


def value_asof(points: list[tuple[date, float]], target: date) -> float | None:
    """Last observation at or before `target` (None if the series starts later)."""
    val = None
    for d, v in points:
        if d <= target:
            val = v
        else:
            break
    return val


def _window(points: list[tuple[date, float]], days: int, end: date) -> list[float]:
    start = end - timedelta(days=days)
    return [v for d, v in points if start <= d <= end]


def _change(level: float, prior: float | None, unit: str) -> float | None:
    """Yields/spreads in bp, prices/indices in %, zero-centred series in raw diff.

    The `level` branch is not a stylistic choice: a percent change on a series
    that sits near zero and flips sign is division noise. CFNAI at -0.02 reported
    a "-89.5%" monthly move, which says nothing about the economy.
    """
    if prior is None:
        return None
    if unit == "pct":
        return round((level - prior) * 100, 1)
    if unit == "level":
        return round(level - prior, 3)
    if prior == 0:
        return None
    return round((level / prior - 1) * 100, 2)


def zscore(values: list[float], level: float) -> float | None:
    if len(values) < 2:
        return None
    sd = statistics.stdev(values)
    if sd == 0:
        return None
    return round((level - statistics.fmean(values)) / sd, 2)


def percentile(values: list[float], level: float) -> float | None:
    if not values:
        return None
    below = sum(1 for v in values if v <= level)
    return round(100.0 * below / len(values), 1)


def moving_average(points: list[tuple[date, float]], n: int) -> float | None:
    """Mean of the last n observations — claims are unusable un-smoothed."""
    if len(points) < n or n <= 0:
        return None
    return statistics.fmean(v for _d, v in points[-n:])


def change_z(points: list[tuple[date, float]], days: int, *,
             window_days: int = _Z_WINDOW_DAYS) -> float | None:
    """z-score of the latest `days`-change against that change's own history.

    Distinguishing a shock from a trend (docs/MACRO_ANALYST.md §5.3) needs the
    distribution of CHANGES, not of levels: a 30bp monthly move is ordinary for
    some series and a three-sigma event for others, and the level's z-score
    cannot tell you which.
    """
    if len(points) < 3:
        return None
    end = points[-1][0]
    start = end - timedelta(days=window_days)
    deltas = [v - prior for d, v in points
              if d >= start and (prior := value_asof(points, d - timedelta(days=days))) is not None]
    if len(deltas) < 3:
        return None
    latest = deltas[-1]
    sd = statistics.stdev(deltas)
    if sd == 0:
        return None
    return round((latest - statistics.fmean(deltas)) / sd, 2)


def reading(key: str, series, *, label: str = "", unit: str = "pct",
            source: str = "", freq: str = "daily",
            as_of: date | None = None) -> IndicatorReading:
    """Build one IndicatorReading. Missing/short series degrade to None fields."""
    points = as_points(series)
    if not points:
        return IndicatorReading(key=key, label=label, unit=unit, source=source,
                                stale=True)
    last_date, level = points[-1]
    ref = as_of or last_date
    return IndicatorReading(
        key=key, label=label, unit=unit, source=source, level=round(level, 4),
        d_1w=_change(level, value_asof(points, ref - timedelta(days=7)), unit),
        d_1m=_change(level, value_asof(points, ref - timedelta(days=30)), unit),
        d_3m=_change(level, value_asof(points, ref - timedelta(days=90)), unit),
        z_3y=zscore(_window(points, _Z_WINDOW_DAYS, last_date), level),
        pct_10y=percentile(_window(points, _PCT_WINDOW_DAYS, last_date), level),
        as_of=last_date,
        stale=(ref - last_date).days > _STALE_DAYS.get(freq, 7),
    )


def build_readings(series_by_key: dict, spec: dict,
                   *, as_of: date | None = None) -> list[IndicatorReading]:
    """Readings for every series we actually got, in `spec` order.

    `spec` is data.macro.series_spec(): key -> (code, label, unit, freq).
    A key missing from `series_by_key` yields a stale empty reading rather than
    vanishing — a dropped feed should be visible in the report, not silent.
    """
    out: list[IndicatorReading] = []
    for key, (code, label, unit, freq) in spec.items():
        prefix = "yf" if code.endswith("=F") or code.startswith("^") or "-" in code else "fred"
        out.append(reading(key, series_by_key.get(key), label=label, unit=unit,
                           source=f"{prefix}:{code}", freq=freq, as_of=as_of))
    return out
