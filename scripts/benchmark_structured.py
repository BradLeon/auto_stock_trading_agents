#!/usr/bin/env python3
"""Reproducible SQLite baseline for the low-frequency structured data workload."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
import tempfile
from time import perf_counter

from ats.structured import (
    ArtifactDescriptor,
    ObservationInput,
    SeriesIdentity,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


THRESHOLDS = {
    "load_seconds_max": 30.0,
    "point_query_p95_ms_max": 100.0,
    "concurrent_write_p95_ms_max": 500.0,
    "concurrent_write_errors_max": 0,
    "concurrent_read_errors_max": 0,
    "database_bytes_max": 64 * 1024 * 1024,
}


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * quantile))]


def _period(index: int) -> str:
    year, month_index = divmod(index, 12)
    return f"{2018 + year:04d}-{month_index + 1:02d}"


def _observation(*, entity: str, period: str, value: float,
                 artifact_id: str, known_at: datetime) -> ObservationInput:
    return ObservationInput(
        series=SeriesIdentity(
            source_id="tw_mof_exports", dataset_id="regional_tw_exports",
            entity_id=entity, metric_id="regional.tw_ic_exports.value",
            unit="USD M", currency="USD", period_basis="month"),
        period=period, period_start=f"{period}-01", period_end=f"{period}-28",
        value=value, published_at=known_at, known_at=known_at, fetched_at=known_at,
        artifact_id=artifact_id, raw={"synthetic": True, "value": value})


def run_benchmark(root: Path, *, records: int = 12_000,
                  point_queries: int = 200) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "structured-benchmark.sqlite"
    artifact_root = root / "artifacts"
    repo = SQLiteStructuredRepository(db_path, artifact_root=artifact_root)
    repo.bootstrap_catalog(StructuredCatalog.load())
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    artifact = repo.put_artifact(
        {"kind": "synthetic-benchmark", "records": records},
        ArtifactDescriptor(
            source_id="tw_mof_exports", dataset_id="regional_tw_exports",
            fetched_at=now, query_scope={"synthetic_records": records},
            source_version="benchmark-v1", media_type="application/json"))

    periods_per_entity = 120
    load_started = perf_counter()
    for index in range(records):
        entity_number, period_number = divmod(index, periods_per_entity)
        repo.save_observation(_observation(
            entity=f"SYN{entity_number:05d}", period=_period(period_number),
            value=float(index + 1), artifact_id=artifact.id, known_at=now))
    load_seconds = perf_counter() - load_started

    query_ms = []
    entity_count = max(1, (records + periods_per_entity - 1) // periods_per_entity)
    for index in range(point_queries):
        started = perf_counter()
        repo.observations(
            dataset_id="regional_tw_exports",
            metric_id="regional.tw_ic_exports.value",
            entity_id=f"SYN{index % entity_count:05d}", latest_only=True)
        query_ms.append((perf_counter() - started) * 1000)

    read_errors = []

    def reader(index: int):
        local = SQLiteStructuredRepository(db_path, artifact_root=artifact_root)
        try:
            return len(local.observations(
                entity_id=f"SYN{index % entity_count:05d}", latest_only=True))
        except Exception as exc:  # reported, never hidden
            read_errors.append(type(exc).__name__)
            return 0
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(reader, range(80)))

    write_ms: list[float] = []
    write_errors: list[str] = []

    def writer(worker: int):
        local = SQLiteStructuredRepository(db_path, artifact_root=artifact_root)
        try:
            for offset in range(30):
                started = perf_counter()
                try:
                    local.save_observation(_observation(
                        entity=f"LOCK{worker:02d}", period=_period(offset),
                        value=float(worker * 100 + offset), artifact_id=artifact.id,
                        known_at=now))
                except Exception as exc:  # lock errors are part of the baseline
                    write_errors.append(type(exc).__name__)
                write_ms.append((perf_counter() - started) * 1000)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer, range(4)))

    repo.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    repo.close()
    database_bytes = db_path.stat().st_size
    artifact_bytes = sum(path.stat().st_size for path in artifact_root.rglob("*")
                         if path.is_file())
    metrics = {
        "requested_records": records,
        "stored_observations": records + 120,
        "load_seconds": round(load_seconds, 6),
        "load_rows_per_second": round(records / load_seconds, 2),
        "point_query_mean_ms": round(mean(query_ms), 6),
        "point_query_p95_ms": round(_percentile(query_ms, 0.95), 6),
        "concurrent_write_mean_ms": round(mean(write_ms), 6),
        "concurrent_write_p95_ms": round(_percentile(write_ms, 0.95), 6),
        "concurrent_write_errors": len(write_errors),
        "concurrent_read_errors": len(read_errors),
        "database_bytes": database_bytes,
        "artifact_bytes": artifact_bytes,
        "journal_mode": "wal",
        "writer_threads": 4,
        "reader_threads": 8,
    }
    checks = {
        "load_seconds": metrics["load_seconds"] <= THRESHOLDS["load_seconds_max"],
        "point_query_p95_ms": (
            metrics["point_query_p95_ms"] <= THRESHOLDS["point_query_p95_ms_max"]),
        "concurrent_write_p95_ms": (
            metrics["concurrent_write_p95_ms"] <=
            THRESHOLDS["concurrent_write_p95_ms_max"]),
        "concurrent_write_errors": (
            metrics["concurrent_write_errors"] <=
            THRESHOLDS["concurrent_write_errors_max"]),
        "concurrent_read_errors": (
            metrics["concurrent_read_errors"] <=
            THRESHOLDS["concurrent_read_errors_max"]),
        "database_bytes": metrics["database_bytes"] <= THRESHOLDS["database_bytes_max"],
    }
    return {"thresholds": THRESHOLDS, "metrics": metrics,
            "checks": checks, "passed": all(checks.values())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--records", type=int, default=12_000)
    parser.add_argument("--point-queries", type=int, default=200)
    args = parser.parse_args()
    if args.root:
        result = run_benchmark(
            args.root, records=args.records, point_queries=args.point_queries)
    else:
        with tempfile.TemporaryDirectory(prefix="ats-structured-benchmark-") as directory:
            result = run_benchmark(
                Path(directory), records=args.records, point_queries=args.point_queries)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
