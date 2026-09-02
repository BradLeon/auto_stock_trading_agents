"""SEC XBRL and defeatbeta stock_statement governed adapters."""

from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pandas as pd

from ats.data.defeatbeta import DatasetSnapshot
from ats.data.sources.company_financials import (
    CompanyDisclosuresAdapter,
    DefeatBetaStatementAdapter,
    SECCompanyFactsAdapter,
    YFinanceFinancialStatementsAdapter,
    parse_amazon_quarterly_release,
    parse_companyfacts,
    parse_tsmc_quarterly_release,
)
from ats.data.structured import (
    FetchRequest,
    IngestionPipeline,
    SQLiteStructuredRepository,
    StructuredCatalog,
)
from ats.data.structured.quality import financial_quality


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)

TSMC_Q2_RELEASE = """
TSMC Reports Second Quarter EPS of NT$27.25. TSMC today announced consolidated revenue
of NT$1,270.38 billion, net income of NT$706.56 billion, and diluted earnings per share
of NT$27.25 (US$4.31 per ADR unit) for the second quarter ended June 30, 2026. All figures were prepared in
accordance with TIFRS on a consolidated basis. TSMC's 2026 second quarter consolidated
results: (Unit: NT$ million, except for EPS) 2Q26 Amount Net sales 1,270,381 Gross profit
860,311 Income from operations 766,603 Income before tax 862,430 Net income 706,562
EPS (NT$) 27.25.
"""

AMAZON_Q2_RELEASE = """
AMAZON.COM ANNOUNCES SECOND QUARTER RESULTS. Amazon.com announced financial results for
its second quarter ended June 30, 2026. Consolidated Statements of Cash Flows (in millions)
Three Months Ended June 30, Six Months Ended June 30, Twelve Months Ended June 30, 2025 2026
2025 2026 2025 2026 Net cash provided by (used in) operating activities 32,515 45,387 49,530
71,419 121,137 161,403 Purchases of property and equipment (32,183) (54,208) (57,202)
(98,411) (107,656) (173,028) Consolidated Statements of Operations (in millions, except per
share data) Three Months Ended June 30, Six Months Ended June 30, 2025 2026 2025 2026 Total
net sales 167,702 200,606 323,369 382,125 Operating expenses: Cost of sales 80,809 95,778
157,785 183,241 Operating income 19,171 27,461 37,576 51,313 Net income 18,164 62,647 35,291
92,902 Diluted earnings per share $ 1.68 $ 5.75 $ 3.27 $ 8.53 Consolidated Statements of
Comprehensive Income
"""


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
        "DebtLongtermAndShorttermCombinedAmount": {"units": {currency: [
            _fact(30, start="")] }},
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
    by_metric = {row.provider_field: row for row in records}
    assert by_metric["us-gaap:DebtLongtermAndShorttermCombinedAmount"].value == 30
    assert by_metric["us-gaap:LongTermDebtAndFinanceLeaseObligations"].value == 25


def test_companyfacts_derives_missing_standard_rows_from_official_xbrl_components():
    payload = _companyfacts()
    facts = payload["facts"]["us-gaap"]
    facts.pop("GrossProfit")
    facts.pop("Liabilities")
    facts.pop("DebtLongtermAndShorttermCombinedAmount")
    facts["CostOfGoodsAndServicesSold"] = {"units": {"USD": [_fact(42)]}}
    facts["LiabilitiesAndStockholdersEquity"] = {"units": {"USD": [_fact(200, start="")]}}
    facts["LongTermDebtCurrent"] = {"units": {"USD": [_fact(5, start="")]}}
    facts["LongTermDebtNoncurrent"] = {"units": {"USD": [_fact(25, start="")]}}

    records = {row.provider_field: row for row in parse_companyfacts(payload, symbol="MSFT")}

    assert records["derived:revenue_minus_cost_of_revenue"].value == 60
    assert records["derived:liabilities_and_equity_minus_equity"].value == 120
    assert records["derived:current_debt_plus_noncurrent_debt"].value == 30
    assert records["derived:current_debt_plus_noncurrent_debt"].raw["calculation"] == \
        "long_term_debt_current + long_term_debt_noncurrent"


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


