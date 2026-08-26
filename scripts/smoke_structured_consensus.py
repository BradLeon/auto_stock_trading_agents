#!/usr/bin/env python3
"""Isolated real-source validation for persistent yfinance consensus snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from ats.data import consensus as legacy_consensus
from ats.data.sources.market_consensus import YFinanceConsensusAdapter
from ats.data_platform import DataProducts
from ats.structured import (
    FetchRequest,
    IngestionPipeline,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


ENTITIES = ["AMZN", "MSFT", "KLAC", "TSM"]
SCALARS = (
    "eps", "revenue", "eps_low", "eps_high", "revenue_low", "revenue_high",
    "target_mean", "target_median", "target_low", "target_high",
    "rating_strong_buy", "rating_buy", "rating_hold", "rating_sell",
    "rating_strong_sell",
)


class _Store:
    def projection_lineage(self, _identifier):
        return None


def _compare(left: dict, right: dict) -> dict:
    compared = []
    mismatches = []
    missing = []
    for key in SCALARS:
        a, b = left.get(key), right.get(key)
        if a is None or b is None:
            missing.append({"field": key, "legacy": a, "structured": b})
            continue
        difference = abs(float(a) - float(b))
        tolerance = max(1e-6, abs(float(a)) * 1e-6)
        row = {"field": key, "legacy": a, "structured": b,
               "absolute_difference": difference, "tolerance": tolerance}
        compared.append(row)
        if difference > tolerance:
            mismatches.append(row)
    return {"compared": compared, "missing_one_side": missing,
            "mismatches": mismatches}


def run(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    repo = SQLiteStructuredRepository(
        root / "consensus-smoke.sqlite", artifact_root=root / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    pipeline = IngestionPipeline(repo)
    products = DataProducts(store=_Store(), structured_repository=repo)
    started = datetime.now(timezone.utc)
    runs = []
    errors = []
    legacy_values = {}
    first_msft_known = None
    for entity in ENTITIES:
        request = FetchRequest(
            source_id="yfinance_consensus", dataset_id="market_consensus",
            entities=[entity], query_scope={})
        try:
            outcome = pipeline.run(YFinanceConsensusAdapter(), request)
            runs.append({"entity": entity, **{key: value for key, value in outcome.items()
                                              if key != "results"}})
            legacy_values[entity] = legacy_consensus._legacy_fetch(entity)
            if entity == "MSFT":
                first_msft_known = products.consensus_snapshot(entity="MSFT")["known_at"]
        except Exception as exc:
            errors.append({"entity": entity, "error_type": type(exc).__name__,
                           "message": str(exc)})

    # A second real fetch creates a second defensible known_at even if values did not
    # change. No sleep is needed: the intervening entity fetches separate the clocks.
    try:
        second = pipeline.run(YFinanceConsensusAdapter(), FetchRequest(
            source_id="yfinance_consensus", dataset_id="market_consensus",
            entities=["MSFT"], query_scope={}))
        runs.append({"entity": "MSFT_SECOND_VISIBLE_TIME",
                     **{key: value for key, value in second.items() if key != "results"}})
    except Exception as exc:
        errors.append({"entity": "MSFT_SECOND_VISIBLE_TIME",
                       "error_type": type(exc).__name__, "message": str(exc)})

    comparisons = {}
    coverage = []
    for entity in ENTITIES:
        snapshot = products.consensus_snapshot(entity=entity)
        structured = products.consensus_legacy_dict(entity=entity)
        comparisons[entity] = _compare(legacy_values.get(entity, {}), structured)
        coverage.append({
            "entity": entity, "status": snapshot["status"],
            "known_at": snapshot.get("known_at"),
            "target_periods": snapshot.get("target_periods", []),
            "metrics": sorted({row["metric_id"] for row in snapshot.get("rows", [])}),
            "records": len(snapshot.get("rows", [])),
        })

    before = started - timedelta(microseconds=1)
    future_leaks = [entity for entity in ENTITIES
                    if products.consensus_snapshot(entity=entity, as_of=before)["status"] == "ok"]
    replay = {"first_known_at": first_msft_known, "second_known_at": None,
              "early_eps": None, "latest_eps": None, "passed": False}
    latest_msft = products.consensus_snapshot(entity="MSFT")
    replay["second_known_at"] = latest_msft.get("known_at")
    if first_msft_known and replay["second_known_at"] and \
            first_msft_known < replay["second_known_at"]:
        first_time = datetime.fromisoformat(first_msft_known)
        second_time = datetime.fromisoformat(replay["second_known_at"])
        midpoint = first_time + (second_time - first_time) / 2
        replay["early_eps"] = products.consensus_legacy_dict(
            entity="MSFT", as_of=midpoint).get("eps")
        replay["latest_eps"] = products.consensus_legacy_dict(entity="MSFT").get("eps")
        replay["passed"] = replay["early_eps"] is not None and replay["latest_eps"] is not None

    history = repo.ingestion_history(
        source_id="yfinance_consensus", dataset_id="market_consensus", limit=100)
    quality = products.consensus_quality()
    artifacts = [dict(row) for row in repo.conn.execute(
        "SELECT a.source_id,a.source_url,a.source_version,b.relative_path,b.bytes,"
        "b.content_hash FROM structured_artifacts a JOIN structured_artifact_blobs b "
        "ON b.blob_id=a.blob_id ORDER BY a.fetched_at").fetchall()]
    mismatch_count = sum(len(value["mismatches"]) for value in comparisons.values())
    report = {
        "database": str(root / "consensus-smoke.sqlite"),
        "artifact_root": str(root / "artifacts"),
        "runs": runs, "errors": errors, "coverage": coverage,
        "comparisons": comparisons, "comparison_mismatch_count": mismatch_count,
        "future_leaks": future_leaks, "two_visible_times": replay,
        "quality": quality, "ingestion_history": history, "artifacts": artifacts,
    }
    report["passed"] = (
        not errors and not future_leaks and replay["passed"] and mismatch_count == 0
        and all(row["status"] == "ok" and row["target_periods"] for row in coverage)
        and quality["status"] == "passed")
    report["rollout_decision"] = "shadow" if report["passed"] else "legacy"
    report_path = root / "validation.json"
    report["report_json"] = str(report_path)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    repo.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.root:
        result = run(args.root)
    else:
        with tempfile.TemporaryDirectory(prefix="ats-consensus-smoke-") as directory:
            result = run(Path(directory))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
