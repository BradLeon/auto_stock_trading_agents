"""Ledger-level helpers: origin splitting for performance & internal state.

Clerk-adjacent deterministic logic that both the performance rebuild (task 5.1)
and the Internal State API (task 6.1) need: separating SYSTEM trades — the
only class that may enter system performance — from MANUAL orders (deliberate
human trades, shown separately) and UNATTRIBUTED fills (unknown, flagged,
excluded from system performance but never hidden).

§11.1: unattributable is not ignorable, and it is not manual either.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .ids import exception_id, model_id, run_id  # noqa: F401  (re-exported)

MANUAL_ORIGIN = "manual"
UNATTRIBUTED_ORIGIN = "unattributed"
SYSTEM_ORIGIN = "system"

# Origins that may NEVER enter system performance / attribution.
NON_SYSTEM_ORIGINS = frozenset({MANUAL_ORIGIN, UNATTRIBUTED_ORIGIN})


def split_by_origin(rows: Iterable[Mapping]) -> dict[str, list[Mapping]]:
    """Split ledger rows into system / manual / unattributed buckets.

    Rows without an `origin` field (or with an empty one) land in
    `unattributed`: absent evidence is unknown, not system.
    """
    out: dict[str, list[Mapping]] = {"system": [], "manual": [], "unattributed": []}
    for r in rows:
        origin = (r.get("origin") or "").strip() if hasattr(r, "get") else ""
        out[origin if origin in out else "unattributed"].append(r)
    return out


def system_only(rows: Iterable[Mapping]) -> list[Mapping]:
    """The rows allowed into system performance / attribution (task 2.5).

    ONLY an explicit `origin == "system"` qualifies — a missing origin is
    unknown, and unknown never enters system performance.
    """
    return [r for r in rows if (r.get("origin") or "").strip() == SYSTEM_ORIGIN]
