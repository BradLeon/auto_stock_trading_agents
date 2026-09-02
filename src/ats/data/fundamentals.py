"""Fundamental data: yfinance key metrics + recent SEC filings (EDGAR).

yfinance needs no key; SEC needs only a descriptive User-Agent (set in .env).
Both degrade to notes on failure.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from functools import lru_cache
import logging

from ..config import get_config
from ..schemas.fundamentals import Filing, FinancialStatements, FundamentalData, StatementMetric
from .base import safe_fetch

name = "fundamentals"
log = logging.getLogger("ats.data.fundamentals")

_METRIC_KEYS = {
    "market_cap": "marketCap",
    "trailing_pe": "trailingPE",
    "forward_pe": "forwardPE",
    "price_to_sales": "priceToSalesTrailing12Months",
    "profit_margin": "profitMargins",
    "revenue_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth",
    "free_cashflow": "freeCashflow",
    "dividend_yield": "dividendYield",
}
_FORMS = {"10-K", "10-Q", "8-K"}


def _legacy_fetch(symbol: str, *, include_statements: bool = True) -> FundamentalData:
    data = FundamentalData(symbol=symbol, as_of=datetime.now(timezone.utc))

    info = safe_fetch(lambda: _yf_info(symbol), source=f"yf-info:{symbol}")
    if info is None:
        data.notes.append("yfinance fundamentals unavailable")
    else:
        for field, key in _METRIC_KEYS.items():
            val = info.get(key)
            if isinstance(val, (int, float)):
                setattr(data, field, float(val))

    if include_statements:
        data.statements = safe_fetch(lambda: _statements(symbol), source=f"yf-stmt:{symbol}")
        if data.statements is None:
            data.notes.append("quarterly statements unavailable")

    filings = safe_fetch(lambda: _sec_filings(symbol), source=f"sec:{symbol}", attempts=2)
    if filings:
        data.recent_filings = filings
    elif filings is None:
        data.notes.append("SEC filings unavailable")
    return data


def fetch(symbol: str, *, consumer: str = "pead_fundamentals") -> FundamentalData:
    """Compatibility DTO with reversible legacy/shadow/platform/fallback reads."""
    from .structured import read_mode

    mode = read_mode(consumer, source_id="sec_companyfacts")
    if mode == "legacy":
        return _legacy_fetch(symbol)
    if mode == "platform":
        return _platform_fetch(symbol, consumer=consumer)
    if mode == "fallback":
        try:
            platform = _platform_fetch(symbol, consumer=consumer)
        except Exception as exc:
            log.warning("fundamentals: platform fallback failed for %s: %s", symbol, exc)
            return _legacy_fetch(symbol)
        return platform if platform.statements and platform.statements.lines \
            else _legacy_fetch(symbol)
    legacy = _legacy_fetch(symbol)
    try:
        platform = _platform_fetch(symbol, consumer=consumer)
    except Exception as exc:
        # Shadow is an observability mode, so a platform outage must never turn
        # into a PEAD availability outage while legacy remains healthy.
        legacy_signature = _statement_signature(legacy.statements)
        _record_shadow_comparison(
            consumer=consumer, symbol=symbol, matched=False,
            legacy_signature=legacy_signature, platform_signature=(),
            reconciliation={"matched": False, "kind": "platform_failure",
                            "reason": type(exc).__name__})
        log.warning("fundamentals: structured shadow refresh failed for %s: %s", symbol, exc)
        return legacy
    legacy_signature = _statement_signature(legacy.statements)
    platform_signature = _statement_signature(platform.statements)
    comparison = _reconcile_statements(legacy.statements, platform.statements)
    _record_shadow_comparison(
        consumer=consumer, symbol=symbol, matched=comparison["matched"],
        legacy_signature=legacy_signature, platform_signature=platform_signature,
        reconciliation=comparison)
    if not comparison["matched"]:
        log.warning("fundamentals: structured shadow mismatch for %s", symbol)
    return legacy


def _record_shadow_comparison(*, consumer: str, symbol: str, matched: bool,
                              legacy_signature: tuple, platform_signature: tuple,
                              reconciliation: dict | None = None) -> None:
    """Persist PEAD's DTO comparison; failure to audit never changes legacy output."""
    try:
        from .cutover import record_consumer_comparison
        from .runtime import platform_data_db_path

        record_consumer_comparison(
            consumer=consumer, entity=symbol, data_db=platform_data_db_path(),
            status="reconciled" if matched else "mismatch",
            details={"input": "fundamental_statements", "legacy": legacy_signature,
                     "platform": platform_signature,
                     "reconciliation": reconciliation or {}},
        )
    except Exception as exc:  # shadow audit is observability, never an availability risk
        log.warning("fundamentals: failed to record shadow comparison for %s: %s", symbol, exc)


