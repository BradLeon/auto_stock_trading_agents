"""Human release gate of company disclosure metrics: confirm, reject, correct.

Candidates live in `structured_evidence_candidates`; every decision is a row of `structured_evidence_reviews`. Only a
confirmed candidate becomes an observation. Withdrawing a confirmed one marks it superseded, which removes its
observation from the default series without deleting it.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ...core.structured_models import (
    ArtifactDescriptor,
    EvidenceLink,
    ObservationInput,
    QualityStatus,
    SeriesIdentity,
    VerificationStatus,
)
from ...pipelines.structured import disclosure_metrics as pipeline


def _candidate(repository: Any, candidate_id: str) -> dict:
    candidate = repository.evidence_candidate(candidate_id)
    if candidate is None or candidate["dataset_id"] != pipeline.DATASET_ID:
        raise ValueError(f"disclosure_candidate_not_found:{candidate_id}")
    return candidate


def _period_start(end: str, basis: str) -> str:
    months = pipeline.BASIS_MONTHS.get(basis)
    if not months:
        return ""
    following = date.fromisoformat(end) + timedelta(days=1)
    month = following.month - months
    return date(following.year + (month - 1) // 12, (month - 1) % 12 + 1, following.day).isoformat()


def _released(repository: Any, candidate: dict) -> list[dict]:
    """Confirmed candidates of the same entity, metric, period, basis and bucket."""
    raw = json.loads(candidate["raw_payload"])
    held = []
    for other in repository.evidence_candidates(status="accepted", entity_id=candidate["entity_id"], limit=100_000):
        other_raw = json.loads(other["raw_payload"])
        if (other["dataset_id"] == pipeline.DATASET_ID and other["metric_id"] == candidate["metric_id"]
                and other["period"] == candidate["period"] and other["candidate_id"] != candidate["candidate_id"]
                and (other_raw["period_basis"], other_raw["bucket"]) == (raw["period_basis"], raw["bucket"])):
            held.append(other)
    return held


def confirm(repository: Any, candidate_id: str, *, reviewer: str, note: str = "", basis_note: str = "",
            at: datetime | None = None) -> dict:
    candidate = _candidate(repository, candidate_id)
    if not reviewer.strip():
        raise ValueError("reviewer_required")
    if candidate["status"] != "needs_evidence":
        raise ValueError(f"disclosure_candidate_not_pending:{candidate['status']}")
    reasons = json.loads(candidate["reason_codes_json"] or "[]")
    if reasons:
        raise ValueError("disclosure_candidate_evidence_incomplete:" + ",".join(reasons))
    raw = json.loads(candidate["raw_payload"])
    others = _released(repository, candidate)
    clash = [other for other in others
             if other["document_id"] == candidate["document_id"] and other["value"] != candidate["value"]]
    if clash:
        raise ValueError(f"disclosure_same_filing_value_conflict:{clash[0]['candidate_id']}")
    differing = [other for other in others if other["value"] != candidate["value"]]
    at = (at or datetime.now(UTC)).astimezone(UTC)
    artifact = repository.put_artifact(
        {"candidate_id": candidate_id, "document_id": candidate["document_id"],
         "version_id": candidate["version_id"], "char_start": candidate["char_start"],
         "char_end": candidate["char_end"], "quote": raw["quote"]},
        ArtifactDescriptor(
            source_id=pipeline.SOURCE_ID, dataset_id=pipeline.DATASET_ID, fetched_at=at,
            query_scope={"candidate_id": candidate_id}, source_url=f"document://{candidate['document_id']}",
            source_version=candidate["version_id"], media_type="application/json", retention="evidence_link_only",
            storage_mode="pointer",
            pointer=f"{candidate['document_id']}#{candidate['char_start']}:{candidate['char_end']}"))
    filed = datetime.fromisoformat(candidate["event_time"])
    vintage = repository.save_observation(ObservationInput(
        series=SeriesIdentity(
            source_id=pipeline.SOURCE_ID, dataset_id=pipeline.DATASET_ID, entity_id=candidate["entity_id"],
            metric_id=candidate["metric_id"], unit=candidate["unit"], currency=candidate["currency"],
            period_basis=raw["period_basis"],
            dimensions={raw["dimension"]: raw["bucket"]} if raw.get("dimension") else {}),
        period=candidate["period"], value=float(candidate["value"]),
        period_start=_period_start(candidate["period"], raw["period_basis"]), period_end=candidate["period"],
        event_time=filed, published_at=filed, known_at=at, fetched_at=at, artifact_id=artifact.id,
        quality_status=QualityStatus.CONFLICT if differing else QualityStatus.ACCEPTED,
        quality={"human_verified": True, "reviewer": reviewer, "basis_note": basis_note},
        raw={key: raw.get(key, "") for key in (
            "form_type", "filing_date", "accession_number", "qualifier", "quote", "security")}
        | {"candidate_id": candidate_id, "document_id": candidate["document_id"]}))
    repository.save_evidence_link(EvidenceLink(
        observation_id=vintage.id, candidate_id=candidate_id, document_id=candidate["document_id"],
        version_id=candidate["version_id"], char_start=candidate["char_start"], char_end=candidate["char_end"],
        extraction_method=candidate["extraction_method"], source_tier=candidate["source_tier"],
        verification_status=VerificationStatus.ACCEPTED, reviewer=reviewer, reviewed_at=at))
    return repository.transition_evidence_candidate(
        candidate_id=candidate_id, to_status="accepted", reviewer=reviewer, note=note or basis_note,
        observation_id=vintage.id, at=at)


def reject(repository: Any, candidate_id: str, *, reviewer: str, note: str, at: datetime | None = None) -> dict:
    """Reject a pending candidate, or withdraw a confirmed one."""
    candidate = _candidate(repository, candidate_id)
    if not reviewer.strip() or not note.strip():
        raise ValueError("reviewer_and_note_required")
    target = {"needs_evidence": "rejected", "accepted": "superseded"}.get(candidate["status"])
    if not target:
        raise ValueError(f"disclosure_candidate_not_rejectable:{candidate['status']}")
    return repository.transition_evidence_candidate(
        candidate_id=candidate_id, to_status=target, reviewer=reviewer, note=note, at=at)


def correct(repository: Any, documents: Any, candidate_id: str, *, reviewer: str, note: str,
            changes: dict[str, Any], at: datetime | None = None) -> dict:
    """Replace a candidate with a corrected reading, checked again against the filing. The old one keeps its history."""
    old = _candidate(repository, candidate_id)
    if not reviewer.strip() or not note.strip():
        raise ValueError("reviewer_and_note_required")
    changes = {key: value for key, value in changes.items() if value not in (None, "")}
    if not changes:
        raise ValueError("disclosure_correction_without_changes")
    raw = json.loads(old["raw_payload"])
    document = pipeline.load_document(documents, raw["security"], document_id=old["document_id"])
    if document["version_id"] != old["version_id"]:
        raise ValueError("disclosure_document_version_changed")
    _, metrics = pipeline.issuer_metrics(raw["security"], [old["metric_id"].removeprefix(pipeline.METRIC_PREFIX)])
    reading = {key: raw.get(key) for key in (
        "quote", "value_text", "scale", "scale_quote", "period_basis", "period_quote", "header_quote",
        "column_index", "bucket", "qualifier")} | {"period_end": old["period"], "passage": 0} | changes
    held = repository.document_extraction(raw["extraction_id"])
    windows = [tuple(item) for item in json.loads(held["passages_json"])] if held else []
    quote_at = document["text"].translate(pipeline._PLAIN).find(pipeline.plain(reading["quote"]))
    if quote_at >= 0:
        # A corrected quote may lie outside the passages the model saw: check it in its own surroundings.
        start, end = max(0, quote_at - 1500), min(len(document["text"]), quote_at + 1500)
        windows = [(start, end), *windows]
    checked = pipeline.verify(reading, metrics[0], document, windows)
    if checked["value"] is None:
        raise ValueError("disclosure_correction_unreadable:" + ",".join(checked["reasons"]))
    new_id = pipeline.save_candidate(
        repository, document, metrics[0], checked, extraction_id=raw["extraction_id"], method="manual_correction",
        corrects=candidate_id, now=at)
    if old["status"] in {"needs_evidence", "accepted"}:
        reject(repository, candidate_id, reviewer=reviewer, note=f"更正为 {new_id}：{note}", at=at)
    return repository.evidence_candidate(new_id) or {}
