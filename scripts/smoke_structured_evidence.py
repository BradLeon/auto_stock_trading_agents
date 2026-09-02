#!/usr/bin/env python3
"""Isolated evidence-gated validation using phase-one accepted document assets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from ats.data import source_cache
from ats.structured import (
    EvidenceCandidateInput,
    EvidenceWorkbench,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


BLOOMBERG_DOC = (
    "NEWS:https-finance.yahoo.com-technology-ai-articles-anthropic-expects-match-"
    "spacex-re-512607cdb536:news_item")
BLOOMBERG_VERSION = BLOOMBERG_DOC + "@3cbc67bb7cb1cc84"
DUPLICATE_DOC = (
    "NEWS:https-finance.yahoo.com-m-3b2d99db-61ba-3a77-ac5c-f75a051a0c8f-"
    "anthropic-ipo-may-6c2d5ad0d794:news_item")
DUPLICATE_VERSION = DUPLICATE_DOC + "@e46fa03cc234044f"
SUMMARY_DOC = (
    "NEWS:https-finnhub.io-api-news-id-9c1c904945c3a9493e34e5947e8958b71d57cb7cb"
    "faac5b7cf8-12577bfb6815:news_item")
SUMMARY_VERSION = SUMMARY_DOC + "@ceb2b20c018fa079"


class DocumentCatalogResolver:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def __call__(self, document_id: str, version_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT d.document_id,d.source,d.source_url,d.completeness,d.carrier_format,"
            "v.version_id,v.local_path,v.chars FROM source_documents d "
            "JOIN document_versions v ON v.document_id=d.document_id "
            "WHERE d.document_id=? AND v.version_id=?", (document_id, version_id)).fetchone()
        if row is None:
            return None
        path = Path(row["local_path"])
        if not path.is_file():
            return None
        _, body = source_cache._split_frontmatter(
            path.read_text(encoding="utf-8", errors="ignore"))
        # Metadata-only headlines link elsewhere and are not complete evidence even
        # when the carrier itself was fully downloaded.
        evidence_complete = not (
            row["carrier_format"] == "api_json" and int(row["chars"] or 0) < 500)
        return {**dict(row), "text": body.strip(),
                "evidence_complete": evidence_complete}

    def close(self):
        self.conn.close()


def _candidate(resolver, *, entity: str, metric: str, value: float,
               document_id: str, version_id: str, quote: str,
               event_type: str, event_date: str, event_label: str,
               period: str, source_tier: str = "reliable_media",
               confidence: float = 0.99, date_precision: str = "day"):
    document = resolver(document_id, version_id)
    body = document["text"] if document else ""
    start = body.find(quote)
    if start < 0:
        raise ValueError(f"fixed evidence quote not found in {document_id}")
    return EvidenceCandidateInput(
        entity=entity, metric_id=metric, value=value, unit="USD", currency="USD",
        period=period, event_type=event_type, event_date=event_date,
        event_label=event_label, document_id=document_id, version_id=version_id,
        char_start=start, char_end=start + len(quote), extraction_method="manual_fixture",
        source_tier=source_tier, confidence=confidence,
        published_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        raw={"date_precision": date_precision, "quote": quote,
             "source_url": (document or {}).get("source_url", "")})


def run(root: Path, documents_db: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    repo = SQLiteStructuredRepository(
        root / "evidence-smoke.sqlite", artifact_root=root / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    resolver = DocumentCatalogResolver(documents_db)
    workbench = EvidenceWorkbench(repo, document_resolver=resolver)

    anthropic_round = (
        "The five-year-old company raised $65 billion in May at a $965 billion valuation, "
        "surpassing rival OpenAI’s valuation of $852 billion in March when the ChatGPT "
        "maker raised $122 billion.")
    anthropic_arr = (
        "The company saw preliminary second quarter revenue of more than $11.5 billion, "
        "compared to $787 million in the corresponding period in 2025, and its run rate, "
        "a metric that projects full-year revenue from a shorter period, hit $65 billion "
        "by the end of July, Bloomberg News reported.")
    duplicate_round = (
        "Anthropic is not starting from zero. It closed a $65 billion round on May 28 at "
        "a $965 billion valuation. Altimeter, Dragoneer, Greenoaks, and Sequoia led it. "
        "Amazon added $5 billion.")
    duplicate_arr = (
        "Anthropic's annualized revenue run rate reached $65 billion in July, up from "
        "$47 billion in May. It booked roughly $10 billion in all of 2025.")
    summary_quote = "'Anthropic Revenue Run Rate Surpasses $65 Billion Ahead of IPO' - Bloomberg"

    candidates = [
        ("accept", _candidate(
            resolver, entity="ANTHROPIC", metric="event.funding.amount",
            value=65_000_000_000, document_id=BLOOMBERG_DOC,
            version_id=BLOOMBERG_VERSION, quote=anthropic_round,
            event_type="funding", event_date="2026-05-01",
            event_label="2026 May private round", period="2026-05",
            date_precision="month")),
        ("accept", _candidate(
            resolver, entity="ANTHROPIC", metric="event.valuation.reported",
            value=965_000_000_000, document_id=BLOOMBERG_DOC,
            version_id=BLOOMBERG_VERSION, quote=anthropic_round,
            event_type="funding", event_date="2026-05-01",
            event_label="2026 May private round", period="2026-05",
            date_precision="month")),
        ("accept", _candidate(
            resolver, entity="OPENAI", metric="event.funding.amount",
            value=122_000_000_000, document_id=BLOOMBERG_DOC,
            version_id=BLOOMBERG_VERSION, quote=anthropic_round,
            event_type="funding", event_date="2026-03-01",
            event_label="2026 March private round", period="2026-03",
            date_precision="month")),
        ("accept", _candidate(
            resolver, entity="OPENAI", metric="event.valuation.reported",
            value=852_000_000_000, document_id=BLOOMBERG_DOC,
            version_id=BLOOMBERG_VERSION, quote=anthropic_round,
            event_type="funding", event_date="2026-03-01",
            event_label="2026 March private round", period="2026-03",
            date_precision="month")),
        ("accept", _candidate(
            resolver, entity="ANTHROPIC", metric="company.arr",
            value=65_000_000_000, document_id=BLOOMBERG_DOC,
            version_id=BLOOMBERG_VERSION, quote=anthropic_arr,
            event_type="arr", event_date="2026-07-31",
            event_label="2026 July revenue run rate", period="2026-07-31")),
        # Two media retellings bind to the same events/observations, not new rounds.
        ("accept", _candidate(
            resolver, entity="ANTHROPIC", metric="event.funding.amount",
            value=65_000_000_000, document_id=DUPLICATE_DOC,
            version_id=DUPLICATE_VERSION, quote=duplicate_round,
            event_type="funding", event_date="2026-05-01",
            event_label="2026 May private round", period="2026-05",
            date_precision="month")),
        ("accept", _candidate(
            resolver, entity="ANTHROPIC", metric="company.arr",
            value=65_000_000_000, document_id=DUPLICATE_DOC,
            version_id=DUPLICATE_VERSION, quote=duplicate_arr,
            event_type="arr", event_date="2026-07-31",
            event_label="2026 July revenue run rate", period="2026-07-31")),
        # This headline is a pointer to inaccessible body text: keep, but reject it.
        ("reject", _candidate(
            resolver, entity="ANTHROPIC", metric="company.arr",
            value=65_000_000_000, document_id=SUMMARY_DOC,
            version_id=SUMMARY_VERSION, quote=summary_quote,
            event_type="arr", event_date="2026-07-31",
            event_label="2026 July revenue run rate", period="2026-07-31")),
        # The span says valuation; assigning it to funding is a semantic error that
        # deterministic shape checks retain for review and the reviewer rejects.
        ("reject", _candidate(
            resolver, entity="ANTHROPIC", metric="event.funding.amount",
            value=965_000_000_000, document_id=BLOOMBERG_DOC,
            version_id=BLOOMBERG_VERSION, quote=anthropic_round,
            event_type="funding", event_date="2026-05-01",
            event_label="2026 May private round", period="2026-05",
            date_precision="month")),
    ]

    reviewed = []
    for decision, item in candidates:
        proposed = workbench.propose(item)
        status = "accepted" if decision == "accept" else "rejected"
        reviewed.append(workbench.review(
            proposed["candidate_id"], status=status,
            reviewer="fixture.manual-inspection",
            note=("checked entity, metric semantics, value, USD unit, event/period and exact span"
                  if decision == "accept" else
                  "rejected: incomplete source or metric/span semantic mismatch")))

    observations = repo.observations(
        dataset_id="private_company_events", latest_only=False, accepted_only=True)
    manual_checks = []
    for row in observations:
        lineage = repo.lineage(row["observation_id"])
        evidence = [item for item in lineage["evidence"]
                    if item["verification_status"] == "accepted"]
        candidate_rows = [repo.evidence_candidate(item["candidate_id"]) for item in evidence]
        passed = bool(evidence) and all(
            item and item["entity_id"] == row["entity_id"]
            and item["metric_id"] == row["metric_id"]
            and float(item["value"]) == float(row["value"])
            and item["unit"] == row["unit"] and item["currency"] == row["currency"]
            and item["period"] == row["period"] and item["char_end"] > item["char_start"]
            for item in candidate_rows)
        manual_checks.append({
            "observation_id": row["observation_id"], "entity": row["entity_id"],
            "metric": row["metric_id"], "value": row["value"], "unit": row["unit"],
            "period": row["period"], "accepted_evidence": len(evidence), "passed": passed,
        })

    candidate_rows = repo.evidence_candidates(limit=100)
    rejected = [row for row in candidate_rows if row["status"] == "rejected"]
    incomplete = next(row for row in rejected if row["document_id"] == SUMMARY_DOC)
    events = repo.events()
    reviews = [item for row in candidate_rows
               for item in repo.evidence_reviews(row["candidate_id"])]
    artifacts = [dict(row) for row in repo.conn.execute(
        "SELECT a.source_id,a.source_version,a.pointer,b.relative_path,b.bytes "
        "FROM structured_artifacts a JOIN structured_artifact_blobs b "
        "ON b.blob_id=a.blob_id ORDER BY a.fetched_at").fetchall()]
    result = {
        "database": str(root / "evidence-smoke.sqlite"),
        "artifact_root": str(root / "artifacts"),
        "phase1_documents_db": str(documents_db),
        "candidates": {"total": len(candidate_rows),
                       "accepted": sum(row["status"] == "accepted" for row in candidate_rows),
                       "rejected": len(rejected),
                       "needs_evidence": sum(row["status"] == "needs_evidence"
                                             for row in candidate_rows)},
        "published_observations": len(observations), "events": events,
        "event_deduplication": {
            "accepted_candidates": sum(row["status"] == "accepted" for row in candidate_rows),
            "published_observations": len(observations),
            "anthropic_funding_events": len([
                row for row in events if row["entity_id"] == "ANTHROPIC"
                and row["event_type"] == "funding"]),
        },
        "incomplete_source_reason_codes": json.loads(incomplete["reason_codes_json"]),
        "manual_checks": manual_checks,
        "manual_accuracy": (sum(row["passed"] for row in manual_checks) /
                            len(manual_checks) if manual_checks else None),
        "review_log_entries": len(reviews), "artifacts": artifacts,
    }
    result["passed"] = (
        len(observations) == 5 and len(events) == 3
        and result["event_deduplication"]["anthropic_funding_events"] == 1
        and result["manual_accuracy"] == 1.0
        and "evidence_incomplete" in result["incomplete_source_reason_codes"]
        and all(row["status"] != "needs_evidence" for row in candidate_rows))
    report_path = root / "validation.json"
    result["report_json"] = str(report_path)
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    resolver.close()
    repo.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--documents-db", type=Path,
        default=Path("var/phase1_inspection_20260824_hardened/stage1.sqlite"))
    args = parser.parse_args()
    result = run(args.root, args.documents_db)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
