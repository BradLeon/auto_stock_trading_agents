"""Read-only inventory of legacy `cycles`/`decisions` rows (Phase B task 1.6).

Reports how many legacy decision rows can be mapped into §12.3
`decision_revisions` (`mappable`) and how many carry gaps that force the
`legacy_unknown` marker, broken down by reason. The classification logic is
IMPORTED from :mod:`ats.decision.legacy` — the same function the one-time store
migration uses — so the report and the backfill cannot disagree.

The database is opened with SQLite's `mode=ro` URI, which makes any write a
hard error at the driver level: this script has no write path by construction.

Usage:
    python scripts/audit_legacy_decisions.py var/ats.sqlite [--output report.json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ats.decision.legacy import classify_legacy_decisions  # noqa: E402


def build_report(db_path: str) -> dict:
    """Open the database read-only and classify its legacy decision rows."""
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        inventory = classify_legacy_decisions(conn)
    finally:
        conn.close()
    reason_counts = Counter(row.reason for row in inventory.unmappable)
    cycles_with_unknown = sorted({
        row.cycle_id for row in inventory.unmappable})
    return {
        "database": str(Path(db_path).resolve()),
        "read_only": True,
        "legacy_cycles": inventory.cycles_seen,
        "decision_rows": len(inventory.rows),
        "mappable": len(inventory.mappable),
        "legacy_unknown": len(inventory.unmappable),
        "legacy_unknown_by_reason": dict(sorted(reason_counts.items())),
        "cycles_with_unknown_revisions": cycles_with_unknown,
        "unknown_samples": [
            {"decisions.rowid": row.rowid, "cycle_id": row.cycle_id,
             "symbol": row.symbol, "action": row.action, "reason": row.reason}
            for row in inventory.unmappable[:20]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db_path", help="path to the workflow-memory SQLite file")
    parser.add_argument("--output", help="also write the JSON report to this path")
    args = parser.parse_args(argv)

    report = build_report(args.db_path)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"report written to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
