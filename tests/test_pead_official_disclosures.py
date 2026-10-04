from datetime import date
from io import StringIO
import json

from ats.data import defeatbeta, sec
from ats.data.earnings_events import EventEvidence, resolve_event
from ats.data.pead_official_disclosures import (
    DisclosureRoleResult,
    OfficialDisclosurePackage,
    collect_active_packages,
    collect_latest_package,
    render_acceptance_markdown,
)
from ats.memory import get_store


def _event(symbol="AMD", year=2026, quarter=2, report_date="2026-08-04"):
    return resolve_event(symbol, [
        EventEvidence("calendar", report_date, year, quarter),
    ])


def _release(symbol="AMD", form="8-K", period="Q2 FY2026"):
    body = (
        f"{symbol} reports second quarter 2026 financial results. Revenue, net income, "
        "earnings per share and guidance were discussed. "
    ) * 100
    return {
        "label": f"SEC {form} earnings release (2026-08-04)", "text": body,
        "filed": date(2026, 8, 4),
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/release.htm",
        "accession": "0000000000-26-000001", "cik": "1", "form_type": form,
        "document_role": "company_release", "claimed_period": period,
    }


def _filing(symbol="AMD", form="10-Q", period="Q2 FY2026"):
    body = (
        f"{symbol} FORM {form} quarterly report. Financial statements include revenue, "
        "assets, liabilities, and cash flows. "
    ) * 100
    return {
        "label": f"SEC {form} filing (2026-08-05)", "text": body,
        "filed": date(2026, 8, 5),
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/filing.htm",
        "accession": "0000000000-26-000002", "cik": "1", "form_type": form,
        "report_date": "2026-06-30", "filing_regime": "domestic",
        "document_role": "regulatory_filing", "claimed_period": period,
    }


def _transcript(symbol="AMD", year=2026, quarter=2, report_date="2026-08-04"):
    paragraphs = (
        defeatbeta.Paragraph(0, "Operator", "Welcome to the earnings call."),
        defeatbeta.Paragraph(1, "Lisa Su", "Prepared remarks cover revenue and guidance."),
        defeatbeta.Paragraph(2, "Operator", "We will now begin the question-and-answer session."),
        defeatbeta.Paragraph(3, "Analyst", "What is the outlook?"),
        defeatbeta.Paragraph(4, "Lisa Su", "Thank you. This concludes the call."),
    )
    # Keep the text above the production lower bound while retaining a valid structure.
    expanded = tuple(
        defeatbeta.Paragraph(item.ordinal, item.speaker, item.content + " Detail." * 600)
        for item in paragraphs
    )
    return defeatbeta.Transcript(symbol, year, quarter, report_date, paragraphs=expanded)


def _result(record=None, *, status="succeeded", stage="filing_index"):
    return sec.SecRecordResult(record=record, status=status, stage=stage)


def test_event_package_collects_three_independent_accepted_roles():
    store = get_store()
    package = collect_latest_package(
        "AMD", store=store, now=date(2026, 8, 6),
        event_resolver=lambda *_a, **_k: _event(),
        release_fetcher=lambda *_a, **_k: _result(_release()),
        filing_fetcher=lambda *_a, **_k: _result(_filing()),
        transcript_fetcher=lambda *_a, **_k: _transcript(),
    )

    assert package.complete
    assert [role.status for role in package.roles] == ["accepted"] * 3
    assert {row["doc_type"] for row in store.documents(entity="AMD")} == {
        "company_release", "regulatory_filing", "earnings_transcript",
    }
    health = {row["source_id"]: row for row in store.data_source_health()}
    assert health["sec_official:earnings_release:AMD"]["status"] == "succeeded"
    assert health["sec_official:periodic_filing:AMD"]["status"] == "succeeded"
    assert health["defeatbeta_transcript:AMD"]["status"] == "succeeded"


def test_unresolved_event_blocks_all_roles_without_running_collectors():
    store = get_store()
    unresolved = resolve_event("MRVL", [EventEvidence("entity", reference="entity:MRVL")])
    calls = []
    package = collect_latest_package(
        "MRVL", store=store, now=date(2026, 8, 28),
        event_resolver=lambda *_a, **_k: unresolved,
        release_fetcher=lambda *_a, **_k: calls.append("release"),
        filing_fetcher=lambda *_a, **_k: calls.append("filing"),
        transcript_fetcher=lambda *_a, **_k: calls.append("transcript"),
    )

    assert not package.complete and calls == []
    assert {role.status for role in package.roles} == {"quarantined"}
    assert {"event_unresolved", "fiscal_period", "report_date"} <= set(
        package.roles[0].reason_codes)


