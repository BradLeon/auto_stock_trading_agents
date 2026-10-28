"""Deterministic identifiers for the Clerk/ledger layer (Phase C).

Same principle as the decision-layer hashing: an id is DERIVED from its
content, so replaying the same discovery twice yields the same id and the
same row — idempotency by construction, not by bookkeeping.
"""

from __future__ import annotations

import hashlib


def exception_id(kind: str, subject_key: str) -> str:
    """Stable id for a ledger exception: `exc_<sha1(kind|subject)[:24]>`."""
    body = f"{kind}|{subject_key}"
    return "exc_" + hashlib.sha1(body.encode()).hexdigest()[:24]


def run_id(kind: str, window_start: str, window_end: str, as_of: str) -> str:
    """Stable id for a Clerk run: kind + window + as-of, NEVER the request
    moment (same principle as `TriggerContext.idempotency_key`)."""
    body = f"run|{kind}|{window_start}|{window_end}|{as_of}"
    return "run_" + hashlib.sha1(body.encode()).hexdigest()[:24]


def model_id(kind: str, period: str, method_version: str) -> str:
    """Stable id for a derived read-model row."""
    body = f"model|{kind}|{period}|{method_version}"
    return "mdl_" + hashlib.sha1(body.encode()).hexdigest()[:24]
