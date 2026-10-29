"""tasks 2.6 — no approval or ledger code reads the legacy `cycles`/`decisions` tables.

The paired approval/ledger switch (design D4, plan B) is only safe if the audit tables are the only
decision record any reader uses. The audit script proves that statically; the doctor tests prove the
report that used to read the legacy tables now answers from the audit.
"""

from __future__ import annotations

from pathlib import Path

from test_journal_doctor import _find, _trade  # noqa: F401  (helpers only; no test is re-collected)

from ats.decision.repository import DecisionAuditRepository
from ats.journal import doctor
from ats.memory import get_store
from ats.workflow import decision_read_audit as audit

SRC = Path(__file__).resolve().parents[1] / "src" / "ats"


def test_no_reader_outside_the_allowlist_reads_the_legacy_decision_tables():
    assert [r.render() for r in audit.scan_tree(SRC)] == []


def test_every_allowed_reader_still_exists_and_still_reads():
    """An allowlist entry that no longer matches anything is a stale exemption, not a safety net."""
    found = {f"{r.path}::{r.function}" for r in audit.scan_tree(SRC, allowed={})}
    found |= {r.path for r in audit.scan_tree(SRC, allowed={})}
    for entry in audit.ALLOWED_READERS:
        assert entry in found, f"allowlist entry {entry!r} no longer reads a legacy table"


def test_a_select_or_join_on_a_legacy_table_is_reported_with_its_function():
    source = '''
def approvals(conn):
    return conn.execute("SELECT approval_status FROM cycles WHERE cycle_id = ?")

def chain(conn):
    return conn.execute("SELECT 1 FROM trades t JOIN decisions d ON d.cycle_id = t.cycle_id")

def fstring(conn, x):
    return conn.execute(f"select * from decisions where symbol = '{x}'")
'''
    found = audit.scan_source(source, "x.py")
    assert [(r.function, r.table) for r in found] == [
        ("approvals", "cycles"), ("chain", "decisions"), ("fstring", "decisions")]


def test_audit_tables_and_legacy_writes_are_not_reads():
    source = '''
def ok(conn):
    conn.execute("SELECT * FROM decision_cycles")
    conn.execute("SELECT * FROM boss_approvals")
    conn.execute("UPDATE cycles SET approval_status = ? WHERE cycle_id = ?")
    conn.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)")
'''
    assert audit.scan_source(source, "x.py") == []


def test_a_function_exemption_does_not_cover_the_rest_of_its_file(tmp_path):
    (tmp_path / "decision").mkdir()
    (tmp_path / "decision" / "repository.py").write_text(
        'def mirror_revision_to_legacy(c):\n    c.execute("SELECT 1 FROM decisions")\n'
        'def other(c):\n    c.execute("SELECT 1 FROM cycles")\n')
    found = audit.scan_tree(tmp_path)
    assert [(r.function, r.table) for r in found] == [("other", "cycles")]


# --- the report that used to read the legacy tables now reads the audit ------------------------------- #
def _cycle_with_order(conn, cycle="c1", symbol="GOOG", *, approved=True):
    repo = DecisionAuditRepository(get_store())
    repo.create_cycle(cycle_id=cycle, trigger_source="chief")
    repo.append_revision(cycle_id=cycle, rationale="r", orders=[
        {"symbol": symbol, "action": "trim", "notional_usd": 1000, "rationale": "r"}])
    if approved:
        repo.record_approval(approval_id=f"{cycle}:a", cycle_id=cycle, revision_no=1,
                             decision_hash="h", decision="approved", reviewer="t", idempotency_key=f"{cycle}:k")


def test_doctor_judges_approval_from_the_audit_not_from_cycles():
    conn = get_store().conn
    _cycle_with_order(conn, "c1", "GOOG", approved=True)
    _cycle_with_order(conn, "c2", "ASML", approved=False)
    _trade(conn, cycle="c1", symbol="GOOG", order_id="1")
    _trade(conn, cycle="c2", symbol="ASML", order_id="2")
    f = _find(doctor.collect(conn), "1.", "下单的周期都有审批记录")
    assert f.ok is False and f.value.startswith("1/2")


def test_doctor_chain_joins_audit_orders_to_trades():
    conn = get_store().conn
    _cycle_with_order(conn, "c1", "GOOG")
    _cycle_with_order(conn, "c3", "NVDA")                      # decided, never traded
    _trade(conn, cycle="c1", symbol="GOOG", order_id="1")
    _trade(conn, cycle="c9", symbol="TSLA", order_id="9")      # traded with no audit decision
    sections = doctor.collect(conn)
    assert _find(sections, "3.", "有决策但无订单").value == "1"
    assert _find(sections, "3.", "有订单但无决策").value == "1"
