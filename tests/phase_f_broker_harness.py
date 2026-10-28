"""Transport substitute for tests of the real IBKRBroker write boundary."""
import json
import os
from contextlib import contextmanager
from types import SimpleNamespace

from ats.broker.ibkr import IBKRBroker


class FakeIB:
    def __init__(self, *, accounts=None, before_qualify=None, on_place=None,
                 accepted_file=None, status="Filled"):
        self.accounts = ["DU1"] if accounts is None else accounts
        self.before_qualify = before_qualify
        self.on_place = on_place
        self.accepted_file = accepted_file
        self.status = status
        self.accepted = []

    def managedAccounts(self):
        if isinstance(self.accounts, Exception):
            raise self.accounts
        return list(self.accounts)

    def qualifyContracts(self, *contracts):
        if self.before_qualify:
            self.before_qualify()
        return list(contracts)

    def placeOrder(self, contract, order):
        row = {"pid": os.getpid(), "account": order.account,
               "order_ref": order.orderRef, "symbol": contract.symbol,
               "qty": order.totalQuantity}
        self.accepted.append(row)
        if self.accepted_file:
            fd = os.open(self.accepted_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.write(fd, (json.dumps(row) + "\n").encode())
            finally:
                os.close(fd)
        if self.on_place:
            self.on_place()
        order.orderId = len(self.accepted)
        return SimpleNamespace(order=order, orderStatus=SimpleNamespace(
            status=self.status, filled=order.totalQuantity if self.status == "Filled" else 0,
            avgFillPrice=100 if self.status == "Filled" else 0))

    def sleep(self, seconds):
        pass


def broker_for(ib, *, expected="DU1"):
    broker = IBKRBroker.__new__(IBKRBroker)
    broker._expected_account = expected
    broker._ib = None

    @contextmanager
    def session():
        broker._ib = ib
        try:
            yield ib
        finally:
            broker._ib = None

    broker.session = session
    return broker


def record_boundary_proof(record_property, site, kind):
    """JUnit properties bind an executed local scenario to its scope and mode."""
    from ats.workflow.scoped_routes import RouteIdentity, canonical_json

    who = RouteIdentity("ai_hardware", "trader", "v1", {
        "kind": "sector", "id": "ai_hardware", "entities": ["NVDA", "AMD"],
        "time_range": {"start": "2026-10-07T00:00:00Z", "end": "2026-10-08T00:00:00Z"}})
    for key, value in {"entry_exercised": site, "mode": "isolated", "path_kind": kind,
                       "identity_json": canonical_json(who.as_row()),
                       "negative_side_effect_count": "0" if kind == "negative" else "n/a"}.items():
        record_property(key, value)
