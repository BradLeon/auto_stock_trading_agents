"""SEC XBRL and defeatbeta stock_statement governed adapters."""

from datetime import datetime, timezone
from decimal import Decimal
import json

from ats.data.defeatbeta import DatasetSnapshot
from ats.data.sources.company_financials import (
    DefeatBetaStatementAdapter,
    SECCompanyFactsAdapter,
    parse_companyfacts,
)
from ats.structured import (
    FetchRequest,
    IngestionPipeline,
    SQLiteStructuredRepository,
    StructuredCatalog,
)
from ats.structured.quality import financial_quality


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}

    def json(self):
        return self.body

    def raise_for_status(self):
        return None


class _SECClient:
    def __init__(self, payload, symbol="MSFT", cik=789019):
        self.payload = payload
        self.symbol = symbol
        self.cik = cik
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if url.endswith("company_tickers.json"):
            return _Response({"0": {"ticker": self.symbol, "cik_str": self.cik}})
        return _Response(self.payload, {"etag": '"companyfacts-v3"'})


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.args = []

    def execute(self, sql, args):
        self.sql = sql
        self.args = list(args)
        return self

    def fetchall(self):
        return self.rows


def _fact(value, *, start="2026-04-01", end="2026-06-30", filed="2026-07-30",
          form="10-Q", fy=2026, fp="Q4", accn="0001"):
    row = {"val": value, "end": end, "filed": filed, "form": form,
           "fy": fy, "fp": fp, "accn": accn}
    if start:
        row["start"] = start
    return row