def _yf_info(symbol: str) -> dict:
    import yfinance as yf
    from .base import yf_symbol

    info = yf.Ticker(yf_symbol(symbol)).get_info()
    if not info:
        raise ValueError(f"no info for {symbol}")
    return info


_LIGHT_KEYS = {"market_cap": "marketCap", "pe": "trailingPE", "fwd_pe": "forwardPE",
               "gross_margin": "grossMargins", "op_margin": "operatingMargins",
               "rev_growth": "revenueGrowth", "beta": "beta"}
_CONSTITUENT_ACCOUNTING_KEYS = ("gross_margin", "op_margin", "rev_growth")
_CONSTITUENT_RUNTIME_KEYS = ("market_cap", "pe", "fwd_pe", "beta")
SECTOR_CONSTITUENT_FINANCIALS = "sector_constituent_financials"


_LIGHT_CACHE: dict[str, tuple[float, dict]] = {}
_LIGHT_TTL = 1800.0        # in-process cache: dedupe repeat pulls within a run/hour


def _platform_constituent_accounting(symbol: str) -> dict:
    """Read low-frequency operating metrics from the selected report package.

    This deliberately does not refresh a Provider.  Sector reviews may call this
    for a wide universe, so report-package ingestion remains an explicit pipeline
    action.  Market capitalization, valuation and beta remain runtime inputs.
    """
    from ..data.products import DataProducts
    from ..data.runtime import get_platform_structured_repository

    repository = get_platform_structured_repository()
    try:
        package = None
        for source_id in _FINANCIAL_SOURCE_PRIORITY:
            package = _complete_report_package(repository, source_id=source_id,
                                                symbol=symbol.upper())
            if package is not None:
                break
        if package is None:
            package = _complete_report_package(
                repository, source_id=("sec_companyfacts", "company_disclosures"),
                symbol=symbol.upper())
        if package is None:
            return {"metrics": {}, "source": "", "report_period": ""}
        statement, _ = _structured_statements(
            symbol.upper(), DataProducts(structured_repository=repository),
            source_id=package["source_id"], report_period=package["period"],
            source_by_metric=package.get("source_by_metric"))
        if statement is None:
            return {"metrics": {}, "source": "", "report_period": ""}
        lines = {line.label: line for line in statement.lines}
        result = {}
        if lines.get("Gross Margin") and lines["Gross Margin"].value is not None:
            result["gross_margin"] = lines["Gross Margin"].value / 100
        if lines.get("Operating Margin") and lines["Operating Margin"].value is not None:
            result["op_margin"] = lines["Operating Margin"].value / 100
        if lines.get("Revenue") and lines["Revenue"].yoy is not None:
            result["rev_growth"] = lines["Revenue"].yoy / 100
        return {"metrics": result, "source": package["source_id"],
                "report_period": package["period"]}
    finally:
        repository.close()


def _platform_light(symbol: str) -> dict:
    """Compatibility view of governed accounting metrics without lineage metadata."""
    return _platform_constituent_accounting(symbol)["metrics"]


def fetch_light(symbol: str, *, consumer: str = "runtime_light") -> dict:
    """One-call valuation/margin/beta snapshot for wide-universe scans.
    Returns {market_cap, pe, fwd_pe, gross_margin, op_margin, rev_growth, beta} (None-filled).
    yfinance primary; finnhub fills gaps when yf rate-limits or lacks micro-cap
    coverage (the '数据缺失' source in the sector review). Cached, never raises."""
    import time as _t

    hit = _LIGHT_CACHE.get(symbol)
    if hit and _t.time() - hit[0] < _LIGHT_TTL:
        return dict(hit[1])

    out: dict = {k: None for k in _LIGHT_KEYS}
    info = safe_fetch(lambda: _yf_info(symbol), source=f"yf-light:{symbol}", attempts=2)
    if info:
        for field, key in _LIGHT_KEYS.items():
            val = info.get(key)
            if isinstance(val, (int, float)):
                out[field] = float(val)

    # finnhub fallback for any core gap (rate-limit / thin coverage)
    if any(out[k] is None for k in ("market_cap", "gross_margin", "rev_growth", "beta")):
        fh = safe_fetch(lambda: _finnhub_light(symbol), source=f"fh-light:{symbol}", attempts=1)
        if fh:
            for k, v in fh.items():
                if out.get(k) is None and v is not None:
                    out[k] = v

    if any(v is not None for v in out.values()):
        _LIGHT_CACHE[symbol] = (_t.time(), dict(out))
    from .structured import read_mode

    mode = read_mode(consumer)
    if mode == "legacy":
        return out
    try:
        governed = _platform_light(symbol)
    except Exception as exc:  # the runtime snapshot remains the declared fallback
        log.warning("fundamentals: governed light snapshot unavailable for %s: %s", symbol, exc)
        return out
    if mode == "shadow":
        if governed and any(out.get(key) != value for key, value in governed.items()):
            log.info("fundamentals: governed light shadow difference for %s", symbol)
        return out
    # Platform/fallback use governed low-frequency accounting fields first.  Fields
    # absent from a valid report package remain runtime values rather than being
    # fabricated as zero; market cap, P/E and beta are runtime by design.
    return {**out, **governed}


