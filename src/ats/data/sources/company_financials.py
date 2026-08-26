"""Official SEC Company Facts and defeatbeta statement-slice adapters."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
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
    "financial.total_debt.gaap": [
        "us-gaap:LongTermDebtAndFinanceLeaseObligations", "us-gaap:LongTermDebt",
        "ifrs-full:Borrowings"],
    "financial.total_assets.gaap": ["us-gaap:Assets", "ifrs-full:Assets"],
    "financial.total_liabilities.gaap": ["us-gaap:Liabilities", "ifrs-full:Liabilities"],
    "financial.stockholders_equity.gaap": [
        "us-gaap:StockholdersEquity", "ifrs-full:Equity"],
}
_FIELD_TO_METRIC = {field: metric for metric, fields in XBRL_CONCEPTS.items()
                    for field in fields}
_FIELD_PRIORITY = {field: rank for fields in XBRL_CONCEPTS.values()
                   for rank, field in enumerate(fields)}
_INSTANT_METRICS = {
    "financial.cash_and_equivalents.gaap", "financial.inventory.gaap",
    "financial.total_debt.gaap", "financial.total_assets.gaap",
    "financial.total_liabilities.gaap", "financial.stockholders_equity.gaap",
}
_EPS_METRICS = {"financial.eps.diluted.gaap"}
_CAPEX_FIELDS = {"capital_expenditure", "purchase_of_ppe"}
DEFEATBETA_CONCEPTS = {
    "financial.revenue.gaap": ["total_revenue", "operating_revenue"],
    "financial.gross_profit.gaap": ["gross_profit"],
    "financial.operating_income.gaap": [
        "operating_income", "total_operating_income_as_reported"],
    "financial.net_income.gaap": ["net_income", "net_income_common_stockholders"],
    "financial.eps.diluted.gaap": ["diluted_eps"],
    "financial.cash_from_operations.gaap": [
        "operating_cash_flow", "cash_flow_from_continuing_operating_activities"],
    "financial.capex.gaap": ["capital_expenditure", "purchase_of_ppe"],
    "financial.cash_and_equivalents.gaap": ["cash_and_cash_equivalents", "cash"],
    "financial.inventory.gaap": ["inventory"],
    "financial.total_debt.gaap": ["total_debt"],
    "financial.total_assets.gaap": ["total_assets"],
    "financial.total_liabilities.gaap": ["total_liabilities_net_minority_interest"],
    "financial.stockholders_equity.gaap": ["stockholders_equity"],
}
_DEFEATBETA_TO_METRIC = {field: metric for metric, fields in DEFEATBETA_CONCEPTS.items()
                         for field in fields}
_DEFEATBETA_PRIORITY = {field: rank for fields in DEFEATBETA_CONCEPTS.values()
                        for rank, field in enumerate(fields)}


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
    facts_root = payload.get("facts", {}) if isinstance(payload, dict) else {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        for concept, body in (facts_root.get(taxonomy, {}) or {}).items():
            provider_field = f"{taxonomy}:{concept}"
            metric = _FIELD_TO_METRIC.get(provider_field)
            if not metric:
                continue
            for xbrl_unit, facts in (body.get("units", {}) or {}).items():
                currency, unit = _currency_and_unit(metric, xbrl_unit)
                for fact in facts or []:
                    if str(fact.get("form", "")).upper() not in {
                            "10-Q", "10-K", "20-F", "40-F", "6-K"}:
                        continue
                    end = str(fact.get("end", ""))[:10]
                    if not end or fact.get("val") is None:
                        continue
                    basis = _period_basis(metric, fact)
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
                    key = (metric, end, basis)
                    rank = (_FIELD_PRIORITY[provider_field],
                            str(fact.get("filed", "")), str(fact.get("accn", "")))
                    current = candidates.get(key)
                    # Lower alias priority wins; within one concept the latest filing wins.
                    if current is None or rank[0] < current[0][0] or (
                            rank[0] == current[0][0] and rank[1:] > current[0][1:]):
                        candidates[key] = (rank, record)
    return sorted((record for _, record in candidates.values()),
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


def _statement_unit(item_name: str, currency: str) -> str:
    return f"{currency}/share" if item_name in {"diluted_eps", "basic_eps"} else currency


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
            item_name = str(row["item_name"])
            metric = _DEFEATBETA_TO_METRIC.get(item_name)
            if metric is None:
                unknown_rows.append(row)
                continue
            key = (str(row["symbol"]).upper(), str(row["report_date"])[:10],
                   str(row["period_type"]), metric)
            current = selected_rows.get(key)
            if current is None or _DEFEATBETA_PRIORITY[item_name] < \
                    _DEFEATBETA_PRIORITY[str(current["item_name"])]:
                selected_rows[key] = row
        normalized_rows = [*selected_rows.values(), *unknown_rows]
        currency_default = str(request.query_scope.get("currency", ""))
        currency_map = {str(key).upper(): str(value) for key, value in
                        (request.query_scope.get("currency_by_entity", {}) or {}).items()}
        records = []
        for row in normalized_rows:
            if row["item_value"] is None:
                continue
            symbol = str(row["symbol"]).upper()
            item_name = str(row["item_name"])
            currency = currency_map.get(symbol, currency_default)
            value = float(row["item_value"])
            if item_name in _CAPEX_FIELDS:
                value = abs(value)
            records.append(NativeRecord(
                entity_id=symbol, provider_field=item_name,
                period=str(row["report_date"])[:10], value=value,
                unit=_statement_unit(item_name, currency), currency=currency,
                period_basis="annual" if str(row["period_type"]).lower() == "annual"
                else "quarter", adjustment="gaap",
                period_end=str(row["report_date"])[:10],
                dimensions={"finance_type": str(row["finance_type"]),
                            "statement_scope": "reported"}, raw=row))
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
                })],
            provider_metadata={"source_version": version, "coverage": coverage})
