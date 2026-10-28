"""Clerk's read-only broker adapter, retaining source IDs and local audit chains."""
from __future__ import annotations

from ...schemas.portfolio import PortfolioSnapshot
from ...workflow.consumer_reads import record_read
from .broker import portfolio_snapshot


class UnavailableBrokerReads:
    """A failed connection cannot trigger fresh connections in later steps."""
    def __init__(self, reason):
        self.reason = reason

    def _unavailable(self):
        from ...broker import IBKRUnavailable

        raise IBKRUnavailable("broker unavailable: " + self.reason)

    get_portfolio = get_fills = completed_orders = _unavailable


class ClerkBrokerReads:
    def __init__(self, broker, store):
        self.broker = broker
        self.store = store

    def _gate(self):
        from ...workflow.runtime_reads import current_read_context, gate_read

        context = current_read_context()
        if context:
            gate_read(context.identity)

    def get_portfolio(self):
        self._gate()
        packet = portfolio_snapshot(self.broker)
        record_read("clerk", "ats.data.runtime.broker.portfolio_snapshot", status=packet["status"],
                    refs=[f"broker-portfolio:{packet['source_as_of']}"] if packet["source_as_of"] else [])
        return PortfolioSnapshot.model_validate(packet["payload"]) if packet["payload"] else None

    def _reports(self, method, id_field):
        self._gate()
        from ...decision.repository import DecisionAuditRepository

        try:
            rows = getattr(self.broker, method)()
        except Exception as exc:
            record_read("clerk", f"ats.data.runtime.clerk_reads.{method}",
                        status="unavailable", error=str(exc))
            raise
        if not isinstance(rows, list):
            raise ValueError("broker_read_not_collection")
        record_read("clerk", f"ats.data.runtime.clerk_reads.{method}",
                    refs=[str(row.get(id_field) or row.get("order_ref") or "") for row in rows])
        cycles = {str(row["cycle_id"]) for row in self.store.conn.execute(
            "SELECT DISTINCT cycle_id FROM trades WHERE cycle_id IS NOT NULL AND cycle_id != ''")}
        audit = DecisionAuditRepository(self.store)
        for cycle in sorted(cycles):
            chain = audit.read_chain(cycle)
            record_read("clerk", "ats.decision.repository.DecisionAuditRepository.read_chain",
                        refs=[cycle], status="complete" if chain.get("cycle") else "no_coverage")
        return rows

    def get_fills(self):
        return self._reports("get_fills", "exec_id")

    def completed_orders(self):
        return self._reports("completed_orders", "perm_id")
