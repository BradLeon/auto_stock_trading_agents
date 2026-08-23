"""Consumer-facing research data products.

Callers ask domain questions here instead of knowing which compatibility table, file,
or index currently serves them. Storage can evolve without changing agent contracts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json


class DataProducts:
    def __init__(self, store=None):
        if store is None:
            from ..memory import get_store

            store = get_store()
        self.store = store

    def indicator_series(self, *, source_id: str | None = None,
                         series: str | None = None, entity: str | None = None,
                         since: str | None = None, as_of: datetime | None = None,
                         include_vintages: bool = False, as_frame: bool = False):
        rows = self.store.measurements(
            source_id=source_id, series=series, entity=entity, since=since,
            as_of=as_of, latest_only=not include_vintages)
        if not as_frame:
            return rows
        try:
            import pandas as pd
        except ImportError as exc:  # optional dependency, explicit only when requested
            raise RuntimeError("pandas is required for as_frame=True") from exc
        return pd.DataFrame(rows)

    def company_research_package(self, entity: str, *,
                                 since: datetime | None = None) -> dict:
        """Shared facts plus task-specific views for one economic entity."""
        key = entity.upper()
        return {
            "entity": key,
            "documents": self.store.documents(
                entity=key, published_since=since.isoformat() if since else None, limit=1000),
            "measurements": self.store.measurements(entity=key, limit=2000),
            "facts": self.store.facts(entity=key, since=since, limit=1000),
            "pead_projections": self.store.task_projections(
                profile="pead_research", target_type="entity", target_id=key, limit=500),
        }

    def claim_evidence_package(self, concept: str, *, limit: int = 500) -> dict:
        projections = self.store.fact_projections(concept=concept, limit=limit)
        fact_ids = {p["fact_id"] for p in projections}
        facts = {f["fact_id"]: f for f in self.store.facts(
            include_superseded=False, limit=max(limit * 4, 500)) if f["fact_id"] in fact_ids}
        return {
            "concept": concept,
            "evidence": [dict(p, fact=facts.get(p["fact_id"])) for p in projections],
            "missing_facts": sorted(fact_ids - set(facts)),
        }

    def search_documents(self, query: str, *, entity: str | None = None,
                         source_contains: str | None = None,
                         published_since: str | None = None,
                         limit: int = 20) -> list[dict]:
        return self.store.search_document_chunks(
            query, entity=entity, source_contains=source_contains,
            published_since=published_since, limit=limit)

    def health(self) -> dict:
        processing = self.store.document_processing(limit=5000)
        return {
            "structured_sources": self.store.data_source_health(),
            "document_sources": self.store.document_source_health(),
            "candidate_admission": self.store.document_candidate_health(),
            "processing": {
                "total": len(processing),
                "running": sum(r["status"] == "running" for r in processing),
                "failed": sum(r["status"] == "failed" for r in processing),
                "succeeded": sum(r["status"] == "succeeded" for r in processing),
            },
        }

    def quality(self) -> dict:
        """Queryable release-gate metrics for accepted and quarantined documents."""
        from ..data import document_assets

        candidates = self.store.document_candidates(limit=100_000)
        check_counts = {
            "identity": {"checked": 0, "passed": 0},
            "period": {"checked": 0, "passed": 0},
        }
        candidate_check_counts = {
            "identity": {"checked": 0, "passed": 0},
            "period": {"checked": 0, "passed": 0},
        }
        reasons: Counter[str] = Counter()
        for row in candidates:
            try:
                validation = json.loads(row.get("validation_json") or "{}")
            except json.JSONDecodeError:
                validation = {}
                reasons["invalid_validation_json"] += 1
            checks = validation.get("checks") or {}
            for name in candidate_check_counts:
                if name in checks:
                    candidate_check_counts[name]["checked"] += 1
                    candidate_check_counts[name]["passed"] += int(bool(checks[name]))
                    if row.get("status") == "accepted":
                        check_counts[name]["checked"] += 1
                        check_counts[name]["passed"] += int(bool(checks[name]))
            for issue in validation.get("issues") or ():
                reasons[str(issue.get("code") or "unknown_reason")] += 1

        inventory = self.store.document_quality_inventory()
        total_docs = sum(int(row["documents"] or 0) for row in inventory)
        full_docs = sum(int(row["documents"] or 0) for row in inventory
                        if row["completeness"] == "full")
        docs = self.store.documents(limit=100_000)
        consistency_issues: list[dict] = []
        checked = 0
        for row in docs:
            checked += 1
            version = self.store.latest_document_version(row["document_id"])
            if version is None:
                consistency_issues.append({
                    "document_id": row["document_id"], "reason": "version_missing"})
                continue
            body = document_assets.read_document(row["document_id"], store=self.store)
            if not body:
                consistency_issues.append({
                    "document_id": row["document_id"], "reason": "read_mismatch"})

        accepted_candidates = sum(row.get("status") == "accepted" for row in candidates)
        quarantined_candidates = sum(row.get("status") == "quarantined"
                                     for row in candidates)

        def ratio(passed: int, denominator: int) -> float | None:
            return round(passed / denominator, 6) if denominator else None

        return {
            "coverage": {
                "accepted_documents": total_docs,
                "candidates": len(candidates),
                "accepted_candidates": accepted_candidates,
                "quarantined_candidates": quarantined_candidates,
                "admission_rate": ratio(accepted_candidates, len(candidates)),
            },
            "correctness": {
                name: {**counts, "rate": ratio(counts["passed"], counts["checked"])}
                for name, counts in check_counts.items()
            },
            "candidate_checks": {
                name: {**counts, "rate": ratio(counts["passed"], counts["checked"])}
                for name, counts in candidate_check_counts.items()
            },
            "completeness": {
                "documents": total_docs, "full": full_docs,
                "rate": ratio(full_docs, total_docs),
            },
            "source_lag": [
                {"source_id": row["source_id"], "status": row.get("status"),
                 "snapshot_updated_at": row.get("snapshot_updated_at"),
                 "snapshot_lag_hours": row.get("snapshot_lag_hours")}
                for row in self.store.data_source_health()
            ],
            "read_consistency": {
                "checked": checked, "passed": checked - len(consistency_issues),
                "rate": ratio(checked - len(consistency_issues), checked),
                "issues": consistency_issues,
            },
            "reason_codes": dict(sorted(reasons.items())),
            "inventory": inventory,
        }

    def lineage(self, projection_id: str) -> dict | None:
        return self.store.projection_lineage(projection_id)


def get_data_products() -> DataProducts:
    return DataProducts()
