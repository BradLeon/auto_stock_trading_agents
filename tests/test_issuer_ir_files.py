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
    return IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW)


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
    result = _run(repo, IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW), "TSM")
    assert result["accepted"] == 28 and result["quarantined"] == 14       # 14 rows for 2026-09-30 held back
    assert not [r for r in _accepted(repo, "TSM") if r["period"] == "2026-09-30"]
    reasons = {code for r in result["results"] for code in r["reason_codes"]}
    assert any(code.startswith("check_failed:cash_") for code in reasons)


def test_unparseable_file_is_parse_failed_and_unknown_issuer_or_empty_folder_are_not_errors(tmp_path):
    _drop(tmp_path, "TSM", "TSM_FS.pdf", b"this is not a pdf")
    repo = _repo(tmp_path)
    adapter = IssuerIRFilesAdapter(root=tmp_path, clock=lambda: NOW)
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
