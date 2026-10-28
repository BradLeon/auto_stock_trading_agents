"""Classification of legacy `cycles` / `decisions` rows for §12.3 migration.

One definition, two consumers: the read-only inventory script
(`scripts/audit_legacy_decisions.py`) and the one-time store migration
(`TradingMemory._migrate_legacy_decisions`). Sharing it is the point — the
pre-migration report and the actual backfill must never disagree about what
"mappable" means.

A legacy decision row is MAPPABLE when the original proposal is fully
recoverable: its cycle row exists (it carries the as-of/trigger context we no
longer have anywhere else), and the order fields (symbol, normalizable action,
notional) are all present. Anything with a gap is `legacy_unknown`: the
migration keeps the original identifiers but fabricates NO hash, NO snapshot
and NO approval linkage, and the revision is structurally barred from
execution authorization (see `ats.decision.state`).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..schemas.decision import UnknownActionError, normalize_action

MAPPABLE = "mappable"
UNMAPPABLE = "legacy_unknown"


@dataclass
class LegacyDecisionRow:
    """One `decisions` row plus its classification and, when unmappable, why."""

    rowid: int
    cycle_id: str
    symbol: str | None
    action: str | None
    notional_usd: float | None
    limit_price: float | None
    conviction: float | None
    rationale: str | None
    classification: str
    reason: str = ""


@dataclass
class LegacyInventory:
    """The full classification of one database's legacy decision rows."""

    rows: list[LegacyDecisionRow] = field(default_factory=list)
    cycles_seen: int = 0

    @property
    def mappable(self) -> list[LegacyDecisionRow]:
        return [r for r in self.rows if r.classification == MAPPABLE]

    @property
    def unmappable(self) -> list[LegacyDecisionRow]:
        return [r for r in self.rows if r.classification == UNMAPPABLE]

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.unmappable:
            counts[row.reason] = counts.get(row.reason, 0) + 1
        return counts


_DECISION_QUERY = (
    "SELECT rowid AS rid, cycle_id, symbol, action, notional_usd, "
    "limit_price, conviction, rationale FROM decisions"
)


def classify_legacy_decisions(conn: sqlite3.Connection) -> LegacyInventory:
    """Read-only scan of `cycles`/`decisions`; writes nothing, creates nothing."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    inventory = LegacyInventory()
    if "decisions" not in tables:
        return inventory
    known_cycles: set[str] = set()
    if "cycles" in tables:
        known_cycles = {r[0] for r in conn.execute("SELECT cycle_id FROM cycles")}
        inventory.cycles_seen = len(known_cycles)
    for rid, cycle_id, symbol, action, notional, limit_price, conviction, rationale \
            in conn.execute(_DECISION_QUERY):
        row = LegacyDecisionRow(
            rowid=rid, cycle_id=cycle_id or "", symbol=symbol, action=action,
            notional_usd=notional, limit_price=limit_price,
            conviction=conviction, rationale=rationale, classification=MAPPABLE)
        if not cycle_id:
            row.classification, row.reason = UNMAPPABLE, "missing_cycle_id"
        elif cycle_id not in known_cycles:
            # Without the cycle row we have no as-of / trigger context, so the
            # proposal's decision context is not recoverable — mark unknown.
            row.classification, row.reason = UNMAPPABLE, "cycle_row_missing"
        elif not symbol:
            row.classification, row.reason = UNMAPPABLE, "missing_symbol"
        elif notional is None:
            row.classification, row.reason = UNMAPPABLE, "missing_notional"
        else:
            try:
                normalize_action(action or "", where=f"decisions.rowid={rid}")
            except UnknownActionError:
                row.classification, row.reason = UNMAPPABLE, "unknown_action"
        inventory.rows.append(row)
    return inventory
