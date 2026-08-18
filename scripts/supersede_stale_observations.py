"""Retire superseded extraction runs in the evidence ledger.

    PYTHONPATH=src .venv/bin/python scripts/supersede_stale_observations.py          # dry run
    PYTHONPATH=src .venv/bin/python scripts/supersede_stale_observations.py --apply

WHY THIS EXISTS
---------------
`concept` — which claim dimension a fact belongs to — is decided by the model AT
EXTRACTION TIME and frozen in the row. So when a claim's dimension definitions change,
re-extracting the document is the only way for the new definitions to take effect.

But re-extraction did not remove the previous run's rows. Observation ids are
deterministic over (document, entity, metric, period), and the model rarely reproduces
the exact same `metric` label twice, so a re-run mostly wrote NEW rows beside the old
ones. Both readings of the same sentence then counted as evidence, and the older,
wronger classification kept voting.

Measured case (2026-08-18): `xpu_value_capture_per_capacity` was tightened specifically
to exclude consolidated gross margin. The re-extraction correctly dropped that row —
and the verdict did not move, because the previous run's copy was still mapped to
`xpu_margin_retention` and still refuting. One document, one speaker, two contradictory
classifications, both counted.

`observer.observe_document` now supersedes before writing, so this cannot recur. This
script is the one-time backfill for everything already in the ledger.

WHAT IT DOES
------------
For each (document_id, source_entity), all rows share one `observed_at` per extraction
run (the observer stamps one `now` across the whole run). Keep the LATEST run live;
mark every earlier run `superseded_at`.

Rows are MARKED, NEVER DELETED. "Superseded" and "never observed" are different states,
and the audit trail is why spans are stored at all. Assessment reads skip retired rows;
history reads can still see them via `include_superseded=True`.

`discovery_evidence` rows are retired like any other — the flag is sticky on the row and
the freeze already did its job (it recorded that this material prompted a proposal).
Their content survives in the newer run.
"""

from __future__ import annotations

import argparse
from collections import defaultdict


def main() -> int:
    from ats.memory import get_store

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="write the change (default: dry run, prints what would happen)")
    args = ap.parse_args()

    store = get_store()
    db = store.conn

    runs = defaultdict(list)
    for r in db.execute(
            "SELECT document_id, source_entity, observed_at, COUNT(*) n "
            "FROM evidence_observations WHERE superseded_at IS NULL "
            "GROUP BY document_id, source_entity, observed_at "
            "ORDER BY document_id, source_entity, observed_at"):
        runs[(r["document_id"], r["source_entity"])].append((r["observed_at"], r["n"]))

    stale_total = live_total = 0
    affected = []
    for (doc, speaker), by_run in sorted(runs.items()):
        live_total += by_run[-1][1]
        if len(by_run) == 1:
            continue
        stale = sum(n for _, n in by_run[:-1])
        stale_total += stale
        affected.append((doc, speaker, len(by_run), stale, by_run[-1][1]))

    print(f"{'document':38} {'speaker':10} {'runs':>4} {'retire':>7} {'keep':>5}")
    for doc, speaker, nruns, stale, keep in sorted(affected, key=lambda x: -x[3])[:40]:
        print(f"{doc[:38]:38} {speaker[:10]:10} {nruns:>4} {stale:>7} {keep:>5}")
    if len(affected) > 40:
        print(f"... 另有 {len(affected) - 40} 个 (document, speaker) 组")
    print(f"\n{len(affected)} 个组有多轮抽取 · 将退休 {stale_total} 行 · 保留 {live_total} 行")

    if not args.apply:
        print("\n(dry run — 加 --apply 才写入)")
        return 0

    n = 0
    for (doc, speaker), by_run in runs.items():
        if len(by_run) == 1:
            continue
        latest = by_run[-1][0]
        cur = db.execute(
            "UPDATE evidence_observations SET superseded_at = ? "
            "WHERE document_id = ? AND source_entity = ? AND observed_at < ? "
            "AND superseded_at IS NULL",
            (latest, doc, speaker, latest))
        n += cur.rowcount
    db.commit()
    print(f"\n✅ 已退休 {n} 行（未删除，superseded_at 已标记）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