def _companyfacts(*, taxonomy="us-gaap", currency="USD"):
    concepts = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "label": "Revenue", "units": {currency: [
                _fact(100, filed="2026-07-29", accn="old"),
                _fact(102, filed="2026-07-30", accn="new"),
                _fact(190, start="2026-01-01", filed="2026-07-30", accn="ytd"),
            ]}},
        "GrossProfit": {"units": {currency: [_fact(60)]}},
        "OperatingIncomeLoss": {"units": {currency: [_fact(30)]}},
        "NetIncomeLoss": {"units": {currency: [_fact(20)]}},
        "EarningsPerShareDiluted": {"units": {f"{currency}/shares": [_fact(2.5)]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {currency: [_fact(40)]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {currency: [_fact(-10)]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {currency: [
            _fact(50, start="")] }},
        "InventoryNet": {"units": {currency: [_fact(15, start="")] }},
        "LongTermDebtAndFinanceLeaseObligations": {"units": {currency: [
            _fact(25, start="")] }},
        "Assets": {"units": {currency: [_fact(200, start="")] }},
        "Liabilities": {"units": {currency: [_fact(120, start="")] }},
        "StockholdersEquity": {"units": {currency: [_fact(80, start="")] }},
    }
    if taxonomy == "ifrs-full":
        rename = {
            "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
            "OperatingIncomeLoss": "ProfitLossFromOperatingActivities",
            "NetIncomeLoss": "ProfitLoss",
            "EarningsPerShareDiluted": "DilutedEarningsLossPerShare",
            "NetCashProvidedByUsedInOperatingActivities": "CashFlowsFromUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment": "PurchaseOfPropertyPlantAndEquipment",
            "CashAndCashEquivalentsAtCarryingValue": "CashAndCashEquivalents",
            "InventoryNet": "Inventories", "LongTermDebtAndFinanceLeaseObligations": "Borrowings",
            "StockholdersEquity": "Equity",
        }
        concepts = {rename.get(key, key): value for key, value in concepts.items()}
    return {"entityName": "Fixture Corp", "facts": {taxonomy: concepts}}


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def test_companyfacts_prefers_latest_revision_and_distinguishes_quarter_ytd_instant():
    records = parse_companyfacts(_companyfacts(), symbol="MSFT")
    revenue = [row for row in records if "Revenue" in row.provider_field]

    assert {(row.period_basis, row.value) for row in revenue} == {
        ("quarter", 102.0), ("ytd", 190.0)}
    assert all(row.period == "2026-06-30" for row in revenue)
    assert next(row for row in records if row.provider_field.endswith("Assets")).period_basis \
        == "instant"
    assert next(row for row in records
                if "PaymentsToAcquire" in row.provider_field).value == 10
    assert next(row for row in records
                if "EarningsPerShare" in row.provider_field).unit == "USD/share"


def test_sec_adapter_ingests_full_artifact_and_ifrs_foreign_issuer(tmp_path):
    repo = _repo(tmp_path)
    payload = _companyfacts(taxonomy="ifrs-full", currency="TWD")
    adapter = SECCompanyFactsAdapter(
        client=_SECClient(payload, symbol="TSM", cik=1046179), clock=lambda: NOW)
    request = FetchRequest(
        source_id="sec_companyfacts", dataset_id="company_financials",
        entities=["TSM"], query_scope={"since": "2026-01-01"})

    result = IngestionPipeline(repo).run(adapter, request)
    rows = repo.observations(entity_id="TSM")

    assert result["status"] == "succeeded" and result["quarantined"] == 0
    assert {row["currency"] for row in rows} == {"TWD"}
    assert {row["metric_id"] for row in rows} >= {
        "financial.revenue.gaap", "financial.total_assets.gaap",
        "financial.stockholders_equity.gaap"}
    lineage = repo.lineage(rows[0]["observation_id"])
    assert lineage["artifact"]["source_url"].endswith("CIK0001046179.json")
    assert lineage["artifact"]["source_version"] == '"companyfacts-v3"'
    assert json.loads(repo.artifacts.read(
        lineage["artifact"]["relative_path"]))["entityName"] == "Fixture Corp"


def test_sec_missing_cik_is_no_coverage_not_wrong_entity(tmp_path):
    repo = _repo(tmp_path)
    adapter = SECCompanyFactsAdapter(
        client=_SECClient({}, symbol="OTHER"), clock=lambda: NOW)
    request = FetchRequest(
        source_id="sec_companyfacts", dataset_id="company_financials",
        entities=["MISSING"])

    result = IngestionPipeline(repo).run(adapter, request)

    assert result["status"] == "no_coverage"
    assert repo.observations() == []


def test_defeatbeta_predicate_slice_mapping_pending_pool_and_provenance(tmp_path):
    repo = _repo(tmp_path)
    connection = _Connection([
        ("MSFT", "2026-06-30", "total_revenue", Decimal("102.00"),
         "income_statement", "quarterly"),
        ("MSFT", "2026-06-30", "operating_revenue", Decimal("101.00"),
         "income_statement", "quarterly"),
        ("MSFT", "2026-06-30", "capital_expenditure", Decimal("-10.00"),
         "cash_flow", "quarterly"),
        ("MSFT", "2026-06-30", "mystery_row", Decimal("7.00"),
         "income_statement", "quarterly"),
        ("MSFT", "2026-06-30", "inventory", None, "balance_sheet", "quarterly"),
    ])
    snapshot = DatasetSnapshot(
        updated_at="2026-08-24T00:00:00+00:00", checked_at=NOW.isoformat(), lag_hours=32)
    adapter = DefeatBetaStatementAdapter(
        uri="https://example.test/stock_statement.parquet",
        connection_factory=lambda _: connection,
        snapshot_loader=lambda **_: snapshot, clock=lambda: NOW)
    request = FetchRequest(
        source_id="defeatbeta_stock_statement", dataset_id="company_financials",
        entities=["MSFT"], periods=["2026-06-30"],
        query_scope={"currency": "USD", "since": "2026-01-01"})

    result = IngestionPipeline(repo).run(adapter, request)
    rows = repo.observations()

    assert "symbol IN (?)" in connection.sql and "report_date IN (?)" in connection.sql
    assert connection.args == ["MSFT", "2026-01-01", "2026-06-30"]
    assert result["accepted"] == 2 and result["quarantined"] == 1
    assert {row["metric_id"]: row["value"] for row in rows} == {
        "financial.revenue.gaap": 102.0, "financial.capex.gaap": 10.0}
    pending = repo.pending_mappings()
    assert [row["provider_field"] for row in pending] == ["mystery_row"]
    artifact = repo.lineage(rows[0]["observation_id"])["artifact"]
    metadata = json.loads(artifact["metadata_json"])
    assert metadata["host"] == "huggingface"
    assert metadata["dataset"] == "defeatbeta/yahoo-finance-data"
    assert artifact["source_version"] == snapshot.updated_at


def test_official_and_mirror_remain_parallel_and_official_wins(tmp_path):
    repo = _repo(tmp_path)
    sec_adapter = SECCompanyFactsAdapter(
        client=_SECClient(_companyfacts()), clock=lambda: NOW)
    sec_request = FetchRequest(
        source_id="sec_companyfacts", dataset_id="company_financials",
        entities=["MSFT"], periods=["2026-06-30"])
    IngestionPipeline(repo).run(sec_adapter, sec_request)
    connection = _Connection([
        ("MSFT", "2026-06-30", "total_revenue", Decimal("99.00"),
         "income_statement", "quarterly")])
    mirror = DefeatBetaStatementAdapter(
        uri="fixture.parquet", connection_factory=lambda _: connection,
        snapshot_loader=lambda **_: DatasetSnapshot(updated_at=NOW.isoformat()),
        clock=lambda: NOW)
    mirror_request = FetchRequest(
        source_id="defeatbeta_stock_statement", dataset_id="company_financials",
        entities=["MSFT"], periods=["2026-06-30"], query_scope={"currency": "USD"})
    IngestionPipeline(repo).run(mirror, mirror_request)

    from ats.data_platform import DataProducts

    products = DataProducts(structured_repository=repo)
    loose = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials",
        quality="loose")

    assert loose["rows"][0]["source_id"] == "sec_companyfacts"
    assert loose["rows"][0]["value"] == 102
    assert loose["rows"][0]["conflict"] is True
    assert repo.conn.execute("SELECT count(*) FROM structured_conflicts").fetchone()[0] >= 1


def test_report_date_identity_supports_different_fiscal_year_ends():
    amazon = _companyfacts()
    amazon["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"] \
        ["units"]["USD"] = [_fact(
            200, start="2025-10-01", end="2025-12-31", fy=2025, fp="FY")]
    kla = _companyfacts()
    kla["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"] \
        ["units"]["USD"] = [_fact(
            150, start="2026-04-01", end="2026-06-30", fy=2026, fp="Q4")]

    amazon_row = next(row for row in parse_companyfacts(amazon, symbol="AMZN")
                      if "Revenue" in row.provider_field)
    kla_row = next(row for row in parse_companyfacts(kla, symbol="KLAC")
                   if "Revenue" in row.provider_field)

    assert (amazon_row.period, amazon_row.raw["fiscal_label"]) == ("2025-12-31", "FY2025FY")
    assert (kla_row.period, kla_row.raw["fiscal_label"]) == ("2026-06-30", "FY2026Q4")
    assert amazon_row.period_basis == "quarter" and kla_row.period_basis == "quarter"


def test_mirror_missing_entity_is_explicit_zero_match(tmp_path):
    repo = _repo(tmp_path)
    connection = _Connection([])
    adapter = DefeatBetaStatementAdapter(
        uri="fixture.parquet", connection_factory=lambda _: connection,
        snapshot_loader=lambda **_: DatasetSnapshot(updated_at=NOW.isoformat()),
        clock=lambda: NOW)
    request = FetchRequest(
        source_id="defeatbeta_stock_statement", dataset_id="company_financials",
        entities=["MIRROR_MISSING"], query_scope={"currency": "USD"})

    result = IngestionPipeline(repo).run(adapter, request)

    assert result["status"] == "zero_match" and repo.observations() == []


def test_financial_quality_checks_identity_continuity_basis_and_unit_scale():
    base = {
        "source_id": "sec_companyfacts", "entity_id": "MSFT", "period": "2026-06-30",
        "period_end": "2026-06-30", "unit": "USD", "currency": "USD",
        "period_basis": "instant", "value": 0,
    }
    rows = [
        base | {"metric_id": "financial.total_assets.gaap", "value": 200},
        base | {"metric_id": "financial.total_liabilities.gaap", "value": 120},
        base | {"metric_id": "financial.stockholders_equity.gaap", "value": 80},
        base | {"metric_id": "financial.revenue.gaap", "period_basis": "quarter",
                "period": "2026-03-31", "period_end": "2026-03-31", "value": 100},
        base | {"metric_id": "financial.revenue.gaap", "period_basis": "quarter",
                "value": 110},
        base | {"metric_id": "financial.revenue.gaap", "period_basis": "ytd",
                "value": 190},
    ]
    good = financial_quality(rows)
    bad_rows = [dict(row) for row in rows]
    bad_rows[0]["value"] = 250
    bad_rows[4]["value"] = 110_000_000
    bad_rows[5]["value"] = 90
    bad = financial_quality(bad_rows)

    assert good["status"] == "passed"
    assert good["checks"]["balance_sheet_identities"] == 1
    assert good["checks"]["quarter_ytd_pairs"] == 1
    assert {issue["code"] for issue in bad["issues"]} >= {
        "balance_sheet_identity", "unit_scale_jump", "ytd_less_than_quarter"}


def test_core_financial_formulas_are_versioned_query_time_results(tmp_path):
    repo = _repo(tmp_path)
    adapter = SECCompanyFactsAdapter(
        client=_SECClient(_companyfacts()), clock=lambda: NOW)
    IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="sec_companyfacts", dataset_id="company_financials",
        entities=["MSFT"], periods=["2026-06-30"]))
    from ats.data_platform import DataProducts

    products = DataProducts(structured_repository=repo)
    fcf = products.financial_derived(metric="financial.free_cash_flow", entity="MSFT")
    gross = products.financial_derived(
        metric="financial.gross_margin.gaap", entity="MSFT")
    operating = products.financial_derived(
        metric="financial.operating_margin.gaap", entity="MSFT")

    assert fcf["rows"][0]["value"] == 30
    assert gross["rows"][0]["value"] == 60 / 102
    assert operating["rows"][0]["value"] == 30 / 102
    assert gross["rows"][0]["unit"] == "ratio"
    assert len(gross["rows"][0]["lineage_observation_ids"]) == 2
    assert {row["definition_version"] for row in repo.derivations()} == {"v1"}
