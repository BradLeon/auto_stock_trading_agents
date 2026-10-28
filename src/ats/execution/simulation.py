"""Explicit no-network broker for full isolated business executions.

The process prohibition remains armed. This transport has no IBKR delegation,
socket or configurable broker factory; naming an environment 'paper' cannot select it.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from ats.workflow.evaluation_clock import now as evaluation_now

from ..schemas.memory import TradeLogEntry

_CURRENT: ContextVar[object | None] = ContextVar("isolated_simulation_broker", default=None)


def _assert_isolated(store):
    from ..workflow.cutover import CLERK_PUBLICATION
    from ..workflow.cutover_wiring import assert_write_destination
    from ..workflow.isolation import verified_isolation_root
    from .broker_write_guard import assert_broker_writes_prohibited

    if verified_isolation_root() is None:
        raise PermissionError("simulation_requires_complete_isolation")
    assert_broker_writes_prohibited(operation="simulation", caller="FakeBroker")
    assert_write_destination(store, CLERK_PUBLICATION)


class FakeBroker:
    """In-memory acknowledgements/fills with the real route/grant/idempotency gates."""

    def __init__(self, store, account):
        self.store, self.account = store, account
        self.accepted = []
        self.fills = []
        self.placed = []

    def assert_active(self):
        if type(self) is not FakeBroker or _CURRENT.get() is not self:
            raise PermissionError("simulation_transport_not_active")
        _assert_isolated(self.store)

    def place_orders(self, items, cycle_id, *, revision_no=0, chain=None,
                     execution_check=None, order_sequences=None):
        from ..broker.ibkr import IBKRBroker, order_ref
        from . import broker_write_guard as guard
        from . import route_registry as rr
        from .broker_write_guard import check_grant
        from .route_arbitration import authority_lock

        self.assert_active()
        chain = chain or {}
        entries = []
        for seq, (decision, qty) in zip(order_sequences or range(len(items)), items):
            with authority_lock(rr.default_registry_path()), guard._STATE.lock:
                self.assert_active()
                check_grant(operation="simulated_placeOrder", caller="FakeBroker",
                            state_reader=rr.read_state, freeze_reader=rr.read_freeze,
                            account=self.account, environment="paper", require_binding=True)
                state = rr.read_state()
                if state.environment != "paper":
                    raise PermissionError("simulation_requires_paper_route")
                if execution_check is None:
                    raise PermissionError("simulation_execution_check_required")
                execution_check(decision, qty)
                ref = order_ref(cycle_id, revision_no, seq, decision.symbol)
                payload = {"decision": decision.model_dump(mode="json"), "qty": qty,
                           "account": self.account, "chain": chain}
                import hashlib
                import json

                digest = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
                intent = IBKRBroker._intent_id(cycle_id, revision_no, seq)
                rr.reserve_submission(intent, digest, state, order_ref=ref, cycle_id=cycle_id,
                                      revision_no=revision_no, sequence=seq)
                order_id = f"sim-{len(self.accepted) + 1}"
                from ..workflow.isolation import verified_isolation_root

                row = TradeLogEntry(
                    isolation_root=str(verified_isolation_root()),
                    order_id=order_id, perm_id=order_id, order_ref=ref, cycle_id=cycle_id,
                    revision_no=revision_no, order_seq=seq, symbol=decision.symbol,
                    action=decision.action, qty=qty, order_type=decision.order_type,
                    limit_price=decision.limit_price, submitted_at=evaluation_now(UTC),
                    status="submitted", rationale=decision.rationale,
                    decision_hash=chain.get("decision_hash", ""),
                    approval_id=chain.get("approval_id", ""))
                self.accepted.append(row)
                self.placed.append((decision.model_copy(deep=True), qty))
                rr.record_submission_result(intent, "submitted", order_id)
                entries.append(row)
        return entries

    def get_fills(self):
        self.assert_active()
        return list(self.fills)

    def simulate_fill(self, order_id, *, shares, price, at=None):
        """Generate partial/late synthetic reports after a real simulated acceptance."""
        import math

        from ..broker.ibkr import IBKRBroker
        from . import route_registry as rr

        self.assert_active()
        row = next(item for item in self.accepted if item.order_id == order_id)
        already = sum(item["shares"] for item in self.fills if item["order_id"] == order_id)
        if not all(math.isfinite(v) and v > 0 for v in (shares, price)) or shares + already > row.qty:
            raise ValueError("invalid_simulated_fill")
        stamp = at or evaluation_now(UTC)
        self.fills.append({"isolation_root": row.isolation_root,
                           "exec_id": f"{order_id}-fill-{len(self.fills) + 1}",
                           "symbol": row.symbol, "side": "BOT" if row.action in {"buy", "add"} else "SLD",
                           "shares": shares, "price": price, "time": stamp.isoformat(),
                           "order_id": order_id, "perm_id": row.perm_id, "order_ref": row.order_ref,
                           "commission": 0, "realized_pnl": None})
        row.status = "filled" if shares + already == row.qty else "partial"
        row.avg_fill_price = sum(f["shares"] * f["price"] for f in self.fills
                                 if f["order_id"] == order_id) / (shares + already)
        row.filled_at = stamp if row.status == "filled" else None
        rr.record_submission_result(IBKRBroker._intent_id(row.cycle_id, row.revision_no, row.order_seq),
                                    row.status, order_id)


@contextmanager
def simulated_execution(*, store, account):
    """Select the only allowed test transport, within an existing isolated_run."""
    _assert_isolated(store)
    broker = FakeBroker(store, account)
    token = _CURRENT.set(broker)
    try:
        yield broker
    finally:
        from .broker_write_guard import revoke_grant

        # A test grant cannot survive return to a production environment, even
        # if the caller later points the same process at a matching route name.
        revoke_grant("isolated simulation exited")
        _CURRENT.reset(token)


def selected_simulation():
    broker = _CURRENT.get()
    if broker is not None:
        broker.assert_active()
    return broker
