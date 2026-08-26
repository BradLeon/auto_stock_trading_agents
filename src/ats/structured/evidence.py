"""Evidence-first candidate, human review and publication workflow."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

from .models import (
    ArtifactDescriptor,
    EvidenceCandidateInput,
    EvidenceLink,
    ObservationInput,
    QualityStatus,
    SeriesIdentity,
    VerificationStatus,
)


DocumentResolver = Callable[[str, str], dict | None]
SOURCE_TIERS = {
    "company_primary", "regulatory", "reliable_media", "institutional_estimate",
}
TRANSITIONS = {
    "needs_evidence": {"accepted", "rejected"},
    "rejected": {"needs_evidence"},
    "accepted": {"superseded"},
    "superseded": {"needs_evidence"},
}


def _utc(value: datetime | None = None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _default_document_resolver(document_id: str, version_id: str) -> dict | None:
    from ..data import source_cache
    from ..memory import get_store

    store = get_store()
    version = next((row for row in store.document_versions(document_id)
                    if row["version_id"] == version_id), None)
    if version is None:
        return None
    path = Path(version.get("local_path") or "")
    if not path.is_file():
        return None
    _, body = source_cache._split_frontmatter(
        path.read_text(encoding="utf-8", errors="ignore"))
    return {"document_id": document_id, "version_id": version_id,
            "text": body.strip(), "local_path": str(path)}


class EvidenceWorkbench:
    """Deterministic release gate; extraction confidence is audit metadata only."""

    source_id = "accepted_document_evidence"
    dataset_id = "private_company_events"

    def __init__(self, repository, *, document_resolver: DocumentResolver | None = None,
                 clock=None):
        self.repository = repository
        self.document_resolver = document_resolver or _default_document_resolver
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def propose(self, candidate: EvidenceCandidateInput) -> dict:
        now = _utc(self.clock())
        reasons = []
        entity_id = self.repository.resolve_entity(candidate.entity)
        if not entity_id:
            entity_id = candidate.entity.strip().upper()
            reasons.append("entity_unresolved")
        metric = self.repository.metric(candidate.metric_id)
        if metric is None:
            reasons.append("metric_unregistered")
        dataset = self.repository.dataset(self.dataset_id)
        core = set(json.loads((dataset or {}).get("core_metrics_json") or "[]"))
        if candidate.metric_id not in core:
            reasons.append("metric_outside_dataset")
        if not candidate.event_date:
            reasons.append("event_date_unresolved")
        if not candidate.period:
            reasons.append("period_unresolved")
        if not candidate.unit:
            reasons.append("unit_unresolved")
        if metric and metric["unit_family"] in {"currency", "currency_per_share"} \
                and not candidate.currency:
            reasons.append("currency_unresolved")
        if candidate.source_tier not in SOURCE_TIERS:
            reasons.append("source_tier_unresolved")

        document = self.document_resolver(candidate.document_id, candidate.version_id)
        span_text = ""
        if document is None:
            reasons.append("document_version_missing")
        else:
            body = str(document.get("text") or "")
            if document.get("evidence_complete") is False:
                reasons.append("evidence_incomplete")
            if not candidate.char_end:
                reasons.append("evidence_span_missing")
            elif candidate.char_end > len(body):
                reasons.append("evidence_span_out_of_bounds")
            else:
                span_text = body[candidate.char_start:candidate.char_end]
                if not span_text.strip():
                    reasons.append("evidence_span_empty")

        event_id = self.repository.ensure_event(
            dataset_id=self.dataset_id, entity_id=entity_id,
            event_type=candidate.event_type, event_date=candidate.event_date,
            event_label=candidate.event_label,
            status="needs_evidence" if reasons else "active")
        identity = {
            "event_id": event_id, "entity_id": entity_id,
            "metric_id": candidate.metric_id, "period": candidate.period,
            "value": candidate.value, "unit": candidate.unit,
            "currency": candidate.currency, "document_id": candidate.document_id,
            "version_id": candidate.version_id, "char_start": candidate.char_start,
            "char_end": candidate.char_end,
        }
        candidate_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        self.repository.save_evidence_candidate({
            "candidate_id": candidate_id, "event_id": event_id,
            "source_id": self.source_id, "dataset_id": self.dataset_id,
            "entity_id": entity_id, "metric_id": candidate.metric_id,
            "period": candidate.period,
            "event_time": f"{candidate.event_date[:10]}T00:00:00+00:00",
            "published_at": candidate.published_at.isoformat()
            if candidate.published_at else "",
            "value": candidate.value, "unit": candidate.unit,
            "currency": candidate.currency, "document_id": candidate.document_id,
            "version_id": candidate.version_id, "char_start": candidate.char_start,
            "char_end": candidate.char_end,
            "extraction_method": candidate.extraction_method,
            "source_tier": candidate.source_tier, "confidence": candidate.confidence,
            "status": "needs_evidence", "reason_codes": reasons,
            "raw": {**candidate.raw, "evidence_span": span_text}, "at": now,
        })
        if document is not None and candidate.char_end > candidate.char_start:
            self.repository.save_evidence_link(EvidenceLink(
                candidate_id=candidate_id, document_id=candidate.document_id,
                version_id=candidate.version_id, char_start=candidate.char_start,
                char_end=candidate.char_end, extraction_method=candidate.extraction_method,
                source_tier=candidate.source_tier,
                verification_status=VerificationStatus.NEEDS_EVIDENCE))
        return self.repository.evidence_candidate(candidate_id) or {}

    def review(self, candidate_id: str, *, status: VerificationStatus | str,
               reviewer: str, note: str = "", at: datetime | None = None) -> dict:
        target = VerificationStatus(status).value
        candidate = self.repository.evidence_candidate(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        current = candidate["status"]
        if target not in TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid evidence transition {current}->{target}")
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        reviewed_at = _utc(at or self.clock())
        if target != "accepted":
            return self.repository.transition_evidence_candidate(
                candidate_id=candidate_id, to_status=target, reviewer=reviewer,
                note=note, at=reviewed_at)

        reasons = json.loads(candidate["reason_codes_json"] or "[]")
        if reasons:
            raise ValueError("candidate cannot be accepted: " + ",".join(reasons))
        observation_id = self._publish(candidate, reviewed_at=reviewed_at)
        self.repository.save_evidence_link(EvidenceLink(
            observation_id=observation_id, candidate_id=candidate_id,
            document_id=candidate["document_id"], version_id=candidate["version_id"],
            char_start=candidate["char_start"], char_end=candidate["char_end"],
            extraction_method=candidate["extraction_method"],
            source_tier=candidate["source_tier"],
            verification_status=VerificationStatus.ACCEPTED,
            reviewer=reviewer, reviewed_at=reviewed_at))
        return self.repository.transition_evidence_candidate(
            candidate_id=candidate_id, to_status=target, reviewer=reviewer,
            note=note, observation_id=observation_id, at=reviewed_at)

    def _publish(self, candidate: dict, *, reviewed_at: datetime) -> str:
        event_id = candidate["event_id"]
        existing = []
        for row in self.repository.observations(
                dataset_id=self.dataset_id, source_id=self.source_id,
                entity_id=candidate["entity_id"], metric_id=candidate["metric_id"],
                latest_only=False, accepted_only=False, limit=10_000):
            dimensions = json.loads(row.get("dimensions_json") or "{}")
            if dimensions.get("event_id") != event_id:
                continue
            accepted_links = [link for link in self.repository.evidence_links(
                observation_id=row["observation_id"])
                if link["verification_status"] == "accepted"]
            if accepted_links:
                existing.append(row)
        for row in existing:
            if (float(row["value"]) == float(candidate["value"])
                    and row["unit"] == candidate["unit"]
                    and row["currency"] == candidate["currency"]):
                return row["observation_id"]
        if existing:
            raise ValueError("event_value_conflict: supersede accepted evidence first")

        artifact = self.repository.put_artifact(
            {"document_id": candidate["document_id"],
             "version_id": candidate["version_id"],
             "char_start": candidate["char_start"], "char_end": candidate["char_end"]},
            ArtifactDescriptor(
                source_id=self.source_id, dataset_id=self.dataset_id,
                fetched_at=reviewed_at,
                query_scope={"candidate_id": candidate["candidate_id"],
                             "event_id": event_id},
                source_url=f"document://{candidate['document_id']}",
                source_version=candidate["version_id"],
                media_type="application/json", retention="evidence_link_only",
                storage_mode="pointer", pointer=(
                    f"{candidate['document_id']}#{candidate['char_start']}:"
                    f"{candidate['char_end']}")))
        published_at = (datetime.fromisoformat(candidate["published_at"])
                        if candidate["published_at"] else None)
        period_basis = "point_in_time" if candidate["metric_id"] == "company.arr" else "event"
        vintage = self.repository.save_observation(ObservationInput(
            series=SeriesIdentity(
                source_id=self.source_id, dataset_id=self.dataset_id,
                entity_id=candidate["entity_id"], metric_id=candidate["metric_id"],
                unit=candidate["unit"], currency=candidate["currency"],
                period_basis=period_basis,
                dimensions={"event_id": event_id,
                            "event_type": (self.repository.event(event_id) or {}).get(
                                "event_type", "")}),
            period=candidate["period"], value=float(candidate["value"]),
            event_time=datetime.fromisoformat(candidate["event_time"]),
            published_at=published_at, known_at=reviewed_at, fetched_at=reviewed_at,
            artifact_id=artifact.id, quality_status=QualityStatus.ACCEPTED,
            quality={"human_verified": True},
            raw={"candidate_id": candidate["candidate_id"],
                 "source_tier": candidate["source_tier"]}))
        return vintage.id