def fetch_constituent_financials(symbol: str, *,
                                 consumer: str = SECTOR_CONSTITUENT_FINANCIALS) -> dict:
    """Return one sector constituent's governed accounting plus runtime market view.

    This is deliberately a consumer view, not an industry-level dataset.  Its
    accounting metrics use exactly the same complete report-package selection as
    PEAD; market cap, valuation and beta remain transient runtime inputs.  In
    platform/fallback mode a missing report package is explicit and never filled
    from a Provider's TTM/web fields.
    """
    from .structured import read_mode

    # Preserve the established, rate-limited runtime query and legacy comparison
    # path without allowing its accounting fields to masquerade as platform data.
    legacy = fetch_light(symbol, consumer="runtime_light")
    runtime = {key: legacy.get(key) for key in _CONSTITUENT_RUNTIME_KEYS}
    mode = read_mode(consumer)
    if mode == "legacy":
        return {
            **legacy,
            "accounting_status": "legacy_provider",
            "accounting_source": "runtime_provider",
            "accounting_report_period": "",
        }

    try:
        governed_detail = _platform_constituent_accounting(symbol)
    except Exception as exc:  # no silent Provider accounting fallback
        log.warning("fundamentals: governed constituent accounting unavailable for %s: %s", symbol, exc)
        governed_detail = {"metrics": {}, "source": "", "report_period": ""}
    governed = governed_detail["metrics"]

    platform = {
        **runtime,
        **{key: governed.get(key) for key in _CONSTITUENT_ACCOUNTING_KEYS},
        "accounting_status": "covered" if governed else "no_coverage",
        "accounting_source": governed_detail["source"],
        "accounting_report_period": governed_detail["report_period"],
    }
    if mode == "shadow":
        if any(legacy.get(key) != platform.get(key) for key in _CONSTITUENT_ACCOUNTING_KEYS):
            log.info("fundamentals: constituent accounting shadow difference for %s", symbol)
        return {
            **legacy,
            "accounting_status": "legacy_provider",
            "accounting_source": "runtime_provider",
            "accounting_report_period": "",
        }
    if mode == "fallback" and not governed:
        return {
            **legacy,
            "accounting_status": "legacy_provider_fallback",
            "accounting_source": "runtime_provider",
            "accounting_report_period": "",
        }
    return platform


def _finnhub_light(symbol: str) -> dict:
    """Finnhub /stock/metric fallback for fetch_light. Finnhub returns market cap
    in $M and margins/growth in percent; normalize to fetch_light's units
    (market_cap in $, margins/growth as fractions) so the two sources are mixable."""
    import httpx

    key = get_config().secrets.finnhub_api_key
    if not key:
        raise ValueError("no FINNHUB_API_KEY")
    r = httpx.get("https://finnhub.io/api/v1/stock/metric", timeout=15,
                  params={"symbol": symbol, "metric": "all", "token": key})
    r.raise_for_status()
    m = (r.json() or {}).get("metric", {}) or {}

    def g(*keys):
        for k in keys:
            v = m.get(k)
            if isinstance(v, (int, float)) and v == v:
                return float(v)
        return None

    mc = g("marketCapitalization")
    gm = g("grossMarginTTM", "grossMarginAnnual")
    om = g("operatingMarginTTM", "operatingMarginAnnual")
    rg = g("revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy", "revenueGrowth5Y")
    return {
        "market_cap": mc * 1e6 if mc is not None else None,
        "pe": g("peTTM", "peBasicExclExtraTTM"),
        "fwd_pe": g("forwardPE"),                    # finnhub rarely has it -> None ok
        "gross_margin": gm / 100 if gm is not None else None,
        "op_margin": om / 100 if om is not None else None,
        "rev_growth": rg / 100 if rg is not None else None,
        "beta": g("beta"),
    }


