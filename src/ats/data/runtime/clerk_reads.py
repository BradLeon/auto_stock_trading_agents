"""Clerk's read-only broker adapter: every read goes through the governed read API.

Each broker read is one `BROKER_STATE` step (portfolio, fills, completed orders), so a step keeps its own
failure (B4); the decision chains the reports belong to are read as `DECISION_APPROVAL_CONTEXT`.
"""
from __future__ import annotations

from ...schemas.portfolio import PortfolioSnapshot


class UnavailableBrokerReads:
    """A failed connection cannot trigger fresh connections in later steps."""
    def __init__(self, reason):
        self.reason = reason

    def _unavailable(self):
        from ...broker import IBKRUnavailable

        raise IBKRUnavailable("broker unavailable: " + self.reason)

    get_portfolio = get_fills = completed_orders = _unavailable


def _raise_read_failure(packet) -> None:
    """Hand a failed read back as the broker's own error, so callers that tell TWS-down from other failures
    (performance snapshot, the Clerk run's unreachable check) still can."""
    from ...broker import IBKRUnavailable

    detail = ";".join(packet.gaps) or packet.status
    for gap in packet.gaps:
        if gap.startswith("input_unavailable:"):
            _, kind, message = gap.split(":", 2)
            if kind == "IBKRUnavailable":
                raise IBKRUnavailable(message)
            raise RuntimeError(f"{kind}: {message}")
    raise RuntimeError(detail)


def _read_input(*args, **kwargs):
    from ..consumer_api import read_input

    return read_input(*args, **kwargs)


class ClerkBrokerReads:
    def __init__(self, broker, store):
        self.broker = broker
        self.store = store

    def _read(self, step):
        return _read_input("clerk", "BROKER_STATE", scope={"kind": "broker", "step": step}, broker=self.broker)

    def get_portfolio(self):
        packet = self._read("portfolio")
        return PortfolioSnapshot.model_validate(packet.payload) if packet.payload else None

    def _reports(self, step):
        packet = self._read(step)
        if packet.status != "complete":
            _raise_read_failure(packet)
        from ...decision.repository import DecisionAuditRepository

        cycles = {str(row["cycle_id"]) for row in self.store.conn.execute(
            "SELECT DISTINCT cycle_id FROM trades WHERE cycle_id IS NOT NULL AND cycle_id != ''")}
        audit = DecisionAuditRepository(self.store)
        for cycle in sorted(cycles):
            _read_input("clerk", "DECISION_APPROVAL_CONTEXT", scope={"cycle_id": cycle}, audit=audit)
        return packet.payload

    def get_fills(self):
        return self._reports("fills")

    def completed_orders(self):
        return self._reports("completed_orders")
