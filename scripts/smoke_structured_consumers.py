#!/usr/bin/env python3
"""Exercise compatibility consumers against copied real-source validation data."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3

from ats.data import fundamentals
from ats.data_platform import DataProducts
from ats.structured import (
    DerivationDefinition,
    ObservationInput,
    SeriesIdentity,
    SQLiteStructuredRepository,
)


def _copy_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as incoming, sqlite3.connect(destination) as outgoing:
        incoming.backup(outgoing)


def _repo(source: Path, destination: Path) -> SQLiteStructuredRepository:
    _copy_database(source, destination)
    repository = SQLiteStructuredRepository(
        destination, artifact_root=source.parent / "artifacts")
    repository.bootstrap_catalog()
    return repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--financial-db", type=Path, required=True)
    parser.add_argument("--consensus-db", type=Path, required=True)
    parser.add_argument("--regional-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    financial_repo = _repo(
        args.financial_db.resolve(), output_dir / "financial-consumer.sqlite")
    consensus_repo = _repo(
        args.consensus_db.resolve(), output_dir / "consensus-consumer.sqlite")
    regional_repo = _repo(
        args.regional_db.resolve(), output_dir / "regional-consumer.sqlite")

    financial_products = DataProducts(structured_repository=financial_repo)
    financial = {}
    all_financial_rows = []
    for symbol in ("AMZN", "MSFT", "KLAC", "TSM"):
        statement, rows = fundamentals._structured_statements(symbol, financial_products)
        if statement is None or not statement.lines:
            raise AssertionError(f"no platform statement DTO for {symbol}")
        manifest = financial_products.snapshot_manifest(
            consumer="pead_fundamentals", purpose=f"consumer-smoke:{symbol}",
            as_of=now, rows=rows, metadata={"runtime_inputs_included": False})
        replay = financial_products.replay_snapshot(manifest["snapshot_id"])
        if len(replay["rows"]) != len(rows):
            raise AssertionError(f"financial snapshot replay mismatch for {symbol}")
        financial[symbol] = {
            "period": statement.period,
            "lines": [line.label for line in statement.lines],
            "input_observations": len(rows),
            "snapshot_id": manifest["snapshot_id"],
        }
        all_financial_rows.extend(rows)

    consensus_products = DataProducts(structured_repository=consensus_repo)
    consensus = {}
    for symbol in ("AMZN", "MSFT", "KLAC", "TSM"):
        snapshot = consensus_products.consensus_snapshot(entity=symbol)
        legacy = consensus_products.consensus_legacy_dict(entity=symbol)
        if snapshot["status"] != "ok" or not any(
                legacy.get(key) is not None for key in ("eps", "revenue", "target_mean")):
            raise AssertionError(f"consensus compatibility output missing for {symbol}")
        manifest = consensus_products.snapshot_manifest(
            consumer="pead_consensus", purpose=f"consumer-smoke:{symbol}",
            as_of=datetime.fromisoformat(snapshot["known_at"]), rows=snapshot["rows"],
            metadata={"runtime_inputs_included": False})
        consensus[symbol] = {
            "known_at": snapshot["known_at"], "records": len(snapshot["rows"]),
            "target_periods": snapshot["target_periods"],
            "snapshot_id": manifest["snapshot_id"],
            "legacy_non_null_scalars": sum(
                legacy.get(key) is not None for key in legacy
                if key not in {"rating_trend", "upgrades_downgrades"}),
        }

    regional_products = DataProducts(structured_repository=regional_repo)
    regional = {}
    for dataset, source, entity, metric in (
        ("regional_tw_exports", "tw_mof_exports", "TW_IC_EXPORT",
         "regional.tw_ic_exports.value"),
        ("regional_kr_exports", "kr_ecos_exports", "KR_SEMI_EXPORT",
         "regional.kr_semiconductor_exports.index"),
    ):
        levels = regional_products.metric_series(
            metric=metric, entity=entity, dataset=dataset, source_id=source,
            quality="loose")
        yoy = regional_products.derive(operation="yoy", query_result=levels)
        if not levels["rows"] or not any(
                row["derivation_status"] == "ok" for row in yoy["rows"]):
            raise AssertionError(f"regional compatibility output missing for {dataset}")
        manifest = regional_products.snapshot_manifest(
            consumer="chain_regional", purpose=f"consumer-smoke:{dataset}",
            as_of=now, rows=yoy["rows"],
            metadata={"runtime_inputs_included": False})
        regional[dataset] = {
            "levels": len(levels["rows"]), "derived": len(yoy["rows"]),
            "snapshot_id": manifest["snapshot_id"],
        }

    # Later observation, source-priority and formula changes must not mutate a manifest.
    reference_manifest = financial["MSFT"]["snapshot_id"]
    before = financial_products.replay_snapshot(reference_manifest)
    original = all_financial_rows[0]
    later = now + timedelta(seconds=1)
    financial_repo.save_observation(ObservationInput(
        series=SeriesIdentity(
            source_id=original["source_id"], dataset_id=original["dataset_id"],
            entity_id=original["entity_id"], metric_id=original["metric_id"],
            unit=original["unit"], currency=original["currency"],
            period_basis=original["period_basis"], adjustment=original["adjustment"],
            dimensions=json.loads(original["dimensions_json"] or "{}")),
        period=original["period"], period_start=original["period_start"],
        period_end=original["period_end"], value=float(original["value"]) + 1,
        published_at=later, known_at=later, fetched_at=later,
        artifact_id=original["artifact_id"]))
    financial_repo.conn.execute(
        "UPDATE structured_datasets SET primary_sources_json=? WHERE dataset_id=?",
        ('["defeatbeta_stock_statement"]', "company_financials"))
    financial_repo.conn.commit()
    regional_repo.register_derivation(DerivationDefinition(
        id="yoy:regional.tw_ic_exports.value", version="v99", operation="yoy",
        inputs=["regional.tw_ic_exports.value"],
        output_metric_id="regional.tw_ic_exports.value"))
    after = financial_products.replay_snapshot(reference_manifest)
    replay_stable = ([row["observation_id"] for row in before["rows"]]
                     == [row["observation_id"] for row in after["rows"]])
    if not replay_stable:
        raise AssertionError("snapshot changed after later data/source/formula updates")

    combined = financial_products.compose_inputs(
        persistent=all_financial_rows[:3],
        runtime=[{"kind": "ticker_price", "symbol": "MSFT", "value": 500.0},
                 {"kind": "option_chain", "symbol": "MSFT", "atm_iv": 30.0}])
    boundary_manifest = financial_products.snapshot_manifest(
        consumer="pead", purpose="persistent-runtime-boundary", as_of=now,
        rows=combined["persistent"] + combined["runtime"])
    boundary_replay = financial_products.replay_snapshot(boundary_manifest["snapshot_id"])
    runtime_excluded = len(boundary_replay["items"]) == len(combined["persistent"])
    if not runtime_excluded:
        raise AssertionError("runtime market input leaked into structured snapshot")

    output = {
        "generated_at": now.isoformat(), "isolated": True,
        "financial": financial, "consensus": consensus, "regional": regional,
        "snapshot_replay_stable": replay_stable,
        "runtime_market_inputs_excluded": runtime_excluded,
        "passed": True,
    }
    path = output_dir / "validation.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(json.dumps({"report": str(path), "passed": True}, ensure_ascii=False))
    financial_repo.close()
    consensus_repo.close()
    regional_repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
