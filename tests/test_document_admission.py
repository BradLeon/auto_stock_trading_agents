import json

from ats.data.admission import (
    CandidateDocument,
    ValidationIssue,
    admit,
    validate_candidate,
)
from ats.data.document_types import CarrierFormat
from ats.memory import get_store


def _candidate(**updates):
    values = {
        "expected_entity": "TSM",
        "claimed_entity": "TSM",
        "target_period": "2026Q2",
        "claimed_period": "Q2 FY2026",
        "expected_semantic": "earnings_transcript",
        "claimed_semantic": "transcript",
        "text": "Operator: welcome. Prepared remarks. Question-and-answer. " * 30,
        "source": "fixture",
        "source_url": "https://example.test/tsm-q2",
        "external_id": "fixture:tsm:2026q2",
        "carrier_format": CarrierFormat.STRUCTURED_TEXT,
        "completeness": "full",
        "min_chars": 100,
    }
    values.update(updates)
    return CandidateDocument(**values)


def test_validator_reports_identity_period_type_and_completeness_together():
    result = validate_candidate(_candidate(
        expected_entity="SKHY",
        claimed_entity="GSK",
        claimed_period="2026Q1",
        claimed_semantic="news",
        text="short teaser",
        completeness="teaser",
    ))

    assert result.status == "quarantined"
    assert set(result.reason_codes) == {
        "identity_mismatch", "period_mismatch", "type_mismatch",
        "completeness_too_short", "completeness_teaser",
    }
    assert result.checks == {
        "identity": False, "period": False, "type": False, "completeness": False,
    }


def test_tsm_candidate_for_a_different_company_is_quarantined():
    result = validate_candidate(_candidate(claimed_entity="CRTO"))

    assert result.reason_codes == ("identity_mismatch",)


def test_empty_todo_and_unparseable_strong_fields_never_auto_pass():
    result = validate_candidate(_candidate(
        expected_entity="TODO", claimed_entity="", target_period="FY2026",
        claimed_period="TODO", expected_semantic="", claimed_semantic="mystery",
        text="", completeness="unknown",
    ))

    assert {"identity_unresolved", "period_unresolved", "type_unresolved",
            "completeness_empty", "completeness_unresolved"} <= set(result.reason_codes)


def test_source_specific_validator_can_add_a_reason_without_hiding_central_results():
    def official_domain(candidate):
        return [ValidationIssue("source", "official_domain_mismatch", candidate.source_url)]

    result = validate_candidate(_candidate(), extensions=(official_domain,))

    assert result.reason_codes == ("official_domain_mismatch",)
    assert result.checks["identity"] is True
    assert result.checks["source:official_domain_mismatch"] is False


def test_quarantined_candidate_keeps_provenance_but_is_not_an_asset():
    store = get_store()
    candidate = _candidate(claimed_entity="GSK")

    outcome = admit(candidate, store=store)

    assert outcome.validation.status == "quarantined"
    assert outcome.quarantine_path and outcome.quarantine_path.is_file()
    rows = store.document_candidates(status="quarantined")
    assert len(rows) == 1
    assert rows[0]["source_url"] == candidate.source_url
    assert json.loads(rows[0]["reason_codes"]) == ["identity_mismatch"]
    assert store.documents(entity="TSM") == []
    assert store.conn.execute("SELECT count(*) FROM document_versions").fetchone()[0] == 0
    assert store.conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0] == 0


def test_accepted_candidate_enters_assets_and_legacy_type_queries():
    store = get_store()

    outcome = admit(_candidate(), store=store)

    assert outcome.validation.accepted
    assert outcome.document is not None
    assert store.document_candidates(status="accepted")[0]["document_id"] \
        == outcome.document.document_id
    assert len(store.documents(entity="TSM", doc_type="earnings_transcript")) == 1
    assert len(store.documents(entity="TSM", doc_type="transcript")) == 1
    assert store.conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0] > 0