def test_tsmc_official_release_parses_reported_quarter_only() -> None:
    records = parse_tsmc_quarterly_release(TSMC_Q2_RELEASE, published_at=NOW)

    assert {row.provider_field: row.value for row in records} == {
        "tsmc_release:net_sales": 1_270_381_000_000.0,
        "tsmc_release:gross_profit": 860_311_000_000.0,
        "tsmc_release:operating_income": 766_603_000_000.0,
        "tsmc_release:net_income": 706_562_000_000.0,
        "tsmc_release:diluted_eps": 27.25,
        "tsmc_release:diluted_eps_adr": 4.31,
    }
    assert {row.period for row in records} == {"2026-06-30"}
    assert {row.period_start for row in records} == {"2026-04-01"}
    assert {row.currency for row in records} == {"TWD", "USD"}
    assert next(row for row in records
                if row.provider_field == "tsmc_release:diluted_eps_adr").unit == "USD/ADR"
    assert all(row.dimensions["taxonomy"] == "TIFRS" for row in records)


def test_amazon_official_release_parses_current_quarter_core_fields() -> None:
    records = parse_amazon_quarterly_release(AMAZON_Q2_RELEASE, published_at=NOW)
    by_field = {row.provider_field: row for row in records}

    assert by_field["amzn_release:net_sales"].value == 200_606_000_000
    assert by_field["amzn_release:gross_profit_derived"].value == 104_828_000_000
    assert by_field["amzn_release:capex"].value == 54_208_000_000
    assert by_field["amzn_release:diluted_eps"].value == 5.75
    assert by_field["amzn_release:gross_profit_derived"].raw["calculation"] == \
        "Total net sales - Cost of sales"


def test_company_disclosures_adapter_ingests_verified_tsm_release(tmp_path):
    repo = _repo(tmp_path)
    result = SimpleNamespace(
        status="succeeded", stage="exhibit", record={
            "text": TSMC_Q2_RELEASE, "filed": NOW.date(),
            "source_url": "https://www.sec.gov/Archives/edgar/data/1046179/release.htm",
            "accession": "0001046179-26-000451", "cik": "0001046179", "form_type": "6-K",
        })
    adapter = CompanyDisclosuresAdapter(release_fetcher=lambda *_, **__: result,
                                        clock=lambda: NOW)
    request = FetchRequest(
        source_id="company_disclosures", dataset_id="company_financials", entities=["TSM"],
        query_scope={"near": "2026-07-16", "period": "Q2 FY2026"})

    outcome = IngestionPipeline(repo).run(adapter, request)
    rows = repo.observations(entity_id="TSM")

    assert outcome["status"] == "succeeded" and outcome["accepted"] == 6
    assert {row["metric_id"] for row in rows} == {
        "financial.revenue.gaap", "financial.gross_profit.gaap",
        "financial.operating_income.gaap", "financial.net_income.gaap",
        "financial.eps.diluted.gaap", "financial.eps.diluted.adr"}
    assert {row["period"] for row in rows} == {"2026-06-30"}
    assert {row["source_id"] for row in rows} == {"company_disclosures"}
    by_metric = {row["metric_id"]: row for row in rows}
    assert by_metric["financial.revenue.gaap"]["value"] == 1_270_381_000_000.0
    assert by_metric["financial.net_income.gaap"]["value"] == 706_562_000_000.0
    assert by_metric["financial.eps.diluted.gaap"]["value"] == 27.25
    assert by_metric["financial.eps.diluted.adr"]["value"] == 4.31
    assert by_metric["financial.eps.diluted.adr"]["unit"] == "USD/ADR"
    artifact = repo.lineage(rows[0]["observation_id"])["artifact"]
    assert artifact["source_version"] == "0001046179-26-000451"
    assert artifact["source_url"].endswith("release.htm")


