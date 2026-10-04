"""Task 4.4: five remaining roles, read-only live inputs and isolated audit.

No order submission, production approval, collection queue or dataset writes.
Live results are captured once in memory and reused for fault/recovery drills.
Only counts/statuses/hashes are exported, never account values or raw quotes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from ats.data.consumer_api import _json, read_input
from ats.data.runtime.broker import broker_state
from ats.data.runtime.market_data import fetch_close_history_many
from ats.data.runtime.options import fetch_runtime
from ats.decision.repository import DecisionAuditRepository
from ats.execution.authorization import build_authorization, validate_authorization
from ats.execution.state_api import get_internal_state
from ats.memory.store import TradingMemory
from ats.schemas.memory import PerformanceRecord


def digest(value):
    return sha256(json.dumps(_json(value), sort_keys=True).encode()).hexdigest()


def comparable(value):
    value = _json(value)
    if isinstance(value, dict) and value.get("schema_version") == "runtime-broker-v1":
        # The acquisition instant is stable; envelope assembly time is not.
        return {k: v for k, v in value.items() if k != "queried_at"}
    return value


class CapturedBroker:
    def __init__(self, portfolio, orders, fills):
        self.portfolio, self.orders, self.fills = portfolio, orders, fills

    def get_portfolio(self):
        return self.portfolio

    def completed_orders(self):
        return self.orders

    def get_fills(self):
        return self.fills


def live_broker():
    from ib_async import IB
    from ats.broker.ibkr import IBKRBroker

    class ReadonlyIBKR(IBKRBroker):
        @contextmanager
        def session(self, timeout=6):
            yield self._ib

    broker = ReadonlyIBKR(client_id=9044)
    ib = IB()
    try:
        # readonly=True suppresses order synchronization; only read methods
        # are invoked. No placeOrder/cancelOrder/clerk writer is reachable here.
        ib.connect(broker.host, broker.port, clientId=broker.client_id,
                   timeout=8, readonly=True)
        broker._ib = ib
        return CapturedBroker(broker.get_portfolio(), broker.completed_orders(), broker.get_fills())
    finally:
        ib.disconnect()


def readonly_store(path):
    store = object.__new__(TradingMemory)
    store.conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    store.conn.row_factory = sqlite3.Row
    store.conn.execute("PRAGMA query_only=ON")
    return store


def drill(role, product, scope, native, dependency, **kwargs):
    """Fault only wrapper boundary; fallback and recovery run real owner API."""
    packet = read_input(role, product, scope=scope, **kwargs)
    assert packet.status == "complete", (role, product, packet.status, packet.gaps)
    expected = comparable(native())
    assert comparable(packet.payload) == expected, (role, product, "native_mismatch")
    with patch(dependency, side_effect=RuntimeError("isolated_read_fault")):
        failed = read_input(role, product, scope=scope, **kwargs)
    assert failed.status == "unavailable" and failed.payload is None
    fallback = comparable(native())
    restored = read_input(role, product, scope=scope, **kwargs)
    assert restored.status == "complete" and fallback == comparable(restored.payload) == comparable(packet.payload)
    return {"role": role, "product": product, "status": "passed",
            "positive": packet.status, "fault": failed.status,
            "fallback_equal": True, "restored_equal": True,
            "payload_hash": digest(comparable(packet.payload)), "scope_hash": digest(scope),
            "source_as_of": packet.source_as_of, "read_only": True}


def isolated(repo, cycle, now):
    repo.create_cycle(cycle_id=cycle, trigger_source="manual")
    rev = repo.append_revision(cycle_id=cycle, orders=[], rationale="isolated API validation; no trade")
    repo.record_review(review_id="isolated-review", cycle_id=cycle,
                       revision_no=rev["revision_no"], decision_hash=rev["decision_hash"],
                       ruleset_version="isolated-rules", portfolio_snapshot_id="isolated-snapshot",
                       market_as_of=now.isoformat(), verdict="approved")
    repo.record_approval(approval_id="isolated-approval", cycle_id=cycle,
                         revision_no=rev["revision_no"], decision_hash=rev["decision_hash"],
                         decision="approved", reviewer="ISOLATED-NOT-PRODUCTION-BOSS",
                         idempotency_key="isolated-key")


def run(internal_path):
    result = {"schema_version": "task4-runtime-roles-v1", "checked_at": datetime.now(timezone.utc).isoformat(),
              "production_qualification_granted": False, "orders_submitted": 0,
              "production_routes_changed": False, "runtime_persisted": False,
              "proofs": [], "live": {}, "production_internal": {}, "checks": []}
    # Once per run, never once per consumer/product/test.
    market = fetch_close_history_many(["NVDA"])
    options = fetch_runtime("NVDA")
    try:
        broker = live_broker()
        envelope = broker_state(broker)
        result["live"]["broker"] = {"status": envelope["status"], "source_as_of": envelope["source_as_of"],
                                       "orders_count": len(broker.orders), "fills_count": len(broker.fills)}
    except Exception as exc:
        broker = None
        result["live"]["broker"] = {"status": "unavailable", "reason": type(exc).__name__}
    result["live"]["market"] = {symbol: {"status": r.status, "bar_as_of": str(r.bar_as_of),
                                           "count": len(r.closes)} for symbol, r in market.items()}
    result["live"]["options"] = {k: options[k] for k in ("status", "source_as_of", "reason")}
    with patch("ats.data.runtime.market_data.fetch_close_history_many", return_value=market), \
            patch("ats.data.runtime.options.fetch_runtime", return_value=options), \
            tempfile.TemporaryDirectory(prefix="task44-") as directory:
        store = TradingMemory(Path(directory) / "isolated.sqlite")
        now = datetime.now(timezone.utc)
        store.save_performance(PerformanceRecord(cycle_id="isolated", as_of=now,
                                                net_liquidation=1000, daily_pnl=0, cumulative_pnl=0))
        store.conn.execute("INSERT OR REPLACE INTO journal_meta(key,value) VALUES (?,?)",
                           ("last_reconcile_at", now.isoformat()))
        store.conn.commit()
        audit = DecisionAuditRepository(store)
        isolated(audit, "isolated-cycle", now)
        for role in ("technical", "risk"):
            result["proofs"].append(drill(role, "MARKET_DATA", {"entity": "NVDA"},
                lambda: market, "ats.data.runtime.market_data.fetch_close_history_many"))
        # Options is a separate runtime branch, not invented as a complete quote.
        option_packet = read_input("technical", "MARKET_DATA", scope={"entity": "NVDA", "kind": "options"})
        assert option_packet.status == options["status"] and option_packet.payload == options["payload"]
        with patch("ats.data.runtime.options.fetch_runtime", side_effect=RuntimeError("isolated_fault")):
            assert read_input("technical", "MARKET_DATA", scope={"entity": "NVDA", "kind": "options"}).status == "unavailable"
        result["checks"].append("options_status_payload_fault_verified")
        for role, product in (("chief", "PORTFOLIO_DATA"), ("chief", "HISTORY_DATA"), ("risk", "PORTFOLIO_DATA")):
            def state_payload(product=product):
                state = get_internal_state(store)
                return state.portfolio if product == "PORTFOLIO_DATA" else {
                    "trades": state.trades, "fills": state.fills,
                    "performance": state.performance, "attribution": state.attribution}
            result["proofs"].append(drill(role, product, {}, state_payload,
                "ats.execution.state_api.get_internal_state", store=store))
        from ats.config import get_config
        result["proofs"].append(drill("risk", "RISK_RULES", {}, lambda: get_config().app.risk,
                                      "ats.config.get_config"))
        scope = {"cycle_id": "isolated-cycle", "snapshot_as_of": now.isoformat()}
        def authorization():
            auth = build_authorization(audit, scope["cycle_id"])
            assert not validate_authorization(audit, auth, snapshot_as_of=now)
            return auth
        result["proofs"].append(drill("trader", "APPROVED_EXECUTION_AUTHORIZATION", scope,
            authorization, "ats.execution.authorization.build_authorization", audit=audit))
        result["proofs"].append(drill("clerk", "DECISION_APPROVAL_CONTEXT", {"cycle_id": "isolated-cycle"},
            lambda: audit.read_chain("isolated-cycle"), "ats.decision.repository.DecisionAuditRepository.read_chain", audit=audit))
        reopened = readonly_store(Path(directory) / "isolated.sqlite")
        try:
            restored_audit = DecisionAuditRepository(reopened)
            assert read_input("trader", "APPROVED_EXECUTION_AUTHORIZATION", scope=scope,
                              audit=restored_audit).payload == _json(authorization())
            assert read_input("clerk", "DECISION_APPROVAL_CONTEXT", scope={"cycle_id": "isolated-cycle"},
                              audit=restored_audit).payload == audit.read_chain("isolated-cycle")
            assert read_input("chief", "PORTFOLIO_DATA", scope={}, store=reopened).status == "complete"
            assert read_input("trader", "APPROVED_EXECUTION_AUTHORIZATION", scope={"cycle_id": "absent"},
                              audit=restored_audit).status == "unavailable"
            assert read_input("clerk", "DECISION_APPROVAL_CONTEXT", scope={"cycle_id": "absent"},
                              audit=restored_audit).status == "no_coverage"
        finally:
            reopened.conn.close()
        result["checks"].extend(["readonly_reopen_approval_context_account_preserved", "missing_cycle_explicit"])
        if broker:
            result["proofs"].append(drill("clerk", "BROKER_STATE", {}, lambda: broker_state(broker),
                "ats.data.runtime.broker.broker_state", broker=broker))
            broken = CapturedBroker(broker.portfolio, None, broker.fills)
            partial = read_input("clerk", "BROKER_STATE", scope={}, broker=broken)
            assert partial.status == "partial" and partial.payload["orders"] is None
            empty = CapturedBroker(broker.portfolio, [], [])
            assert read_input("clerk", "BROKER_STATE", scope={}, broker=empty).status == "complete"
            result["checks"].extend(["broker_failed_orders_partial", "healthy_empty_orders_fills_complete"])
        # Negative states must never be presented as positive acceptance.
        old_scope = {**scope, "snapshot_as_of": (now - timedelta(seconds=120)).isoformat()}
        assert read_input("trader", "APPROVED_EXECUTION_AUTHORIZATION", scope=old_scope, audit=audit).status == "rejected"
        store.record_ledger_exception(kind="broken_link", subject_key="isolated-gap")
        assert read_input("chief", "PORTFOLIO_DATA", scope={}, store=store).status == "partial"
        assert read_input("chief", "HISTORY_DATA", scope={}, store=store).status == "partial"
        result["checks"].extend(["stale_authorization_rejected", "broken_ledger_remains_partial"])
        # Legacy metadata null must not block account projection, but numeric
        # corruption must fail closed. Modify only isolated test database.
        row = store.conn.execute("SELECT rowid,payload FROM performance ORDER BY rowid DESC LIMIT 1").fetchone()
        payload = json.loads(row["payload"])
        payload["invalidation_source"] = None
        store.conn.execute("UPDATE performance SET payload=? WHERE rowid=?", (json.dumps(payload), row["rowid"]))
        assert get_internal_state(store).portfolio
        payload["net_liquidation"] = "not-a-number"
        store.conn.execute("UPDATE performance SET payload=? WHERE rowid=?", (json.dumps(payload), row["rowid"]))
        assert read_input("chief", "PORTFOLIO_DATA", scope={}, store=store).status == "unavailable"
        result["checks"].extend(["legacy_metadata_null_projection_readable", "invalid_account_value_rejected"])
        store.conn.commit()
        audit.append_revision(cycle_id="isolated-cycle", orders=[], rationale="isolated superseding revision")
        assert read_input("trader", "APPROVED_EXECUTION_AUTHORIZATION", scope=scope, audit=audit).status == "unavailable"
        result["checks"].append("superseded_revision_not_authorized")
        store.conn.close()
    live_store = readonly_store(internal_path)
    try:
        for role, product in (("chief", "PORTFOLIO_DATA"), ("chief", "HISTORY_DATA"), ("risk", "PORTFOLIO_DATA")):
            packet = read_input(role, product, scope={}, store=live_store)
            state = get_internal_state(live_store)
            result["production_internal"][role + ":" + product] = {
                "status": packet.status, "source_as_of": packet.source_as_of,
                "completeness": state.completeness.model_dump(), "payload_hash": digest(packet.payload)}
        result["production_internal"]["cycles_count"] = live_store.conn.execute("SELECT COUNT(*) FROM decision_cycles").fetchone()[0]
    finally:
        live_store.conn.close()
    result["passed"] = (len(result["proofs"]) == 9 and broker is not None
                         and options["status"] in {"complete", "partial"})
    from ats.config import REPO_ROOT
    paths = ("src/ats/data/consumer_api.py", "src/ats/data/runtime/broker.py",
             "src/ats/execution/state_api.py", "config/data/target_dataflow_coverage.yaml",
             "scripts/verify_dataflow_runtime_roles.py")
    result["code_config_fingerprints"] = {p: sha256((REPO_ROOT / p).read_bytes()).hexdigest() for p in paths}
    result["isolated_audit_not_production_approval"] = True
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--internal-cache", type=Path, default=Path("var/ats.sqlite"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps({"passed": False, "status": "running_or_incomplete"}) + "\n")
    report = run(args.internal_cache)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
