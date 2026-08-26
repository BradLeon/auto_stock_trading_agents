#!/usr/bin/env python3
"""Isolated real-source validation for SEC Company Facts and stock_statement."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from ats.data.sources.company_financials import (
    DefeatBetaStatementAdapter,
    SECCompanyFactsAdapter,
)
from ats.data_platform import DataProducts
from ats.structured import (
    FetchRequest,
    IngestionPipeline,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


ENTITIES = ["AMZN", "MSFT", "KLAC", "TSM"]
CURRENCIES = {"AMZN": "USD", "MSFT": "USD", "KLAC": "USD", "TSM": "TWD"}


class _Store:
    def projection_lineage(self, _identifier):
        return None


def _coverage(repo, entity: str, source: str, raw_core: set[str]) -> dict:
    rows = repo.observations(
        dataset_id="company_financials", entity_id=entity, source_id=source,
        latest_only=True, accepted_only=True, limit=100_000)
    if not rows:
        return {"entity": entity, "source_id": source, "records": 0,
                "first_period": "", "last_period": "", "latest_metric_coverage": 0.0,
                "latest_metrics": []}
    latest_period = max(row["period"] for row in rows)
    latest_metrics = sorted({row["metric_id"] for row in rows
                             if row["period"] == latest_period})
    return {
        "entity": entity, "source_id": source, "records": len(rows),
        "first_period": min(row["period"] for row in rows),
        "last_period": latest_period,
        "latest_metric_coverage": len(set(latest_metrics) & raw_core) / len(raw_core),
        "latest_metrics": latest_metrics,
        "latest_published_at": max((row["published_at"] for row in rows
                                    if row.get("published_at")), default=""),
        "latest_known_at": max((row["known_at"] for row in rows), default=""),
        "vintages": len(repo.observations(
            dataset_id="company_financials", entity_id=entity, source_id=source,
            latest_only=False, accepted_only=True, limit=100_000)),
    }


def run(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    repo = SQLiteStructuredRepository(
        root / "financial-smoke.sqlite", artifact_root=root / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    pipeline = IngestionPipeline(repo)
    started = datetime.now(timezone.utc)
    runs = []
    errors = []
    for entity in ENTITIES:
        request = FetchRequest(
            source_id="sec_companyfacts", dataset_id="company_financials",
            entities=[entity], query_scope={"since": "2024-01-01"})
        try:
            outcome = pipeline.run(SECCompanyFactsAdapter(), request)
            runs.append({"entity": entity, "source_id": request.source_id,
                         **{key: value for key, value in outcome.items()
                            if key != "results"}})
        except Exception as exc:
            errors.append({"entity": entity, "source_id": request.source_id,
                           "error_type": type(exc).__name__, "message": str(exc)})
    mirror_entities = [*ENTITIES, "MIRROR_MISSING_ENTITY"]
    request = FetchRequest(
        source_id="defeatbeta_stock_statement", dataset_id="company_financials",
        entities=mirror_entities, query_scope={
            "since": "2024-01-01", "currency_by_entity": CURRENCIES})
    try:
        outcome = pipeline.run(DefeatBetaStatementAdapter(), request)
        runs.append({"entity": ",".join(mirror_entities), "source_id": request.source_id,
                     **{key: value for key, value in outcome.items()
                        if key != "results"}})
    except Exception as exc:
        errors.append({"entity": ",".join(mirror_entities), "source_id": request.source_id,
                       "error_type": type(exc).__name__, "message": str(exc)})

    dataset = repo.dataset("company_financials") or {}
    core = set(json.loads(dataset.get("core_metrics_json", "[]")))
    derived = {row["metric_id"] for row in repo.metrics() if row["derived"]}
    raw_core = core - derived
    coverage = [_coverage(repo, entity, source, raw_core)
                for entity in [*ENTITIES, "MIRROR_MISSING_ENTITY"]
                for source in ("sec_companyfacts", "defeatbeta_stock_statement")]
    products = DataProducts(store=_Store(), structured_repository=repo)
    quality = products.financial_quality()
    candidates = repo.candidates(dataset_id="company_financials", limit=1_000_000)
    accepted = sum(row["status"] == "accepted" for row in candidates)
    quarantined = sum(row["status"] == "quarantined" for row in candidates)
    pending = repo.pending_mappings(limit=100_000)
    conflicts = repo.conflicts(dataset_id="company_financials", limit=100_000)
    before = started - timedelta(microseconds=1)
    future_leaks = []
    for entity in ENTITIES:
        if repo.observations(
                dataset_id="company_financials", entity_id=entity,
                as_of=before, latest_only=True):
            future_leaks.append(entity)
    artifacts = [dict(row) for row in repo.conn.execute(
        "SELECT a.source_id,a.source_url,a.source_version,b.relative_path,b.bytes,"
        "b.content_hash FROM structured_artifacts a JOIN structured_artifact_blobs b "
        "ON b.blob_id=a.blob_id ORDER BY a.source_id,a.fetched_at").fetchall()]
    mirror_missing = next(row for row in coverage
                          if row["entity"] == "MIRROR_MISSING_ENTITY"
                          and row["source_id"] == "defeatbeta_stock_statement")
    issue_counts = {}
    for issue in quality["issues"]:
        issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1
    result = {
        "database": str(root / "financial-smoke.sqlite"),
        "artifact_root": str(root / "artifacts"),
        "runs": runs, "errors": errors, "coverage": coverage,
        "candidate_admission": {
            "accepted": accepted, "quarantined": quarantined,
            "rate": accepted / (accepted + quarantined) if accepted + quarantined else None},
        "pending_mapping_count": len(pending),
        "pending_mapping_field_sample": sorted(
            {row["provider_field"] for row in pending})[:30],
        "conflict_count": len(conflicts),
        "quality": {"status": quality["status"], "checks": quality["checks"],
                    "issue_counts": issue_counts, "issue_sample": quality["issues"][:20]},
        "future_leaks": future_leaks,
        "mirror_missing_explicit": mirror_missing["records"] == 0,
        "artifacts": artifacts,
    }
    official_coverage = [row for row in coverage if row["entity"] in ENTITIES
                         and row["source_id"] == "sec_companyfacts"]
    freshness_hours = float(json.loads(
        dataset.get("quality_json", "{}")).get("freshness_hours_max", 240))
    freshness = {}
    now = datetime.now(timezone.utc)
    for row in official_coverage:
        published = row.get("latest_published_at")
        freshness[row["entity"]] = (
            (now - datetime.fromisoformat(published)).total_seconds() / 3600
            if published else None)
    result["official_freshness_hours"] = freshness
    result["thresholds"] = {
        "official_latest_raw_core_coverage_min": 0.80,
        "official_freshness_hours_max": freshness_hours,
        "future_leaks_max": 0,
        "mirror_missing_must_be_explicit": True,
        "adapter_errors_max": 0,
    }
    result["passed"] = (
        not errors and not future_leaks and result["mirror_missing_explicit"]
        and all(row["latest_metric_coverage"] >= 0.80 for row in official_coverage)
        and all(value is not None and value <= freshness_hours
                for value in freshness.values()))
    result["rollout_decision"] = "shadow" if result["passed"] else "legacy"
    repo.close()
    report_path = root / "validation.json"
    result["report_json"] = str(report_path)
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.root:
        result = run(args.root)
    else:
        with tempfile.TemporaryDirectory(prefix="ats-financial-smoke-") as directory:
            result = run(Path(directory))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    # A failed release gate is a valid diagnostic result: the script completed and
    # records rollout_decision=legacy. Transport/parser exceptions still fail the run.
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