def test_company_disclosures_explicitly_reports_non_tsm_as_no_coverage(tmp_path):
    repo = _repo(tmp_path)
    adapter = CompanyDisclosuresAdapter(clock=lambda: NOW)
    request = FetchRequest(
        source_id="company_disclosures", dataset_id="company_financials", entities=["MSFT"],
        query_scope={"near": "2026-07-16", "period": "Q2 FY2026"})

    outcome = IngestionPipeline(repo).run(adapter, request)

    assert outcome["status"] == "no_coverage"
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
        query_scope={"currency": "USD", "since": "2026-01-01", "include_unmapped": True})

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


def test_defeatbeta_uses_entity_currency_and_instant_basis_for_balance_sheet(tmp_path):
    repo = _repo(tmp_path)
    connection = _Connection([
        ("TSM", "2026-06-30", "total_revenue", Decimal("1270381000000"),
         "income_statement", "quarterly"),
        ("TSM", "2026-06-30", "capital_expenditure", Decimal("-496002000000"),
         "cash_flow", "quarterly"),
        ("TSM", "2026-06-30", "operating_cash_flow", Decimal("783365000000"),
         "cash_flow", "quarterly"),
        ("TSM", "2026-06-30", "total_debt", Decimal("982447000000"),
         "balance_sheet", "quarterly"),
        ("TSM", "2026-06-30", "diluted_eps", Decimal("136.23"),
         "income_statement", "quarterly"),
        ("TSM", "2026-06-30", "unmapped_yahoo_field", Decimal("1"),
         "balance_sheet", "quarterly"),
    ])
    adapter = DefeatBetaStatementAdapter(
        uri="fixture.parquet", connection_factory=lambda _: connection,
        snapshot_loader=lambda **_: DatasetSnapshot(updated_at=NOW.isoformat()),
        clock=lambda: NOW)

    result = IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="defeatbeta_stock_statement", dataset_id="company_financials",
        entities=["TSM"], periods=["2026-06-30"]))

    rows = {row["metric_id"]: row for row in repo.observations(entity_id="TSM")}
    assert result["accepted"] == 5 and result["quarantined"] == 0
    assert rows["financial.capex.gaap"]["value"] == 496_002_000_000
    assert rows["financial.capex.gaap"]["currency"] == "TWD"
    assert rows["financial.total_debt.provider_reported"]["period_basis"] == "instant"
    assert rows["financial.eps.diluted.adr"]["value"] == 136.23
    assert rows["financial.eps.diluted.adr"]["unit"] == "TWD/ADR"
    assert repo.pending_mappings() == []


