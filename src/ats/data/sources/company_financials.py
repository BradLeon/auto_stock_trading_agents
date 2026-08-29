"""Official SEC Company Facts and defeatbeta statement-slice adapters."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import html
import re

from ...structured import (
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
)


COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts"
DEFEATBETA_STATEMENTS = (
    "https://huggingface.co/datasets/defeatbeta/yahoo-finance-data/resolve/main/data/"
    "stock_statement.parquet")

# Ordered aliases: the first available concept wins per metric/period. This avoids
# publishing multiple semantically equivalent XBRL concepts as competing source rows.
XBRL_CONCEPTS = {
    "financial.revenue.gaap": [
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues", "us-gaap:SalesRevenueNet", "ifrs-full:Revenue"],
    "financial.gross_profit.gaap": ["us-gaap:GrossProfit", "ifrs-full:GrossProfit"],
    "financial.operating_income.gaap": [
        "us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"],
    "financial.net_income.gaap": ["us-gaap:NetIncomeLoss", "ifrs-full:ProfitLoss"],
    "financial.eps.diluted.gaap": [
        "us-gaap:EarningsPerShareDiluted", "ifrs-full:DilutedEarningsLossPerShare"],
    "financial.cash_from_operations.gaap": [
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "ifrs-full:CashFlowsFromUsedInOperatingActivities"],
    "financial.capex.gaap": [
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "ifrs-full:PurchaseOfPropertyPlantAndEquipment"],
    "financial.cash_and_equivalents.gaap": [
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "ifrs-full:CashAndCashEquivalents"],
    "financial.inventory.gaap": ["us-gaap:InventoryNet", "ifrs-full:Inventories"],
    # Keep an explicit total-debt fact separate from long-term debt.  The older
    # mapping treated ``LongTermDebt`` as total debt, even though its SEC definition
    # excludes current maturities and capital leases.  For issuers that provide it,
    # the combined XBRL concept is the directly reported total.
    "financial.total_debt.gaap": [
        "us-gaap:DebtLongtermAndShorttermCombinedAmount", "ifrs-full:Borrowings"],
    "financial.long_term_debt.gaap": [
        "us-gaap:LongTermDebtAndFinanceLeaseObligations", "us-gaap:LongTermDebt"],
    "financial.total_assets.gaap": ["us-gaap:Assets", "ifrs-full:Assets"],
    "financial.total_liabilities.gaap": ["us-gaap:Liabilities", "ifrs-full:Liabilities"],
    "financial.stockholders_equity.gaap": [
        "us-gaap:StockholdersEquity", "ifrs-full:Equity"],
}
_FIELD_TO_METRIC = {field: metric for metric, fields in XBRL_CONCEPTS.items()
                    for field in fields}
_FIELD_PRIORITY = {field: rank for fields in XBRL_CONCEPTS.values()
                   for rank, field in enumerate(fields)}
_DERIVED_INPUT_CONCEPTS = {
    "us-gaap:CostOfGoodsAndServicesSold": "cost_of_revenue",
    "ifrs-full:CostOfSales": "cost_of_revenue",
    "us-gaap:LiabilitiesAndStockholdersEquity": "liabilities_and_equity",
    "us-gaap:LongTermDebtCurrent": "debt_current",
    "us-gaap:LongTermDebtNoncurrent": "debt_noncurrent",
}
_DERIVED_INPUT_BASIS_METRIC = {
    "cost_of_revenue": "financial.revenue.gaap",
    "liabilities_and_equity": "financial.total_assets.gaap",
    "debt_current": "financial.total_debt.gaap",
    "debt_noncurrent": "financial.total_debt.gaap",
}
_INSTANT_METRICS = {
    "financial.cash_and_equivalents.gaap", "financial.inventory.gaap",
    "financial.total_debt.gaap", "financial.long_term_debt.gaap",
    "financial.total_debt.provider_reported",
    "financial.total_assets.gaap",
    "financial.total_liabilities.gaap", "financial.stockholders_equity.gaap",
}
_EPS_METRICS = {"financial.eps.diluted.gaap", "financial.eps.diluted.adr"}
_CAPEX_FIELDS = {"capital_expenditure", "purchase_of_ppe"}
DEFEATBETA_CONCEPTS = {
    "financial.revenue.gaap": ["total_revenue", "operating_revenue"],
    "financial.gross_profit.gaap": ["gross_profit"],
    "financial.operating_income.gaap": [
        "operating_income", "total_operating_income_as_reported"],
    "financial.net_income.gaap": ["net_income", "net_income_common_stockholders"],
    # Yahoo's per-share history is split-adjusted.  It remains valuable for
    # market-comparable historical EPS, but must not be reconciled as the raw
    # issuer-reported GAAP share count.
    "financial.eps.diluted.market_adjusted": ["diluted_eps"],
    # Yahoo's TSM statement EPS is on the NYSE ADR share basis, while issuer
    # TIFRS tables use ordinary shares.  It is therefore a separate metric.
    "financial.eps.diluted.adr": ["tsm_diluted_eps_adr_twd"],
    "financial.cash_from_operations.gaap": [
        "operating_cash_flow", "cash_flow_from_continuing_operating_activities"],
    "financial.capex.gaap": ["capital_expenditure", "purchase_of_ppe"],
    "financial.cash_and_equivalents.gaap": ["cash_and_cash_equivalents", "cash"],
    "financial.inventory.gaap": ["inventory"],
    # The Yahoo statement field can include lease liabilities (for example KLAC's
    # operating lease liability).  Preserve it as a provider-reported fallback,
    # rather than claiming it shares the SEC total-debt definition.
    "financial.total_debt.provider_reported": ["total_debt"],
    "financial.total_assets.gaap": ["total_assets"],
    "financial.total_liabilities.gaap": ["total_liabilities_net_minority_interest"],
    "financial.stockholders_equity.gaap": ["stockholders_equity"],
}
_DEFEATBETA_TO_METRIC = {field: metric for metric, fields in DEFEATBETA_CONCEPTS.items()
                         for field in fields}
_DEFEATBETA_PRIORITY = {field: rank for fields in DEFEATBETA_CONCEPTS.values()
                        for rank, field in enumerate(fields)}

# The source-native identifiers are intentionally narrower than the generic metric
# contract.  They describe the reported TIFRS table in TSMC's official quarterly
# results release, not a reconstructed statement or a Yahoo mirror.
TSMC_RELEASE_FIELDS = {
    "tsmc_release:net_sales": "financial.revenue.gaap",
    "tsmc_release:gross_profit": "financial.gross_profit.gaap",
    "tsmc_release:operating_income": "financial.operating_income.gaap",
    "tsmc_release:net_income": "financial.net_income.gaap",
    "tsmc_release:diluted_eps": "financial.eps.diluted.gaap",
    "tsmc_release:diluted_eps_adr": "financial.eps.diluted.adr",
}
AMAZON_RELEASE_FIELDS = {
    "amzn_release:net_sales": "financial.revenue.gaap",
    "amzn_release:gross_profit_derived": "financial.gross_profit.gaap",
    "amzn_release:operating_income": "financial.operating_income.gaap",
    "amzn_release:net_income": "financial.net_income.gaap",
    "amzn_release:diluted_eps": "financial.eps.diluted.gaap",
    "amzn_release:cash_from_operations": "financial.cash_from_operations.gaap",
    "amzn_release:capex": "financial.capex.gaap",
}


def _quarter_start(end: date) -> date:
    month = ((end.month - 1) // 3) * 3 + 1
    return date(end.year, month, 1)


def _tsmc_release_period(text: str) -> date | None:
    match = re.search(
        r"(?:first|second|third|fourth|[1-4](?:st|nd|rd|th)?)\s+quarter"
        r"(?:\s+\d{4})?\s+ended\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        text, re.I)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date()
    except ValueError:
        return None


def parse_tsmc_quarterly_release(text: str, *, symbol: str = "TSM",
                                 published_at: datetime | None = None) -> list[NativeRecord]:
    """Parse the reported-quarter rows from TSMC's official SEC-hosted release.

    The release declares the table unit as NT$ million (except EPS).  We retain that
    native unit and the TIFRS/consolidated provenance rather than converting to USD or
    inferring values from prose.  A missing period or an incomplete table yields no
    records: that is safer than accidentally publishing an adjacent-quarter release.
    """
    normalized = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    period_end = _tsmc_release_period(normalized)
    if period_end is None:
        return []

    values: dict[str, float] = {}
    labels = {
        "tsmc_release:net_sales": r"Net sales\s+([\d,]+)",
        "tsmc_release:gross_profit": r"Gross profit\s+([\d,]+)",
        "tsmc_release:operating_income": r"Income from operations\s+([\d,]+)",
        "tsmc_release:net_income": r"Net income\s+([\d,]+)",
        "tsmc_release:diluted_eps": r"EPS\s*\(NT\$\)\s+([0-9]+(?:\.[0-9]+)?)",
    }
    for field, pattern in labels.items():
        match = re.search(pattern, normalized, re.I)
        if match:
            values[field] = float(match.group(1).replace(",", ""))

    # TSMC's release explicitly reports a USD value per NYSE ADR in the headline.
    # It is intentionally stored alongside, not substituted for, the TIFRS
    # ordinary-share EPS in the reported table.
    adr_match = re.search(
        r"US\$\s*([0-9]+(?:\.[0-9]+)?)\s+per\s+ADR\s+unit", normalized, re.I)
    if adr_match:
        values["tsmc_release:diluted_eps_adr"] = float(adr_match.group(1))

    # The table is the acceptance boundary: prose figures are rounded and therefore
    # never substituted for a missing reported table value.
    required = set(TSMC_RELEASE_FIELDS) - {"tsmc_release:diluted_eps_adr"}
    if not required.issubset(values):
        return []
    start = _quarter_start(period_end).isoformat()
    end = period_end.isoformat()
    records = []
    for field, value in values.items():
        is_eps = field in {"tsmc_release:diluted_eps", "tsmc_release:diluted_eps_adr"}
        is_adr = field == "tsmc_release:diluted_eps_adr"
        records.append(NativeRecord(
            # Structured observations use base currency units, consistently with
            # Company Facts.  The release table's NT$-million presentation stays in
            # raw lineage so a consumer never mistakes 1,270,381 for NT$1.27m.
            entity_id=symbol.upper(), provider_field=field, period=end,
            value=value if is_eps else value * 1_000_000,
            unit="USD/ADR" if is_adr else "TWD/share" if is_eps else "TWD",
            currency="USD" if is_adr else "TWD",
            period_basis="quarter", adjustment="gaap", period_start=start,
            period_end=end, published_at=published_at,
            dimensions={"taxonomy": "TIFRS", "statement_scope": "consolidated",
                        "document_role": "earnings_release",
                        "share_basis": "adr" if is_adr else "ordinary_share" if is_eps else ""},
            raw={"table_unit": "NT$ million, except for EPS", "field": field,
                 "reported_value": value, "stored_unit_scale": 1 if is_eps else 1_000_000,
                 "period_end": end,
                 "reported_location": "release_headline" if is_adr else "results_table"},
        ))
    return records


def parse_amazon_quarterly_release(text: str, *, symbol: str = "AMZN",
                                   published_at: datetime | None = None) -> list[NativeRecord]:
    """Parse AMZN's issuer-reported Q2 table, preserving its source calculations.

    Amazon reports net sales and cost of sales, rather than a standalone gross-profit
    line.  The normalized gross-profit observation is therefore explicitly marked as
    ``net_sales - cost_of_sales`` in raw lineage; no prose rounding is accepted.
    """
    normalized = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    period_end = _tsmc_release_period(normalized)
    if period_end is None:
        return []

    operations = re.search(
        r"Consolidated Statements of Operations.*?Consolidated Statements of Comprehensive Income",
        normalized, re.I)
    cashflows = re.search(
        r"Consolidated Statements of Cash Flows.*?Consolidated Statements of Operations",
        normalized, re.I)
    if not operations or not cashflows:
        return []
    ops_text, cash_text = operations.group(0), cashflows.group(0)
    sales = pair_from_text(ops_text, r"Total net sales")
    cost = pair_from_text(ops_text, r"Cost of sales")
    operating = pair_from_text(ops_text, r"Operating income")
    net_income = pair_from_text(ops_text, r"Net income")
    eps = pair_from_text(ops_text, r"Diluted earnings per share")
    cfo = pair_from_text(cash_text, r"Net cash provided by \(used in\) operating activities")
    capex = pair_from_text(cash_text, r"Purchases of property and equipment")
    if not all((sales, cost, operating, net_income, eps, cfo, capex)):
        return []
    values = {
        "amzn_release:net_sales": sales[1],
        "amzn_release:gross_profit_derived": sales[1] - cost[1],
        "amzn_release:operating_income": operating[1],
        "amzn_release:net_income": net_income[1],
        "amzn_release:diluted_eps": eps[1],
        "amzn_release:cash_from_operations": cfo[1],
        "amzn_release:capex": abs(capex[1]),
    }
    start, end = _quarter_start(period_end).isoformat(), period_end.isoformat()
    records = []
    for field, value in values.items():
        is_eps = field == "amzn_release:diluted_eps"
        raw = {"table_unit": "USD million, except per share data", "field": field,
               "reported_value": value, "period_end": end}
        if field == "amzn_release:gross_profit_derived":
            raw["calculation"] = "Total net sales - Cost of sales"
            raw["inputs"] = {"net_sales": sales[1], "cost_of_sales": cost[1]}
        records.append(NativeRecord(
            entity_id=symbol.upper(), provider_field=field, period=end,
            value=value if is_eps else value * 1_000_000,
            unit="USD/share" if is_eps else "USD", currency="USD",
            period_basis="quarter", adjustment="gaap", period_start=start, period_end=end,
            published_at=published_at,
            dimensions={"statement_scope": "consolidated", "document_role": "earnings_release",
                        "gross_profit_basis": "derived_from_reported_table"
                        if field == "amzn_release:gross_profit_derived" else "reported"},
            raw=raw))
    return records


def pair_from_text(text: str, label: str) -> tuple[float, float] | None:
    """Return the first two quarter columns in a normalized issuer table row."""
    match = re.search(
        label + r"\s+\$?\s*\(?([\d,.]+)\)?\s+\$?\s*\(?([\d,.]+)\)?\s+"
        r"(?:\$?\s*\(?[\d,.]+\)?\s+){2}", text, re.I)
    if not match:
        return None
    return (float(match.group(1).replace(",", "")),
            float(match.group(2).replace(",", "")))


def _aware_date(value: str) -> datetime | None:
    try:
        return datetime.combine(date.fromisoformat(value[:10]), datetime.min.time(),
                                tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _period_basis(metric: str, fact: dict) -> str:
    if metric in _INSTANT_METRICS or not fact.get("start"):
        return "instant"
    try:
        days = (date.fromisoformat(str(fact["end"])[:10])
                - date.fromisoformat(str(fact["start"])[:10])).days
    except (KeyError, ValueError):
        days = 0
    form = str(fact.get("form", "")).upper()
    if form in {"10-K", "20-F", "40-F"}:
        return "annual"
    if form == "10-Q" and days >= 300:
        return "unsupported"
    if days >= 300:
        return "annual"
    if days > 120:
        return "ytd"
    return "quarter"


def _currency_and_unit(metric: str, xbrl_unit: str) -> tuple[str, str]:
    raw = xbrl_unit.strip()
    currency = raw.split("/")[0] if re.match(r"^[A-Z]{3}(?:/|$)", raw) else ""
    if metric in _EPS_METRICS:
        return currency, f"{currency}/share" if currency else raw
    if metric in _INSTANT_METRICS or metric.startswith("financial."):
        return currency, currency or raw
    return "", raw


def parse_companyfacts(payload: dict, *, symbol: str) -> list[NativeRecord]:
    """Normalize only mapped concepts and retain the newest filed fact per period."""
    candidates: dict[tuple[str, str, str], tuple[tuple, NativeRecord]] = {}
    derivation_inputs: dict[tuple[str, str, str], tuple[tuple, NativeRecord]] = {}
    facts_root = payload.get("facts", {}) if isinstance(payload, dict) else {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        for concept, body in (facts_root.get(taxonomy, {}) or {}).items():
            provider_field = f"{taxonomy}:{concept}"
            metric = _FIELD_TO_METRIC.get(provider_field)
            input_role = _DERIVED_INPUT_CONCEPTS.get(provider_field)
            if not metric and not input_role:
                continue
            for xbrl_unit, facts in (body.get("units", {}) or {}).items():
                record_metric = metric or _DERIVED_INPUT_BASIS_METRIC[input_role]
                currency, unit = _currency_and_unit(record_metric, xbrl_unit)
                for fact in facts or []:
                    if str(fact.get("form", "")).upper() not in {
                            "10-Q", "10-K", "20-F", "40-F", "6-K"}:
                        continue
                    end = str(fact.get("end", ""))[:10]
                    if not end or fact.get("val") is None:
                        continue
                    basis = _period_basis(record_metric, fact)
                    if basis == "unsupported":
                        continue
                    published = _aware_date(str(fact.get("filed", "")))
                    fiscal_label = ""
                    if fact.get("fy") and fact.get("fp"):
                        fiscal_label = f"FY{fact['fy']}{fact['fp']}"
                    value = float(fact["val"])
                    if provider_field.endswith((
                            "PaymentsToAcquirePropertyPlantAndEquipment",
                            "PurchaseOfPropertyPlantAndEquipment")):
                        value = abs(value)
                    record = NativeRecord(
                        entity_id=symbol, provider_field=provider_field, period=end,
                        value=value, unit=unit, currency=currency, period_basis=basis,
                        adjustment="gaap", period_start=str(fact.get("start", ""))[:10],
                        period_end=end, published_at=published,
                        dimensions={"taxonomy": taxonomy, "statement_scope": "reported"},
                        raw={
                            "taxonomy": taxonomy, "concept": concept,
                            "label": body.get("label", ""), "description": body.get("description", ""),
                            "xbrl_unit": xbrl_unit, "fact": fact,
                            "fiscal_label": fiscal_label,
                        })
                    rank = (_FIELD_PRIORITY.get(provider_field, 999),
                            str(fact.get("filed", "")), str(fact.get("accn", "")))
                    if metric:
                        key = (metric, end, basis)
                        current = candidates.get(key)
                        # Lower alias priority wins; within one concept the latest filing wins.
                        if current is None or rank[0] < current[0][0] or (
                                rank[0] == current[0][0] and rank[1:] > current[0][1:]):
                            candidates[key] = (rank, record)
                    else:
                        key = (input_role, end, basis)
                        current = derivation_inputs.get(key)
                        if current is None or rank[1:] > current[0][1:]:
                            derivation_inputs[key] = (rank, record)

    selected = {key: record for key, (_, record) in candidates.items()}
    inputs = {key: record for key, (_, record) in derivation_inputs.items()}

    def add_derived(*, metric: str, provider_field: str, period: str,
                    basis: str, left: NativeRecord, right: NativeRecord,
                    value: float, calculation: str) -> None:
        if (metric, period, basis) in selected:
            return
        published = [item for item in (left.published_at, right.published_at) if item]
        selected[(metric, period, basis)] = NativeRecord(
            entity_id=symbol, provider_field=provider_field, period=period, value=value,
            unit=left.unit, currency=left.currency, period_basis=basis, adjustment="gaap",
            period_start=left.period_start, period_end=period,
            published_at=max(published) if published else None,
            dimensions={"taxonomy": "us-gaap", "statement_scope": "reported",
                        "derivation": "official_xbrl_components"},
            raw={"calculation": calculation,
                 "left": {"provider_field": left.provider_field, "value": left.value,
                          "fact": left.raw.get("fact", {})},
                 "right": {"provider_field": right.provider_field, "value": right.value,
                           "fact": right.raw.get("fact", {})}})

    for (_, period, basis), revenue in list(selected.items()):
        if revenue.provider_field not in XBRL_CONCEPTS["financial.revenue.gaap"]:
            continue
        cost = inputs.get(("cost_of_revenue", period, basis))
        if cost and revenue.currency == cost.currency:
            add_derived(metric="financial.gross_profit.gaap",
                        provider_field="derived:revenue_minus_cost_of_revenue",
                        period=period, basis=basis, left=revenue, right=cost,
                        value=revenue.value - cost.value,
                        calculation="revenue - cost_of_revenue")
    for (_, period, basis), total in list(inputs.items()):
        if total.provider_field != "us-gaap:LiabilitiesAndStockholdersEquity":
            continue
        equity = selected.get(("financial.stockholders_equity.gaap", period, basis))
        if equity and total.currency == equity.currency:
            add_derived(metric="financial.total_liabilities.gaap",
                        provider_field="derived:liabilities_and_equity_minus_equity",
                        period=period, basis=basis, left=total, right=equity,
                        value=total.value - equity.value,
                        calculation="liabilities_and_equity - stockholders_equity")
    for (role, period, basis), current in list(inputs.items()):
        if role != "debt_current":
            continue
        noncurrent = inputs.get(("debt_noncurrent", period, basis))
        if noncurrent and current.currency == noncurrent.currency:
            add_derived(metric="financial.total_debt.gaap",
                        provider_field="derived:current_debt_plus_noncurrent_debt",
                        period=period, basis=basis, left=current, right=noncurrent,
                        value=current.value + noncurrent.value,
                        calculation="long_term_debt_current + long_term_debt_noncurrent")
    return sorted(selected.values(),
                  key=lambda row: (row.period, row.provider_field, row.period_basis))


class SECCompanyFactsAdapter:
    source_id = "sec_companyfacts"
    dataset_id = "company_financials"

    def __init__(self, *, client=None, clock=None):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx

        from .. import sec

        if len(request.entities) != 1:
            raise ValueError("SEC Company Facts adapter requires exactly one entity per slice")
        client = self.client or httpx
        symbol = request.entities[0].upper()
        fetched_at = self.clock().astimezone(timezone.utc)
        cik = str(request.query_scope.get("cik", ""))
        if not cik:
            tickers = client.get(
                sec.COMPANY_TICKERS, headers=sec._headers(), timeout=30).json()
            cik = next((str(row.get("cik_str", "")) for row in tickers.values()
                        if str(row.get("ticker", "")).upper() == symbol), "")
        if not cik.isdigit():
            return AdapterBatch(
                source_id=request.source_id, dataset_id=request.dataset_id,
                status=IngestionStatus.NO_COVERAGE, fetched_at=fetched_at,
                failures=[AdapterFailure(
                    status=IngestionStatus.NO_COVERAGE, entity_id=symbol,
                    message="SEC ticker-to-CIK mapping missing")])
        url = f"{COMPANYFACTS}/CIK{int(cik):010d}.json"
        response = client.get(url, headers=sec._headers(), timeout=60)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json()
        entity_name = str(payload.get("entityName", ""))
        records = parse_companyfacts(payload, symbol=symbol)
        requested_periods = set(request.periods)
        if requested_periods:
            records = [record for record in records if record.period in requested_periods]
        since = str(request.query_scope.get("since", ""))
        if since:
            records = [record for record in records if record.period >= since]
        headers = getattr(response, "headers", {}) or {}
        version = str(headers.get("etag") or headers.get("last-modified") or "")
        coverage = {
            "first_period": records[0].period if records else "",
            "last_period": records[-1].period if records else "",
            "record_count": len(records), "cik": f"{int(cik):010d}",
            "entity_name": entity_name,
        }
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH,
            fetched_at=fetched_at, records=records,
            artifacts=[AdapterArtifact(
                payload=payload, query_scope={**request.query_scope, "entity": symbol},
                source_url=url, source_version=version, media_type="application/json",
                retention="full_response", metadata={"coverage": coverage})],
            provider_metadata={"source_version": version, "coverage": coverage})


class CompanyDisclosuresAdapter:
    """Official quarterly earnings-release adapter for issuers not timely in XBRL.

    Initial governed coverage is deliberately limited to TSM and AMZN.  The adapter uses the
    existing event-bound SEC release resolver, which validates issuer identity and
    fiscal period before exposing the text.  Other symbols are an explicit
    ``no_coverage`` outcome, allowing the next configured source to serve them.
    """

    source_id = "company_disclosures"
    dataset_id = "company_financials"

    def __init__(self, *, release_fetcher=None, clock=None):
        self.release_fetcher = release_fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        if len(request.entities) != 1:
            raise ValueError("company disclosure adapter requires exactly one entity per slice")
        symbol = request.entities[0].upper()
        fetched_at = self.clock().astimezone(timezone.utc)
        parsers = {"TSM": parse_tsmc_quarterly_release,
                   "AMZN": parse_amazon_quarterly_release}
        parser = parsers.get(symbol)
        if parser is None:
            return AdapterBatch(
                source_id=request.source_id, dataset_id=request.dataset_id,
                status=IngestionStatus.NO_COVERAGE, fetched_at=fetched_at,
                failures=[AdapterFailure(
                    status=IngestionStatus.NO_COVERAGE, entity_id=symbol,
                    message="official quarterly disclosure coverage is currently TSM and AMZN only")],
            )

        if not request.query_scope.get("near") or not request.query_scope.get("period"):
            return AdapterBatch(
                source_id=request.source_id, dataset_id=request.dataset_id,
                status=IngestionStatus.NO_COVERAGE, fetched_at=fetched_at,
                failures=[AdapterFailure(
                    status=IngestionStatus.NO_COVERAGE, entity_id=symbol,
                    message=f"{symbol} quarterly disclosure requires near and period event anchors")],
            )
        if self.release_fetcher is None:
            from .. import sec

            release_fetcher = sec.earnings_release_result
        else:
            release_fetcher = self.release_fetcher
        result = release_fetcher(
            symbol, near=str(request.query_scope["near"]),
            period=str(request.query_scope["period"]))
        record = getattr(result, "record", None)
        status = str(getattr(result, "status", "missing"))
        if not record:
            mapped = IngestionStatus.UNREACHABLE if status == "unreachable" \
                else IngestionStatus.NO_COVERAGE
            return AdapterBatch(
                source_id=request.source_id, dataset_id=request.dataset_id,
                status=mapped, fetched_at=fetched_at,
                failures=[AdapterFailure(
                    status=mapped, entity_id=symbol,
                    message=f"{symbol} official quarterly release unavailable: {status}")],
                provider_metadata={"resolver_status": status,
                                   "resolver_stage": str(getattr(result, "stage", ""))},
            )

        published_date = record.get("filed")
        published_at = datetime.combine(
            published_date, datetime.min.time(), tzinfo=timezone.utc) \
            if isinstance(published_date, date) else None
        text = str(record.get("text", ""))
        records = parser(text, symbol=symbol, published_at=published_at)
        if request.periods:
            records = [item for item in records if item.period in set(request.periods)]
        if not records:
            return AdapterBatch(
                source_id=request.source_id, dataset_id=request.dataset_id,
                status=IngestionStatus.PARSE_FAILED, fetched_at=fetched_at,
                failures=[AdapterFailure(
                    status=IngestionStatus.PARSE_FAILED, entity_id=symbol,
                    message=f"{symbol} release lacks a complete reported-quarter table")],
            )
        accession = str(record.get("accession", ""))
        coverage = {"entity": symbol, "period": records[0].period,
                    "record_count": len(records), "document_role": "earnings_release",
                    "form_type": str(record.get("form_type", ""))}
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED, fetched_at=fetched_at, records=records,
            artifacts=[AdapterArtifact(
                payload=text,
                query_scope={**request.query_scope, "entity": symbol,
                             "period": records[0].period},
                source_url=str(record.get("source_url", "")), source_version=accession,
                media_type="text/html", retention="full_response",
                metadata={"coverage": coverage, "cik": str(record.get("cik", "")),
                          "published_at": published_at.isoformat() if published_at else ""},
            )],
            provider_metadata={"source_version": accession, "coverage": coverage},
        )


def _statement_unit(item_name: str, currency: str) -> str:
    if item_name == "tsm_diluted_eps_adr_twd":
        return "TWD/ADR"
    return f"{currency}/share" if item_name in {"diluted_eps", "basic_eps"} else currency


def _defeatbeta_provider_field(symbol: str, item_name: str) -> str:
    """Preserve TSM ADR EPS as a distinct share-class series."""
    if symbol.upper() == "TSM" and item_name == "diluted_eps":
        return "tsm_diluted_eps_adr_twd"
    return item_name


def _statement_adjustment(provider_field: str) -> str:
    """Expose source adjustments instead of silently calling all rows GAAP."""
    if provider_field == "total_debt":
        return "provider_reported"
    if provider_field == "diluted_eps":
        return "split_adjusted"
    if provider_field == "tsm_diluted_eps_adr_twd":
        return "provider_reported"
    return "gaap"


def _entity_statement_currency(symbol: str, *, default: str = "",
                               overrides: dict[str, str] | None = None) -> str:
    """Resolve an issuer reporting currency without inferring an ADR quote currency."""
    symbol = symbol.upper()
    if overrides and overrides.get(symbol):
        return str(overrides[symbol])
    if default:
        return default
    from ...config import entity_meta

    meta = entity_meta(symbol)
    currency = str(meta.get("financial_statement_currency", ""))
    # The curated entity registry establishes USD only for domestic US issuers.
    # Other listings must declare a reporting currency explicitly.
    return currency or ("USD" if meta.get("market") == "US" else "")


class DefeatBetaStatementAdapter:
    source_id = "defeatbeta_stock_statement"
    dataset_id = "company_financials"

    def __init__(self, *, uri: str = DEFEATBETA_STATEMENTS,
                 connection_factory=None, snapshot_loader=None, clock=None):
        self.uri = uri
        self.connection_factory = connection_factory
        self.snapshot_loader = snapshot_loader
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        from .. import defeatbeta

        fetched_at = self.clock().astimezone(timezone.utc)
        entities = [entity.upper() for entity in request.entities]
        if not entities:
            raise ValueError("defeatbeta statement adapter requires at least one entity")
        connection = ((self.connection_factory or defeatbeta._connect)(self.uri))
        placeholders = ",".join("?" for _ in entities)
        where = [f"symbol IN ({placeholders})"]
        where.append("lower(period_type) IN ('annual','quarterly')")
        where.append("regexp_matches(report_date, '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')")
        args: list = list(entities)
        since = str(request.query_scope.get("since", ""))
        if since:
            where.append("report_date>=?")
            args.append(since)
        if request.periods:
            period_placeholders = ",".join("?" for _ in request.periods)
            where.append(f"report_date IN ({period_placeholders})")
            args.extend(request.periods)
        sql = (
            "SELECT symbol,report_date,item_name,item_value,finance_type,period_type "
            f"FROM read_parquet('{self.uri.replace(chr(39), chr(39) * 2)}') "
            f"WHERE {' AND '.join(where)} ORDER BY symbol,report_date,item_name")
        rows = connection.execute(sql, args).fetchall()
        columns = ("symbol", "report_date", "item_name", "item_value",
                   "finance_type", "period_type")
        slice_rows = [{key: (float(value) if isinstance(value, Decimal) else value)
                       for key, value in zip(columns, row)} for row in rows]
        selected_rows: dict[tuple, dict] = {}
        unknown_rows = []
        for row in slice_rows:
            symbol = str(row["symbol"]).upper()
            item_name = str(row["item_name"])
            provider_field = _defeatbeta_provider_field(symbol, item_name)
            metric = _DEFEATBETA_TO_METRIC.get(provider_field)
            if metric is None:
                unknown_rows.append(row)
                continue
            key = (symbol, str(row["report_date"])[:10],
                   str(row["period_type"]), metric)
            current = selected_rows.get(key)
            current_field = _defeatbeta_provider_field(
                str(current["symbol"]).upper(), str(current["item_name"])) if current else ""
            if current is None or _DEFEATBETA_PRIORITY[provider_field] < \
                    _DEFEATBETA_PRIORITY[current_field]:
                selected_rows[key] = row
        # Normal ingestion publishes the governed statement slice only.  The mirror
        # includes hundreds of Yahoo-specific fields outside our current financial
        # contract; treating all of them as failed candidates makes a healthy
        # 13-field refresh look like a 1,200-row failure.  A mapping-review run can
        # opt in to preserve those unknown fields in the pending-mapping pool.
        include_unmapped = bool(request.query_scope.get("include_unmapped", False))
        normalized_rows = [*selected_rows.values(), *unknown_rows] if include_unmapped \
            else list(selected_rows.values())
        currency_default = str(request.query_scope.get("currency", ""))
        currency_map = {str(key).upper(): str(value) for key, value in
                        (request.query_scope.get("currency_by_entity", {}) or {}).items()}
        records = []
        for row in normalized_rows:
            if row["item_value"] is None:
                continue
            symbol = str(row["symbol"]).upper()
            item_name = str(row["item_name"])
            provider_field = _defeatbeta_provider_field(symbol, item_name)
            metric = _DEFEATBETA_TO_METRIC.get(provider_field)
            currency = _entity_statement_currency(
                symbol, default=currency_default, overrides=currency_map)
            value = float(row["item_value"])
            if item_name in _CAPEX_FIELDS:
                value = abs(value)
            records.append(NativeRecord(
                entity_id=symbol, provider_field=provider_field,
                period=str(row["report_date"])[:10], value=value,
                unit=_statement_unit(provider_field, currency), currency=currency,
                # Balance-sheet rows are point-in-time facts even when the Yahoo
                # mirror labels the statement as "quarterly".  This makes debt,
                # cash and inventory selectable alongside SEC instant facts.
                period_basis=("instant" if metric in _INSTANT_METRICS else
                              "annual" if str(row["period_type"]).lower() == "annual"
                              else "quarter"), adjustment=_statement_adjustment(provider_field),
                period_end=str(row["report_date"])[:10],
                dimensions={"finance_type": str(row["finance_type"]),
                            "statement_scope": "reported",
                            "share_basis": "adr" if provider_field.startswith("tsm_") else ""},
                raw={**row, "provider_field": provider_field,
                     "source_item_name": item_name}))
        loader = self.snapshot_loader or defeatbeta.dataset_snapshot
        snapshot = loader(
            now=fetched_at, dataset_file="stock_statement.parquet")
        version = getattr(snapshot, "updated_at", "") or fetched_at.isoformat()
        coverage = {
            "entities": entities,
            "first_period": min((record.period for record in records), default=""),
            "last_period": max((record.period for record in records), default=""),
            "row_count": len(slice_rows), "non_null_records": len(records),
            "snapshot_updated_at": getattr(snapshot, "updated_at", ""),
            "snapshot_lag_hours": getattr(snapshot, "lag_hours", None),
            "publication_time_status": "not_supplied_by_mirror",
        }
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH,
            fetched_at=fetched_at, records=records,
            artifacts=[AdapterArtifact(
                payload=slice_rows,
                query_scope={**request.query_scope, "entities": entities,
                             "periods": request.periods},
                source_url=self.uri, source_version=version,
                media_type="application/json", retention="query_slice",
                metadata={
                    "coverage": coverage, "host": "huggingface",
                    "dataset": "defeatbeta/yahoo-finance-data",
                    "upstream_claim": "Yahoo Finance structured statement data",
                    "unmapped_rows_excluded": 0 if include_unmapped else len(unknown_rows),
                })],
            provider_metadata={"source_version": version, "coverage": coverage})


_YFINANCE_STATEMENT_FIELDS = {
    "income_statement": (
        ("total_revenue", ("Total Revenue", "Operating Revenue")),
        ("gross_profit", ("Gross Profit",)),
        ("operating_income", ("Operating Income", "Operating Income Or Loss")),
        ("net_income", ("Net Income", "Net Income Common Stockholders")),
        ("diluted_eps", ("Diluted EPS", "Basic EPS")),
    ),
    "cash_flow": (
        ("operating_cash_flow", (
            "Operating Cash Flow", "Total Cash From Operating Activities",
            "Cash Flow From Continuing Operating Activities")),
        ("capital_expenditure", ("Capital Expenditure", "Capital Expenditures")),
    ),
    "balance_sheet": (
        ("cash_and_cash_equivalents", ("Cash And Cash Equivalents", "Cash")),
        ("inventory", ("Inventory",)),
        ("total_debt", ("Total Debt",)),
        ("total_assets", ("Total Assets",)),
        ("total_liabilities_net_minority_interest", (
            "Total Liabilities Net Minority Interest", "Total Liabilities")),
        ("stockholders_equity", ("Stockholders Equity", "Stockholders' Equity")),
    ),
}


class YFinanceFinancialStatementsAdapter:
    """Governed ingestion of legacy yfinance statement tables only.

    This adapter deliberately never asks yfinance for price history, quote metadata,
    options or market data.  It turns the same three statement frames used by the
    legacy fundamentals reader into versioned, entity-bound financial observations.
    """

    source_id = "yfinance_financials"
    dataset_id = "company_financials"

    def __init__(self, *, ticker_factory=None, clock=None):
        self.ticker_factory = ticker_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _value(frame, labels: tuple[str, ...], column):
        for label in labels:
            if label not in frame.index:
                continue
            value = frame.loc[label, column]
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value == value:
                return label, value
        return "", None

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        if len(request.entities) != 1:
            raise ValueError("yfinance financial adapter requires exactly one entity per slice")
        symbol = request.entities[0].upper()
        fetched_at = self.clock().astimezone(timezone.utc)
        if self.ticker_factory is None:
            import yfinance as yf

            from ..base import yf_symbol

            ticker = yf.Ticker(yf_symbol(symbol))
            version = getattr(yf, "__version__", "")
        else:
            ticker = self.ticker_factory(symbol)
            version = "injected"
        frames = (
            ("quarter", "income_statement", getattr(ticker, "quarterly_income_stmt", None)),
            ("quarter", "cash_flow", getattr(ticker, "quarterly_cashflow", None)),
            ("quarter", "balance_sheet", getattr(ticker, "quarterly_balance_sheet", None)),
            ("annual", "income_statement", getattr(ticker, "income_stmt", None)),
            ("annual", "cash_flow", getattr(ticker, "cashflow", None)),
            ("annual", "balance_sheet", getattr(ticker, "balance_sheet", None)),
        )
        currency = _entity_statement_currency(
            symbol, default=str(request.query_scope.get("currency", "")),
            overrides={str(key).upper(): str(value) for key, value in
                       (request.query_scope.get("currency_by_entity", {}) or {}).items()})
        requested_periods = set(request.periods)
        since = str(request.query_scope.get("since", ""))
        records: list[NativeRecord] = []
        artifact_rows: list[dict] = []
        for period_type, statement, frame in frames:
            if frame is None or getattr(frame, "empty", True):
                continue
            for column in frame.columns:
                period = str(column)[:10]
                if (requested_periods and period not in requested_periods) or \
                        (since and period < since):
                    continue
                for provider_field, labels in _YFINANCE_STATEMENT_FIELDS[statement]:
                    label, value = self._value(frame, labels, column)
                    if value is None:
                        continue
                    if provider_field in _CAPEX_FIELDS:
                        value = abs(value)
                    basis = "instant" if provider_field in {
                        "cash_and_cash_equivalents", "inventory", "total_debt",
                        "total_assets", "total_liabilities_net_minority_interest",
                        "stockholders_equity",
                    } else period_type
                    raw = {
                        "statement": statement, "period_type": period_type,
                        "provider_label": label, "provider_field": provider_field,
                        "raw_value": value, "report_date": period,
                    }
                    artifact_rows.append(raw)
                    records.append(NativeRecord(
                        entity_id=symbol, provider_field=provider_field, period=period,
                        value=value, unit=_statement_unit(provider_field, currency),
                        currency=currency, period_basis=basis,
                        adjustment=_statement_adjustment(provider_field),
                        period_end=period,
                        dimensions={"statement": statement, "statement_scope": "reported"},
                        raw=raw))
        coverage = {
            "entity": symbol, "first_period": min((item.period for item in records), default=""),
            "last_period": max((item.period for item in records), default=""),
            "record_count": len(records), "currency": currency,
            "market_data_requested": False,
        }
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH,
            fetched_at=fetched_at, records=records,
            artifacts=[AdapterArtifact(
                payload=artifact_rows,
                query_scope={**request.query_scope, "entity": symbol,
                             "periods": request.periods},
                source_url=f"https://finance.yahoo.com/quote/{symbol}/financials",
                source_version=f"yfinance:{version}", media_type="application/json",
                retention="query_slice", metadata={"coverage": coverage,
                                                    "provider": "Yahoo Finance via yfinance"})],
            provider_metadata={"source_version": f"yfinance:{version}",
                               "coverage": coverage})
