"""Explicit current-report and historical group views; never splice reports."""

from datetime import date, datetime, timezone

from ..sources.factset_contracts import GroupIdentity
from .earnings_insight import DATASET_ID, _observation


def _gap_state(row: dict | None, reasons: list[str]) -> str:
    if row is None:
        return 'not_located'
    if any(reason in {'deferred', 'supplementary_deferred'} for reason in reasons):
        return 'deferred'
    if any(reason.startswith('not_disclosed') for reason in reasons):
        return 'not_disclosed'
    if any('extraction' in reason or 'unreadable' in reason for reason in reasons):
        return 'extraction_failed'
    if any('review' in reason for reason in reasons):
        return 'pending_review'
    return 'shadow' if row['passed'] else 'blocked'


def load_groups(
    products,
    *,
    version_id: str,
    report_date: date,
    expected_groups: list[GroupIdentity],
    as_of: datetime | None = None,
) -> dict:
    """Coverage is supplied by the report inventory, never inferred from success.

    An empty inventory is unknown, not a complete report. Historical groups are
    returned separately, keyed by full period/basis/state identity. Shadow
    releases remain diagnostics and are not consumer-available observations.
    """
    reference = as_of or datetime.now(timezone.utc)
    if reference.tzinfo is None or not version_id:
        raise ValueError("aware_as_of_and_report_version_required")
    identities = {group.key: group for group in expected_groups}
    if len(identities) != len(expected_groups):
        raise ValueError("duplicate_expected_group")
    from ..sources.factset_report_layout import load_layout_policy

    priorities = {
        scope_id: details.get('priority', 'unknown')
        for scope_id, details in load_layout_policy()['scope_policy']['scopes'].items()
    }
    current, history = {}, {}
    manifests = products.structured.release_manifests(
        dataset_id=DATASET_ID, as_of=reference, limit=10000
    )
    for key, group in identities.items():
        def compatible(row):
            source_group = row['quality'].get('group', {})
            return (row['partition_name'].startswith('sector_group:')
                    and source_group.get('chart_id') == group.chart_id
                    and source_group.get('period_basis') == group.period_basis
                    and source_group.get('estimate_state') == group.estimate_state
                    and source_group.get('scope_id', 'all_sectors') == group.scope_id
                    and source_group.get('scope_version', 'legacy') == group.scope_version
                    and set(source_group.get('entity_ids', [])) == set(group.entity_ids)
                    and (group.period_basis == 'snapshot' or source_group.get('period') == group.period))

        releases = [row for row in manifests if compatible(row)]
        latest = next((row for row in releases if row["version_id"] == version_id
                       and row['partition_name'] == 'sector_group:' + key), None)

        def view(row):
            if row is None:
                return {
                    "group": group.model_dump(mode="json"),
                    "state": "not_located",
                    "priority": priorities.get(group.scope_id, 'unknown'),
                    "passed": False,
                    "observations": [],
                    "reason_codes": ["group_not_released"],
                }
            usable = row["passed"] and row["status"] == "platform"
            reasons = list(row["quality"].get("reason_codes", []))
            if usable:
                with products.structured._lock:
                    review = products.structured.conn.execute(
                        "SELECT review_id,decision FROM factset_group_reviews "
                        "WHERE package_hash=? AND reviewed_at<=? "
                        "ORDER BY reviewed_at DESC,rowid DESC LIMIT 1",
                        (
                            row["quality"].get("package_hash", ""),
                            reference.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                        ),
                    ).fetchone()
                if (
                    review is None
                    or review[0] != row["quality"].get("review_id")
                    or review[1] != "approve"
                ):
                    usable = False
                    reasons.append("review_missing_stale_or_rejected")
            values = (
                [_observation(products, oid) for oid in row["observation_ids"]] if usable else []
            )
            complete = bool(values) and all(
                value is not None
                and value.known_at <= reference
                and value.dimensions.get("document_version") == row["version_id"]
                for value in values
            )
            if usable and not complete:
                reasons.append("release_observation_missing_or_future")
            available = usable and complete
            return {
                "group": row['quality']['group'],
                "state": "published" if available else _gap_state(row, reasons),
                "priority": row['quality'].get('priority', 'unknown'),
                "passed": available,
                "release_id": row["release_id"],
                "version_id": row["version_id"],
                "report_date": row["report_date"],
                "known_at": row["known_at"],
                "quality": row["quality"],
                "reason_codes": reasons,
                "observations": [v.model_dump(mode="json") for v in values] if available else [],
            }

        current[key] = view(latest)
        # Latest attempt per version wins: a rejected revision cannot expose an
        # earlier passing revision of that same report through the history view.
        seen = {version_id}
        for row in releases:
            if row["version_id"] in seen:
                continue
            seen.add(row["version_id"])
            if row["report_date"] >= report_date.isoformat():
                continue
            candidate = view(row)
            if candidate["passed"]:
                candidate["historical"] = True
                candidate['stale_relative_to_current_report'] = True
                candidate["current_reason_codes"] = current[key]["reason_codes"]
                candidate['current_state'] = current[key]['state']
                history[key] = candidate
                break
    published = sum(item["passed"] for item in current.values())
    current_summary = next((row for row in manifests
        if row['partition_name'] == 'sector_core' and row['version_id'] == version_id
        and 'core_acceptance' in row['quality']), None)
    summary_quality = current_summary['quality'] if current_summary else {}
    legacy_history = next((row for row in manifests
        if row['partition_name'] == 'sector_core' and row['passed']
        and row['status'] == 'platform' and row['report_date'] < report_date.isoformat()
        and 'core_acceptance' not in row['quality']), None)
    historical_sector_core = ({
        'release_id': legacy_history['release_id'],
        'report_date': legacy_history['report_date'],
        'version_id': legacy_history['version_id'],
        'known_at': legacy_history['known_at'],
        'stale_relative_to_current_report': True,
        'not_current_report': True,
    } if legacy_history else None)
    gaps = [
        {'group_key': key, 'group': item['group'],
         'priority': item['priority'], 'state': item['state'],
         'reason_codes': item['reason_codes']}
        for key, item in current.items() if not item['passed']
    ]
    return {
        "version_id": version_id,
        "report_date": report_date.isoformat(),
        "as_of": reference.isoformat(),
        "state": "complete" if current and published == len(current) else "partial",
        'core_acceptance': summary_quality.get('core_acceptance', 'blocked'),
        'report_coverage': summary_quality.get('report_coverage', 'partial'),
        'summary_release_id': current_summary['release_id'] if current_summary else '',
        "expected_groups": len(current),
        "published_groups": published,
        "coverage": published / len(current) if current else None,
        'gaps': gaps,
        "current": current,
        "latest_valid_history": history,
        'historical_sector_core': historical_sector_core,
    }
