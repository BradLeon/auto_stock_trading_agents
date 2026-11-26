"""Read side of company disclosure metrics: confirmed values by filing, newest filing first.

The same metric and period may be stated by several filings (a prospectus and its amendments, a 10-Q and the later
10-K). The default value is the one of the newest filing, whatever order a person confirmed them in; older values are
returned with it, and a difference is marked as a conflict. Values are never filled in or interpolated.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ...config import ledger_entity
from ..pipelines.structured import disclosure_metrics as pipeline
from ..pipelines.unstructured.registration import FORMS


def _filing_order(item: dict) -> tuple:
    form = item["form_type"]
    return (item["filing_date"], FORMS.index(form) if form in FORMS else len(FORMS), item["accession_number"],
            item["known_at"])


def series(repository: Any, entity: str, *, metric: str = "", as_of: datetime | None = None) -> list[dict]:
    """One row per (metric, period, basis, bucket) with the superseded filings' values."""
    _, metrics = pipeline.issuer_metrics(entity, [metric] if metric else None)
    declared = {item.metric_id: item for item in metrics}
    cutoff = as_of.astimezone(UTC).isoformat() if as_of else ""
    groups: dict[tuple, list[dict]] = {}
    for row in repository.observations(
            dataset_id=pipeline.DATASET_ID, entity_id=ledger_entity(entity.upper()), as_of=as_of,
            latest_only=False, limit=100_000):
        if row["metric_id"] not in declared:
            continue
        links = repository.evidence_links(observation_id=row["observation_id"])
        # Withdrawn confirmations leave the observation in place but no longer accepted.
        if not any(link["verification_status"] == "accepted" or
                   (cutoff and link["reviewed_at"] > cutoff) for link in links):
            continue
        raw = json.loads(row["raw_payload"])
        quality = json.loads(row["quality_json"])
        dimensions = json.loads(row["dimensions_json"] or "{}")
        bucket = next(iter(dimensions.values()), "")
        groups.setdefault((row["metric_id"], row["period"], row["period_basis"], bucket), []).append({
            "value": row["value"], "unit": row["unit"], "form_type": raw["form_type"],
            "filing_date": raw["filing_date"], "accession_number": raw["accession_number"],
            "qualifier": raw["qualifier"], "quote": raw["quote"], "document_id": raw["document_id"],
            "candidate_id": raw["candidate_id"], "observation_id": row["observation_id"],
            "known_at": row["known_at"], "reviewer": quality.get("reviewer", ""),
            "basis_note": quality.get("basis_note", "")})
    out = []
    for (metric_id, period, basis, bucket), items in sorted(groups.items()):
        items.sort(key=_filing_order)
        chosen = items[-1]
        out.append({
            "metric": declared[metric_id].key, "metric_id": metric_id, "name": declared[metric_id].name,
            "period": period, "period_basis": basis, "bucket": bucket, **chosen,
            "superseded": [{key: item[key] for key in
                            ("value", "form_type", "filing_date", "observation_id", "basis_note")}
                           for item in items[:-1]],
            "conflict": any(item["value"] != chosen["value"] for item in items[:-1]),
        })
    return out


def coverage(repository: Any, entity: str) -> list[dict]:
    """Per declared metric and filing: published, waiting, incomplete, not disclosed, not located or failed."""
    _, metrics = pipeline.issuer_metrics(entity)
    subject = ledger_entity(entity.upper())
    latest: dict[tuple, dict] = {}
    for row in repository.document_extractions(entity_id=subject):          # oldest first
        latest[(row["metric_id"], row["document_id"])] = row
    candidates = [row for row in repository.evidence_candidates(entity_id=subject, limit=100_000)
                  if row["dataset_id"] == pipeline.DATASET_ID]
    out = []
    for metric in metrics:
        rows = [row for (metric_id, _), row in latest.items() if metric_id == metric.metric_id]
        filings = []
        for row in rows:
            mine = [item for item in candidates
                    if item["metric_id"] == metric.metric_id and item["document_id"] == row["document_id"]]
            pending = [item for item in mine if item["status"] == "needs_evidence"]
            filings.append({
                "document_id": row["document_id"], "state": row["status"],
                "published": sum(item["status"] == "accepted" for item in mine),
                "pending_confirmation": sum(not json.loads(item["reason_codes_json"]) for item in pending),
                "evidence_incomplete": sum(bool(json.loads(item["reason_codes_json"])) for item in pending)})
        out.append({"metric": metric.key, "name": metric.name,
                    "state": "not_extracted" if not filings else
                    "published" if any(item["published"] for item in filings) else
                    "waiting" if any(item["pending_confirmation"] for item in filings) else
                    "incomplete" if any(item["evidence_incomplete"] for item in filings) else
                    "failed" if any(item["state"] == "failed" for item in filings) else "not_disclosed",
                    "filings": filings})
    return out
