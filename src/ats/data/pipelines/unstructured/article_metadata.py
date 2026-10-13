"""Bounded re-interpretation of admitted article bytes, through platform ownership."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...articles.material_quality import (
    POLICY_VERSION, COMPLETENESS_VERSION, visible_analysis_view, association_evidence, completeness_assessment,
)


def proposed_metadata(row: dict, body: str, *, entities: list[str],
                      field_evidence: dict | None = None) -> dict:
    """Never recover dates from fetch time or an incomplete calendar in the body.

    Explicit field corrections must be tied to the exact immutable body and span;
    they cannot silently overwrite fields merely because a model suggested them.
    """
    title = str(row.get("title") or "")
    quality = dict(row.get("quality") or {})
    provenance = dict(quality.get("provenance") or {})
    for field, evidence in (field_evidence or {}).items():
        if field not in {"title", "lede", "subtitle"}:
            raise ValueError("only visible text fields can be recovered from body spans")
        if evidence.get("version_id") != row["version"]["version_id"]:
            raise ValueError("field evidence version mismatch")
        start, end = evidence["start"], evidence["end"]
        if not 0 <= start < end <= len(body) or body[start:end] != evidence["text"]:
            raise ValueError("field evidence does not resolve against immutable body")
        if field == "title":
            title = evidence["text"]
        provenance[field] = evidence["text"]
        provenance[f"{field}_source"] = "body:verified_span"
    view = visible_analysis_view(body, title=title, source=str(row.get("source") or ""))
    evidence = association_evidence({"title": title, "lede": provenance.get("lede", ""),
        "subtitle": provenance.get("subtitle", ""), "body": view.text}, entities)
    for item in evidence:
        if item["field"] == "body":
            item.update(view.locator(item["start"], item["end"]))
    stored = str(row.get("completeness") or "unknown")
    baseline = stored
    if quality.get("original_hash") == view.original_hash and (
            quality.get("visibility") or {}).get("policy_version") in {"visible-content-v2", COMPLETENESS_VERSION}:
        baseline = quality["visibility"].get("stored", stored)
    visibility = (completeness_assessment(body, stored=baseline)
                  if row.get("source") == "semianalysis" else {})
    quality.update(provenance=provenance, policy_version=POLICY_VERSION,
        entity_evidence=evidence, original_hash=view.original_hash,
        cleaner_version=view.version, effective_chars=len(view.text))
    if field_evidence:
        quality["field_evidence"] = field_evidence
    if visibility:
        quality["visibility"] = visibility
    return {"title": title, "published_at": str(row.get("published_at") or ""),
        "completeness": visibility.get("completeness", stored),
        "truncation_reason": visibility.get("reason") or str(row.get("truncation_reason") or ""),
        "related_entities": sorted({item["entity"] for item in evidence}), "quality": quality}


def reconcile_article_metadata(source_id: str, *, now: datetime, repository=None,
                               dry_run: bool = True, limit: int = 200,
                               field_evidence: dict | None = None,
                               document_ids: list[str] | None = None) -> dict:
    """No fetch/model calls. Production mutation requires the source's queue lease.

    Dry-run is the default; isolated repositories can be used to test the identical
    mutation path. Unreadable immutable bytes are gaps, never repaired by invention.
    """
    from ....config import load_entities
    from ...runtime.repository import platform_data_db_path
    from ...stores.unstructured import (
        get_platform_unstructured_repository, get_platform_unstructured_store,
    )
    from .article_ingest import _article_sources

    if source_id not in _article_sources():
        raise ValueError("unregistered article source")
    if now.tzinfo is None:
        raise ValueError("metadata revision needs a timezone-aware known_at")
    if not 1 <= limit <= 1000:
        raise ValueError("metadata revision limit must be between 1 and 1000")
    if document_ids is not None and len(document_ids) > 1000:
        raise ValueError("metadata revision document scope is too large")
    owned = repository is None
    if owned and not dry_run:
        from ...persistent_queue import require_queue_worker

        require_queue_worker(source_id)
    repo = repository or (get_platform_unstructured_repository() if dry_run else
                           get_platform_unstructured_store())
    try:
        if not dry_run and Path(repo.path).resolve() == platform_data_db_path().resolve():
            from ...persistent_queue import require_queue_worker

            require_queue_worker(source_id)
        rows = [row for row in repo.document_materials_at(as_of=now, limit=10000)
                if row.get("source") == source_id and
                (document_ids is None or row["document_id"] in document_ids)][:limit]
        result = {"source_id": source_id, "known_at": now.isoformat(), "dry_run": dry_run,
                  "rows": [], "body_versions_created": 0, "model_calls": 0, "fetches": 0}
        for row in rows:
            # Resolve the selected exact version, not a newer current-index body.
            from ...products.unstructured import _read_version_text

            body = _read_version_text(row["version"])
            if not body:
                result["rows"].append({"document_id": row["document_id"], "status": "body_integrity_gap"})
                continue
            metadata = proposed_metadata(row, body, entities=list(load_entities()),
                field_evidence=(field_evidence or {}).get(row["document_id"]))
            publication_id = ""
            if not dry_run:
                current = repo.latest_document_version(row["document_id"])
                if current["version_id"] != row["version"]["version_id"]:
                    raise ValueError("cannot revise a superseded body through current metadata index")
                publication_id = repo.revise_document_metadata(row["document_id"],
                    metadata=metadata, known_at=now)
            result["rows"].append({"document_id": row["document_id"],
                "version_id": row["version"]["version_id"], "publication_id": publication_id,
                "status": "proposed" if dry_run else "revised" if publication_id else "no_change",
                "metadata": metadata,
                "gaps": [name for name in ("title", "published_at") if not metadata[name]]})
        return result
    finally:
        if owned:
            repo.close()
