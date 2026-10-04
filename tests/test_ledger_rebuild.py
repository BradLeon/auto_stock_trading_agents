"""Phase C group 5: performance & attribution rebuild (§15.4, tasks 5.1–5.5).

Rebuilds are deterministic, method-versioned, write ONLY derived read models,
skip when the facts hash matches, and fail without publishing partials.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import clerk, rebuild
from ats.memory import get_store
from ats.schemas.memory import TradeLogEntry

NOW = datetime.now(timezone.utc)
PERIOD = NOW.strftime("%Y-%m")
CYCLE = "chief-20260923-050000"
CHAIN = {"revision_no": 1, "decision_hash": "a" * 32,
         "approval_id": f"{CYCLE}:r1:approval"}


@pytest.fixture
def store():
    return get_store()


def _seed_system_trade(store, *, symbol="NVDA", pnl=150.0):
    store.save_trades([TradeLogEntry(
        order_id="", cycle_id=CYCLE, symbol=symbol, action="buy", qty=10.0,
        status="filled", submitted_at=NOW, avg_fill_price=100.0,
        realized_pnl=pnl, order_seq=0, **CHAIN)],
        cycle_id=CYCLE, source="chief")
    # realized_pnl normally arrives via reconciliation; seed it directly (the
    # trade upsert deliberately leaves it NULL on the write path).
    store.conn.execute("UPDATE trades SET realized_pnl = ? WHERE symbol = ?",
                       (pnl, symbol))
    store.conn.commit()


def _model(store, kind):
    return store.conn.execute(
        "SELECT * FROM ledger_read_models WHERE kind = ?", (kind,)).fetchone()


# --------------------------------------------------------------------------- #
# 5.1 / 5.3 — deterministic rebuild with version + rebuild time
# --------------------------------------------------------------------------- #

def test_rebuild_performance_records_version_and_time(store):
    _seed_system_trade(store)
    out = rebuild.rebuild_performance(store, period=PERIOD)
    assert out["status"] == "rebuilt"
    row = _model(store, "performance")
    assert row["method_version"] == rebuild.METHOD_VERSION
    assert row["rebuilt_at"] is not None
    assert row["source_facts_hash"] == clerk.source_facts_hash(store)
    payload = __import__("json").loads(row["payload"])
    assert payload["system_trades"] == 1
    assert payload["system_realized_pnl"] == 150.0


def test_rebuild_is_deterministic_same_facts_same_payload(store):
    _seed_system_trade(store)
    out1 = rebuild.rebuild_performance(store, period=PERIOD)
    out2 = rebuild.rebuild_attribution(store, period=PERIOD)
    # delete the derived rows entirely and rebuild from the same facts
    store.conn.execute("DELETE FROM ledger_read_models")
    store.conn.commit()
    out1b = rebuild.rebuild_performance(store, period=PERIOD)
    out2b = rebuild.rebuild_attribution(store, period=PERIOD)
    assert out1["payload"] == out1b["payload"]
    assert out2["payload"] == out2b["payload"]


# --------------------------------------------------------------------------- #
# 5.2 — attribution reuses the existing analytics formulas
# --------------------------------------------------------------------------- #

def test_attribution_reuses_analytics_and_is_per_symbol(store):
    _seed_system_trade(store, symbol="NVDA", pnl=150.0)
    _seed_system_trade(store, symbol="MSFT", pnl=-50.0)
    out = rebuild.rebuild_attribution(store, period=PERIOD)
    assert out["status"] == "rebuilt"
    assert out["payload"]["by_symbol"]["NVDA"]["realized_pnl"] == 150.0
    assert out["payload"]["by_symbol"]["MSFT"]["realized_pnl"] == -50.0
    assert out["payload"]["total_realized_pnl"] == 100.0


# --------------------------------------------------------------------------- #
# 5.4 — a new method version coexists with the old one
# --------------------------------------------------------------------------- #

def test_method_version_change_coexists(store):
    _seed_system_trade(store)
    rebuild.rebuild_performance(store, period=PERIOD, method_version="v1")
    rebuild.rebuild_performance(store, period=PERIOD, method_version="v2")
    versions = [r["method_version"] for r in store.conn.execute(
        "SELECT method_version FROM ledger_read_models WHERE kind='performance' "
        "ORDER BY method_version")]
    assert versions == ["v1", "v2"]


# --------------------------------------------------------------------------- #
# 5.5 — failure writes nothing and publishes no partial model
# --------------------------------------------------------------------------- #

def test_failed_rebuild_writes_nothing_and_leaves_facts_alone(store, monkeypatch):
    _seed_system_trade(store)
    facts_before = store.conn.execute(
        "SELECT client_order_id, status, avg_fill_price, realized_pnl FROM trades"
    ).fetchall()

    import ats.trader.analytics as analytics

    def _boom(*a, **k):
        raise RuntimeError("analytics exploded")

    monkeypatch.setattr(analytics, "summarize", _boom)
    out = rebuild.rebuild_performance(store, period=PERIOD)
    assert out["status"] == "failed" and "analytics exploded" in out["error"]
    # no read model row was published
    assert _model(store, "performance") is None
    # original facts untouched
    facts_after = store.conn.execute(
        "SELECT client_order_id, status, avg_fill_price, realized_pnl FROM trades"
    ).fetchall()
    assert [tuple(r) for r in facts_before] == [tuple(r) for r in facts_after]


# --------------------------------------------------------------------------- #
# 4.5 integration — the skip condition actually skips
# --------------------------------------------------------------------------- #

def test_rebuild_skips_when_facts_hash_matches(store):
    _seed_system_trade(store)
    out1 = rebuild.rebuild_performance(store, period=PERIOD)
    assert out1["status"] == "rebuilt"
    first_rebuilt_at = _model(store, "performance")["rebuilt_at"]
    out2 = rebuild.rebuild_performance(store, period=PERIOD)
    assert out2["status"] == "skipped"
    assert _model(store, "performance")["rebuilt_at"] == first_rebuilt_at


# --------------------------------------------------------------------------- #
# 5.6 — CLI entries exist and only write derived models
# --------------------------------------------------------------------------- #

def test_cli_rebuild_and_gaps_entries(store, capsys, monkeypatch):
    import ats.runtime.cli as cli_mod

    _seed_system_trade(store)
    # rebuild via the CLI dispatch path
    argv = ["prog", "clerk", "rebuild", "--period", PERIOD]
    monkeypatch.setattr("sys.argv", argv)
    rc = cli_mod.main()
    assert rc == 0
    assert _model(store, "performance") is not None
    assert _model(store, "attribution") is not None

    # gaps listing on a clean ledger
    argv = ["prog", "clerk", "gaps"]
    monkeypatch.setattr("sys.argv", argv)
    rc = cli_mod.main()
    assert rc == 0
    assert "无未清偿审计异常" in capsys.readouterr().out
