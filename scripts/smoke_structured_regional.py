#!/usr/bin/env python3
"""Isolated real-source smoke test for Taiwan MoF and Korea ECOS only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from ats.data.sources import kr_ecos, tw_mof
from ats.data_platform import DataProducts
from ats.structured import (
    FetchRequest,
    IngestionPipeline,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


class _NoLegacyStore:
    def projection_lineage(self, _identifier):
        return None


class _FetchedBatch:
    def __init__(self, batch):
        self.batch = batch

    def fetch(self, _request):
        return self.batch


def _continuous(periods: list[str]) -> bool:
    values = []
    for period in periods:
        year, month = (int(value) for value in period.split("-"))
        values.append(year * 12 + month - 1)
    return all(right - left == 1 for left, right in zip(values, values[1:]))


def _source(repo, products, *, name, adapter, request, legacy_builder):
    batch = adapter.fetch(request)
    ingestion = IngestionPipeline(repo).run(_FetchedBatch(batch), request)
    metric = (tw_mof.METRIC_ID if name == "tw_mof_exports" else kr_ecos.METRIC_ID)
    query = products.metric_series(
        metric=metric, entity=request.entities[0], dataset=request.dataset_id,
        source_id=request.source_id, quality="loose", include_vintages=False)
    yoy = products.derive(operation="yoy", query_result=query)
    mom = products.derive(operation="mom", query_result=query)
    legacy = legacy_builder(batch.records, min(6, len(batch.records)))
    by_period_yoy = {row["period"]: row["value"] for row in yoy["rows"]}
    by_period_mom = {row["period"]: row["value"] for row in mom["rows"]}
    differences = []
    for point in legacy:
        if point.yoy != by_period_yoy.get(point.period):
            differences.append({"period": point.period, "field": "yoy"})
        if point.mom != by_period_mom.get(point.period):
            differences.append({"period": point.period, "field": "mom"})
    periods = [row["period"] for row in query["rows"]]
    known = max((datetime.fromisoformat(row["known_at"]) for row in query["rows"]),
                default=None)
    age_hours = ((datetime.now(timezone.utc) - known).total_seconds() / 3600
                 if known else None)
    return {
        "source_id": name,
        "status": ingestion["status"],
        "records": len(batch.records),
        "accepted": ingestion.get("accepted", 0),
        "quarantined": ingestion.get("quarantined", 0),
        "coverage": batch.provider_metadata.get("coverage", {}),
        "continuous": _continuous(periods),
        "latest_period": periods[-1] if periods else "",
        "known_at": known.isoformat() if known else None,
        "snapshot_age_hours": age_hours,
        "publication_time_status": batch.provider_metadata.get(
            "coverage", {}).get("publication_time_status"),
        "yoy_available": sum(row["value"] is not None for row in yoy["rows"]),
        "mom_available": sum(row["value"] is not None for row in mom["rows"]),
        "legacy_derivation_differences": differences,
        "artifact_count": repo.conn.execute(
            "SELECT count(*) FROM structured_artifacts WHERE source_id=?", (name,)
        ).fetchone()[0],
    }


def run(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    repo = SQLiteStructuredRepository(
        root / "regional-smoke.sqlite", artifact_root=root / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    products = DataProducts(store=_NoLegacyStore(), structured_repository=repo)
    lookback = 20
    checks = []
    errors = []
    jobs = [
        ("tw_mof_exports", tw_mof.TaiwanMOFAdapter(), FetchRequest(
            source_id="tw_mof_exports", dataset_id="regional_tw_exports",
            entities=["TW_IC_EXPORT"], query_scope={"lookback_months": lookback}),
         tw_mof._legacy_points),
        ("kr_ecos_exports", kr_ecos.KoreaECOSAdapter(), FetchRequest(
            source_id="kr_ecos_exports", dataset_id="regional_kr_exports",
            entities=["KR_SEMI_EXPORT"], query_scope={
                "lookback_months": lookback, "stat": "403Y001", "item": "3091AA"}),
         kr_ecos._legacy_points),
    ]
    for name, adapter, request, legacy_builder in jobs:
        try:
            checks.append(_source(
                repo, products, name=name, adapter=adapter, request=request,
                legacy_builder=legacy_builder))
        except Exception as exc:
            errors.append({"source_id": name, "error_type": type(exc).__name__,
                           "message": str(exc)})
    result = {
        "database": str(root / "regional-smoke.sqlite"),
        "artifact_root": str(root / "artifacts"),
        "sources": checks,
        "errors": errors,
    }
    result["passed"] = not errors and all(
        row["status"] in {"succeeded", "no_change"}
        and row["records"] >= 13 and row["continuous"]
        and row["yoy_available"] >= 1 and row["mom_available"] >= 1
        and not row["legacy_derivation_differences"]
        for row in checks)
    repo.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.root:
        result = run(args.root)
    else:
        with tempfile.TemporaryDirectory(prefix="ats-regional-smoke-") as directory:
            result = run(Path(directory))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