def test_release_identity_or_period_failure_is_quarantined_not_accepted():
    package = collect_latest_package(
        "MRVL", now=date(2026, 8, 28),
        event_resolver=lambda *_a, **_k: _event("MRVL", 2027, 2, "2026-08-27"),
        release_fetcher=lambda *_a, **_k: _result(_release("MSFT", period="Q2 FY2027")),
        filing_fetcher=lambda *_a, **_k: _result(None, status="missing"),
        transcript_fetcher=lambda *_a, **_k: None,
    )

    release = package.roles[0]
    assert release.status == "quarantined"
    assert "identity_unresolved" in release.reason_codes
    assert package.roles[1].status == "not_yet_available"
    assert package.roles[2].status == "not_yet_available"


def test_foreign_issuer_6k_is_an_independent_regulatory_role():
    package = collect_latest_package(
        "TSM", now=date(2026, 7, 20),
        event_resolver=lambda *_a, **_k: _event("TSM", 2026, 2, "2026-07-16"),
        release_fetcher=lambda *_a, **_k: _result(_release("TSM", "6-K")),
        filing_fetcher=lambda *_a, **_k: _result(_filing("TSM", "6-K")),
        transcript_fetcher=lambda *_a, **_k: _transcript("TSM"),
    )

    filing = package.roles[1]
    assert filing.status == "accepted"
    assert filing.metadata["form_type"] == "6-K"


def test_transcript_delay_is_not_misreported_as_missing():
    package = collect_latest_package(
        "AMD", now=date(2026, 8, 6),
        event_resolver=lambda *_a, **_k: _event(),
        release_fetcher=lambda *_a, **_k: _result(_release()),
        filing_fetcher=lambda *_a, **_k: _result(_filing()),
        transcript_fetcher=lambda *_a, **_k: None,
    )

    assert package.roles[2].status == "not_yet_available"


def test_active_sweep_continues_after_one_unexpected_issuer_failure(monkeypatch):
    good = OfficialDisclosurePackage(
        "AMD", _event(), tuple(
            DisclosureRoleResult(role, "accepted")
            for role in ("earnings_release", "regulatory_filing", "earnings_transcript")
        ), "2026-08-06T00:00:00+00:00",
    )

    def collect(symbol, **_kwargs):
        if symbol == "BAD":
            raise RuntimeError("isolated failure")
        return good

    monkeypatch.setattr("ats.data.pead_official_disclosures.collect_latest_package", collect)
    packages = collect_active_packages(symbols=["BAD", "AMD"], now=date(2026, 8, 6))

    assert [item.entity for item in packages] == ["BAD", "AMD"]
    assert packages[0].roles[0].reason_codes == ("orchestration_error", "RuntimeError")
    assert packages[1].complete


def test_acceptance_markdown_exposes_role_provenance_and_event_gaps():
    package = OfficialDisclosurePackage(
        "MRVL", _event("MRVL", 2027, 2, "2026-08-27"), (
            DisclosureRoleResult("earnings_release", "accepted", source="sec",
                                 source_url="https://www.sec.gov/release", published_at="2026-08-27",
                                 metadata={"form_type": "8-K", "accession": "abc"}),
            DisclosureRoleResult("regulatory_filing", "not_yet_available", ("filing_metadata",)),
            DisclosureRoleResult("earnings_transcript", "not_yet_available", ("transcript_not_returned",)),
        ), "2026-08-28T00:00:00+00:00",
    )

    report = render_acceptance_markdown([package])

    assert "Q2 FY2027 / 2026-08-27" in report
    assert "form_type=8-K" in report and "accession=abc" in report
    assert "transcript_not_returned" in report


def test_cli_runs_in_isolated_paths_and_writes_machine_and_human_results(tmp_path, monkeypatch):
    from ats.runtime.cli import run_data

    package = OfficialDisclosurePackage(
        "AMD", _event(), tuple(
            DisclosureRoleResult(role, "accepted", source="test")
            for role in ("earnings_release", "regulatory_filing", "earnings_transcript")
        ), "2026-08-06T00:00:00+00:00",
    )
    monkeypatch.setattr(
        "ats.data.pead_official_disclosures.collect_active_packages",
        lambda **_kwargs: [package],
    )
    monkeypatch.setattr(
        "ats.data.pead_official_disclosures.active_pead_targets", lambda: ["AMD"])
    database, artifacts = tmp_path / "acceptance.sqlite", tmp_path / "artifacts"
    stdout = StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    assert run_data(
        "pead-official-disclosure-coverage", db_path=str(database),
        artifact_root=str(artifacts),
    ) == 0
    output = json.loads(stdout.getvalue())
    assert output["scope"] == ["AMD"] and output["complete"] is True
    assert output["side_effects"] == {
        "llm": 0, "pead_scoring": 0, "chief": 0, "broker_orders": 0, "trades": 0,
    }
    assert (artifacts / "PEAD_OFFICIAL_DISCLOSURE_ACCEPTANCE.md").is_file()