# --------------------------------------------------------------------------- #
# Quarterly statements (income / balance / cash flow) with QoQ + YoY
# --------------------------------------------------------------------------- #
def _row(df, *candidates):
    """Latest, prior-quarter, and year-ago values for the first matching row."""
    if df is None or df.empty:
        return None, None, None
    for name in candidates:
        if name in df.index:
            cols = list(df.columns)  # descending: col0=latest
            vals = [df.loc[name, c] for c in cols]
            cur = _num(vals[0]) if len(vals) > 0 else None
            qoq = _num(vals[1]) if len(vals) > 1 else None
            yoy = _num(vals[4]) if len(vals) > 4 else None
            return cur, qoq, yoy
    return None, None, None


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _pct(cur, base):
    if cur is None or not base:
        return None
    if (cur < 0) != (base < 0):   # sign flip -> percentage change is not meaningful
        return None
    return round((cur / base - 1) * 100, 1)


def _dollar_metric(label, cur, prev, yago, *, unit: str = "$M"):
    return StatementMetric(label=label, value=round(cur / 1e6, 0) if cur is not None else None,
                           qoq=_pct(cur, prev), yoy=_pct(cur, yago), unit=unit, delta_unit="%")


def _statements(symbol: str) -> FinancialStatements:
    import yfinance as yf
    from .base import yf_symbol

    t = yf.Ticker(yf_symbol(symbol))
    inc, bs, cf = t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow
    if inc is None or inc.empty:
        raise ValueError(f"no quarterly statements for {symbol}")

    period = str(inc.columns[0])[:10]
    rev = _row(inc, "Total Revenue", "Operating Revenue")
    gp = _row(inc, "Gross Profit")
    op = _row(inc, "Operating Income", "Operating Income Or Loss")
    ni = _row(inc, "Net Income", "Net Income Common Stockholders")
    eps = _row(inc, "Diluted EPS", "Basic EPS")
    capex = _row(cf, "Capital Expenditure", "Capital Expenditures")
    fcf = _row(cf, "Free Cash Flow")
    debt = _row(bs, "Total Debt")

    lines = [_dollar_metric("Revenue", *rev)]
    lines.append(_margin("Gross Margin", gp, rev))
    lines.append(_margin("Operating Margin", op, rev))
    lines.append(_dollar_metric("Net Income", *ni))
    if eps[0] is not None:
        lines.append(StatementMetric(label="Diluted EPS", value=round(eps[0], 2),
                                     qoq=_pct(eps[0], eps[1]), yoy=_pct(eps[0], eps[2]), unit="$"))
    lines.append(_dollar_metric("CapEx", *capex))
    lines.append(_dollar_metric("Free Cash Flow", *fcf))
    lines.append(_dollar_metric("Total Debt", *debt))
    return FinancialStatements(period=period, lines=[ln for ln in lines if ln.value is not None])


def _margin(label, profit, rev):
    """Margin (%) with QoQ/YoY as percentage-point deltas."""
    def m(p, r):
        return round(p / r * 100, 1) if (p is not None and r) else None

    cur, qoq_v, yoy_v = m(profit[0], rev[0]), m(profit[1], rev[1]), m(profit[2], rev[2])
    return StatementMetric(label=label, value=cur,
                           qoq=round(cur - qoq_v, 1) if (cur is not None and qoq_v is not None) else None,
                           yoy=round(cur - yoy_v, 1) if (cur is not None and yoy_v is not None) else None,
                           unit="%", delta_unit="pp")


def _statement_signature(value: FinancialStatements | None) -> tuple:
    if value is None:
        return ()
    return (value.period, tuple(
        (line.label, line.value, line.qoq, line.yoy, line.unit, line.delta_unit)
        for line in value.lines))


_PEAD_CORE_STATEMENT_LINES = frozenset({
    "Revenue", "Gross Margin", "Operating Margin", "Net Income", "Diluted EPS",
    "CapEx", "Free Cash Flow", "Total Debt",
})

# A company-quarter is publishable only as one coherent report package.  These
# are the persistent source rows required to support the PEAD statement view and
# the standard income-statement, cash-flow and balance-sheet queries.  Margins
# and free cash flow are intentionally derived from these raw rows; P/E remains
# a runtime calculation because ticker price is not persistent data.
_FINANCIAL_SOURCE_PRIORITY = (
    "defeatbeta_stock_statement",
    "yfinance_financials",
    "sec_companyfacts",
    "company_disclosures",
)
_PACKAGE_QUARTER_METRICS = frozenset({
    "financial.revenue.gaap",
    "financial.gross_profit.gaap",
    "financial.operating_income.gaap",
    "financial.net_income.gaap",
    "financial.cash_from_operations.gaap",
    "financial.capex.gaap",
})
_PACKAGE_INSTANT_METRICS = frozenset({
    "financial.cash_and_equivalents.gaap",
    "financial.total_assets.gaap",
    "financial.total_liabilities.gaap",
    "financial.stockholders_equity.gaap",
})
_PACKAGE_EPS_METRICS = (
    "financial.eps.diluted.adr",
    "financial.eps.diluted.market_adjusted",
    "financial.eps.diluted.gaap",
)
_PACKAGE_DEBT_METRICS = (
    "financial.total_debt.gaap",
    "financial.total_debt.provider_reported",
)


