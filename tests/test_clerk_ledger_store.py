"""Phase C group 1: Clerk/ledger storage, inventory script, and the
"everything but the primary key is nullable" ruling (design D3, tasks 1.1-1.5).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ats.memory import get_store  # noqa: E402
from ats.memory.store import TradingMemory  # noqa: E402

CLERK_TABLES = ("ledger_exceptions", "clerk_runs", "ledger_read_models")


@pytest.fixture
def store():
    return get_store()


def _pk_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")
            if r["pk"]}


# --------------------------------------------------------------------------- #
# 1.1 / 1.2 / 1.3 — tables, indexes, ownership
# --------------------------------------------------------------------------- #

def test_clerk_ledger_tables_and_indexes_exist(store):
    conn = get_store().conn
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in CLERK_TABLES:
        assert t in tables
    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    for ix in ("idx_ledger_exceptions_subject", "idx_clerk_runs_window",
               "idx_ledger_read_models_key"):
        assert ix in idx


def test_new_tables_registered_as_workflow_memory():
    from ats.data.stores import ownership

    assert set(CLERK_TABLES) <= ownership.WORKFLOW_MEMORY_TABLES


def test_clerk_runs_same_window_yields_one_row(store):
    """Task 1.2: the window unique index is the idempotency backstop — a second
    run for the same (kind, window, as_of) cannot create a second trail row."""
    conn = get_store().conn
    row = {"run_id": "run-1", "kind": "reconcile", "window_start": "2026-09-01",
           "window_end": "2026-09-01", "as_of": "2026-09-01T21:00:00+00:00",
           "status": "completed"}
    conn.execute(
        "INSERT INTO clerk_runs (run_id, kind, window_start, window_end, as_of, "
        "status) VALUES (:run_id, :kind, :window_start, :window_end, :as_of, "
        ":status)", row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO clerk_runs (run_id, kind, window_start, window_end, "
            "as_of, status) VALUES ('run-2', :kind, :window_start, :window_end, "
            ":as_of, :status)", row)


def test_ledger_read_models_same_key_yields_one_row(store):
    conn = get_store().conn
    base = {"kind": "performance", "period": "2026-09", "method_version": "v1"}
    conn.execute(
        "INSERT INTO ledger_read_models (model_id, kind, period, method_version, "
        "payload) VALUES ('m1', :kind, :period, :method_version, '{}')", base)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ledger_read_models (model_id, kind, period, "
            "method_version, payload) VALUES ('m2', :kind, :period, "
            ":method_version, '{}')", base)


# --------------------------------------------------------------------------- #
# 1.5 — the D3 ruling: nothing but the PK is NOT NULL, and the three
# chain-less row kinds (history / manual / unattributed) must be writable.
# --------------------------------------------------------------------------- #

def test_every_non_pk_column_is_nullable(store):
    conn = get_store().conn
    for table in CLERK_TABLES:
        pks = _pk_cols(conn, table)
        for r in conn.execute(f"PRAGMA table_info({table})"):
            if r["name"] in pks:
                continue
            assert r["notnull"] == 0, f"{table}.{r['name']} is NOT NULL"


def test_chainless_rows_of_all_three_kinds_are_writable(store):
    """History / manual / unattributed rows have no decision chain to fill —
    the schema must accept them with empty linkage (design D3)."""
    conn = get_store().conn
    # history order row
    conn.execute(
        "INSERT INTO ledger_exceptions (exception_id, kind, subject_key) "
        "VALUES ('e1', 'broken_link', 'trade:legacy-1')")
    # manual fill row (no cycle linkage)
    conn.execute(
        "INSERT INTO ledger_exceptions (exception_id, kind, subject_key, detail_json) "
        "VALUES ('e2', 'unattributed_fill', 'fill:manual-1', '{}')")
    # reconciliation gap row (window only, no symbol)
    conn.execute(
        "INSERT INTO ledger_exceptions (exception_id, kind, subject_key, "
        "window_start, window_end) VALUES ('e3', 'reconciliation_gap', "
        "'2026-07-23', '2026-07-23', '2026-07-23')")
    n = conn.execute("SELECT COUNT(*) n FROM ledger_exceptions").fetchone()["n"]
    assert n == 3


# --------------------------------------------------------------------------- #
# 1.4 — the read-only inventory script
# --------------------------------------------------------------------------- #

def _seed_inventory_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE trades (cycle_id TEXT, revision_no INTEGER,
            decision_hash TEXT, approval_id TEXT, symbol TEXT);
        CREATE TABLE fills (origin TEXT, link_confidence TEXT, captured_at TEXT);
        CREATE TABLE journal_meta (key TEXT, value TEXT);
        -- complete / partial / empty linkage rows
        INSERT INTO trades VALUES ('c1', 1, 'h1', 'a1', 'NVDA');
        INSERT INTO trades VALUES ('c2', 1, NULL, NULL, 'MSFT');   -- partial
        INSERT INTO trades VALUES (NULL, NULL, NULL, NULL, 'GOOG'); -- empty
        -- evidence-less manual fill + one attributed fill
        INSERT INTO fills VALUES ('manual', 'none', '2026-09-01T15:00:00');
        INSERT INTO fills VALUES ('system', 'order_ref', '2026-08-01T15:00:00');
        INSERT INTO journal_meta VALUES ('last_reconcile_at', '2026-08-15T00:00:00');
    """)
    conn.commit()
    conn.close()


def test_inventory_script_counts_and_never_writes(tmp_path):
    from audit_ledger_inventory import build_report

    db = tmp_path / "inv.sqlite"
    _seed_inventory_db(db)
    before = db.read_bytes()

    report = build_report(str(db))

    assert report["order_linkage"]["total_rows"] == 3
    assert report["order_linkage"]["complete_chain"] == 1
    assert report["order_linkage"]["partial_chain"] == 1
    assert report["order_linkage"]["empty_chain"] == 1
    assert report["fill_attribution"]["manual_without_evidence"] == 1
    assert report["reconciliation"]["fills_after_checkpoint"] == 1
    assert report["reconciliation"]["first_unseen_day"] == "2026-09-01"
    # mode=ro by construction — byte-for-byte unchanged
    assert db.read_bytes() == before


def test_inventory_script_handles_pre_phase_b_schema(tmp_path):
    """A real `var/ats.sqlite` copy has no linkage columns at all; the script
    must report that instead of crashing."""
    from audit_ledger_inventory import build_report

    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE trades (cycle_id TEXT, symbol TEXT);
        CREATE TABLE fills (captured_at TEXT);
        CREATE TABLE journal_meta (key TEXT, value TEXT);
        INSERT INTO trades VALUES ('c1', 'NVDA');
        INSERT INTO fills VALUES ('2026-09-01T15:00:00');
        INSERT INTO journal_meta VALUES ('last_reconcile_at', '2026-08-15T00:00:00');
    """)
    conn.commit()
    conn.close()

    report = build_report(str(db))

    assert report["order_linkage"]["schema_missing_columns"] == [
        "revision_no", "decision_hash", "approval_id"]
    assert report["order_linkage"]["empty_chain"] == 1


def test_inventory_script_on_real_db_copy_if_present():
    """Task 1.4's acceptance run: open the real database copy read-only and
    report counts. Skipped when the DB does not exist locally."""
    real = Path(__file__).resolve().parents[1] / "var" / "ats.sqlite"
    if not real.exists():
        pytest.skip("no local var/ats.sqlite")
    from audit_ledger_inventory import build_report

    report = build_report(str(real))
    assert report["order_linkage"]["total_rows"] > 0
    assert json.dumps(report)                       # fully serialisable
