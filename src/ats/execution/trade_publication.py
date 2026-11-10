"""Guard at the actual trades/fills INSERT point (moved out of the shadow tools, switch-trade r5 8.4).

`memory.store` calls `assert_trade_publication` before it writes `trades` or `fills`. What it refuses:

- an isolated result (a simulated order or fill stamped with its isolation root) being written to a ledger outside
  that root — the long-term isolation guarantee that a rehearsal can never publish into production;
- any trade write from a process running in shadow mode (`ATS_RUN_MODE=shadow` or a shadow read context): such a
  process exists to observe, so a ledger write is a bug, refused loudly rather than redirected;
- a write to a destination the cutover wiring does not register for Clerk publication (narrowed in R5d).

The shadow refusal transport and its separate intent ledger were retired; this module keeps only the checks that
protect the real ledger.
"""
from __future__ import annotations

import os
from pathlib import Path


class TradePublicationRefused(RuntimeError):
    """A trades/fills write was refused because it would publish where it must not."""

    reason_code = "trade_publication_refused"


def _field(entry, name: str) -> str:
    return (entry.get(name, "") if isinstance(entry, dict) else getattr(entry, name, "")) or ""


def assert_trade_publication(store, entries=()) -> None:
    """Refuse before the INSERT/UPDATE, including through stale store handles."""
    from ..workflow.cutover import CLERK_PUBLICATION
    from ..workflow.cutover_wiring import assert_write_destination
    from ..workflow.runtime_reads import current_read_context

    filename = next((row[2] for row in store.conn.execute("PRAGMA database_list")
                     if row[1] == "main"), "")
    for entry in entries:
        origin = _field(entry, "isolation_root")
        if origin and filename and not Path(filename).resolve().is_relative_to(Path(origin).resolve()):
            raise TradePublicationRefused(
                "isolated result cannot publish outside its original isolation root")
    context = current_read_context()
    if os.environ.get("ATS_RUN_MODE") == "shadow" or (context is not None and context.mode == "shadow"):
        raise TradePublicationRefused(
            "a shadow-mode process attempted to write the real trades ledger; refused rather than redirected")
    assert_write_destination(store, CLERK_PUBLICATION)
