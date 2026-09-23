"""Read-only ledger inventory for the Phase C cutover (task 1.4).

Counts, on the REAL database, the three things the Clerk cutover must know
before it flips any behaviour:

1. **Broken-link order rows** — `trades` rows whose decision-chain linkage
   (`cycle_id` / `revision_no` / `decision_hash` / `approval_id`) is partial or
   empty. Partial chains are the worst kind: they look attributable but cannot
   be verified.
2. **Evidence-less `manual` fills** — fills currently labelled `manual`
   (`origin` / `link_confidence`) whose classification rests on no evidence
   (`link_confidence='none'` or origin set by the silent fallback). These are
   the rows task 2.4 will reclassify as `unattributed`.
3. **Unreconciled session windows** — fills dated after the last recorded
   reconcile checkpoint (`journal_meta.last_reconcile_at`), i.e. windows the
   current ad-hoc reconcile has never seen.

The database is opened with SQLite's `mode=ro` URI, which makes any write a
hard error at the driver level: this script has no write path by construction.

Usage:
    python scripts/audit_ledger_inventory.py var/ats.sqlite [--output report.json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def build_report(db_path: str) -> dict:
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        report: dict = {"db": str(Path(db_path).resolve())}

        # --- 1. broken-link order rows ------------------------------------- #
        # The script opens mode=ro and deliberately NEVER runs migrations, so a
        # pre-Phase-B database may lack the linkage columns entirely. Probe first.
        tcols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)")}
        quad = ("cycle_id", "revision_no", "decision_hash", "approval_id")
        missing_cols = [c for c in quad if c not in tcols]
        total = conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
        complete, partial, empty = 0, 0, 0
        if missing_cols:
            empty = total                     # no linkage columns: every row is unlinked
        else:
            for row in conn.execute(
                "SELECT cycle_id, revision_no, decision_hash, approval_id FROM trades"):
                present = sum(1 for c in quad if row[c] not in (None, ""))
                if present == len(quad):
                    complete += 1
                elif present == 0:
                    empty += 1
                else:
                    partial += 1
        report["order_linkage"] = {
            "total_rows": total,
            "complete_chain": complete,
            "empty_chain": empty,
            "partial_chain": partial,
            "schema_missing_columns": missing_cols,
        }

        # --- 2. evidence-less manual fills ---------------------------------- #
        fcols = {r["name"] for r in conn.execute("PRAGMA table_info(fills)")}
        fills_total = conn.execute("SELECT COUNT(*) n FROM fills").fetchone()["n"]
        if "link_confidence" not in fcols:
            by_conf = {"(column absent)": fills_total}
            manual_no_evidence = fills_total if "origin" not in fcols else 0
        else:
            by_conf = {}
            for row in conn.execute(
                "SELECT COALESCE(link_confidence,'(null)') k, COUNT(*) n "
                "FROM fills GROUP BY 1"):
                by_conf[row["k"]] = row["n"]
            if "origin" in fcols:
                manual_no_evidence = conn.execute(
                    "SELECT COUNT(*) n FROM fills "
                    "WHERE COALESCE(origin,'') = 'manual' "
                    "AND COALESCE(link_confidence,'none') = 'none'").fetchone()["n"]
            else:
                manual_no_evidence = 0
        report["fill_attribution"] = {
            "total_rows": fills_total,
            "by_link_confidence": by_conf,
            "manual_without_evidence": manual_no_evidence,
        }

        # --- 3. unreconciled windows ---------------------------------------- #
        last_reconcile = None
        try:
            row = conn.execute(
                "SELECT value FROM journal_meta WHERE key='last_reconcile_at'"
            ).fetchone()
            last_reconcile = row["value"] if row else None
        except sqlite3.OperationalError:
            pass
        if last_reconcile:
            unseen = conn.execute(
                "SELECT COUNT(*) n, MIN(DATE(captured_at)) first_unseen, "
                "MAX(DATE(captured_at)) last_unseen FROM fills "
                "WHERE captured_at > ?", (last_reconcile,)).fetchone()
        else:
            unseen = conn.execute(
                "SELECT COUNT(*) n, MIN(DATE(captured_at)) first_unseen, "
                "MAX(DATE(captured_at)) last_unseen FROM fills").fetchone()
        report["reconciliation"] = {
            "last_reconcile_at": last_reconcile,
            "fills_after_checkpoint": unseen["n"],
            "first_unseen_day": unseen["first_unseen"],
            "last_unseen_day": unseen["last_unseen"],
        }

        report["interpretation"] = (
            "empty_chain rows will be registered as legacy gaps (task 2.3); "
            "partial_chain rows must be zero after the write-path enforcement; "
            "manual_without_evidence fills become unattributed + exceptions "
            "(task 2.4); fills_after_checkpoint are windows the Clerk must "
            "reconcile or register as gaps (task 3.7).")
        return report
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db_path")
    ap.add_argument("--output", help="write the JSON report to this path")
    args = ap.parse_args()

    report = build_report(args.db_path)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text)
        print(f"report written to {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
