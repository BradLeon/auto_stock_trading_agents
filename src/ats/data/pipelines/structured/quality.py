"""Deterministic quality checks for structured research datasets."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def financial_quality(rows: list[dict], *, identity_relative_tolerance: float = 0.01,
                      source_relative_tolerance: float = 0.001) -> dict:
    issues = []
    series: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        series[(row.get("source_id"), row.get("entity_id"), row.get("metric_id"),
                row.get("unit"), row.get("currency"), row.get("period_basis"))].append(row)

    annual_ends: dict[tuple, set[date]] = defaultdict(set)
    for row in rows:
        if row.get("period_basis") != "annual":
            continue
        parsed = _date(row.get("period_end") or row.get("period", ""))
        if parsed:
            annual_ends[(row.get("source_id"), row.get("entity_id"),
                         row.get("metric_id"), row.get("unit"),
                         row.get("currency"))].add(parsed)

    continuity_checked = 0
    for key, group in series.items():
        basis = key[-1]
        if basis not in {"quarter", "annual"}:
            continue
        dates = sorted(filter(None, (_date(row.get("period_end") or row.get("period", ""))
                                     for row in group)))
        lower, upper = ((60, 140) if basis == "quarter" else (300, 430))
        for left, right in zip(dates, dates[1:]):
            continuity_checked += 1
            gap = (right - left).days
            annual_key = key[:-1]
            annual_between = any(
                left < annual <= right for annual in annual_ends.get(annual_key, set()))
            if basis == "quarter" and annual_between and 150 <= gap <= 210:
                continue  # SEC does not publish a separate Q4 duration fact.
            if not lower <= gap <= upper:
                issues.append({
                    "code": "period_gap", "source_id": key[0], "entity_id": key[1],
                    "metric_id": key[2], "left": left.isoformat(),
                    "right": right.isoformat(), "days": gap})

    # An abrupt 1,000x scale change inside an otherwise identical series is almost
    # always units moving between raw currency and thousands/millions.
    unit_jump_checked = 0
    for key, group in series.items():
        ordered = sorted(group, key=lambda row: row.get("period", ""))
        for left, right in zip(ordered, ordered[1:]):
            if not left.get("value") or not right.get("value"):
                continue
            unit_jump_checked += 1
            ratio = abs(float(right["value"]) / float(left["value"]))
            if ratio >= 1000 or ratio <= 0.001:
                issues.append({
                    "code": "unit_scale_jump", "source_id": key[0],
                    "entity_id": key[1], "metric_id": key[2],
                    "period": right.get("period"), "ratio": ratio})

    by_statement: dict[tuple, dict[str, float]] = defaultdict(dict)
    for row in rows:
        if row.get("period_basis") != "instant":
            continue
        key = (row.get("source_id"), row.get("entity_id"), row.get("period"),
               row.get("currency"), row.get("unit"))
        by_statement[key][row.get("metric_id", "")] = float(row["value"])
    identity_checked = 0
    for key, values in by_statement.items():
        required = {
            "financial.total_assets.gaap", "financial.total_liabilities.gaap",
            "financial.stockholders_equity.gaap"}
        if not required <= set(values):
            continue
        identity_checked += 1
        assets = values["financial.total_assets.gaap"]
        rhs = (values["financial.total_liabilities.gaap"]
               + values["financial.stockholders_equity.gaap"])
        difference = abs(assets - rhs)
        relative = difference / max(abs(assets), 1.0)
        if relative > identity_relative_tolerance:
            issues.append({
                "code": "balance_sheet_identity", "source_id": key[0],
                "entity_id": key[1], "period": key[2],
                "assets": assets, "liabilities_plus_equity": rhs,
                "relative_difference": relative})

    # A duration metric can legitimately have a quarter and YTD fact ending on the
    # same date, but their basis must remain separate and YTD positive flows should not
    # be smaller than the current-quarter component.
    duration: dict[tuple, dict[str, float]] = defaultdict(dict)
    for row in rows:
        if row.get("period_basis") in {"quarter", "ytd"}:
            key = (row.get("source_id"), row.get("entity_id"), row.get("metric_id"),
                   row.get("period"), row.get("unit"), row.get("currency"))
            duration[key][row["period_basis"]] = float(row["value"])
    duration_checked = 0
    for key, values in duration.items():
        if {"quarter", "ytd"} <= set(values):
            duration_checked += 1
            if values["quarter"] >= 0 and values["ytd"] >= 0 \
                    and values["ytd"] < values["quarter"]:
                issues.append({
                    "code": "ytd_less_than_quarter", "source_id": key[0],
                    "entity_id": key[1], "metric_id": key[2], "period": key[3],
                    "quarter": values["quarter"], "ytd": values["ytd"]})

    comparable: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        comparable[(row.get("entity_id"), row.get("metric_id"), row.get("period"),
                    row.get("period_basis"), row.get("unit"),
                    row.get("currency"))].append(row)
    reconciliation_checked = 0
    reconciliation_conflicts = []
    for key, group in comparable.items():
        sources = {row.get("source_id") for row in group}
        if len(sources) < 2:
            continue
        reconciliation_checked += 1
        baseline = float(group[0]["value"])
        for row in group[1:]:
            value = float(row["value"])
            relative = abs(value - baseline) / max(abs(baseline), 1.0)
            if relative > source_relative_tolerance:
                conflict = {
                    "code": "source_reconciliation", "entity_id": key[0],
                    "metric_id": key[1], "period": key[2],
                    "left_source": group[0].get("source_id"),
                    "right_source": row.get("source_id"),
                    "relative_difference": relative,
                }
                issues.append(conflict)
                reconciliation_conflicts.append(conflict)

    coverage = {}
    for entity in sorted({row.get("entity_id") for row in rows}):
        entity_rows = [row for row in rows if row.get("entity_id") == entity]
        coverage[entity] = {
            "records": len(entity_rows),
            "metrics": sorted({row.get("metric_id") for row in entity_rows}),
            "first_period": min((row.get("period", "") for row in entity_rows), default=""),
            "last_period": max((row.get("period", "") for row in entity_rows), default=""),
        }
    return {
        "status": "passed" if not issues else "failed",
        "issues": issues,
        "coverage": coverage,
        "checks": {
            "continuity_pairs": continuity_checked,
            "unit_jump_pairs": unit_jump_checked,
            "balance_sheet_identities": identity_checked,
            "quarter_ytd_pairs": duration_checked,
            "source_reconciliations": reconciliation_checked,
            "source_conflicts": len(reconciliation_conflicts),
        },
    }


def consensus_quality(rows: list[dict], *, now: datetime | None = None,
                      freshness_hours_max: float = 168,
                      latest_ingestion_status: str = "") -> dict:
    """Evaluate snapshots without inventing historical publication timestamps."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issues: list[dict] = []
    checks = {
        "entities": 0, "snapshots": 0, "estimate_ranges": 0,
        "target_bindings": 0, "freshness": 0,
    }
    if latest_ingestion_status in {"unreachable", "unauthorized", "parse_failed"}:
        issues.append({"code": "source_unavailable", "status": latest_ingestion_status})

    by_entity: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_entity[str(row.get("entity_id", ""))].append(row)
    coverage = {}
    estimate_metrics = {
        "consensus.eps.mean", "consensus.eps.low", "consensus.eps.high",
        "consensus.revenue.mean", "consensus.revenue.low", "consensus.revenue.high",
    }
    required_any = {
        "consensus.eps.mean", "consensus.revenue.mean",
        "consensus.price_target.mean",
    }
    for entity, entity_rows in sorted(by_entity.items()):
        checks["entities"] += 1
        latest_known = max((str(row.get("known_at", "")) for row in entity_rows), default="")
        latest = [row for row in entity_rows if str(row.get("known_at", "")) == latest_known]
        checks["snapshots"] += 1
        metrics = {str(row.get("metric_id", "")) for row in latest}
        if not metrics & required_any:
            issues.append({"code": "required_fields_missing", "entity_id": entity,
                           "known_at": latest_known})
        estimate_rows = [row for row in latest if row.get("metric_id") in estimate_metrics]
        target_periods = {str(row.get("period", "")) for row in estimate_rows}
        checks["target_bindings"] += len(estimate_rows)
        if estimate_rows and ("" in target_periods or len(target_periods) != 1):
            issues.append({"code": "target_period_conflict", "entity_id": entity,
                           "known_at": latest_known,
                           "periods": sorted(target_periods)})

        values = {str(row.get("metric_id")): float(row["value"]) for row in latest}
        for prefix in ("consensus.eps", "consensus.revenue"):
            keys = (f"{prefix}.low", f"{prefix}.mean", f"{prefix}.high")
            if set(keys) <= set(values):
                checks["estimate_ranges"] += 1
                if not values[keys[0]] <= values[keys[1]] <= values[keys[2]]:
                    issues.append({"code": "invalid_estimate_range", "entity_id": entity,
                                   "metric_prefix": prefix, "known_at": latest_known})

        age = None
        if latest_known:
            try:
                known = datetime.fromisoformat(latest_known)
                age = max(0.0, (now - known).total_seconds() / 3600)
                checks["freshness"] += 1
                if age > freshness_hours_max:
                    issues.append({"code": "stale", "entity_id": entity,
                                   "known_at": latest_known, "age_hours": age,
                                   "maximum_hours": freshness_hours_max})
            except ValueError:
                issues.append({"code": "known_at_invalid", "entity_id": entity,
                               "known_at": latest_known})
        coverage[entity] = {
            "latest_known_at": latest_known, "age_hours": age,
            "latest_metrics": sorted(metrics), "latest_records": len(latest),
            "target_periods": sorted(target_periods),
        }
    if not rows and not issues:
        issues.append({"code": "no_coverage"})
    return {
        "status": "passed" if not issues else "failed",
        "issues": issues, "coverage": coverage, "checks": checks,
    }
