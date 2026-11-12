"""IBKR option greeks: the market-data request must name an exchange, or IBKR refuses it."""

from types import SimpleNamespace

from ats.broker import ibkr


class _FakeIB:
    def __init__(self):
        self.requested, self.cancelled = [], []

    def reqMktData(self, contract, ticks, snapshot, regulatory):
        if not contract.exchange:                      # what IBKR does: error 321, no ticks
            return SimpleNamespace(modelGreeks=None)
        self.requested.append(contract)
        return SimpleNamespace(modelGreeks=SimpleNamespace(
            delta=-0.83, gamma=0.01, vega=0.2, theta=-0.1, impliedVol=1.01, undPrice=168.59))

    def cancelMktData(self, contract):
        self.cancelled.append(contract)

    def sleep(self, _):
        pass


def _broker():
    return ibkr.IBKRBroker.__new__(ibkr.IBKRBroker)


def test_an_exchange_less_option_contract_is_routed_through_smart_and_gets_ibkr_greeks():
    contract = SimpleNamespace(exchange="", symbol="COHR")
    pos = SimpleNamespace(symbol="COHR", delta=None, gamma=None, vega=None, theta=None, iv=None,
                         underlying_price=None, greeks_source=None)
    ib = _FakeIB()
    _broker()._fetch_option_greeks(ib, [(pos, contract)])
    assert pos.greeks_source == "ibkr" and pos.delta == -0.83 and pos.underlying_price == 168.59
    assert ib.requested[0].exchange == "SMART"
    assert contract.exchange == ""                    # the holding's own contract is not mutated
    assert ib.cancelled == ib.requested               # the cancel names the contract that was requested


def test_a_contract_that_already_names_an_exchange_is_left_alone():
    contract = SimpleNamespace(exchange="CBOE")
    assert ibkr._with_exchange(contract) is contract
