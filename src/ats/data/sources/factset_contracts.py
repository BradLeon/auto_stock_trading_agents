"""Immutable per-report/per-period FactSet candidates and review identities."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CellEvidence(Contract):
    page_number: int = Field(ge=1)
    image_number: int = Field(ge=1)
    image_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    region: tuple[float, float, float, float]
    raw_token: str
    method: str = Field(min_length=1)

    @model_validator(mode="after")
    def bounds(self):
        x0, y0, x1, y1 = self.region
        if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
            raise ValueError("invalid_evidence_region")
        return self


def resolve_estimate_state(evidence: tuple[CellEvidence, ...], policy: dict) -> str:
    """Evidence must belong to this metric/period, not unrelated page prose.

    Only an explicit source label establishes actual. Conditional prose such
    as 'if this is the actual growth rate' is not an actual-performance label.
    Retain the complete original evidence even when defaulting to estimated.
    """
    labels = {label.casefold() for label in policy['estimate_state_policy']['actual_labels']}
    return 'actual' if any(e.raw_token.strip().casefold() in labels for e in evidence) else 'estimated'


class GroupIdentity(Contract):
    scope_id: str = "all_sectors"
    scope_version: str = "legacy"
    entity_ids: tuple[str, ...] = ()
    chart_id: str = Field(min_length=1)
    period: str = Field(min_length=1)
    period_basis: Literal[
        "target_quarter", "calendar_year", "fiscal_year", "mixed_fiscal_years", "snapshot"
    ]
    # Legacy values remain readable without rewriting immutable historical hashes.
    estimate_state: Literal["estimated", "blended", "actual", "not_applicable", "unresolved"] = "estimated"

    @model_validator(mode="after")
    def valid_period(self):
        patterns = {
            "target_quarter": r"20\d{2}Q[1-4]",
            "calendar_year": r"20\d{2}",
            "fiscal_year": r"20\d{2}",
            "mixed_fiscal_years": r"20\d{2}/20\d{2}",
        }
        if self.period_basis == "snapshot":
            date.fromisoformat(self.period)
        elif not re.fullmatch(patterns[self.period_basis], self.period):
            raise ValueError("period_basis_mismatch")
        return self

    @property
    def key(self):
        return digest(self.identity_payload)

    @property
    def identity_payload(self):
        data = self.model_dump(mode="json")
        if self.scope_version == 'legacy' and self.scope_id == 'all_sectors' and not self.entity_ids:
            # Preserve hashes of historical packages; never rewrite old audits.
            for field in ('scope_id', 'scope_version', 'entity_ids'):
                data.pop(field)
        else:
            data['entity_ids'] = sorted(self.entity_ids)
        return data


class SectorCandidate(Contract):
    entity_id: str = Field(pattern=r"^GICS_\d{2}$")
    column: str = Field(min_length=1)
    value: Decimal | None
    unit: Literal["percent", "ratio", "count", "multiple"]
    source_label: str = Field(min_length=1)
    label_evidence: CellEvidence
    value_evidence: tuple[CellEvidence, ...] = Field(min_length=1)
    status: Literal["pending_review", "conflict", "extraction_failed", "manual_reviewed"] = (
        "pending_review"
    )
    reasons: tuple[str, ...] = ()
    comparison_date: date | None = None
    comparison_token: str | None = None
    comparison_label: str | None = None
    corrected_from: str | None = None

    @model_validator(mode="after")
    def finite(self):
        if self.value is not None and not self.value.is_finite():
            raise ValueError("nonfinite_candidate")
        if self.status == "manual_reviewed" and not self.corrected_from:
            raise ValueError("manual_correction_requires_original_reference")
        return self


class GroupPackage(Contract):
    pdf_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_version: str = Field(min_length=1)
    report_date: date
    extractor_version: str = Field(min_length=1)
    policy_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    group: GroupIdentity
    candidates: tuple[SectorCandidate, ...]
    # Only explicit source evidence can waive a registered column.
    undisclosed: tuple[tuple[str, CellEvidence], ...] = ()
    stage_errors: tuple[str, ...] = ()
    estimate_state_evidence: tuple[CellEvidence, ...] = ()

    @property
    def identity_payload(self):
        data = self.model_dump(mode="json")
        if not self.estimate_state_evidence:
            data.pop('estimate_state_evidence')
        data['group'] = self.group.identity_payload
        for cell in data['candidates']:
            if cell['comparison_label'] is None:
                cell.pop('comparison_label')
        data["candidates"] = sorted(
            data["candidates"], key=lambda c: (c["entity_id"], c["column"], digest(c))
        )
        data["undisclosed"] = sorted(data["undisclosed"], key=lambda entry: entry[0])
        return data

    @property
    def package_hash(self):
        return digest(self.identity_payload)


def validate_group(package: GroupPackage, policy: dict) -> list[str]:
    """Structural admission checks; no confidence score confers approval."""
    from .factset_report_layout import policy_hash
    from .factset_earnings_charts import normalize_chart_text
    from .factset_chart_values import parse_printed_number, _unique_sector_label

    reasons = list(package.stage_errors)
    scope = policy['scope_policy']
    group = package.group
    if group.scope_version == 'legacy' and group.scope_id == 'all_sectors' and not group.entity_ids:
        entities = set(policy['sector_aliases'])
    else:
        registered = scope['scopes'].get(group.scope_id)
        entities = set(group.entity_ids)
        if (registered is None or group.scope_version != scope['version']
                or not entities or len(entities) != len(group.entity_ids)
                or entities != set(registered['entities'])):
            reasons.append('entity_scope_mismatch')
    if package.policy_hash != policy_hash(policy):
        reasons.append("policy_hash_mismatch")
    rule = policy["groups"].get(package.group.chart_id)
    if rule is None:
        return sorted(set(reasons + ["unknown_metric_group"]))
    if package.group.estimate_state == "unresolved":
        reasons.append("estimate_state_unresolved")
    elif package.group.estimate_state not in rule["estimate_states"]:
        reasons.append("estimate_state_not_applicable_to_group")
    elif package.group.estimate_state == 'actual':
        if resolve_estimate_state(package.estimate_state_evidence, policy) != 'actual':
            reasons.append('actual_source_evidence_required')
    aliases = {
        normalize_chart_text(label): entity
        for entity, labels in policy["sector_aliases"].items()
        for label in labels
    }
    waived = [column for column, _ in package.undisclosed]
    if len(waived) != len(set(waived)) or not set(waived) <= set(rule["columns"]):
        reasons.append("invalid_undisclosed_columns")
    required = set(rule["columns"]) - set(waived)
    expected = {(entity, column) for entity in entities for column in required}
    supplied = [(c.entity_id, c.column) for c in package.candidates]
    if set(supplied) != expected or len(supplied) != len(expected):
        reasons.append("group_coverage_mismatch")
    for cell in package.candidates:
        if _unique_sector_label(cell.source_label, aliases) != cell.entity_id:
            reasons.append("entity_label_mismatch")
        if _unique_sector_label(cell.label_evidence.raw_token, aliases) != cell.entity_id:
            reasons.append("entity_evidence_mismatch")
        if not cell.label_evidence.raw_token.strip() or not any(
            e.raw_token.strip() for e in cell.value_evidence
        ):
            reasons.append("source_token_missing")
        if cell.unit != rule["column_units"].get(cell.column):
            reasons.append("column_unit_mismatch")
        if cell.value is None or cell.status in {"conflict", "extraction_failed"}:
            reasons.append("unresolved_candidate")
        elif cell.unit == "ratio" and not Decimal(0) <= cell.value <= Decimal(1):
            reasons.append("ratio_out_of_range")
        elif cell.unit == "count" and (
            cell.value < 0 or cell.value != cell.value.to_integral_value()
        ):
            reasons.append("invalid_count")
        elif cell.unit == "multiple" and cell.value < 0:
            reasons.append("negative_multiple")
        evidence = (
            cell.value_evidence[-1:] if cell.status == "manual_reviewed" else cell.value_evidence
        )
        for source in evidence:
            token, unit = parse_printed_number(source.raw_token)
            if token is None:
                reasons.append("numeric_evidence_unreadable")
                continue
            numeric = Decimal(token)
            if cell.unit == "ratio" and unit == "percent":
                numeric /= 100
            if numeric != cell.value:
                reasons.append("numeric_evidence_conflict")
            if cell.unit in {"count", "multiple"} and unit == "percent":
                reasons.append("numeric_evidence_unit_mismatch")
    values = {(c.entity_id, c.column): c.value for c in package.candidates}
    for columns in rule.get("sum_to_one", []):
        if not set(columns) <= required:
            continue
        for entity in entities:
            parts = [values.get((entity, column)) for column in columns]
            if any(value is None for value in parts):
                continue
            total = sum(parts)
            if rule.get("allow_zero_sum") and total == 0:
                continue
            if abs(total - 1) > Decimal(str(policy["admission"]["composition_tolerance"])):
                reasons.append("composition_mismatch")
    if group.chart_id == 'eps_guidance':
        for entity in entities:
            positive = values.get((entity, 'positive_count'))
            negative = values.get((entity, 'negative_count'))
            positive_share = values.get((entity, 'positive_share'))
            negative_share = values.get((entity, 'negative_share'))
            if any(value is None for value in (positive, negative, positive_share, negative_share)):
                continue
            total = positive + negative
            if total and (abs(positive / total - positive_share) > Decimal('.015')
                          or abs(negative / total - negative_share) > Decimal('.015')):
                reasons.append('guidance_count_share_mismatch')
    return sorted(set(reasons))
