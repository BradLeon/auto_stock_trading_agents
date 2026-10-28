"""Reviewed FactSet group publication; report versions are never mixed."""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.structured_models import (
    EvidenceLink,
    ObservationInput,
    SeriesIdentity,
    VerificationStatus,
)
from ..sources.factset_contracts import GroupPackage, digest, validate_group
from ..sources.factset_earnings_charts import FACTSET_CHARTS
from ..sources.factset_report_layout import load_layout_policy
from ..stores.structured.factset_reviews import FactSetReviews
from ..rollout_modes import source_mode
from .factset_earnings_insight import SOURCE_ID, DATASET_ID, _require_managed_write


class FactSetGroupPipeline:
    """Keep extraction packages immutable and publish only exact reviewed groups.

    Manifest identities include the package and review, preventing a later
    approval from rewriting an earlier shadow manifest's historical known_at.
    """

    def __init__(self, repository, *, policy=None, clock=None):
        self.repository = repository
        self.policy = policy or load_layout_policy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(
        self, package: GroupPackage, *, artifact_id: str, document_id: str, review_id: str = ""
    ) -> dict:
        _require_managed_write(self.repository)
        if not isinstance(review_id, str):
            raise ValueError("bound_review_id_required")
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("publication_clock_must_be_aware")
        artifact = self.repository.artifact(artifact_id)
        if artifact is None or artifact["artifact_content_hash"] != package.pdf_hash:
            raise ValueError("package_pdf_artifact_mismatch")
        reviews = FactSetReviews(self.repository)
        reviews.register(package, at=now)
        reasons = validate_group(package, self.policy)
        approval = None
        if review_id and not reasons:
            try:
                approval = reviews.require_approval(
                    package, review_id, policy=self.policy, as_of=now
                )
            except ValueError as exc:
                reasons.append(str(exc))
        elif not review_id:
            reasons.append("independent_review_required")
        passed = approval is not None and not reasons
        partition = "sector_group:" + package.group.key
        # Failure and success get separate immutable release identities.
        revision = (
            package.extractor_version
            + ":"
            + digest(
                {"package": package.package_hash, "review": review_id, "reasons": sorted(reasons)}
            )
        )
        prior = [
            row
            for row in self.repository.release_manifests(
                dataset_id=DATASET_ID, partition=partition, limit=10000
            )
            if row["version_id"] == package.document_version
            and row["extractor_version"] == revision
        ]
        if prior:
            return {
                "status": "no_change",
                "release_id": prior[0]["release_id"],
                "passed": prior[0]["passed"],
                "release_status": prior[0]["status"],
                "quality": prior[0]["quality"],
                "observation_ids": prior[0]["observation_ids"],
                "package_hash": package.package_hash,
            }
        self.repository.bootstrap_catalog()
        run_id = self.repository.begin_ingestion(
            source_id=SOURCE_ID,
            dataset_id=DATASET_ID,
            query_scope={
                "package_hash": package.package_hash,
                "partition": partition,
                "review_id": review_id,
            },
        )
        mapping = {
            definition.chart_id: definition.expected_columns for definition in FACTSET_CHARTS
        }
        mapping["eps_guidance"] = {
            **mapping["eps_guidance"],
            "positive_share": "earnings.guidance.positive_share",
            "negative_share": "earnings.guidance.negative_share",
        }
        observations = []
        for cell in package.candidates:
            metric = mapping.get(package.group.chart_id, {}).get(cell.column, "")
            candidate_id = digest(
                {"package": package.package_hash, "cell": cell.model_dump(mode="json")}
            )
            # The extraction candidate is immutable; review lives separately.
            with self.repository._lock:
                exists = self.repository.conn.execute(
                    "SELECT 1 FROM structured_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
            if not exists:
                self.repository.save_candidate(
                    candidate_id=candidate_id,
                    run_id=run_id,
                    source_id=SOURCE_ID,
                    dataset_id=DATASET_ID,
                    entity_id=cell.entity_id,
                    provider_field=cell.column,
                    metric_id=metric,
                    period=package.group.period,
                    value=float(cell.value) if cell.value is not None else None,
                    unit=cell.unit,
                    currency="",
                    status=cell.status,
                    reason_codes=list(cell.reasons),
                    artifact_id=artifact_id,
                    raw={"package_hash": package.package_hash, **cell.model_dump(mode="json")},
                    at=now,
                )
            observation_id = ""
            if passed:
                if not metric:
                    raise ValueError("unregistered_group_metric")
                dimensions = {
                    "scope_id": package.group.scope_id,
                    "scope_version": package.group.scope_version,
                    "entity_ids": list(package.group.entity_ids),
                    "estimate_state": package.group.estimate_state,
                    "document_version": package.document_version,
                    "package_hash": package.package_hash,
                }
                if cell.comparison_date:
                    dimensions["comparison_date"] = cell.comparison_date.isoformat()
                vintage = self.repository.save_observation(
                    ObservationInput(
                        series=SeriesIdentity(
                            source_id=SOURCE_ID,
                            dataset_id=DATASET_ID,
                            entity_id=cell.entity_id,
                            metric_id=metric,
                            unit=cell.unit,
                            period_basis=package.group.period_basis,
                            dimensions=dimensions,
                        ),
                        period=package.group.period,
                        value=float(cell.value),
                        known_at=now,
                        fetched_at=now,
                        artifact_id=artifact_id,
                        quality={"review_id": review_id, "group_key": package.group.key},
                        raw={
                            "package_hash": package.package_hash,
                            "candidate_id": candidate_id,
                            "report_date": package.report_date.isoformat(),
                            "version_id": package.document_version,
                            "document_id": document_id,
                        },
                    )
                )
                observation_id = vintage.id
                observations.append(observation_id)
            for evidence in (cell.label_evidence,) + cell.value_evidence:
                self.repository.save_evidence_link(
                    EvidenceLink(
                        candidate_id=candidate_id,
                        observation_id=observation_id,
                        document_id=document_id,
                        version_id=package.document_version,
                        anchor_kind="image_region",
                        page_number=evidence.page_number,
                        chart_id=package.group.chart_id,
                        region=evidence.region,
                        extraction_method=evidence.method,
                        source_tier="licensed_primary",
                        verification_status=VerificationStatus.ACCEPTED
                        if passed
                        else VerificationStatus.NEEDS_EVIDENCE,
                        reviewer=approval["reviewer"] if approval else "",
                        reviewed_at=datetime.fromisoformat(approval["reviewed_at"])
                        if approval
                        else None,
                    )
                )
        quality = {
            "priority": self.policy['scope_policy']['scopes'].get(package.group.scope_id, {}).get('priority', 'unknown'),
            "passed": passed,
            "group": package.group.model_dump(mode="json"),
            "package_hash": package.package_hash,
            "policy_hash": package.policy_hash,
            "review_id": review_id,
            "reason_codes": reasons,
            "observed_cells": len(package.candidates),
            "admitted_cells": len(observations),
        }
        release_status = (
            "platform" if passed and source_mode("factset_earnings_insight_sector") == "platform"
            else "shadow"
        )
        release_id = self.repository.save_release_manifest(
            source_id=SOURCE_ID,
            dataset_id=DATASET_ID,
            partition=partition,
            report_date=package.report_date.isoformat(),
            document_id=document_id,
            version_id=package.document_version,
            artifact_id=artifact_id,
            known_at=now,
            extractor_version=revision,
            passed=passed,
            quality=quality,
            observation_ids=observations,
            status=release_status,
        )
        self.repository.finish_ingestion(
            run_id,
            status="succeeded" if passed else "partial",
            discovered=len(package.candidates),
            accepted=len(observations),
            quarantined=0 if passed else len(package.candidates),
            reason_codes={r: 1 for r in reasons},
            at=now,
        )
        return {
            "status": "succeeded" if passed else "partial",
            "passed": passed,
            "release_status": release_status,
            "release_id": release_id,
            "package_hash": package.package_hash,
            "quality": quality,
            "observation_ids": observations,
        }