def test_yfinance_financials_is_statement_only_and_preserves_quarter_fields(tmp_path):
    repo = _repo(tmp_path)
    quarter = pd.Timestamp("2026-06-30")
    annual = pd.Timestamp("2025-12-31")

    def frame(rows):
        return pd.DataFrame({quarter: [value[0] for value in rows.values()],
                             annual: [value[1] for value in rows.values()]},
                            index=list(rows))

    ticker = SimpleNamespace(
        quarterly_income_stmt=frame({
            "Total Revenue": (200_606_000_000, 213_386_000_000),
            "Gross Profit": (103_000_000_000, 103_427_000_000),
            "Operating Income": (27_461_000_000, 24_977_000_000),
            "Net Income": (62_647_000_000, 21_192_000_000),
            "Diluted EPS": (5.75, 2.00),
        }),
        quarterly_cashflow=frame({
            "Operating Cash Flow": (45_387_000_000, 54_459_000_000),
            "Capital Expenditure": (-44_203_000_000, -39_522_000_000),
        }),
        quarterly_balance_sheet=frame({
            "Cash And Cash Equivalents": (80_000_000_000, 75_000_000_000),
            "Inventory": (40_000_000_000, 38_000_000_000),
            "Total Debt": (132_995_000_000, 120_000_000_000),
            "Total Assets": (650_000_000_000, 620_000_000_000),
            "Total Liabilities Net Minority Interest": (390_000_000_000, 370_000_000_000),
            "Stockholders Equity": (260_000_000_000, 250_000_000_000),
        }),
        income_stmt=pd.DataFrame(), cashflow=pd.DataFrame(), balance_sheet=pd.DataFrame(),
    )
    adapter = YFinanceFinancialStatementsAdapter(
        ticker_factory=lambda symbol: ticker, clock=lambda: NOW)

    result = IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="yfinance_financials", dataset_id="company_financials",
        entities=["AMZN"], periods=["2026-06-30"]))

    rows = {row["metric_id"]: row for row in repo.observations(entity_id="AMZN")}
    assert result["status"] == "succeeded" and result["quarantined"] == 0
    assert rows["financial.gross_profit.gaap"]["value"] == 103_000_000_000
    assert rows["financial.capex.gaap"]["value"] == 44_203_000_000
    assert rows["financial.eps.diluted.market_adjusted"]["value"] == 5.75
    assert rows["financial.eps.diluted.market_adjusted"]["adjustment"] == "split_adjusted"
    assert rows["financial.total_debt.provider_reported"]["period_basis"] == "instant"
    assert rows["financial.total_debt.provider_reported"]["adjustment"] == "provider_reported"
    assert {row["currency"] for row in rows.values()} == {"USD"}
    artifact = repo.lineage(rows["financial.revenue.gaap"]["observation_id"])["artifact"]
    metadata = json.loads(artifact["metadata_json"])
    assert metadata["coverage"]["market_data_requested"] is False
    assert "financials" in artifact["source_url"]


def test_official_and_mirror_remain_parallel_and_configured_report_source_wins(tmp_path):
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

    from ats.data.products import DataProducts

    products = DataProducts(structured_repository=repo)
    loose = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials",
        quality="loose")

    assert loose["rows"][0]["source_id"] == "defeatbeta_stock_statement"
    assert loose["rows"][0]["value"] == 99
    assert loose["rows"][0]["conflict"] is True
    assert repo.conn.execute("SELECT count(*) FROM structured_conflicts").fetchone()[0] >= 1


def test_market_adjusted_eps_and_provider_debt_do_not_conflict_with_official_series(tmp_path):
    repo = _repo(tmp_path)
    sec_adapter = SECCompanyFactsAdapter(
        client=_SECClient(_companyfacts()), clock=lambda: NOW)
    IngestionPipeline(repo).run(sec_adapter, FetchRequest(
        source_id="sec_companyfacts", dataset_id="company_financials",
        entities=["MSFT"], periods=["2026-06-30"]))
    mirror = DefeatBetaStatementAdapter(
        uri="fixture.parquet", connection_factory=lambda _: _Connection([
            ("MSFT", "2026-06-30", "diluted_eps", Decimal("0.25"),
             "income_statement", "quarterly"),
            ("MSFT", "2026-06-30", "total_debt", Decimal("50.00"),
             "balance_sheet", "quarterly"),
        ]), snapshot_loader=lambda **_: DatasetSnapshot(updated_at=NOW.isoformat()),
        clock=lambda: NOW)
    outcome = IngestionPipeline(repo).run(mirror, FetchRequest(
        source_id="defeatbeta_stock_statement", dataset_id="company_financials",
        entities=["MSFT"], periods=["2026-06-30"], query_scope={"currency": "USD"}))

    assert outcome["accepted"] == 2
    assert repo.conn.execute("SELECT count(*) FROM structured_conflicts").fetchone()[0] == 0
    rows = {row["metric_id"]: row for row in repo.observations(entity_id="MSFT")}
    assert rows["financial.eps.diluted.market_adjusted"]["adjustment"] == "split_adjusted"
    assert rows["financial.total_debt.provider_reported"]["adjustment"] == "provider_reported"


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
    from ats.data.products import DataProducts

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
