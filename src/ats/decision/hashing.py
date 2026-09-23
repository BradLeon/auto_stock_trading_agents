"""`decision_hash` — the content identity of one revision (design D3).

One normalization, reused from Phase A: :func:`ats.agent.task_projection.canonical_json`
eliminates key order and whitespace noise, and :func:`normalize_refs` freezes
input references. The warehouse must not grow a second "content hash" semantic —
the envelope hash and the revision hash stay mutually explainable.

Input is the triple named by design D3: the normalized trade instructions, the
decision rationale and the key input references. Audit fields (timestamps,
reviewer names, status) are deliberately excluded — they change without the
proposal changing, and a hash that moves on them would make identical proposals
look like different ones.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from ..agent.task_projection import canonical_json, normalize_refs


def decision_hash(
    *,
    orders: Sequence[Mapping[str, Any]],
    rationale: Any = None,
    input_refs: Any = None,
) -> str:
    """sha256 over the canonical revision content, truncated to 32 hex chars.

    Orders are canonicalized individually and then sorted, so submitting the
    same set of instructions in a different list order is the same revision.
    Per-order key order and JSON whitespace are eliminated by `canonical_json`.
    Any substantive change — symbol, action, notional, limit price, rationale,
    an input ref — moves the hash.

    `rationale` may be a string or a mapping (the legacy migration stores a
    symbol→rationale object); it is hashed through the same canonicalization
    either way.
    """
    canonical_orders = sorted(canonical_json(dict(order)) for order in orders)
    material = canonical_json({
        "orders": canonical_orders,
        "rationale": rationale if rationale is not None else "",
        "input_refs": normalize_refs(input_refs),
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
