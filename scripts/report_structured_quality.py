#!/usr/bin/env python3
"""Aggregate isolated structured validation databases into one quality report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from ats.structured import SQLiteStructuredRepository, build_quality_report


def _inputs(values: list[str]) -> dict[str, Path]:
    parsed = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--database must be DATASET=/absolute/or/relative/path.sqlite")
        dataset, path = value.split("=", 1)
        parsed[dataset] = Path(path).resolve()
    return parsed


def _markdown(report: dict) -> str:
    lines = [
        "# 结构化数据层五维质量验收（2026-08-25）",
        "",
        "> 范围：阶段二各专项隔离数据库的统一只读汇总；未写入生产数据库。  ",
        "> 口径：状态来自机器目录和实际 ingestion/observation/candidate 表，不用缺失值补 0。",
        "",
        "## 结论",
        "",
        "本报告用于确认统一质量入口能够跨专项展示 Coverage、Accuracy / Reconciliation、Freshness、Completeness 和 Availability。`warning` 或 `failed` 是实际切换门的输入，不代表通过格式检查后自动上线。",
        "",
        "| Dataset | Overall | Coverage | Accuracy | Freshness | Completeness | Availability | Accepted observations |",
        "|---|---|---|---|---|---|---|---:|",
    ]
    for row in report["datasets"]:
        dims = row["dimensions"]
        lines.append(
            f"| `{row['dataset_id']}` | `{row['overall_status']}` | "
            f"`{dims['coverage']['status']}` | "
            f"`{dims['accuracy_reconciliation']['status']}` | "
            f"`{dims['freshness']['status']}` | "
            f"`{dims['completeness']['status']}` | "
            f"`{dims['availability']['status']}` | "
            f"{dims['coverage']['observations']} |")

    lines.extend([
        "", "## 数量对账", "",
        "| Dataset | Query observations | Run discovered | Run accepted | Run quarantined | Candidate statuses | Reconciled |",
        "|---|---:|---:|---:|---:|---|---|",
    ])
    for row in report["reconciliation"]:
        statuses = ", ".join(
            f"{key}={value}" for key, value in row["candidate_statuses"].items()) or "—"
        lines.append(
            f"| `{row['dataset_id']}` | {row['query_observations']} | "
            f"{row['run_discovered']} | {row['run_accepted']} | "
            f"{row['run_quarantined']} | {statuses} | "
            f"`{str(row['reconciled']).lower()}` |")

    usage = report["artifacts"]
    lines.extend([
        "", "## Artifact 用量", "",
        f"- 跨隔离库 logical artifacts：{usage['artifacts']}",
        f"- 各库 unique blobs 合计：{usage['unique_blobs']}",
        f"- physical bytes 合计：{usage['physical_bytes']}",
        f"- 库内去重引用合计：{usage['deduplicated_references']}",
        "- 各专项使用独立 artifact 根目录，因此不把跨库相同哈希虚报为全局去重。",
        "", "## 来源矩阵", "",
        "| Source | Catalog status | Persistence | Latest validation status |",
        "|---|---|---|---|",
    ])
    for row in report["sources"]:
        lines.append(
            f"| `{row['source_id']}` | `{row['catalog_status']}` | "
            f"`{row['persistence']}` | `{row['latest_status']}` |")
    lines.extend([
        "", "## 解释边界", "",
        "- `company_disclosures` 仍为 planned；不能因 SEC Company Facts 已实现而写成已接入。",
        "- `industry_dram_contract_price` 在本轮聚合库中没有独立真实专项，报告保留 `no_coverage/no_run`，不伪造通过。",
        "- 证据型数据由候选核验后去重发布，一个 observation 可由多条 accepted evidence 支撑，因此 accepted candidates 不要求等于 observations。",
        "- ticker 行情与期权四个来源只显示 `runtime_excluded`，不应出现 ingestion、artifact 或 dataset。",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", action="append", required=True,
                        help="repeat DATASET=PATH; artifact root is PATH.parent/artifacts")
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args(argv)

    inputs = _inputs(args.database)
    now = datetime.now(timezone.utc)
    dataset_reports = []
    reconciliation = []
    source_latest: dict[str, dict] = {}
    usage = {key: 0 for key in (
        "artifacts", "unique_blobs", "deduplicated_references", "physical_bytes")}
    catalog_sources: dict[str, dict] = {}
    counted_databases: set[Path] = set()

    for dataset_id, path in inputs.items():
        repo = SQLiteStructuredRepository(path, artifact_root=path.parent / "artifacts")
        repo.bootstrap_catalog()
        quality = build_quality_report(repo, dataset_id=dataset_id, now=now)
        dataset_report = quality["datasets"][0]
        dataset_reports.append(dataset_report)
        if path not in counted_databases:
            counted_databases.add(path)
            for key in usage:
                usage[key] += int(quality["artifacts"][key])
            for source in repo.sources():
                catalog_sources[source["source_id"]] = source
            for health in repo.source_health():
                if health.get("last_started_at") and (
                        health["source_id"] not in source_latest or
                        health["last_started_at"] >
                        source_latest[health["source_id"]].get("last_started_at", "")):
                    source_latest[health["source_id"]] = health

        runs = repo.ingestion_history(dataset_id=dataset_id, limit=1_000_000)
        candidates = repo.candidates(dataset_id=dataset_id, limit=1_000_000)
        evidence = repo.evidence_candidates(limit=1_000_000) \
            if dataset_id == "private_company_events" else []
        statuses: dict[str, int] = {}
        for candidate in [*candidates, *evidence]:
            statuses[candidate["status"]] = statuses.get(candidate["status"], 0) + 1
        observations = dataset_report["dimensions"]["coverage"]["observations"]
        # Ingestion accepted is a write-attempt count. It may exceed query rows after
        # idempotent writes; evidence has a separate review pipeline and no run rows.
        accepted = sum(int(row["accepted"]) for row in runs)
        reconciled = (accepted >= observations) if runs else (
            dataset_id == "private_company_events" or observations == 0)
        reconciliation.append({
            "dataset_id": dataset_id,
            "query_observations": observations,
            "run_discovered": sum(int(row["discovered"]) for row in runs),
            "run_accepted": accepted,
            "run_quarantined": sum(int(row["quarantined"]) for row in runs),
            "candidate_statuses": dict(sorted(statuses.items())),
            "reconciled": reconciled,
        })
        repo.close()

    sources = []
    for source_id, source in sorted(catalog_sources.items()):
        latest = source_latest.get(source_id) or {}
        sources.append({
            "source_id": source_id,
            "catalog_status": source["catalog_status"],
            "persistence": source["persistence"],
            "latest_status": latest.get("last_status") or "no_run",
        })
    output = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "isolated": True,
        "inputs": {key: str(value) for key, value in inputs.items()},
        "datasets": sorted(dataset_reports, key=lambda row: row["dataset_id"]),
        "reconciliation": sorted(reconciliation, key=lambda row: row["dataset_id"]),
        "artifacts": usage,
        "sources": sources,
        "all_reconciled": all(row["reconciled"] for row in reconciliation),
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8")
    args.markdown_output.write_text(_markdown(output), encoding="utf-8")
    print(json.dumps({
        "json": str(args.json_output), "markdown": str(args.markdown_output),
        "datasets": len(dataset_reports), "all_reconciled": output["all_reconciled"],
    }, ensure_ascii=False))
    return 0 if output["all_reconciled"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
