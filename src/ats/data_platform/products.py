"""Consumer-facing research data products.

Callers ask domain questions here instead of knowing which compatibility table, file,
or index currently serves them. Storage can evolve without changing agent contracts.
"""

from __future__ import annotations

from datetime import datetime


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
            "processing": {
                "total": len(processing),
                "running": sum(r["status"] == "running" for r in processing),
                "failed": sum(r["status"] == "failed" for r in processing),
                "succeeded": sum(r["status"] == "succeeded" for r in processing),
            },
        }

    def lineage(self, projection_id: str) -> dict | None:
        return self.store.projection_lineage(projection_id)


def get_data_products() -> DataProducts:
    return DataProducts()
