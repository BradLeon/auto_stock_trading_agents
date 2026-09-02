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

from datetime import datetime
import logging

from .base import safe_fetch

name = "consensus"
log = logging.getLogger("ats.data.consensus")

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


def _legacy_fetch(symbol: str) -> dict:
    out: dict = {"eps": None, "revenue": None, "eps_low": None, "eps_high": None,
                 "revenue_low": None, "revenue_high": None, **_ANALYST_DEFAULTS}
    est = safe_fetch(lambda: _yf_consensus(symbol), source=f"{name}:{symbol}")
    if est:
        out.update(est)
    an = safe_fetch(lambda: _yf_analyst(symbol), source=f"{name}-analyst:{symbol}")
    if an:
        out.update(an)
    return out


def _has_values(value: dict) -> bool:
    return any(item is not None and item != [] for item in value.values())


def _comparison_signature(value: dict) -> dict:
    return {key: value.get(key) for key in (
        "eps", "revenue", "eps_low", "eps_high", "revenue_low", "revenue_high",
        "target_mean", "target_median", "target_low", "target_high",
        "rating_strong_buy", "rating_buy", "rating_hold", "rating_sell",
        "rating_strong_sell")}


def _record_shadow_comparison(*, consumer: str, symbol: str, legacy: dict,
                              platform: dict, matched: bool, reason: str) -> None:
    """Persist provider-snapshot evidence without changing a legacy response.

    Analyst consensus is a point-in-time provider snapshot, not a company filing.
    Different analyst universes and later provider revisions are expected to move
    numbers.  A valid governed snapshot from the registered provider is therefore
    accepted as a new snapshot even when a same-run legacy pull differs; the raw
    values, provider and comparison outcome stay in the audit record.
    """
    try:
        from .cutover import record_consumer_comparison
        from .runtime import platform_data_db_path
        provider_snapshot = _has_values(platform)
        record_consumer_comparison(
            consumer=consumer, entity=symbol, data_db=platform_data_db_path(),
            status="reconciled" if matched or provider_snapshot else "mismatch",
            details={"input": "market_consensus",
                     "reason": ("identical_provider_snapshot" if matched else
                                "registered_provider_snapshot_revision" if provider_snapshot
                                else reason),
                     "reconciliation": {
                         "kind": ("exact" if matched else
                                  "authoritative_provider_snapshot" if provider_snapshot
                                  else "platform_failure"),
                         "matched": matched,
                         "source_id": "yfinance_consensus",
                         "provider": "Yahoo Finance via yfinance",
                         "policy": "provider_snapshot_may_differ_by_analyst_universe_or_revision",
                     },
                     "legacy": _comparison_signature(legacy),
                     "platform": _comparison_signature(platform)},
        )
    except Exception as exc:  # observability must not break a Sector review
        log.warning("consensus: failed to record %s comparison for %s: %s", consumer, symbol, exc)


def _platform_fetch(symbol: str, *, consumer: str = "pead_consensus") -> dict:
    from ..data.products import DataProducts
    from ..data.runtime import get_platform_structured_repository
    from .structured import FetchRequest, IngestionPipeline
    from .sources.market_consensus import YFinanceConsensusAdapter

    repository = get_platform_structured_repository()
    try:
        request = FetchRequest(
            source_id="yfinance_consensus", dataset_id="market_consensus",
            entities=[symbol], query_scope={})
        result = IngestionPipeline(repository).run(YFinanceConsensusAdapter(), request)
        if result["status"] not in {"succeeded", "partial", "no_change"}:
            return {"eps": None, "revenue": None, "eps_low": None, "eps_high": None,
                    "revenue_low": None, "revenue_high": None, **_ANALYST_DEFAULTS}
        products = DataProducts(structured_repository=repository)
        snapshot = products.consensus_snapshot(entity=symbol)
        if snapshot["rows"]:
            products.snapshot_manifest(
                consumer=consumer, purpose=f"consensus:{symbol.upper()}",
                as_of=datetime.fromisoformat(snapshot["known_at"]), rows=snapshot["rows"],
                metadata={"symbol": symbol.upper(), "runtime_inputs_included": False})
        return products.consensus_legacy_dict(entity=symbol)
    finally:
        repository.close()


def fetch(symbol: str, *, consumer: str = "pead_consensus") -> dict:
    """Compatibility contract with independently reversible structured read modes."""
    from .structured import read_mode

    mode = read_mode(consumer, source_id="yfinance_consensus")
    platform_fetch = lambda: (_platform_fetch(symbol) if consumer == "pead_consensus"
                              else _platform_fetch(symbol, consumer=consumer))
    if mode == "legacy":
        return _legacy_fetch(symbol)
    if mode == "platform":
        return platform_fetch()
    if mode == "fallback":
        try:
            platform = platform_fetch()
        except Exception as exc:
            log.warning("consensus: platform fallback failed for %s: %s", symbol, exc)
            return _legacy_fetch(symbol)
        return platform if _has_values(platform) else _legacy_fetch(symbol)
    legacy = _legacy_fetch(symbol)
    try:
        platform = platform_fetch()
    except Exception as exc:
        _record_shadow_comparison(
            consumer=consumer, symbol=symbol, legacy=legacy, platform={}, matched=False,
            reason=f"platform_failure:{type(exc).__name__}")
        log.warning("consensus: structured shadow refresh failed for %s: %s", symbol, exc)
        return legacy
    matched = _comparison_signature(legacy) == _comparison_signature(platform)
    _record_shadow_comparison(
        consumer=consumer, symbol=symbol, legacy=legacy, platform=platform, matched=matched,
        reason="identical_consensus_snapshot" if matched else "consensus_signature_mismatch")
    if not matched:
        log.info("consensus: accepted provider snapshot revision for %s", symbol)
    return legacy
