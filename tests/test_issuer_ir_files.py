"""Issuer IR statement files (TSM FS.pdf, ASML results presentation, SKHY Euroland export).

The text/row fixtures under tests/fixtures/issuer_ir are the pages and rows read from the real
files by pdfplumber / xlrd (file name and sha256 are recorded inside each fixture).  The
`real_file` tests run the same expectations against the files in var/ir_drop when present.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from ats.data.sources.issuer_ir_files import (
    IssuerIRFilesAdapter,
    ParseError,
    build_records,
    load_issuer_configs,
)
from ats.data.sources.issuer_ir_parsers import (
    parse_asml_presentation_pages,
    parse_asml_presentation_pdf,
    parse_euroland_rows,
    parse_euroland_xls,
    parse_tsmc_fs_pages,
    parse_tsmc_fs_pdf,
)
from ats.data.structured import (
    FetchRequest,
    IngestionPipeline,
    SQLiteStructuredRepository,
    StructuredCatalog,
)

FIXTURES = Path(__file__).parent / "fixtures" / "issuer_ir"
REPO = Path(__file__).resolve().parents[1]
CORE_METRICS = {
    "financial.revenue.gaap", "financial.gross_profit.gaap", "financial.operating_income.gaap",
    "financial.net_income.gaap", "financial.eps.diluted.gaap", "financial.cash_from_operations.gaap",
    "financial.capex.gaap", "financial.cash_and_equivalents.gaap", "financial.inventory.gaap",
    "financial.total_debt.gaap",
}
NOW = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)


def _pages(name: str) -> list[str]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["pages"]
    return [payload.get(str(number), "") for number in range(1, max(int(k) for k in payload) + 1)]


def _rows() -> list[list]:
    return json.loads((FIXTURES / "skhy_euroland_2q2026_rows.json").read_text(encoding="utf-8"))["rows"]


def _by_field(doc, period):
    return {v.field: v for v in doc.values if v.period_end == period}


def _records(doc, entity):
    config = load_issuer_configs()[entity]
    records, checks = build_records(doc, config=config, file_name="fixture", sha256="f" * 64,
                                    slice_key="k", published_at=None)
    return records, checks


def _failed(checks):
    return {(c.name, c.period) for c in checks if not c.passed and c.severity == "hard"}


# --------------------------------------------------------------------------- #
# TSM
# --------------------------------------------------------------------------- #
def test_tsm_reads_the_quarter_tables_not_the_nine_month_tables():
    doc = parse_tsmc_fs_pages(_pages("tsm_fs_3q2026_pages.json"))
    assert doc.currency == "TWD" and doc.current_period == "2026-09-30"
    assert doc.periods == ["2025-09-30", "2026-06-30", "2026-09-30"]
    q3 = _by_field(doc, "2026-09-30")
    assert q3["revenue"].value == 1_494_243          # the nine-month table says 3,898,727
    assert q3["gross_profit"].value == 992_379
    assert q3["operating_income"].value == 876_445
    assert q3["net_income"].value == 763_064         # attributable to the parent; total incl. NCI is 763,341
    assert q3["eps_diluted"].value == 29.43
    assert q3["eps_diluted_adr"].value == 147.13     # the "(2)" footnote marker is not a number
    assert q3["cash_from_operations"].value == 919_776
    # "Property, Plant and Equipment" appears under Acquisitions, Proceeds and on the balance sheet
    assert q3["capex"].value == 443_206
    assert q3["cash_and_equivalents"].value == 3_621_894
    assert q3["inventory"].value == 427_753
    assert q3["total_debt"].value == 213_198 + 770_315
    assert q3["total_debt"].derived["calculation"] == "current_portion_of_bonds_payable_and_bank_loans + bonds_payable"
    assert q3["total_assets"].value == 10_052_888 and q3["total_liabilities"].value == 3_009_660
    assert "p2" in q3["revenue"].location and "p4" in q3["cash_from_operations"].location


def test_tsm_checks_pass_and_cash_flow_bridges_to_the_balance_sheet():
    doc = parse_tsmc_fs_pages(_pages("tsm_fs_3q2026_pages.json"))
    records, checks = _records(doc, "TSM")
    assert _failed(checks) == set()
    q3 = doc.lines["2026-09-30"]
    assert q3["cfo"].value + q3["cfi"].value + q3["cff"].value + q3["fx"].value == q3["net_change"].value == 487_676
    assert q3["cash_balance_sheet"].value - doc.lines["2026-06-30"]["cash_balance_sheet"].value == 487_676
    assert not any(r.raw.get("quarantine_reasons") for r in records)
    assert {r.currency for r in records} == {"TWD"}
    adr = next(r for r in records if r.provider_field == "issuer_ir:eps_diluted_adr" and r.period == "2026-09-30")
    assert (adr.unit, adr.value) == ("TWD/ADR", 147.13)
    revenue = next(r for r in records if r.provider_field == "issuer_ir:revenue" and r.period == "2026-09-30")
    assert (revenue.unit, revenue.value, revenue.period_basis) == ("TWD", 1_494_243_000_000, "quarter")
    assert revenue.raw["provenance"]["sha256"] == "f" * 64 and revenue.raw["provenance"]["location"].startswith("p2")


def test_tsm_wrong_cash_flow_number_quarantines_that_period_only():
    pages = _pages("tsm_fs_3q2026_pages.json")
    pages[3] = pages[3].replace("Net Cash Generated by Operating Activities 75,671 2,402,117 919,776",
                                "Net Cash Generated by Operating Activities 75,671 2,402,117 919,000")
    doc = parse_tsmc_fs_pages(pages)
    records, checks = _records(doc, "TSM")
    assert ("cash_flow_bridge", "2026-09-30") in _failed(checks)
    bad = [r for r in records if r.period == "2026-09-30"]
    assert bad and all("check_failed:cash_flow_bridge" in r.raw["quarantine_reasons"] for r in bad)
    assert not any(r.raw.get("quarantine_reasons") for r in records if r.period != "2026-09-30")


def test_tsm_balance_sheet_that_does_not_balance_is_quarantined():
    pages = _pages("tsm_fs_3q2026_pages.json")
    pages[0] = pages[0].replace("Total Assets $ 315,335 $ 10,052,888", "Total Assets $ 315,335 $ 10,152,888")
    _, checks = _records(parse_tsmc_fs_pages(pages), "TSM")
    assert ("balance_sheet_balances", "2026-09-30") in _failed(checks)


def test_tsm_currency_and_issuer_mismatch_quarantine_everything():
    doc = parse_tsmc_fs_pages(_pages("tsm_fs_3q2026_pages.json"))
    config = load_issuer_configs()["TSM"]
    wrong_currency = type(config)(**{**config.__dict__, "currency": "USD"})
    records, checks = build_records(doc, config=wrong_currency, file_name="f", sha256="0" * 64,
                                    slice_key="k", published_at=None)
    assert ("currency_matches_document", "") in _failed(checks)
    assert all("check_failed:currency_matches_document" in r.raw["quarantine_reasons"] for r in records)
    wrong_issuer = type(config)(**{**config.__dict__, "identity_markers": ("SOMEONE ELSE",)})
    _, checks = build_records(doc, config=wrong_issuer, file_name="f", sha256="0" * 64,
                              slice_key="k", published_at=None)
    assert ("issuer_identity", "") in _failed(checks)


def test_tsm_changed_layout_is_a_parse_error_not_a_guess():
    pages = _pages("tsm_fs_3q2026_pages.json")
    broken = [page.replace("Inventories", "Stock") for page in pages]
    with pytest.raises(ParseError, match="Inventories"):
        parse_tsmc_fs_pages(broken)
    with pytest.raises(ParseError):
        parse_tsmc_fs_pages(["not a statement"])


# --------------------------------------------------------------------------- #
# ASML
# --------------------------------------------------------------------------- #
def test_asml_reads_five_quarters_in_euros():
    doc = parse_asml_presentation_pages(_pages("asml_q3_2026_presentation_pages.json"))
    assert doc.currency == "EUR" and doc.current_period == "2026-09-30"
    assert doc.periods == ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    assert doc.published_on.isoformat() == "2026-10-14"
    q3 = _by_field(doc, "2026-09-30")
    assert q3["revenue"].value == 11_662 and q3["gross_profit"].value == 6_700
    assert q3["operating_income"].value == 5_173 and q3["net_income"].value == 4_960
    assert q3["eps_diluted"].value == 12.93 and q3["eps_basic"].value == 12.95
    assert q3["cash_from_operations"].value == 11_798
    assert q3["capex"].value == 447 and "intangible" in q3["capex"].notes["capex_scope"]
    assert q3["cash_and_equivalents"].value == 8_031       # short-term investments (4,704) are not cash
    assert q3["inventory"].value == 11_907 and q3["total_assets"].value == 58_981
    assert q3["total_liabilities"].value == 24_904 + 12_013
    assert "total_debt" not in q3                          # the presentation has no debt line (see 6-K exhibit)
    assert _by_field(doc, "2026-06-30")["revenue"].value == 9_326


def test_asml_checks_pass_and_negative_one_digit_cells_are_numbers_not_footnotes():
    doc = parse_asml_presentation_pages(_pages("asml_q3_2026_presentation_pages.json"))
    records, checks = _records(doc, "ASML")
    assert _failed(checks) == set()
    assert doc.lines["2025-09-30"]["fx"].value == -6    # "(6)" is minus six
    assert doc.lines["2026-09-30"]["fx"].value == 4
    assert {r.currency for r in records} == {"EUR"}
    assert {r.unit for r in records if r.provider_field == "issuer_ir:eps_diluted"} == {"EUR/share"}


def test_asml_free_cash_flow_mismatch_is_caught():
    pages = _pages("asml_q3_2026_presentation_pages.json")
    pages[15] = pages[15].replace("Free cash flow1 244 10,940 (2,608) 1,317 11,351",
                                  "Free cash flow1 244 10,940 (2,608) 1,317 11,000")
    _, checks = _records(parse_asml_presentation_pages(pages), "ASML")
    assert ("free_cash_flow_equals_cfo_minus_capex", "2026-09-30") in _failed(checks)


def test_asml_statements_in_dollars_are_refused():
    pages = [page.replace("(in millions €", "(in millions $") for page in _pages("asml_q3_2026_presentation_pages.json")]
    with pytest.raises(ParseError, match="not in euros"):
        parse_asml_presentation_pages(pages)


# --------------------------------------------------------------------------- #
# SKHY (Euroland, third-party aggregator)
# --------------------------------------------------------------------------- #
def test_skhy_reads_eight_quarters_and_keeps_basic_eps_separate():
    doc = parse_euroland_rows(_rows())
    assert doc.currency == "KRW" and doc.current_period == "2026-06-30" and len(doc.periods) == 8
    assert doc.meta["aggregator"] == "Euroland"
    q2 = _by_field(doc, "2026-06-30")
    assert round(q2["revenue"].value) == 79_318_746 and round(q2["net_income"].value) == 93_922_593
    assert round(q2["eps_basic"].value) == 132_707 and "eps_diluted" not in q2
    assert round(q2["total_debt"].value) == 18_586_634
    assert round(q2["capex"].value) == 10_671_435          # reported negative, stored as an outflow magnitude
    assert round(q2["total_liabilities"].value) == round(348_862_213.704 - 262_693_227.476)
    assert q2["total_liabilities"].derived["calculation"] == "total_assets - stockholders_equity"
    assert q2["revenue"].location == "row 5 'Revenue'"


def test_skhy_records_are_flagged_third_party_with_provider_defined_debt_and_warnings():
    doc = parse_euroland_rows(_rows())
    records, checks = _records(doc, "SKHY")
    assert _failed(checks) == set()
    assert {r.dimensions["source_nature"] for r in records} == {"third_party_aggregator"}
    debt = next(r for r in records if r.period == "2026-06-30" and r.provider_field.startswith("issuer_ir:total_debt"))
    assert debt.provider_field == "issuer_ir:total_debt_provider_defined" and debt.adjustment == "provider_reported"
    eps = next(r for r in records if r.period == "2026-06-30" and r.provider_field == "issuer_ir:eps_basic")
    assert (eps.unit, eps.currency) == ("KRW/share", "KRW")
    # the historical cash mismatch is reported as a warning on the affected quarters, not hidden, not fatal
    warned = {c.period for c in checks if not c.passed and c.severity == "soft"}
    assert warned == {"2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"}
    row = next(r for r in records if r.period == "2025-03-31" and r.provider_field == "issuer_ir:cash_and_equivalents")
    assert "cash_flow_ending_cash_matches_balance_sheet@2025-03-31" in row.raw["check_warnings"]
    latest = next(r for r in records if r.period == "2026-06-30" and r.provider_field == "issuer_ir:cash_and_equivalents")
    assert latest.raw["check_warnings"] == []


def test_skhy_net_income_that_disagrees_between_statements_is_quarantined():
    rows = deepcopy(_rows())
    for row in rows:
        if row[1] == "Net Income" and row[2] == "KRW million":
            row[10] = 90_000_000.0                          # 2026 2Q in the cash-flow statement
    records, checks = _records(parse_euroland_rows(rows), "SKHY")
    assert ("net_income_same_in_income_and_cash_flow_statements", "2026-06-30") in _failed(checks)
    assert all(r.raw.get("quarantine_reasons") for r in records if r.period == "2026-06-30")
    assert not any(r.raw.get("quarantine_reasons") for r in records if r.period == "2026-03-31")


def test_skhy_scopes_rows_by_section_and_refuses_a_missing_row():
    rows = deepcopy(_rows())
    rows = [row for row in rows if not (row[1] == "Inventories")]
    with pytest.raises(ParseError, match="Inventories"):
        parse_euroland_rows(rows)


# --------------------------------------------------------------------------- #
# Ingestion through the central pipeline
# --------------------------------------------------------------------------- #
def _repo(tmp_path):
    repo = SQLiteStructuredRepository(tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def _drop(tmp_path, entity, name, payload=b"", copy_from=None):
    folder = tmp_path / "var" / "ir_drop" / entity
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_bytes(copy_from.read_bytes() if copy_from else payload)
    return target


class _FakeDocParser:
    """Stand-in for a parser: lets ingestion be tested without a binary file."""

    def __init__(self, doc):
        self.doc = doc

    def __call__(self, path, payload):
        return self.doc


@pytest.fixture
def adapter_with_fixture_docs(tmp_path, monkeypatch):
    from ats.data.sources import issuer_ir_parsers
    docs = {
        "tsmc_fs_pdf": parse_tsmc_fs_pages(_pages("tsm_fs_3q2026_pages.json")),
        "asml_presentation_pdf": parse_asml_presentation_pages(_pages("asml_q3_2026_presentation_pages.json")),
        "euroland_xls": parse_euroland_rows(_rows()),
    }
    monkeypatch.setattr(issuer_ir_parsers, "PARSERS", {k: _FakeDocParser(v) for k, v in docs.items()})
    _drop(tmp_path, "TSM", "TSM_FS.pdf", b"tsm-bytes")
    _drop(tmp_path, "ASML", "q3_presentation.pdf", b"asml-bytes")
    _drop(tmp_path, "SKHY", "Data.xls", b"skhy-bytes")
    return IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW, acquirers={})


def _run(repo, adapter, entity, **scope):
    return IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="issuer_ir_files", dataset_id="company_financials", entities=[entity], query_scope=scope))


def _accepted(repo, entity):
    return repo.observations(dataset_id="company_financials", source_id="issuer_ir_files", entity_id=entity,
                             latest_only=True, accepted_only=True, limit=100_000)


def test_ingest_tsm_is_complete_in_twd_with_file_lineage_and_reruns_as_no_change(tmp_path, adapter_with_fixture_docs):
    repo = _repo(tmp_path)
    result = _run(repo, adapter_with_fixture_docs, "TSM")
    assert result["status"] == "succeeded" and result["accepted"] == 42 and result["quarantined"] == 0
    rows = [r for r in _accepted(repo, "TSM") if r["period"] == "2026-09-30"]
    assert CORE_METRICS <= {r["metric_id"] for r in rows}
    assert {r["currency"] for r in rows} == {"TWD"}
    assert _run(repo, adapter_with_fixture_docs, "TSM")["status"] == "no_change"
    from ats.data.fundamentals import _complete_report_package
    package = _complete_report_package(repo, source_id="issuer_ir_files", symbol="TSM")
    assert package["period"] == "2026-09-30" and package["currency"] == "TWD" and package["gaps"] == []
    artifact = repo.artifact(rows[0]["artifact_id"])
    import hashlib
    assert artifact["source_version"] == hashlib.sha256(b"tsm-bytes").hexdigest()      # lineage: the file's sha256
    assert artifact["media_type"] == "application/pdf" and artifact["source_url"].endswith("/TSM/TSM_FS.pdf")


def test_ingest_asml_is_eur_and_incomplete_package_without_debt(tmp_path, adapter_with_fixture_docs):
    repo = _repo(tmp_path)
    result = _run(repo, adapter_with_fixture_docs, "ASML")
    assert result["status"] == "succeeded" and result["accepted"] == 65
    rows = [r for r in _accepted(repo, "ASML") if r["period"] == "2026-09-30"]
    assert {r["currency"] for r in rows} == {"EUR"}
    assert (CORE_METRICS - {"financial.total_debt.gaap"}) <= {r["metric_id"] for r in rows}
    assert "financial.total_debt.gaap" not in {r["metric_id"] for r in rows}


def test_ingest_skhy_is_krw_third_party_and_eps_is_basic_only(tmp_path, adapter_with_fixture_docs):
    repo = _repo(tmp_path)
    result = _run(repo, adapter_with_fixture_docs, "SKHY")
    assert result["status"] == "succeeded" and result["accepted"] == 104
    rows = [r for r in _accepted(repo, "SKHY") if r["period"] == "2026-06-30"]
    ids = {r["metric_id"] for r in rows}
    assert (CORE_METRICS - {"financial.eps.diluted.gaap", "financial.total_debt.gaap"}) <= ids
    assert "financial.eps.basic.gaap" in ids and "financial.total_debt.provider_reported" in ids
    assert "financial.eps.diluted.gaap" not in ids
    assert {r["currency"] for r in rows} == {"KRW"}
    assert all(json.loads(r["dimensions_json"])["source_nature"] == "third_party_aggregator" for r in rows
               if "dimensions_json" in r)


def test_failed_self_check_lands_in_quarantine_with_reason_and_nothing_is_admitted(tmp_path, monkeypatch):
    from ats.data.sources import issuer_ir_parsers
    pages = _pages("tsm_fs_3q2026_pages.json")
    pages[3] = pages[3].replace("Cash and Cash Equivalents at End of Period $ 114,097 $ 3,621,894 $ 3,621,894",
                                "Cash and Cash Equivalents at End of Period $ 114,097 $ 3,621,894 $ 3,600,000")
    monkeypatch.setattr(issuer_ir_parsers, "PARSERS", {"tsmc_fs_pdf": _FakeDocParser(parse_tsmc_fs_pages(pages))})
    _drop(tmp_path, "TSM", "TSM_FS.pdf", b"x")
    repo = _repo(tmp_path)
    result = _run(repo, IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW, acquirers={}), "TSM")
    assert result["accepted"] == 28 and result["quarantined"] == 14       # 14 rows for 2026-09-30 held back
    assert not [r for r in _accepted(repo, "TSM") if r["period"] == "2026-09-30"]
    reasons = {code for r in result["results"] for code in r["reason_codes"]}
    assert any(code.startswith("check_failed:cash_") for code in reasons)


def test_unparseable_file_is_parse_failed_and_unknown_issuer_or_empty_folder_are_not_errors(tmp_path):
    _drop(tmp_path, "TSM", "TSM_FS.pdf", b"this is not a pdf")
    repo = _repo(tmp_path)
    adapter = IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW, acquirers={})
    assert _run(repo, adapter, "TSM")["status"] == "parse_failed"
    assert _run(repo, adapter, "SKHY")["status"] == "no_change"          # nothing dropped yet
    assert _run(repo, adapter, "AAPL")["status"] == "no_coverage"
    assert not _accepted(repo, "TSM")


def test_vendor_statement_currency_for_asml_is_eur_not_the_adr_quote_currency():
    from ats.data.sources.company_financials import _entity_statement_currency
    assert _entity_statement_currency("ASML") == "EUR"
    assert _entity_statement_currency("TSM") == "TWD"


# --------------------------------------------------------------------------- #
# The real files (present on the operator's machine, git-ignored)
# --------------------------------------------------------------------------- #
real_file = pytest.mark.skipif(not (REPO / "var/ir_drop/TSM/TSM_FS.pdf").exists(),
                               reason="real IR files are not in var/ir_drop")


@real_file
def test_real_tsm_file_matches_the_fixture_numbers():
    path = REPO / "var/ir_drop/TSM/TSM_FS.pdf"
    doc = parse_tsmc_fs_pdf(path, path.read_bytes())
    assert _by_field(doc, "2026-09-30")["revenue"].value == 1_494_243
    assert _failed(_records(doc, "TSM")[1]) == set()


@real_file
def test_real_asml_and_skhy_files_parse_and_pass_their_checks():
    asml = REPO / "var/ir_drop/ASML/2026_10_14_presentation_investor_relations_q3_2026.pdf"
    skhy = REPO / "var/ir_drop/SKHY/Data.xls"
    for path, parser, entity in ((asml, parse_asml_presentation_pdf, "ASML"), (skhy, parse_euroland_xls, "SKHY")):
        if not path.exists():
            continue
        doc = parser(path, path.read_bytes())
        assert _failed(_records(doc, entity)[1]) == set()


# --------------------------------------------------------------------------- #
# ASML 6-K exhibit EX-99.3 (slide HTML) and the SEC acquirer
# --------------------------------------------------------------------------- #
from ats.data.sources import issuer_ir_acquire  # noqa: E402
from ats.data.sources.issuer_ir_parsers import parse_asml_ex99_3_html  # noqa: E402

EX99_3 = FIXTURES / "asml_6k_ex99_3_q3_2026_slides.htm"
ASML_ACCESSION = "0001628280-26-065813"


def _ex99():
    return parse_asml_ex99_3_html(EX99_3, EX99_3.read_bytes())


def test_ex99_3_reads_the_five_quarter_slides_with_decimals_and_the_debt_line():
    doc = _ex99()
    assert doc.currency == "EUR" and doc.current_period == "2026-09-30"
    assert doc.periods == ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    q3 = _by_field(doc, "2026-09-30")
    assert q3["revenue"].value == 11_662.0              # the nine-month slide says 29,755.4
    assert (q3["gross_profit"].value, q3["operating_income"].value, q3["net_income"].value) == (6_699.8, 5_172.8, 4_960.4)
    assert (q3["eps_diluted"].value, q3["eps_basic"].value) == (12.93, 12.95)
    assert q3["cash_from_operations"].value == 11_797.6
    assert q3["capex"].value == pytest.approx(413.3 + 33.9) and "intangible" in q3["capex"].notes["capex_scope"]
    assert q3["capex"].derived["calculation"] == "purchase_of_ppe + purchase_of_intangible_assets"
    assert q3["cash_and_equivalents"].value == 8_030.9 and q3["inventory"].value == 11_907.3
    assert q3["total_liabilities"].value == 36_917.3 and q3["stockholders_equity"].value == 22_063.9
    assert q3["long_term_debt"].value == 1_963.0
    assert q3["total_debt"].provider_field == "issuer_ir:total_debt_long_term_only"
    assert "current portion" in q3["total_debt"].notes["debt_definition"]
    # ASML's 4-4-5 quarter ends Sep 27; the series keeps the calendar quarter end like every other source
    assert q3["revenue"].notes["fiscal_period_end"] == "2026-09-27"
    assert _by_field(doc, "2026-06-30")["revenue"].value == 9_326.5


def test_ex99_3_checks_pass_including_net_income_across_statements_and_cash_to_balance_sheet():
    doc = _ex99()
    records, checks = _records(doc, "ASML")
    assert _failed(checks) == set()
    names = {c.name for c in checks if c.passed}
    assert {"net_income_same_in_income_and_cash_flow_statements", "cash_flow_ending_cash_matches_balance_sheet",
            "balance_sheet_balances", "cash_flow_bridge", "liabilities_equal_current_plus_noncurrent"} <= names
    assert {r.currency for r in records} == {"EUR"}
    debt = next(r for r in records if r.period == "2026-09-30" and r.provider_field == "issuer_ir:total_debt_long_term_only")
    assert (debt.adjustment, debt.value) == ("provider_reported", 1_963_000_000)


def test_ex99_3_tampered_balance_sheet_is_quarantined():
    html = EX99_3.read_text(encoding="utf-8").replace("Total assets | 45,097.1", "Total assets | 45,097.1")
    html = html.replace("58,981.2  ", "59,981.2  ", 1) if "58,981.2  " in html else html
    doc = parse_asml_ex99_3_html(EX99_3, html.encode())
    _, checks = _records(doc, "ASML")
    assert any(name.startswith(("balance_sheet", "assets_equal")) for name, _ in _failed(checks))


def test_ex99_3_refuses_other_documents_and_a_changed_layout():
    with pytest.raises(ParseError, match="not ASML's EX-99.3"):
        parse_asml_ex99_3_html(EX99_3, b"<html><body>some other exhibit</body></html>")
    broken = EX99_3.read_text(encoding="utf-8").replace("Long-term debt", "Borrowings")
    with pytest.raises(ParseError, match="Long-term debt"):
        parse_asml_ex99_3_html(EX99_3, broken.encode())


def test_ex99_3_outranks_the_presentation_and_completes_the_asml_report_package(tmp_path, monkeypatch):
    from ats.data.sources import issuer_ir_parsers
    monkeypatch.setitem(issuer_ir_parsers.PARSERS, "asml_presentation_pdf",
                        _FakeDocParser(parse_asml_presentation_pages(_pages("asml_q3_2026_presentation_pages.json"))))
    _drop(tmp_path, "ASML", "q3_presentation.pdf", b"asml-pdf-bytes")
    _drop(tmp_path, "ASML", f"{ASML_ACCESSION}_financial_statementsxusx.htm", copy_from=EX99_3)
    repo = _repo(tmp_path)
    result = _run(repo, IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW, acquirers={}), "ASML")
    assert result["status"] == "succeeded" and result["quarantined"] == 0
    rows = {(r["metric_id"], r["period"]): r for r in _accepted(repo, "ASML")}
    assert rows[("financial.gross_profit.gaap", "2026-09-30")]["value"] == 6_699_800_000   # 6,700 in the presentation
    assert rows[("financial.long_term_debt.gaap", "2026-09-30")]["value"] == 1_963_000_000
    assert rows[("financial.total_debt.provider_reported", "2026-09-30")]["value"] == 1_963_000_000
    from ats.data.fundamentals import _complete_report_package
    package = _complete_report_package(repo, source_id="issuer_ir_files", symbol="ASML")
    assert (package["period"], package["currency"], package["debt_metric"]) == (
        "2026-09-30", "EUR", "financial.total_debt.provider_reported")


class _SecSite:
    """A fake EDGAR: submissions listing, filing indexes and exhibits."""

    def __init__(self, tmp_path):
        self.requests = []
        self.exhibit = EX99_3.read_bytes()
        cik = "0000937966"
        self.listing = {"filings": {"recent": {
            "form": ["6-K", "6-K", "20-F", "6-K"],
            "accessionNumber": [ASML_ACCESSION, "0001628280-26-060001", "0001628280-26-010000", "0001628280-25-000001"],
            "filingDate": ["2026-10-14", "2026-09-10", "2026-02-12", "2025-01-01"],
            "primaryDocument": ["form6-kquarterlyfilings.htm", "x.htm", "y.htm", "z.htm"]}}}
        base = "https://www.sec.gov/Archives/edgar/data/937966"
        self.pages = {
            f"https://data.sec.gov/submissions/CIK{cik}.json": json.dumps(self.listing).encode(),
            f"{base}/000162828026065813/index.json": json.dumps({"directory": {"item": [
                {"name": "form6-kquarterlyfilings.htm"}, {"name": "pressreleasefinancialresul.htm"},
                {"name": "financial_statementsxusx.htm"}, {"name": "financial_statementsxusx001.jpg"}]}}).encode(),
            f"{base}/000162828026065813/financial_statementsxusx.htm": self.exhibit,
            f"{base}/000162828026060001/index.json": json.dumps({"directory": {"item": [
                {"name": "x.htm"}]}}).encode(),
        }

    def __call__(self, url):
        self.requests.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected request {url}")
        return self.pages[url]


def _asml_config():
    return load_issuer_configs()["ASML"]


def test_sec_acquirer_downloads_the_results_exhibit_once_and_a_quiet_day_costs_one_request(tmp_path):
    site = _SecSite(tmp_path)
    drop = tmp_path / "drop"
    first = issuer_ir_acquire.acquire_sec_6k_exhibits(
        _asml_config(), drop, None, NOW, http=site)
    assert first["downloaded"] == [f"{ASML_ACCESSION}_financial_statementsxusx.htm"]
    assert first["requests"] == 4      # listing + 2 filing indexes + 1 exhibit (the 2025 6-K is outside the look-back)
    saved = drop / f"{ASML_ACCESSION}_financial_statementsxusx.htm"
    assert saved.read_bytes() == site.exhibit
    meta = json.loads(saved.with_name(saved.name + ".meta.json").read_text())
    assert meta["source_url"].endswith("/000162828026065813/financial_statementsxusx.htm")
    assert meta["accession"] == ASML_ACCESSION and meta["filed"] == "2026-10-14" and len(meta["sha256"]) == 64
    assert not any("20-F" in url or "y.htm" in url for url in site.requests)

    site.requests.clear()
    second = issuer_ir_acquire.acquire_sec_6k_exhibits(_asml_config(), drop, None, NOW, http=site)
    assert second["downloaded"] == [] and second["requests"] == 1 and second["skipped_seen"] == 2
    assert site.requests == ["https://data.sec.gov/submissions/CIK0000937966.json"]


def test_sec_acquirer_rechecks_a_very_recent_6k_that_has_no_exhibit_yet(tmp_path):
    site = _SecSite(tmp_path)
    site.pages["https://www.sec.gov/Archives/edgar/data/937966/000162828026065813/index.json"] = json.dumps(
        {"directory": {"item": [{"name": "form6-kquarterlyfilings.htm"}]}}).encode()
    drop = tmp_path / "drop"
    the_next_day = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    issuer_ir_acquire.acquire_sec_6k_exhibits(_asml_config(), drop, None, the_next_day, http=site)
    state = json.loads((drop / ".acquisition_state.json").read_text())["sec_6k"]
    assert ASML_ACCESSION not in state                       # filed yesterday, exhibits may still be coming
    assert state["0001628280-26-060001"]["status"] == "no_matching_exhibit"   # old 6-K: looked at once
    issuer_ir_acquire.acquire_sec_6k_exhibits(_asml_config(), drop, None, NOW, http=site)
    assert json.loads((drop / ".acquisition_state.json").read_text())["sec_6k"][ASML_ACCESSION]["status"] \
        == "no_matching_exhibit"                              # a week later it is settled


def test_sec_acquirer_stops_at_the_request_budget_and_requires_a_user_agent(tmp_path, monkeypatch):
    site = _SecSite(tmp_path)
    monkeypatch.setattr(issuer_ir_acquire, "REQUESTS_PER_RUN", 2)
    report = issuer_ir_acquire.acquire_sec_6k_exhibits(_asml_config(), tmp_path / "drop", None, NOW, http=site)
    assert report["requests"] == 2 and "budget" in report["budget_exhausted"] and report["downloaded"] == []
    from ats.data import sec
    monkeypatch.setattr(sec, "_headers", lambda: {"User-Agent": ""})
    with pytest.raises(PermissionError, match="SEC_EDGAR_USER_AGENT"):
        issuer_ir_acquire.acquire_sec_6k_exhibits(_asml_config(), tmp_path / "drop2", None, NOW)


def test_adapter_runs_the_acquirer_first_and_records_the_sec_url_in_lineage(tmp_path):
    site = _SecSite(tmp_path)

    def acquirer(config, drop, request, fetched_at):
        return issuer_ir_acquire.acquire_sec_6k_exhibits(config, drop, request, fetched_at, http=site)

    repo = _repo(tmp_path)
    adapter = IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW, acquirers={"ASML": acquirer})
    result = _run(repo, adapter, "ASML")
    assert result["status"] == "succeeded" and result["quarantined"] == 0
    row = next(r for r in _accepted(repo, "ASML") if r["metric_id"] == "financial.revenue.gaap" and r["period"] == "2026-09-30")
    assert repo.artifact(row["artifact_id"])["source_url"].endswith("/000162828026065813/financial_statementsxusx.htm")
    raw = json.loads(row["raw_payload"])
    assert raw["provenance"]["origin"]["accession"] == ASML_ACCESSION and raw["provenance"]["origin"]["filed"] == "2026-10-14"
    # next day: same file, nothing new
    assert _run(repo, adapter, "ASML")["status"] == "no_change"


def test_acquisition_outage_is_unreachable_but_existing_files_are_still_parsed(tmp_path):
    def broken(config, drop, request, fetched_at):
        raise ConnectionError("TLS reset")

    adapter = IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW, acquirers={"ASML": broken})
    repo = _repo(tmp_path)
    assert _run(repo, adapter, "ASML")["status"] == "unreachable"      # nothing on disk yet
    _drop(tmp_path, "ASML", f"{ASML_ACCESSION}_financial_statementsxusx.htm", copy_from=EX99_3)
    result = _run(repo, adapter, "ASML")
    assert result["status"] == "partial" and result["accepted"] > 0     # data written, the outage stays visible


def test_asml_exhibit_patterns_cover_both_real_file_names_and_skip_the_ifrs_report():
    import fnmatch
    config = _asml_config()
    acquire = config.raw["acquisition"]["exhibit_patterns"]
    rule = next(r for r in config.files if r.parser == "asml_ex99_3_html")
    for name in ("financial_statementsxusx.htm", "financialstatementsusgaa.htm"):     # Oct-2026 / Jul-2026 6-K
        assert any(fnmatch.fnmatch(name, pattern) for pattern in acquire)
        assert Path(f"{ASML_ACCESSION}_{name}").match(rule.pattern)
    for name in ("statutoryinterimreport20.htm", "pressreleasefinancialresul.htm", "presentationinvestorrela.htm",
                 "form6-kquarterlyfilings.htm", "financial_statementsxusx001.jpg"):
        assert not any(fnmatch.fnmatch(name, pattern) for pattern in acquire)
