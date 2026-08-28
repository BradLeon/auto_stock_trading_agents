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
    from ..structured import read_mode

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


_LIGHT_CACHE: dict[str, tuple[float, dict]] = {}
_LIGHT_TTL = 1800.0        # in-process cache: dedupe repeat pulls within a run/hour


def fetch_light(symbol: str) -> dict:
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
    return out


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


def _structured_statements(symbol: str, products) -> tuple[FinancialStatements | None,
                                                            list[dict]]:
    """Assemble the legacy statement DTO from selected persistent observations."""
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
        result = products.metric_series(
            metric=metric, entity=symbol, dataset="company_financials", quality="loose")
        by_metric[metric] = result["rows"]

    def ordered(metric: str, basis: str) -> list[dict]:
        rows = [row for row in by_metric.get(metric, [])
                if row.get("period_basis") == basis]
        return sorted(rows, key=lambda row: row["period"], reverse=True)

    revenue_rows = ordered("financial.revenue.gaap", "quarter")
    if not revenue_rows:
        return None, []
    period = revenue_rows[0]["period"]
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


def _platform_fetch(symbol: str, *, consumer: str) -> FundamentalData:
    """Refresh official facts, then assemble only the persistent statement section."""
    from ..data.products import DataProducts
    from ..data.runtime import get_platform_structured_repository
    from ..structured import FetchRequest, IngestionPipeline
    from .sources.company_financials import (
        CompanyDisclosuresAdapter,
        DefeatBetaStatementAdapter,
        SECCompanyFactsAdapter,
        YFinanceFinancialStatementsAdapter,
    )

    data = _legacy_fetch(symbol, include_statements=False)
    repository = get_platform_structured_repository()
    try:
        if symbol.upper() in {"TSM", "AMZN"}:
            # TSM and AMZN have issuer-verified earnings-release parsers.  Their
            # timely reported quarter is the primary statement anchor.
            from . import earnings_calendar

            latest = earnings_calendar.last_print(symbol, back_days=120)
            if latest and latest.quarter and latest.year:
                try:
                    IngestionPipeline(repository).run(
                        CompanyDisclosuresAdapter(), FetchRequest(
                            source_id="company_disclosures", dataset_id="company_financials",
                            entities=[symbol], query_scope={
                                "near": latest.date.isoformat(),
                                "period": f"Q{latest.quarter} FY{latest.year}",
                            }))
                except Exception as exc:  # retained accepted release rows remain queryable
                    log.warning("fundamentals: official release refresh failed for %s: %s",
                                symbol, exc)
        try:
            IngestionPipeline(repository).run(
                SECCompanyFactsAdapter(), FetchRequest(
                    source_id="sec_companyfacts", dataset_id="company_financials",
                    entities=[symbol], query_scope={"since": "2020-01-01"}))
        except Exception as exc:  # existing accepted rows remain queryable during outages
            log.warning("fundamentals: SEC structured refresh failed for %s: %s", symbol, exc)
        if symbol.upper() == "TSM":
            # TSM's official release is the authoritative same-quarter P&L anchor.
            # Its governed Yahoo-statement mirror fills only the release's omitted
            # CFO, CapEx, cash and debt fields in the same native TWD reporting
            # currency.  No ADR or FX conversion is introduced here.
            try:
                IngestionPipeline(repository).run(
                    DefeatBetaStatementAdapter(), FetchRequest(
                        source_id="defeatbeta_stock_statement",
                        dataset_id="company_financials", entities=[symbol],
                        query_scope={"since": "2020-01-01"}))
            except Exception as exc:  # existing accepted mirror rows remain queryable
                log.warning("fundamentals: TSM statement mirror refresh failed for %s: %s",
                            symbol, exc)
        else:
            from ..config import entity_meta

            if entity_meta(symbol).get("market") == "US":
                # This is the same low-frequency statement endpoint that legacy
                # fundamentals used, now persisted with a governed raw slice.  It
                # is a fallback only: SEC/issuer rows still win on equal periods.
                try:
                    IngestionPipeline(repository).run(
                        YFinanceFinancialStatementsAdapter(), FetchRequest(
                            source_id="yfinance_financials",
                            dataset_id="company_financials", entities=[symbol],
                            query_scope={"since": "2025-01-01"}))
                except Exception as exc:  # historical accepted rows stay queryable
                    log.warning("fundamentals: yfinance statement fallback failed for %s: %s",
                                symbol, exc)
        products = DataProducts(structured_repository=repository)
        data.statements, rows = _structured_statements(symbol.upper(), products)
        if data.statements is None:
            data.notes.append("structured quarterly statements unavailable")
        elif rows:
            manifest = products.snapshot_manifest(
                consumer=consumer, purpose=f"fundamentals:{symbol.upper()}",
                as_of=datetime.now(timezone.utc), rows=rows,
                metadata={"symbol": symbol.upper(), "runtime_inputs_included": False})
            data.notes.append(f"structured snapshot {manifest['snapshot_id']}")
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
