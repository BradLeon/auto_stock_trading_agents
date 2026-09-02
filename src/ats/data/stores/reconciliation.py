"""Deterministic comparison helpers for staged repository cutovers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class ReconciliationResult:
    matched: bool
    compared: int
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)


def reconcile_rows(
    expected: Iterable[dict[str, Any]],
    actual: Iterable[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
) -> ReconciliationResult:
    """Compare two read models and report actionable differences."""

    def keyed(rows):
        return {tuple(row.get(field) for field in key_fields): row for row in rows}

    left, right = keyed(expected), keyed(actual)
    missing = sorted("|".join(str(item) for item in key) for key in left.keys() - right.keys())
    unexpected = sorted("|".join(str(item) for item in key) for key in right.keys() - left.keys())
    mismatches: list[dict[str, Any]] = []
    for key in sorted(left.keys() & right.keys(), key=str):
        for field in value_fields:
            if left[key].get(field) != right[key].get(field):
                mismatches.append({
                    "key": "|".join(str(item) for item in key),
                    "field": field,
                    "expected": left[key].get(field),
                    "actual": right[key].get(field),
                })
    return ReconciliationResult(
        matched=not missing and not unexpected and not mismatches,
        compared=len(left), missing=missing, unexpected=unexpected, mismatches=mismatches,
    )
