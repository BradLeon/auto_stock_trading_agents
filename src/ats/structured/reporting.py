"""Operational quality reports for the governed structured data layer."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from typing import Any


_FAILURE_STATES = {
    "stale", "unreachable", "unauthorized", "parse_failed", "validation_failed",
}


def _json(value: str, default):
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return default


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _age_hours(value: str, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600)


def _dimension(status: str, **details) -> dict[str, Any]:
    return {"status": status, **details}


def build_quality_report(repository, *, dataset_id: str | None = None,
                         now: datetime | None = None) -> dict:
    """Build five-dimensional, threshold-aware structured quality output."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    datasets = repository.datasets()
    if dataset_id:
        datasets = [row for row in datasets if row["dataset_id"] == dataset_id]
        if not datasets:
            raise KeyError(f"unknown structured dataset: {dataset_id}")

    health_by_source = {row["source_id"]: row for row in repository.source_health()}
    reports = []
    for dataset in datasets:
        current_id = dataset["dataset_id"]
        quality = _json(dataset["quality_json"], {})
        core_metrics = _json(dataset["core_metrics_json"], [])
        configured_entities = _json(dataset["entities_json"], [])
        samples = _json(dataset["acceptance_samples_json"], [])
        primary = _json(dataset["primary_sources_json"], [])
        fallback = _json(dataset["fallback_sources_json"], [])
        source_ids = list(dict.fromkeys([*primary, *fallback]))

        rows = repository.observations(
            dataset_id=current_id, latest_only=True, accepted_only=True,
            limit=1_000_000)
        entities = sorted({row["entity_id"] for row in rows})
        metrics = sorted({row["metric_id"] for row in rows})
        expected_entities = configured_entities or samples
        covered_expected = sorted(set(expected_entities) & set(entities))
        present_core = sorted(set(core_metrics) & set(metrics))
        core_ratio = _ratio(len(present_core), len(core_metrics))
        entity_ratio = _ratio(len(covered_expected), len(expected_entities))
        configured_min = quality.get(
            "core_metric_coverage_ratio_min", quality.get("coverage_ratio_min"))
        if not rows:
            coverage_status = "no_coverage"
        elif configured_min is not None and core_ratio is not None \
                and core_ratio < float(configured_min):
            coverage_status = "failed"
        else:
            coverage_status = "passed"
        coverage = _dimension(
            coverage_status, observations=len(rows), entities=entities,
            metrics=metrics, expected_entities=expected_entities,
            covered_expected_entities=covered_expected,
            entity_coverage_ratio=entity_ratio, core_metrics=core_metrics,
            core_metrics_present=present_core, core_metric_coverage_ratio=core_ratio,
            configured_minimum=configured_min,
        )

        conflicts = repository.conflicts(
            dataset_id=current_id, status="open", limit=1_000_000)
        ordinary_candidates = repository.candidates(
            dataset_id=current_id, limit=1_000_000)
        evidence_candidates = (
            repository.evidence_candidates(limit=1_000_000)
            if current_id == "private_company_events" else [])
        candidate_statuses = Counter(
            row["status"] for row in [*ordinary_candidates, *evidence_candidates])
        quarantined = candidate_statuses["quarantined"] + candidate_statuses["needs_evidence"]
        accuracy_status = "failed" if conflicts else (
            "warning" if quarantined else ("passed" if rows else "not_evaluated"))
        accuracy = _dimension(
            accuracy_status, open_conflicts=len(conflicts),
            quarantined_candidates=quarantined,
            candidate_statuses=dict(sorted(candidate_statuses.items())),
            conflicts=conflicts,
        )

        latest_known_at = max((row.get("known_at", "") for row in rows), default="")
        age = _age_hours(latest_known_at, now)
        maximum_age = quality.get("freshness_hours_max")
        if age is None:
            freshness_status = "no_data"
        elif maximum_age is None:
            freshness_status = "not_configured"
        elif age > float(maximum_age):
            freshness_status = "stale"
        else:
            freshness_status = "passed"
        freshness = _dimension(
            freshness_status, latest_known_at=latest_known_at or None,
            age_hours=age, maximum_hours=maximum_age)

        pending = repository.pending_mappings(status="pending", limit=1_000_000)
        pending = [row for row in pending if row["dataset_id"] == current_id]
        with_artifact = sum(bool(row.get("artifact_id")) for row in rows)
        lineage_ratio = _ratio(with_artifact, len(rows))
        accepted_candidates = candidate_statuses["accepted"]
        completeness_status = (
            "no_data" if not rows and not ordinary_candidates and not evidence_candidates
            else "warning" if pending or quarantined or (rows and with_artifact < len(rows))
            else "passed")
        completeness = _dimension(
            completeness_status, accepted_candidates=accepted_candidates,
            candidates=len(ordinary_candidates) + len(evidence_candidates),
            pending_mappings=len(pending), pending_mapping_rows=pending,
            observations_with_artifact=with_artifact,
            artifact_lineage_ratio=lineage_ratio,
        )

        source_health = []
        availability_counts: Counter[str] = Counter()
        for source_id in source_ids:
            row = dict(health_by_source.get(source_id) or {"source_id": source_id})
            state = row.get("last_status") or "no_run"
            row["effective_status"] = state
            source_health.append(row)
            availability_counts[state] += 1
        if any(state in _FAILURE_STATES for state in availability_counts):
            availability_status = "failed"
        elif not source_ids:
            availability_status = "not_applicable"
        elif availability_counts["no_run"] == len(source_ids):
            availability_status = "no_run"
        elif availability_counts["partial"]:
            availability_status = "warning"
        else:
            availability_status = "passed"
        availability = _dimension(
            availability_status, status_counts=dict(sorted(availability_counts.items())),
            sources=source_health)

        dimensions = {
            "coverage": coverage,
            "accuracy_reconciliation": accuracy,
            "freshness": freshness,
            "completeness": completeness,
            "availability": availability,
        }
        statuses = {item["status"] for item in dimensions.values()}
        if statuses & {"failed", "stale"}:
            overall = "failed"
        elif statuses & {"warning", "no_coverage", "no_data", "no_run"}:
            overall = "warning"
        else:
            overall = "passed"
        reports.append({
            "dataset_id": current_id,
            "catalog_status": dataset["catalog_status"],
            "overall_status": overall,
            "thresholds": quality,
            "dimensions": dimensions,
        })

    sources = repository.sources()
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "dataset_filter": dataset_id,
        "datasets": reports,
        "catalog": {
            "persistent": sum(row["persistence"] == "persistent" for row in sources),
            "runtime_excluded": [row["source_id"] for row in sources
                                 if row["catalog_status"] == "runtime_excluded"],
        },
        "artifacts": repository.artifact_usage(),
    }