def _reconcile_statements(legacy: FinancialStatements | None,
                          platform: FinancialStatements | None) -> dict:
    """Compare PEAD statement inputs without mistaking a current governed period
    for a legacy mismatch.

    The legacy fetch remains the returned DTO while in shadow mode.  A newer
    platform quarter can be accepted only when both sides contain PEAD's complete
    statement contract.  Missing fields, a stale platform period, or a different
    value for the same reported period remain release-blocking mismatches.
    """
    if _statement_signature(legacy) == _statement_signature(platform):
        return {"matched": True, "kind": "exact", "reason": "identical_statement_dto"}
    if legacy is None or platform is None:
        return {
            "matched": False, "kind": "mismatch",
            "reason": "statement_unavailable_on_one_side",
        }
    legacy_lines = {line.label for line in legacy.lines if line.value is not None}
    platform_lines = {line.label for line in platform.lines if line.value is not None}
    missing_legacy = sorted(_PEAD_CORE_STATEMENT_LINES - legacy_lines)
    missing_platform = sorted(_PEAD_CORE_STATEMENT_LINES - platform_lines)
    if missing_platform:
        return {
            "matched": False, "kind": "mismatch", "reason": "core_statement_incomplete",
            "missing_legacy": missing_legacy, "missing_platform": missing_platform,
        }
    if missing_legacy:
        if platform.period < legacy.period:
            return {
                "matched": False, "kind": "mismatch", "reason": "platform_period_older",
                "legacy_period": legacy.period, "platform_period": platform.period,
                "missing_legacy": missing_legacy,
            }
        return {
            "matched": True, "kind": "governed_availability_upgrade",
            "reason": "platform_complete_legacy_incomplete",
            "legacy_period": legacy.period, "platform_period": platform.period,
            "missing_legacy": missing_legacy,
            "value_difference_review_required": True,
        }
    if platform.period < legacy.period:
        return {
            "matched": False, "kind": "mismatch", "reason": "platform_period_older",
            "legacy_period": legacy.period, "platform_period": platform.period,
        }
    if platform.period > legacy.period:
        return {
            "matched": True, "kind": "governed_period_upgrade",
            "reason": "complete_current_platform_period",
            "legacy_period": legacy.period, "platform_period": platform.period,
            "value_difference_review_required": True,
        }

    legacy_values = {line.label: line.value for line in legacy.lines}
    platform_values = {line.label: line.value for line in platform.lines}
    changed_values = sorted(
        label for label in _PEAD_CORE_STATEMENT_LINES - {"Total Debt"}
        if legacy_values.get(label) != platform_values.get(label)
    )
    if changed_values:
        return {
            "matched": False, "kind": "mismatch", "reason": "same_period_core_value_difference",
            "period": platform.period, "changed_values": changed_values,
        }
    legacy_units = {line.label: line.unit for line in legacy.lines}
    platform_units = {line.label: line.unit for line in platform.lines}
    changed_units = sorted(
        label for label in _PEAD_CORE_STATEMENT_LINES
        if legacy_units.get(label) != platform_units.get(label)
    )
    debt_changed = legacy_values.get("Total Debt") != platform_values.get("Total Debt")
    return {
        "matched": True,
        "kind": "governed_semantic_upgrade",
        "reason": "same_period_unit_or_debt_definition_correction",
        "period": platform.period,
        "changed_units": changed_units,
        "debt_definition_changed": debt_changed,
    }


