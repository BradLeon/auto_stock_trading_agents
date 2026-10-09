"""Explicit shadow business execution: real broker refusal, independent intents."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from ..workflow import shadow_ledger as ledger

_CURRENT: ContextVar[object | None] = ContextVar("shadow_business_execution", default=None)


class ShadowBroker:
    def __init__(self, run_id, store):
        self.run_id, self.store = run_id, store
        self.authorization, self.execution_audit = None, []
        self.path = Path(ledger.default_shadow_ledger_path()).resolve()

    def assert_active(self):
        from ..workflow.cutover import CLERK_PUBLICATION
        from ..workflow.cutover_wiring import assert_write_destination
        from ..workflow.isolation import verified_isolation_root
        from .broker_write_guard import assert_broker_writes_prohibited

        root = verified_isolation_root()
        if _CURRENT.get() is not self or root is None:
            raise PermissionError("shadow_execution_requires_complete_isolation")
        if not self.path.is_relative_to(root) or self.path in {
                Path(os.environ[k]).resolve() for k in ("ATS_DB_PATH", "ATS_SHADOW_DB_PATH", "ATS_DATA_DB_PATH")}:
            raise PermissionError("shadow_ledger_destination_not_independent")
        if self.path != Path(ledger.default_shadow_ledger_path()).resolve():
            raise PermissionError("shadow_ledger_destination_changed")
        assert_broker_writes_prohibited(operation="shadow_execution", caller=self.run_id)
        assert_write_destination(self.store, CLERK_PUBLICATION)

    def place_orders(self, items, cycle_id, *, revision_no=0, chain=None,
                     execution_check=None, order_sequences=None):
        from ..broker.ibkr import IBKRBroker
        from ..schemas.memory import TradeLogEntry
        from . import broker_write_guard as guard
        from . import route_registry as registry

        self.assert_active()
        if execution_check is None:
            raise PermissionError("shadow_execution_check_required")
        chain = chain or {}
        route = registry.read_state()
        rows = []
        for sequence, (decision, quantity) in zip(order_sequences or range(len(items)), items):
            self.assert_active()
            execution_check(decision, quantity)
            self.assert_active()
            if not self.authorization or not chain.get("decision_hash") or not chain.get("approval_id"):
                raise PermissionError("shadow_authorization_chain_missing")
            identity = hashlib.sha256(json.dumps([self.run_id, cycle_id, revision_no, sequence]).encode()).hexdigest()
            intent = ledger.ShadowIntent(identity, self.run_id, cycle_id, decision.symbol, decision.action,
                quantity, revision_no, sequence, chain["decision_hash"], chain["approval_id"],
                route.route_id, route.generation)
            ledger.record_business_intent(intent, provenance={"order": decision.model_dump(mode="json"),
                "quantity": quantity, "chain": chain, "authorization": self.authorization,
                "route": route.as_row()}, path=self.path)
            try:
                # The production broker facade checks the process prohibition
                # BEFORE session(), grants, contract lookup or a network write.
                receipts = IBKRBroker().place_orders([(decision, quantity)], cycle_id,
                    revision_no=revision_no, chain=chain, order_sequences=[sequence],
                    execution_check=execution_check)
            except guard.BrokerWriteProhibited as exc:
                if exc.reason_code not in {guard.REASON_SHADOW_RUN, guard.REASON_ISOLATED, guard.REASON_EXPLICIT}:
                    raise
                refusal = next(r.as_row() for r in reversed(guard.refusals()) if r.refusal_id == exc.refusal_id)
                ledger.record_submit_attempt(intent_id=identity, accepted=False, refusal_code=exc.reason_code,
                    refusal_id=exc.refusal_id, detail={"pid": os.getpid(), "guard": refusal,
                    "execution_price_audit": self.execution_audit}, path=self.path)
                rows.append(TradeLogEntry(order_id="", cycle_id=cycle_id, symbol=decision.symbol,
                    action=decision.action, qty=quantity, revision_no=revision_no, order_seq=sequence,
                    order_type=decision.order_type, limit_price=decision.limit_price,
                    isolation_root=str(self.path.parent), shadow_run_id=self.run_id,
                    status="rejected", error=f"shadow broker write prohibited:{exc.refusal_id}", submitted_at=datetime.now(UTC), rationale=decision.rationale,
                    decision_hash=chain["decision_hash"], approval_id=chain["approval_id"]))
            else:
                ledger.record_submit_attempt(intent_id=identity, accepted=True,
                    detail={"unexpected": True, "receipts": [r.model_dump(mode="json") for r in receipts]},
                    path=self.path)
                raise ledger.ShadowLedgerWriteRefused("unexpected unblocked shadow broker call; stop batch")
        return rows

    def get_fills(self):
        self.assert_active()
        return []


@contextmanager
def shadow_execution(*, run_id, store):
    """Select refusal transport only inside an existing isolated verification."""
    from .simulation import selected_simulation

    if not run_id or selected_simulation() is not None or _CURRENT.get() is not None:
        raise PermissionError("shadow_run_id_required_or_transport_already_selected")
    broker = ShadowBroker(run_id, store)
    token = _CURRENT.set(broker)
    try:
        broker.assert_active()
        yield broker
    finally:
        _CURRENT.reset(token)


def selected_shadow():
    broker = _CURRENT.get()
    if broker is not None:
        broker.assert_active()
    return broker


def publish_shadow_results(entries, fills=()):
    """Explicit business publication: confirm receipts, never redirect trades."""
    broker = selected_shadow()
    if broker is None:
        return False
    if fills or any(e.status not in {"rejected", "cancelled"} for e in entries):
        raise ledger.ShadowLedgerWriteRefused("unexpected shadow execution result; stop batch")
    intents = ledger.intents(run_id=broker.run_id, path=broker.path) if broker.path.exists() else []
    for entry in entries:
        if entry.shadow_run_id != broker.run_id and entry.error.startswith("shadow broker write prohibited:"):
            raise ledger.ShadowLedgerWriteRefused("shadow result run identity mismatch")
        if entry.error.startswith("shadow broker write prohibited:") and not any(
                r["cycle_id"] == entry.cycle_id and r["revision_no"] == entry.revision_no
                and r["sequence"] == entry.order_seq and r["symbol"] == entry.symbol
                and r["quantity"] == entry.qty and r["decision_hash"] == entry.decision_hash
                and r["approval_id"] == entry.approval_id for r in intents):
            raise ledger.ShadowLedgerWriteRefused("shadow result has no bound business intent")
    return True


def assert_trade_publication(store, entries=()):
    """Guard at the actual INSERT/UPDATE point, including stale store handles."""
    from ..workflow.cutover import CLERK_PUBLICATION
    from ..workflow.cutover_wiring import assert_write_destination
    from ..workflow.runtime_reads import current_read_context

    filename = next((row[2] for row in store.conn.execute("PRAGMA database_list")
                     if row[1] == "main"), "")
    for entry in entries:
        origin = entry.get("isolation_root", "") if isinstance(entry, dict) else getattr(entry, "isolation_root", "")
        shadow_id = entry.get("shadow_run_id", "") if isinstance(entry, dict) else getattr(entry, "shadow_run_id", "")
        error = entry.get("error", "") if isinstance(entry, dict) else entry.error
        if shadow_id or error.startswith("shadow broker write prohibited:"):
            ledger.refuse_trades_write(run_id=shadow_id)
        if origin and filename and not Path(filename).resolve().is_relative_to(Path(origin).resolve()):
            raise ledger.ShadowLedgerWriteRefused("isolated result cannot publish outside its original isolation root")
    context = current_read_context()
    if _CURRENT.get() is not None or os.environ.get("ATS_RUN_MODE") == "shadow" \
            or (context is not None and context.mode == "shadow"):
        ledger.refuse_trades_write(run_id=getattr(_CURRENT.get(), "run_id", ""))
    assert_write_destination(store, CLERK_PUBLICATION)
