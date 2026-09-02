"""Document-linked numeric candidates, human release gate and event deduplication."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from ats.data.products import DataProducts
from ats.data.structured import (
    EvidenceCandidateInput,
    EvidenceWorkbench,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
TEXTS = {
    ("doc-openai-primary", "v1"): (
        "OpenAI announced that the financing raised $40 billion at a $300 billion "
        "post-money valuation."),
    ("doc-openai-media", "v1"): (
        "A media report repeated that OpenAI raised $40 billion at a $300 billion "
        "post-money valuation."),
    ("doc-anthropic-arr", "v1"): (
        "Anthropic annualized revenue reached $5 billion as of July 31, 2025."),
    ("doc-anthropic-summary", "v1"): "Anthropic ARR reportedly reached $5 billion.",
}


class _Store:
    def projection_lineage(self, _identifier):
        return None


def _resolver(document_id, version_id):
    text = TEXTS.get((document_id, version_id))
    return ({"document_id": document_id, "version_id": version_id, "text": text,
             "evidence_complete": document_id != "doc-anthropic-summary"}
            if text is not None else None)


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(
        tmp_path / "evidence.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def _candidate(*, entity="OpenAI", metric="event.funding.amount",
               value=40_000_000_000, document="doc-openai-primary",
               source_tier="company_primary", event_date="2025-03-31",
               period="2025-03-31", start=None, end=None, confidence=0.99):
    text = TEXTS.get((document, "v1"), "")
    needle = "$40 billion" if entity.casefold().startswith("openai") else "$5 billion"
    start = text.find(needle) if start is None else start
    end = start + len(needle) if end is None and start >= 0 else (end or 0)
    return EvidenceCandidateInput(
        entity=entity, metric_id=metric, value=value, unit="USD", currency="USD",
        period=period, event_type="funding" if metric.startswith("event.") else "arr",
        event_date=event_date, event_label="Series fixture",
        document_id=document, version_id="v1", char_start=max(start, 0), char_end=end,
        extraction_method="model" if confidence else "rule",
        source_tier=source_tier, confidence=confidence,
        published_at=NOW - timedelta(days=1), raw={"fixture": True})


def test_private_entities_have_stable_aliases_and_optional_security_mapping(tmp_path):
    repo = _repo(tmp_path)

    assert repo.resolve_entity("OpenAI Group") == "OPENAI"
    assert repo.resolve_entity("Anthropic PBC") == "ANTHROPIC"
    entities = {row["entity_id"]: row for row in repo.entities()}
    assert json.loads(entities["OPENAI"]["securities_json"]) == []


def test_model_candidate_never_auto_publishes_and_human_acceptance_adds_lineage(tmp_path):
    repo = _repo(tmp_path)
    workbench = EvidenceWorkbench(repo, document_resolver=_resolver, clock=lambda: NOW)
    proposed = workbench.propose(_candidate(confidence=1.0))

    assert proposed["status"] == "needs_evidence"
    assert repo.observations(dataset_id="private_company_events") == []

    accepted = workbench.review(
        proposed["candidate_id"], status="accepted", reviewer="human.qa",
        note="checked entity, value, unit, date and exact source span")
    rows = repo.observations(dataset_id="private_company_events")

    assert accepted["status"] == "accepted" and len(rows) == 1
    assert rows[0]["entity_id"] == "OPENAI" and rows[0]["value"] == 40_000_000_000
    lineage = repo.lineage(rows[0]["observation_id"])
    assert lineage["artifact"]["retention"] == "evidence_link_only"
    assert lineage["evidence"][0]["document_id"] == "doc-openai-primary"
    assert lineage["evidence"][0]["verification_status"] == "accepted"
    assert lineage["evidence"][0]["reviewer"] == "human.qa"


def test_incomplete_summary_remains_needs_evidence_and_cannot_be_accepted(tmp_path):
    repo = _repo(tmp_path)
    workbench = EvidenceWorkbench(repo, document_resolver=_resolver, clock=lambda: NOW)
    proposed = workbench.propose(_candidate(
        entity="Anthropic", metric="company.arr", value=5_000_000_000,
        document="missing-summary", source_tier="reliable_media",
        event_date="2025-07-31", period="2025-07-31", start=0, end=10))

    assert "document_version_missing" in json.loads(proposed["reason_codes_json"])
    with pytest.raises(ValueError, match="document_version_missing"):
        workbench.review(
            proposed["candidate_id"], status="accepted", reviewer="human.qa")
    assert repo.observations(dataset_id="private_company_events") == []

    summary = workbench.propose(_candidate(
        entity="Anthropic", metric="company.arr", value=5_000_000_000,
        document="doc-anthropic-summary", source_tier="reliable_media",
        event_date="2025-07-31", period="2025-07-31"))
    assert "evidence_incomplete" in json.loads(summary["reason_codes_json"])


def test_one_funding_event_holds_amount_valuation_and_duplicate_media_evidence(tmp_path):
    repo = _repo(tmp_path)
    workbench = EvidenceWorkbench(repo, document_resolver=_resolver, clock=lambda: NOW)
    amount = workbench.propose(_candidate())
    valuation = workbench.propose(_candidate(
        metric="event.valuation.post_money", value=300_000_000_000,
        start=TEXTS[("doc-openai-primary", "v1")].find("$300 billion"),
        end=TEXTS[("doc-openai-primary", "v1")].find("$300 billion") + 12))
    repeated = workbench.propose(_candidate(
        document="doc-openai-media", source_tier="reliable_media"))
    for row in (amount, valuation, repeated):
        workbench.review(row["candidate_id"], status="accepted", reviewer="human.qa")
    workbench.propose(_candidate(document="missing-summary", start=0, end=10))

    events = repo.events(entity_id="OPENAI", event_type="funding")
    rows = repo.observations(dataset_id="private_company_events")

    assert len(events) == 1 and events[0]["status"] == "active"
    assert {row["metric_id"] for row in rows} == {
        "event.funding.amount", "event.valuation.post_money"}
    amount_row = next(row for row in rows if row["metric_id"] == "event.funding.amount")
    assert len([link for link in repo.evidence_links(
        observation_id=amount_row["observation_id"])
                if link["verification_status"] == "accepted"]) == 2


def test_conflicting_value_requires_superseding_old_evidence_and_audit_is_retained(tmp_path):
    repo = _repo(tmp_path)
    workbench = EvidenceWorkbench(repo, document_resolver=_resolver, clock=lambda: NOW)
    first = workbench.propose(_candidate())
    workbench.review(first["candidate_id"], status="accepted", reviewer="reviewer.one")
    conflict = workbench.propose(_candidate(value=41_000_000_000,
                                            document="doc-openai-media",
                                            source_tier="reliable_media"))
    with pytest.raises(ValueError, match="event_value_conflict"):
        workbench.review(conflict["candidate_id"], status="accepted", reviewer="reviewer.two")

    workbench.review(first["candidate_id"], status="superseded", reviewer="reviewer.two",
                     note="new corrected source")
    workbench.review(conflict["candidate_id"], status="accepted", reviewer="reviewer.two")
    visible = repo.observations(dataset_id="private_company_events")

    assert [row["value"] for row in visible] == [41_000_000_000]
    assert [row["to_status"] for row in repo.evidence_reviews(first["candidate_id"])] == [
        "accepted", "superseded"]
    assert repo.observations(dataset_id="private_company_events", accepted_only=False)


def test_rejected_candidate_can_return_to_needs_evidence_with_full_review_log(tmp_path):
    repo = _repo(tmp_path)
    workbench = EvidenceWorkbench(repo, document_resolver=_resolver, clock=lambda: NOW)
    candidate = workbench.propose(_candidate())
    workbench.review(candidate["candidate_id"], status="rejected", reviewer="qa")
    workbench.review(candidate["candidate_id"], status="needs_evidence", reviewer="qa",
                     note="request better source")

    assert repo.evidence_candidate(candidate["candidate_id"])["status"] == "needs_evidence"
    assert [row["to_status"] for row in repo.evidence_reviews(candidate["candidate_id"])] == [
        "rejected", "needs_evidence"]


def test_company_arr_query_exposes_document_span_and_verification(tmp_path):
    repo = _repo(tmp_path)
    workbench = EvidenceWorkbench(repo, document_resolver=_resolver, clock=lambda: NOW)
    candidate = workbench.propose(_candidate(
        entity="Anthropic", metric="company.arr", value=5_000_000_000,
        document="doc-anthropic-arr", source_tier="reliable_media",
        event_date="2025-07-31", period="2025-07-31"))
    accepted = workbench.review(
        candidate["candidate_id"], status="accepted", reviewer="human.qa")
    products = DataProducts(store=_Store(), structured_repository=repo)
    result = products.metric_series(
        metric="company.arr", entity="ANTHROPIC", dataset="private_company_events")
    lineage = products.lineage(accepted["observation_id"])

    assert result["status"] == "ok" and result["rows"][0]["value"] == 5_000_000_000
    assert lineage["evidence"][0]["char_end"] > lineage["evidence"][0]["char_start"]
    assert lineage["evidence"][0]["source_tier"] == "reliable_media"