def _complete_report_package(repository, *, source_id: str | tuple[str, ...],
                             symbol: str) -> dict | None:
    """Return the newest complete quarterly package from one source or official bundle.

    This deliberately reads all raw accepted observations from one source rather
    than invoking metric-level source selection.  A field-level selector can
    make an incomplete issuer release look complete by borrowing cash-flow or
    balance-sheet facts from another provider, which is not an auditable report
    package.  The sole two-source exception is the explicitly requested SEC +
    issuer-IR official disclosure bundle, whose selected source is retained for
    every field.
    """
    source_ids = (source_id,) if isinstance(source_id, str) else tuple(source_id)
    rows = []
    for current_source in source_ids:
        rows.extend(repository.observations(
            dataset_id="company_financials", source_id=current_source,
            entity_id=symbol.upper(), latest_only=True, accepted_only=True,
            limit=100_000))
    if not rows:
        return None
    by_period: dict[str, list[dict]] = {}
    for row in rows:
        period = str(row.get("period") or "")
        if period:
            by_period.setdefault(period, []).append(row)
    # Do not declare an older complete quarter current merely because the source
    # also exposed a newer, incomplete quarter.  That was the AMZN failure mode:
    # Q1 looked complete while the released Q2 had only EPS in Yahoo.
    latest_observed_period = max(by_period)
    for period in (latest_observed_period,):
        package = by_period[period]
        def choose(metric: str, basis: str) -> dict | None:
            for current_source in source_ids:
                matches = [row for row in package if row["source_id"] == current_source
                           and row["metric_id"] == metric
                           and row.get("period_basis") == basis]
                if matches:
                    return max(matches, key=lambda row: (row["known_at"], row["fetched_at"]))
            return None

        selected = {}
        for metric in _PACKAGE_QUARTER_METRICS:
            selected[metric] = choose(metric, "quarter")
        for metric in _PACKAGE_INSTANT_METRICS:
            selected[metric] = choose(metric, "instant")
        eps_present = next(((metric, choose(metric, "quarter"))
                            for metric in _PACKAGE_EPS_METRICS
                            if choose(metric, "quarter")), None)
        debt_present = next(((metric, choose(metric, "instant"))
                             for metric in _PACKAGE_DEBT_METRICS
                             if choose(metric, "instant")), None)
        missing = sorted(metric for metric, row in selected.items() if row is None)
        if eps_present is None:
            missing.append("financial.eps.diluted.(adr|market_adjusted|gaap)")
        if debt_present is None:
            missing.append("financial.total_debt.(gaap|provider_reported)")
        selected_rows = [row for row in selected.values() if row]
        if eps_present:
            selected_rows.append(eps_present[1])
        if debt_present:
            selected_rows.append(debt_present[1])
        currencies = {str(row.get("currency") or "") for row in selected_rows
                      if row.get("metric_id") not in _PACKAGE_EPS_METRICS}
        if not currencies or "" in currencies or len(currencies) != 1:
            missing.append("reporting_currency")
        if not missing:
            return {
                "source_id": source_ids[0] if len(source_ids) == 1 else "official_disclosure_bundle",
                "source_ids": source_ids, "period": period,
                "currency": next(iter(currencies)), "eps_metric": eps_present[0],
                "debt_metric": debt_present[0], "rows": selected_rows,
                "source_by_metric": {metric: row["source_id"]
                                     for metric, row in selected.items() if row} | {
                    eps_present[0]: eps_present[1]["source_id"],
                    debt_present[0]: debt_present[1]["source_id"],
                },
            }
    return None


