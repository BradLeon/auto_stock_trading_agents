"""Phase B task group 1: the five §12.3 decision-audit tables.

Additive storage, immutable revisions, persistent idempotency keys, and the
honest legacy migration — nothing here changes existing behaviour; the point is
that the audit substrate exists, survives upgrades, and refuses to fabricate
history.
"""

from __future__ import annotations

import sqlite3

import pytest

from ats.decision.state import (REVISION_SOURCE_LEGACY,
                                REVISION_SOURCE_LEGACY_UNKNOWN,
                                revision_authorization_blockers)
from ats.memory.store import TradingMemory

_DECISION_AUDIT_TABLES = {
    "decision_cycles", "decision_revisions", "decision_risk_reviews",
    "boss_approvals", "cycle_events",
}

_EXPECTED_COLUMNS = {
    "decision_cycles": {
        "cycle_id", "trigger_source", "trigger_id", "research_snapshot",
        "status", "current_revision_no", "final_outcome", "superseded_reason",
        "created_at", "updated_at"},
    "decision_revisions": {
        "cycle_id", "revision_no", "decision_hash", "parent_revision_no",
        "orders_json", "rationale", "input_refs", "model_version",
        "prompt_version", "revision_source", "legacy_ref", "created_at"},
    "decision_risk_reviews": {
        "review_id", "cycle_id", "revision_no", "decision_hash",
        "ruleset_version", "portfolio_snapshot_id", "market_as_of", "verdict",
        "violations_json", "allowed_boundary_json", "before_metrics_json",
        "after_metrics_json", "notes", "created_at"},
    "boss_approvals": {
        "approval_id", "cycle_id", "revision_no", "decision_hash", "decision",
        "reviewer", "comment", "channel", "idempotency_key", "created_at"},
    "cycle_events": {
        "event_id", "cycle_id", "event_type", "actor", "from_status",
        "to_status", "idempotency_key", "payload", "error", "created_at"},
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_decision_audit_tables_have_expected_columns():
    """1.1: fresh database gets the five tables with the §12.3 column set."""
    store = TradingMemory(":memory:")
    for table, expected in _EXPECTED_COLUMNS.items():
        assert expected <= _columns(store.conn, table), table


def test_decision_audit_idempotency_keys_are_unique():
    """1.5: a replayed approval callback or retried transition cannot double-write."""
    store = TradingMemory(":memory:")
    store.conn.execute(
        "INSERT INTO boss_approvals (approval_id, cycle_id, revision_no, "
        "decision_hash, decision, reviewer, comment, channel, idempotency_key, "
        "created_at) VALUES ('a1','c1',1,'h','approved','boss','','web','k1','t')")
    store.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO boss_approvals (approval_id, cycle_id, revision_no, "
            "decision_hash, decision, reviewer, comment, channel, idempotency_key, "
            "created_at) VALUES ('a2','c1',1,'h','approved','boss','','web','k1','t')")
    store.conn.execute(
        "INSERT INTO cycle_events (cycle_id, event_type, actor, from_status, "
        "to_status, idempotency_key, created_at) "
        "VALUES ('c1','status_change','system','pending_risk','pending_approval',"
        "'k1','t')")
    store.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO cycle_events (cycle_id, event_type, actor, from_status, "
            "to_status, idempotency_key, created_at) "
            "VALUES ('c1','status_change','system','pending_risk','pending_approval',"
            "'k1','t')")
    # ...but the SAME key under a DIFFERENT cycle is a different fact.
    store.conn.execute(
        "INSERT INTO cycle_events (cycle_id, event_type, actor, from_status, "
        "to_status, idempotency_key, created_at) "
        "VALUES ('c2','status_change','system','pending_risk','pending_approval',"
        "'k1','t')")
    store.conn.commit()


def test_legacy_database_upgrade_creates_decision_audit_tables(tmp_path):
    """1.3: a pre-Phase-B database gains the five tables; legacy columns unchanged."""
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE cycles (cycle_id TEXT PRIMARY KEY, as_of TEXT,
            approval_status TEXT, manager_summary TEXT);
        CREATE TABLE decisions (cycle_id TEXT, symbol TEXT, action TEXT,
            notional_usd REAL, limit_price REAL, conviction REAL, rationale TEXT);
        INSERT INTO cycles VALUES ('c1','2026-01-05','approved','ok');
        INSERT INTO decisions VALUES ('c1','AMD','buy',5000.0,NULL,0.6,'recoverable');
        INSERT INTO decisions VALUES ('c1',NULL,'buy',5000.0,NULL,NULL,'no symbol');
        INSERT INTO decisions VALUES ('c-gone','NVDA','trim',3000.0,NULL,0.5,'cycle row lost');
    """)
    legacy_decision_cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    conn.commit()
    conn.close()

    store = TradingMemory(path)
    for table in _DECISION_AUDIT_TABLES:
        assert _columns(store.conn, table), table
    # Legacy tables keep their column sets verbatim (additive only).
    assert {r["name"] for r in store.conn.execute(
        "PRAGMA table_info(decisions)")} == legacy_decision_cols


def test_legacy_decisions_migration_is_honest(tmp_path):
    """1.7: mappable rows become hashed legacy revisions; gappy rows keep their
    identifiers, get no hash, and can never obtain execution authorization."""
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE cycles (cycle_id TEXT PRIMARY KEY, as_of TEXT,
            approval_status TEXT, manager_summary TEXT);
        CREATE TABLE decisions (cycle_id TEXT, symbol TEXT, action TEXT,
            notional_usd REAL, limit_price REAL, conviction REAL, rationale TEXT);
        INSERT INTO cycles VALUES ('c1','2026-01-05','approved','ok');
        INSERT INTO decisions VALUES ('c1','AMD','buy',5000.0,NULL,0.6,'recoverable');
        INSERT INTO decisions VALUES ('c1',NULL,'buy',5000.0,NULL,NULL,'no symbol');
        INSERT INTO decisions VALUES ('c-gone','NVDA','trim',3000.0,NULL,0.5,'cycle row lost');
    """)
    conn.commit()
    conn.close()

    store = TradingMemory(path)
    rows = store.conn.execute(
        "SELECT * FROM decision_revisions ORDER BY cycle_id, revision_no").fetchall()
    legacy = [r for r in rows
              if r["revision_source"] == REVISION_SOURCE_LEGACY]
    unknown = [r for r in rows
               if r["revision_source"] == REVISION_SOURCE_LEGACY_UNKNOWN]
    assert len(legacy) == 1 and legacy[0]["cycle_id"] == "c1"
    assert legacy[0]["decision_hash"]            # real hash, computed not forged
    assert "AMD" in legacy[0]["orders_json"]
    # Two unmappable rows: the no-symbol row (c1) and the lost-cycle row.
    assert len(unknown) == 2
    assert all(r["decision_hash"] is None for r in unknown)
    assert all(r["orders_json"] is None for r in unknown)
    assert all(r["legacy_ref"] for r in unknown)  # original identifiers kept
    unknown_reasons = {r["cycle_id"]: r["legacy_ref"] for r in unknown}
    assert "c1" in unknown_reasons and "c-gone" in unknown_reasons
    # Unknown revisions are structurally barred from execution authorization;
    # the recovered legacy revision is not.
    for row in unknown:
        blockers = revision_authorization_blockers(dict(row))
        assert "revision_source:legacy_unknown" in blockers
        assert "missing_decision_hash" in blockers
    assert revision_authorization_blockers(dict(legacy[0])) == []


