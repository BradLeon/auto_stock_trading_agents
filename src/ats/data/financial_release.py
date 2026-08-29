"""Data-only release checks for governed company financial report packages."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from .fundamentals import _FINANCIAL_SOURCE_PRIORITY, _complete_report_package


def _derived_rows_recompute(rows: list[dict]) -> list[dict]:
    checks = []
    for row in rows:
        try:
            raw = json.loads(row.get("raw_payload") or "{}")
        except json.JSONDecodeError:
            raw = {}
        calculation = raw.get("calculation")
        left, right = raw.get("left") or {}, raw.get("right") or {}
        if calculation and "value" in left and "value" in right:
            # Derived SEC rows record a human-readable expression (for example
            # ``revenue - cost_of_revenue``); do not infer the operation from a
            # metric name or a unit-scaled display value.
            expected = (float(left["value"]) - float(right["value"])
                        if "-" in calculation or "minus" in calculation else
                        float(left["value"]) + float(right["value"]))
            checks.append({
                "metric_id": row["metric_id"], "period": row["period"],
                "calculation": calculation, "passed": abs(float(row["value"]) - expected)
                <= max(1e-9, abs(expected) * 1e-12),
            })
    return checks


def company_financial_release_check(repository, *, entities: list[str] | None = None,
                                    now: datetime | None = None) -> dict:
    """Check persisted report packages without executing an Agent or Workflow.

    The check verifies the latest selected report package for each acceptance
    entity, its raw-artifact lineage, reporting freshness, balance-sheet identity
    and any stored official-XBRL derivation.  It deliberately does not inspect
    consumer modes or consumer shadow records.
    """
    from ..structured import StructuredCatalog
    from ..structured.quality import financial_quality

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    dataset = repository.dataset("company_financials") or {}
    catalog = StructuredCatalog.load()
    configured = next(
        (item for item in catalog.datasets() if item.id == "company_financials"),
        None,
    )
    if configured is None:
        raise ValueError("company_financials is not registered in the structured catalog")
    target_entities = [item.upper() for item in (entities or configured.acceptance_samples)]
    freshness_limit = float((configured.quality or {}).get("freshness_hours_max", 0) or 0)
    packages = []
    for entity in target_entities:
        package = None
        for source_id in _FINANCIAL_SOURCE_PRIORITY:
            package = _complete_report_package(repository, source_id=source_id, symbol=entity)
            if package is not None:
                break
        if package is None:
            package = _complete_report_package(
                repository,
                source_id=("sec_companyfacts", "company_disclosures"), symbol=entity)
        if package is None:
            packages.append({"entity": entity, "ready": False,
                             "reason": "no_current_complete_report_package"})
            continue
        rows = package["rows"]
        latest_known = max(str(row.get("known_at") or "") for row in rows)
        known = datetime.fromisoformat(latest_known)
        age_hours = max(0.0, (now - known).total_seconds() / 3600)
        financial = financial_quality(rows)
        derived = _derived_rows_recompute(rows)
        checks = [
            {"check": "complete_report_package", "passed": True,
             "detail": package["source_id"]},
            {"check": "artifact_lineage", "passed": all(bool(row.get("artifact_id")) for row in rows),
             "detail": f"{len(rows)}/{len(rows)} selected rows"},
            {"check": "freshness", "passed": not freshness_limit or age_hours <= freshness_limit,
             "detail": {"age_hours": age_hours, "maximum_hours": freshness_limit}},
            {"check": "balance_and_period_quality", "passed": financial["status"] == "passed",
             "detail": financial["issues"]},
            {"check": "raw_to_derived_recomputation", "passed": all(row["passed"] for row in derived),
             "detail": derived},
        ]
        packages.append({
            "entity": entity, "ready": all(check["passed"] for check in checks),
            "source_id": package["source_id"], "source_ids": list(package["source_ids"]),
            "period": package["period"], "currency": package["currency"],
            "source_by_metric": package["source_by_metric"], "latest_known_at": latest_known,
            "checks": checks,
        })
    return {
        "dataset_id": dataset.get("dataset_id", "company_financials"),
        "generated_at": now.isoformat(), "entities": packages,
        "ready": bool(packages) and all(row["ready"] for row in packages),
        "scope": "data_only_no_consumer_or_workflow_gate",
    }


__all__ = ["company_financial_release_check"]