def render_quality_markdown(report: dict) -> str:
    """Render the machine report without changing its quality semantics."""
    lines = [
        "# Structured Data Quality Report",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "| Dataset | Overall | Coverage | Accuracy | Freshness | Completeness | Availability |",
        "|---|---|---|---|---|---|---|",
    ]
    for dataset in report["datasets"]:
        dims = dataset["dimensions"]
        lines.append(
            f"| `{dataset['dataset_id']}` | `{dataset['overall_status']}` | "
            f"`{dims['coverage']['status']}` | "
            f"`{dims['accuracy_reconciliation']['status']}` | "
            f"`{dims['freshness']['status']}` | "
            f"`{dims['completeness']['status']}` | "
            f"`{dims['availability']['status']}` |")
    usage = report["artifacts"]
    lines.extend([
        "", "## Artifact storage", "",
        f"- Logical artifacts: {usage['artifacts']}",
        f"- Unique blobs: {usage['unique_blobs']}",
        f"- Physical bytes: {usage['physical_bytes']}",
        f"- Deduplication rate: {usage['deduplication_rate']:.2%}",
    ])
    for dataset in report["datasets"]:
        lines.extend(["", f"## {dataset['dataset_id']}", ""])
        for name, dimension in dataset["dimensions"].items():
            lines.append(f"- {name}: `{dimension['status']}`")
    return "\n".join(lines) + "\n"
