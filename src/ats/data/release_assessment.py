"""Evidence-based classification for consumer data cutover.

Feature flags choose a read path; they are not proof that a read path is safe.
This module is deliberately read-only.  It interprets durable shadow records and
the checked-in consumer inventory before a release overlay may select platform.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

import yaml

from .cutover import consumer_cutover_status


DIRECT_DATA = "direct_data"
ORCHESTRATION_BOUNDARY = "orchestration_boundary"
EQUIVALENT = "equivalent"
GOVERNED_UPGRADE = "governed_upgrade"
PLATFORM_REGRESSION = "platform_regression"
EVIDENCE_INCOMPLETE = "evidence_incomplete"


def _repo_root() -> Path:
    from ..config import REPO_ROOT

    return REPO_ROOT


def default_consumer_release_path() -> Path:
    return _repo_root() / "config" / "data" / "consumer_release.yaml"


def _consumer_id(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def load_consumer_release_inventory(path: str | Path | None = None) -> dict[str, dict]:
    """Load the checked-in inventory for direct consumers and orchestration boundaries."""
    target = Path(path) if path else default_consumer_release_path()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if int(raw.get("version", 1)) != 1:
        raise ValueError(f"unsupported consumer release inventory: {target}")
    consumers = raw.get("consumers") or {}
    if not isinstance(consumers, dict):
        raise ValueError("consumer release inventory consumers must be a mapping")
    result: dict[str, dict] = {}
    for consumer, body in consumers.items():
        normalized = _consumer_id(str(consumer))
        row = dict(body or {})
        kind = row.get("kind")
        if kind not in {DIRECT_DATA, ORCHESTRATION_BOUNDARY}:
            raise ValueError(f"consumer {normalized} has invalid kind: {kind}")
        result[normalized] = row
    return result


def _records(consumer: str, data_db: str | Path) -> list[dict]:
    path = Path(data_db)
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='data_consumer_cutover_records'"
        ).fetchone()
        if not exists:
            return []
        normalized = _consumer_id(consumer)
        rows = conn.execute(
            "SELECT checked_at,status,entity,details_json "
            "FROM data_consumer_cutover_records WHERE consumer IN (?, ?) "
            "ORDER BY checked_at",
            (normalized, normalized.replace("_", "-")),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for checked_at, status, entity, details_json in rows:
        try:
            raw = json.loads(details_json)
        except (TypeError, json.JSONDecodeError):
            raw = {}
        result.append({
            "checked_at": str(checked_at), "status": str(status),
            "entity": str(entity), "details": raw.get("details", raw),
        })
    return result


def _payload_present(value: Any) -> bool:
    return value not in (None, {}, [], (), "")


def _latest_comparison(records: list[dict]) -> dict | None:
    """Exclude explicit verification records from the shadow comparison decision."""
    for row in reversed(records):
        if row["details"].get("comparison_type") != "release_verification":
            return row
    return None


def _verification_after(records: list[dict], after: str | None) -> dict | None:
    for row in reversed(records):
        details = row["details"]
        if (details.get("comparison_type") == "release_verification"
                and details.get("independently_verified") is True
                and (after is None or row["checked_at"] >= after)):
            return row
    return None


def _comparison_category(row: dict | None) -> tuple[str, str, bool]:
    """Return category, reason, and whether an independent review is needed."""
    if row is None:
        return EVIDENCE_INCOMPLETE, "no_consumer_comparison_record", False
    details = row["details"]
    reconciliation = details.get("reconciliation") or {}
    kind = str(reconciliation.get("kind") or "")
    if row["status"] == "reconciled":
        if kind.startswith("governed_"):
            return GOVERNED_UPGRADE, kind, True
        if details.get("comparisons"):
            return EQUIVALENT, "migrated_storage_equivalence", False
        return EQUIVALENT, kind or "reconciled_consumer_output", False

    legacy, platform = details.get("legacy"), details.get("platform")
    reason = str(details.get("reason") or reconciliation.get("reason") or "mismatch")
    if not _payload_present(legacy) and _payload_present(platform) and (
            reason.endswith("Error") or "unreachable" in reason.lower() or "timeout" in reason.lower()):
        return GOVERNED_UPGRADE, f"legacy_unavailable:{reason}", True
    return PLATFORM_REGRESSION, reason, False


def assess_consumer_release(*, consumer: str, data_db: str | Path,
                            inventory_path: str | Path | None = None,
                            minimum_distinct_reconciled_days: int = 1,
                            maximum_mismatches: int = 0) -> dict:
    """Classify release evidence without changing a mode or writing a record.

    A governed upgrade is deliberately not eligible merely because the legacy
    request failed.  It must have a separate, durable verification record that
    documents the independent review.
    """
    normalized = _consumer_id(consumer)
    profiles = load_consumer_release_inventory(inventory_path)
    profile = profiles.get(normalized)
    if profile is None:
        return {
            "consumer": normalized, "category": EVIDENCE_INCOMPLETE,
            "kind": "unknown", "platform_eligible": False,
            "checks": [{"check": "consumer_inventory", "passed": False,
                        "detail": "unknown_consumer"}],
        }

    kind = profile["kind"]
    if kind == ORCHESTRATION_BOUNDARY:
        ignored_records = _records(normalized, data_db)
        upstream = []
        for upstream_consumer in profile.get("upstream_consumers") or []:
            status = consumer_cutover_status(
                consumer=upstream_consumer, data_db=data_db,
                minimum_distinct_reconciled_days=minimum_distinct_reconciled_days,
                maximum_mismatches=maximum_mismatches)
            upstream.append({"consumer": _consumer_id(str(upstream_consumer)), "status": status})
        checks = [
            {"check": "direct_data_consumer", "passed": False,
             "detail": "orchestration_boundary_has_no_replaceable_legacy_data_read"},
            {"check": "upstream_release_status", "passed": all(item["status"]["eligible"] for item in upstream),
             "detail": "all_upstreams_stable" if upstream and all(item["status"]["eligible"] for item in upstream)
             else "one_or_more_upstreams_not_stable"},
            {"check": "end_to_end_regression", "passed": False,
             "detail": "must_be_recorded_by_workflow_acceptance"},
            {"check": "rollback_drill", "passed": False,
             "detail": "must_be_recorded_by_workflow_acceptance"},
        ]
        return {
            "consumer": normalized, "kind": kind, "category": ORCHESTRATION_BOUNDARY,
            "platform_eligible": False, "interface": profile.get("interface", ""),
            "legacy_comparison_records_ignored": len(ignored_records),
            "upstream": upstream, "required_evidence": profile.get("required_evidence") or [],
            "checks": checks,
            "gap": "validate through upstream status plus workflow end-to-end and rollback evidence; do not publish a consumer platform mode",
        }

    records = _records(normalized, data_db)
    comparison = _latest_comparison(records)
    category, reason, needs_independent_verification = _comparison_category(comparison)
    stability = consumer_cutover_status(
        consumer=normalized, data_db=data_db,
        minimum_distinct_reconciled_days=minimum_distinct_reconciled_days,
        maximum_mismatches=maximum_mismatches)
    verification = _verification_after(
        records, comparison["checked_at"] if comparison else None)
    evidence_complete = comparison is not None and _payload_present(comparison["details"].get("platform"))
    # A table/hash comparison is useful migration evidence but cannot prove that
    # an Agent's product/router selected the same document version or rendered
    # the same output.  It therefore never completes a consumer release gate.
    upgrade_verified = not needs_independent_verification or verification is not None
    classification_passed = category in {EQUIVALENT, GOVERNED_UPGRADE}
    checks = [
        {"check": "direct_data_consumer", "passed": True, "detail": profile.get("interface", "")},
        {"check": "same_day_stability", "passed": stability["eligible"],
         "detail": stability["reason"], "status": stability},
        {"check": "coverage_freshness_output_evidence", "passed": evidence_complete,
         "detail": "comparison_has_platform_output_or_migrated_document_equivalence"
         if evidence_complete else "comparison_has_no_platform_output_evidence"},
        {"check": "independent_governed_upgrade_verification", "passed": upgrade_verified,
         "detail": "not_required_for_equivalent" if not needs_independent_verification
         else "verified" if verification else "required_before_platform_release"},
        {"check": "no_unresolved_platform_regression", "passed": category != PLATFORM_REGRESSION,
         "detail": reason},
    ]
    eligible = all(item["passed"] for item in checks)
    return {
        "consumer": normalized, "kind": kind, "category": category,
        "classification_reason": reason, "platform_eligible": eligible,
        "interface": profile.get("interface", ""), "inputs": profile.get("inputs") or [],
        "required_gates": profile.get("gates") or [], "checks": checks,
        "latest_comparison": comparison,
        "verification": verification,
        "gap": "" if eligible else "retain legacy, shadow, or fallback until all failed checks are resolved",
    }


__all__ = [
    "DIRECT_DATA", "EQUIVALENT", "EVIDENCE_INCOMPLETE", "GOVERNED_UPGRADE",
    "ORCHESTRATION_BOUNDARY", "PLATFORM_REGRESSION", "assess_consumer_release",
    "default_consumer_release_path", "load_consumer_release_inventory",
]