def _structured_statements(symbol: str, products, *, source_id: str = "",
                           report_period: str = "",
                           source_by_metric: dict[str, str] | None = None) -> tuple[FinancialStatements | None,
                                                                                        list[dict]]:
    """Assemble the legacy statement DTO from one selected report-package source."""
    metrics = (
        "financial.revenue.gaap", "financial.gross_profit.gaap",
        "financial.operating_income.gaap", "financial.net_income.gaap",
        "financial.eps.diluted.gaap", "financial.eps.diluted.adr",
        "financial.eps.diluted.market_adjusted",
        "financial.cash_from_operations.gaap",
        "financial.capex.gaap", "financial.total_debt.gaap",
        "financial.total_debt.provider_reported",
    )
    by_metric: dict[str, list[dict]] = {}
    for metric in metrics:
        kwargs = {"metric": metric, "entity": symbol,
                  "dataset": "company_financials", "quality": "loose"}
        selected_source = (source_by_metric or {}).get(metric, source_id)
        if selected_source:
            kwargs["source_id"] = selected_source
        result = products.metric_series(**kwargs)
        by_metric[metric] = [row for row in result["rows"]
                             if not report_period or str(row.get("period") or "") <= report_period]

    def ordered(metric: str, basis: str) -> list[dict]:
        rows = [row for row in by_metric.get(metric, [])
                if row.get("period_basis") == basis]
        return sorted(rows, key=lambda row: row["period"], reverse=True)

    revenue_rows = ordered("financial.revenue.gaap", "quarter")
    if not revenue_rows:
        return None, []
    period = report_period or revenue_rows[0]["period"]
    currency = str(revenue_rows[0].get("currency") or "")
    money_unit = "$M" if currency in {"", "USD"} else f"{currency} M"
    eps_unit = "$" if currency in {"", "USD"} else f"{currency}/share"

    used: dict[str, dict] = {}

    def triple(metric: str, basis: str = "quarter") -> tuple:
        rows = ordered(metric, basis)
        selected = rows[:2] + (rows[4:5] if len(rows) > 4 else [])
        for row in selected:
            used[row["observation_id"]] = row
        current = rows[0]["value"] if rows else None
        previous = rows[1]["value"] if len(rows) > 1 else None
        year_ago = rows[4]["value"] if len(rows) > 4 else None
        return current, previous, year_ago

    revenue = triple("financial.revenue.gaap")
    gross_profit = triple("financial.gross_profit.gaap")
    operating_income = triple("financial.operating_income.gaap")
    net_income = triple("financial.net_income.gaap")
    # For ADR issuers, a directly disclosed ADR EPS is the user-facing default.
    # If it is unavailable, the source-native TWD/ADR fallback is selected before
    # the issuer's ordinary-share EPS.  The unit remains explicit in all cases.
    eps_metric = ("financial.eps.diluted.adr"
                  if ordered("financial.eps.diluted.adr", "quarter")
                  else "financial.eps.diluted.market_adjusted"
                  if ordered("financial.eps.diluted.market_adjusted", "quarter")
                  else "financial.eps.diluted.gaap")
    eps = triple(eps_metric)
    eps_rows = ordered(eps_metric, "quarter")
    eps_unit = str(eps_rows[0].get("unit") or eps_unit) if eps_rows else eps_unit
    cash_from_operations = triple("financial.cash_from_operations.gaap")
    # Persistent CapEx is stored as a positive investment amount.  PEAD's
    # long-standing DTO displays cash outflows as negative values, matching the
    # legacy yfinance statement and keeping FCF = CFO + displayed CapEx.
    capex_raw = triple("financial.capex.gaap")
    capex = tuple(-abs(value) if value is not None else None for value in capex_raw)
    # Official total debt has a controlled XBRL definition.  Provider-reported
    # total debt can remain a useful coverage fallback, but it may include lease
    # obligations and is only selected when the official series is absent.
    debt_rows = (ordered("financial.total_debt.gaap", "instant") or
                 ordered("financial.total_debt.provider_reported", "instant"))
    debt = tuple((debt_rows[index]["value"] if len(debt_rows) > index else None)
                 for index in (0, 1, 4))
    for row in debt_rows[:2] + (debt_rows[4:5] if len(debt_rows) > 4 else []):
        used[row["observation_id"]] = row

    free_cash_flow = tuple(
        (cash_from_operations[index] + capex[index]
         if cash_from_operations[index] is not None and capex_raw[index] is not None else None)
        for index in range(3))
    lines = [
        _dollar_metric("Revenue", *revenue, unit=money_unit),
        _margin("Gross Margin", gross_profit, revenue),
        _margin("Operating Margin", operating_income, revenue),
        _dollar_metric("Net Income", *net_income, unit=money_unit),
    ]
    if eps[0] is not None:
        lines.append(StatementMetric(
            label="Diluted EPS", value=round(eps[0], 2),
            qoq=_pct(eps[0], eps[1]), yoy=_pct(eps[0], eps[2]), unit=eps_unit))
    lines.extend([
        _dollar_metric("CapEx", *capex, unit=money_unit),
        _dollar_metric("Free Cash Flow", *free_cash_flow, unit=money_unit),
        _dollar_metric("Total Debt", *debt, unit=money_unit),
    ])
    return (FinancialStatements(
        period=period, lines=[line for line in lines if line.value is not None]),
        list(used.values()))


def _company_disclosure_scope(symbol: str) -> dict:
    """Return the event anchor required by the bounded IR/release adapter."""
    from . import earnings_calendar

    latest = earnings_calendar.last_print(symbol, back_days=120)
    if latest and latest.quarter and latest.year:
        return {"near": latest.date.isoformat(),
                "period": f"Q{latest.quarter} FY{latest.year}"}
    return {}


