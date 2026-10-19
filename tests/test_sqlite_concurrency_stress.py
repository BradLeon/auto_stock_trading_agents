"""SQLite contention under real thread parallelism (parent 12.2).

The earlier "concurrent" tests are light: 24 inserts over 8 workers, or four sequential alternations
between two repository objects. This module hammers the three shared-state paths the migration
depends on, each from many task-local connections on one database file, and checks the properties the
design relies on: no `database is locked` escapes, no lost or duplicated rows, gapless revision numbers,
and exactly one winner for a contended trigger claim.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.decision.repository import DecisionAuditRepository
from ats.memory.store import TradingMemory
from ats.workflow.store import WorkflowStore

THREADS = 12
PER_THREAD = 25
NOW = "2026-10-19T00:00:00+00:00"


def _envelope(summary: str):
    return build_envelope(
        role="layer_analysis",
        payload={"layer": "L3", "status": "steady", "summary": summary,
                 "findings": ["stress"], "confidence": 0.5},
        scope=ProjectionScope(kind="layer", id="L3"), as_of=NOW, input_refs=["obs-1"],
        data_vintage_refs=["dataset@2026-10-19"],
        valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds"))


def _count(path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_projection_publication_loses_and_duplicates_nothing_under_contention(tmp_path):
    path = tmp_path / "stress.sqlite"
    TradingMemory(path)                       # one-time bootstrap, as the cached store does
    shared = _envelope("identical content published by every worker")

    def work(worker: int) -> int:
        store = TradingMemory(path, initialize=False)    # task-local connection, like a Dispatcher worker
        try:
            for i in range(PER_THREAD):
                store.save_task_projection_envelope(_envelope(f"worker {worker} item {i}"))
                store.save_task_projection_envelope(shared)
            return PER_THREAD
        finally:
            store.conn.close()

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        assert sum(pool.map(work, range(THREADS))) == THREADS * PER_THREAD
    # every distinct item once, the shared content exactly once (insert-or-return-existing)
    assert _count(path, "task_projection_envelopes") == THREADS * PER_THREAD + 1


def test_decision_revision_numbers_stay_gapless_under_parallel_writers(tmp_path):
    path = tmp_path / "decisions.sqlite"
    DecisionAuditRepository(TradingMemory(path)).create_cycle(cycle_id="c1", trigger_source="manual")
    writers, each = 8, 10

    def work(writer: int) -> list[int]:
        repo = DecisionAuditRepository(TradingMemory(path, initialize=False))
        numbers = []
        for i in range(each):
            row = repo.append_revision(
                cycle_id="c1",
                orders=[{"symbol": "AMD", "action": "buy", "notional_usd": 100.0 + writer * 1000 + i}])
            numbers.append(row["revision_no"])
        return numbers

    with ThreadPoolExecutor(max_workers=writers) as pool:
        returned = [n for numbers in pool.map(work, range(writers)) for n in numbers]
    assert sorted(returned) == list(range(1, writers * each + 1))
    stored = [r["revision_no"] for r in
              DecisionAuditRepository(TradingMemory(path, initialize=False)).list_revisions("c1")]
    assert sorted(stored) == list(range(1, writers * each + 1))


def test_exactly_one_worker_wins_a_contended_trigger_claim(tmp_path):
    path = tmp_path / "workflow.sqlite"
    WorkflowStore(path)
    request = {"schedule_id": "daily", "scheduled_for": "2026-10-19T09:00:00+00:00"}

    def claim(worker: int) -> bool:
        return WorkflowStore(path).claim_trigger(
            trigger_key="contended", kind="schedule", workflow_id="macro-review", request=request,
            owner_id=f"worker-{worker}", lease_seconds=300)["acquired"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(claim, range(16)))
    assert results.count(True) == 1
    assert _count(path, "trigger_runs") == 1