def test_unknown_revision_cannot_be_authorized_after_reopen(tmp_path):
    """1.7: the bar on `legacy_unknown` is a property of the stored row, not of
    the process that wrote it — reopening the database changes nothing."""
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE cycles (cycle_id TEXT PRIMARY KEY, as_of TEXT,
            approval_status TEXT, manager_summary TEXT);
        CREATE TABLE decisions (cycle_id TEXT, symbol TEXT, action TEXT,
            notional_usd REAL, limit_price REAL, conviction REAL, rationale TEXT);
        INSERT INTO decisions VALUES ('c-lost','AMD','buy',1000.0,NULL,NULL,'x');
    """)
    conn.commit()
    conn.close()

    first = TradingMemory(path)
    row = dict(first.conn.execute(
        "SELECT * FROM decision_revisions").fetchone())
    assert row["revision_source"] == REVISION_SOURCE_LEGACY_UNKNOWN

    reopened = TradingMemory(path)
    again = dict(reopened.conn.execute(
        "SELECT * FROM decision_revisions").fetchone())
    assert revision_authorization_blockers(again) == \
        revision_authorization_blockers(row)
    assert revision_authorization_blockers(again)  # still barred


def test_trades_and_fills_linkage_columns_nullable(tmp_path):
    """1.4: the linkage quadruple exists, is nullable, and legacy rows read NULL."""
    store = TradingMemory(tmp_path / "fresh.sqlite")
    assert {"cycle_id", "revision_no", "decision_hash", "approval_id"} <= \
        _columns(store.conn, "trades")
    assert {"cycle_id", "revision_no", "decision_hash", "approval_id"} <= \
        _columns(store.conn, "fills")
    store.conn.execute(
        "INSERT INTO trades (order_id, cycle_id, symbol, action, qty, order_type, "
        "status) VALUES ('o1','c1','AMD','buy',10,'market','submitted')")
    store.conn.commit()
    row = store.conn.execute(
        "SELECT revision_no, decision_hash, approval_id FROM trades").fetchone()
    assert (row["revision_no"], row["decision_hash"], row["approval_id"]) == \
        (None, None, None)


def test_decision_audit_tables_survive_retirement_and_ownership():
    """1.2/1.8: the five tables are Workflow memory — registered, not retired,
    and the write-target verification at init accepts them."""
    from ats.data.stores.ownership import WORKFLOW_MEMORY_TABLES
    from ats.memory.store import _DECISION_AUDIT_TABLES

    assert _DECISION_AUDIT_TABLES <= WORKFLOW_MEMORY_TABLES
    store = TradingMemory(":memory:")
    present = {r["name"] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert _DECISION_AUDIT_TABLES <= present          # not dropped post-init
