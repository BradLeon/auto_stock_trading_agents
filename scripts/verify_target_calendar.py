"""Replay public calendars through the real queue in disposable local stores.

Run with: uv run --offline --no-sync python scripts/verify_target_calendar.py
Makes public HTTP reads, never changes production stores, schedules or trades.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ats-target-calendar-"))
    for key, value in {
        "ATS_DATA_DB_PATH": root / "data.sqlite",
        "ATS_DB_PATH": root / "memory.sqlite",
        "ATS_PERSISTENT_QUEUE_PATH": root / "queue.sqlite",
        "ATS_DATA_ARTIFACT_ROOT": root / "artifacts",
    }.items():
        os.environ[key] = str(value)

    from ats.data.calendar_refresh import enqueue_schedule_calendar_refresh
    from ats.data.persistent_queue import PersistentIngestionQueue
    from ats.data.products.calendar import ScheduleCalendarProduct
    from ats.data.stores.schedule_calendar import ScheduleCalendarStore

    selected = ("federal_reserve_fomc", "bls_release_calendar", "bea_release_schedule")
    submitted = enqueue_schedule_calendar_refresh(source_ids=selected,
                                                    trigger_ref="target-calendar-acceptance")
    queue = PersistentIngestionQueue()
    results = []
    for task in submitted:
        outcome = queue.run_one(task_id=task["task_id"], timeout_seconds=90)
        saved = queue.get(task["task_id"])
        source = (outcome or {}).get("stdout", {}).get("sources", [])
        results.append({
            "source_id": task["source_id"], "task_id": task["task_id"],
            "status": (outcome or {}).get("status"), "sources": source,
            "lineage_counts": {key: len(refs) for key, refs in
                               (saved or {}).get("lineage_refs", {}).items()},
        })
    store = ScheduleCalendarStore(root / "data.sqlite")
    snapshot = ScheduleCalendarProduct(store).snapshot()
    passed = (all(row["status"] == "succeeded" for row in results)
              and snapshot["quality"]["status"] == "ok"
              and all(row["lineage_counts"].get("raw", 0) == 1 for row in results))
    report = {"passed": passed, "isolated_root": str(root), "results": results,
              "published_events": len(snapshot["events"]),
              "calendar_quality": snapshot["quality"]["status"],
              "source_runs": store.source_runs(limit=10)}
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