def _refresh_report_package(repository, *, symbol: str) -> dict | None:
    """Ingest only until the first source yields one complete financial package.

    The ordered source list is a data-policy boundary, not merely a display
    preference.  It prevents a later SEC/IR pull from overriding or silently
    patching a usable defeatbeta/yfinance report package.
    """
    from .structured import FetchRequest, IngestionPipeline
    from .sources.company_financials import (
        CompanyDisclosuresAdapter,
        DefeatBetaStatementAdapter,
        SECCompanyFactsAdapter,
        YFinanceFinancialStatementsAdapter,
    )

    adapters = {
        "defeatbeta_stock_statement": DefeatBetaStatementAdapter,
        "yfinance_financials": YFinanceFinancialStatementsAdapter,
        "sec_companyfacts": SECCompanyFactsAdapter,
        "company_disclosures": CompanyDisclosuresAdapter,
    }
    pipeline = IngestionPipeline(repository)
    for source_id in _FINANCIAL_SOURCE_PRIORITY:
        scope = {"since": "2025-01-01"}
        if source_id == "sec_companyfacts":
            scope = {"since": "2020-01-01"}
        elif source_id == "company_disclosures":
            scope = _company_disclosure_scope(symbol)
            if not scope:
                log.info("fundamentals: no issuer-release event anchor for %s", symbol)
                continue
        try:
            result = pipeline.run(
                adapters[source_id](),
                FetchRequest(source_id=source_id, dataset_id="company_financials",
                             entities=[symbol], query_scope=scope))
        except Exception as exc:
            log.warning("fundamentals: %s refresh failed for %s: %s", source_id, symbol, exc)
            continue
        package = _complete_report_package(
            repository, source_id=source_id, symbol=symbol)
        if package is not None:
            package["ingestion_status"] = result.get("status", "")
            return package
        # SEC Facts and a dated issuer release are both official disclosures.
        # They may be composed only after neither one supplied a complete package
        # alone (for example AMZN's SEC facts supply the balance sheet while the
        # issuer release supplies the explicitly reported CapEx).  Provider rows
        # are never mixed into this official bundle.
        if source_id == "company_disclosures":
            package = _complete_report_package(
                repository,
                source_id=("sec_companyfacts", "company_disclosures"),
                symbol=symbol)
            if package is not None:
                package["ingestion_status"] = result.get("status", "")
                return package
        log.info("fundamentals: %s did not provide a complete report package for %s (%s)",
                 source_id, symbol, result.get("status", "unknown"))
    return None


def _platform_fetch(symbol: str, *, consumer: str) -> FundamentalData:
    """Refresh and select one governed company-financial report package."""
    from ..data.products import DataProducts
    from ..data.runtime import get_platform_structured_repository

    data = _legacy_fetch(symbol, include_statements=False)
    repository = get_platform_structured_repository()
    try:
        package = _refresh_report_package(repository, symbol=symbol.upper())
        products = DataProducts(structured_repository=repository)
        if package is None:
            data.statements, rows = None, []
        else:
            data.statements, rows = _structured_statements(
                symbol.upper(), products, source_id=package["source_id"],
                report_period=package["period"],
                source_by_metric=package.get("source_by_metric"))
        if data.statements is None:
            data.notes.append("structured quarterly statements unavailable")
        elif rows:
            manifest = products.snapshot_manifest(
                consumer=consumer, purpose=f"fundamentals:{symbol.upper()}",
                as_of=datetime.now(timezone.utc), rows=rows,
                metadata={"symbol": symbol.upper(), "runtime_inputs_included": False,
                          "report_package_source": package["source_id"],
                          "report_package_sources": list(package.get("source_ids") or []),
                          "report_package_period": package["period"]})
            data.notes.append(f"structured snapshot {manifest['snapshot_id']}")
            data.notes.append(
                f"structured report package {package['source_id']}:{package['period']}")
        return data
    finally:
        repository.close()


# --- SEC EDGAR -------------------------------------------------------------- #
def _headers() -> dict:
    return {"User-Agent": get_config().secrets.sec_edgar_user_agent,
            "Accept-Encoding": "gzip, deflate"}


@lru_cache(maxsize=1)
def _ticker_to_cik() -> dict[str, str]:
    import httpx

    r = httpx.get("https://www.sec.gov/files/company_tickers.json", headers=_headers(), timeout=20)
    r.raise_for_status()
    return {row["ticker"].upper(): f"{int(row['cik_str']):010d}" for row in r.json().values()}


def _sec_filings(symbol: str, limit: int = 5) -> list[Filing]:
    import httpx

    cik = _ticker_to_cik().get(symbol.upper())
    if not cik:
        return []
    r = httpx.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=_headers(), timeout=20)
    r.raise_for_status()
    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    out: list[Filing] = []
    for form, filed, accn, doc in zip(forms, dates, accns, docs):
        if form not in _FORMS:
            continue
        accn_nodash = accn.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/{doc}"
        out.append(Filing(form=form, filed=date.fromisoformat(filed), url=url))
        if len(out) >= limit:
            break
    return out
