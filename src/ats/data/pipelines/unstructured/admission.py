"""Shared document admission gate under the unified namespace."""

from ats.data.admission import (
    AdmissionOutcome,
    CandidateDocument,
    ValidationIssue,
    ValidationResult,
    admit,
    result_json,
    validate_candidate,
)

__all__ = [
    "AdmissionOutcome",
    "CandidateDocument",
    "ValidationIssue",
    "ValidationResult",
    "admit",
    "result_json",
    "validate_candidate",
]
