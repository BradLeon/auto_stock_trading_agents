"""Explicit governed-price/isolated-transport fixture for older approval tests."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from ats.data import execution_prices as prices
from ats.data.runtime.execution_prices import ExecutionPrice
from ats.execution import broker_write_guard as guard
from ats.execution.route_registry import install_route
from ats.execution.simulation import FakeBroker, simulated_execution
from ats.memory import get_store
from ats.workflow.isolation import isolated_run


@contextmanager
def governed_price_run(monkeypatch, tmp_path):
    with isolated_run("approval-regression", root=tmp_path / "governed-prices"):
        monkeypatch.setattr(prices, "session_context", lambda now: (True, now - timedelta(days=1)))
        def provider(symbol, **kwargs):
            now = datetime.now(UTC)
            return ExecutionPrice(symbol=symbol, currency="USD", source="synthetic:approval-regression",
                                  source_as_of=now, queried_at=now, price_kind="bid_ask",
                                  bid=100, ask=100, session="regular", market_data_mode="live",
                                  adjusted=False, min_size=1, size_increment=1, min_tick=.01)
        monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price", provider)
        route = install_route("A", generation=1, environment="paper", account="DU1",
                              actor="test", reason="isolated regression")
        guard.grant_write("A", route.generation, environment="paper", account="DU1")
        original = FakeBroker.place_orders
        def filled(self, *args, **kwargs):
            rows = original(self, *args, **kwargs)
            for row in rows:
                self.simulate_fill(row.order_id, shares=row.qty, price=100)
            return rows
        # Only the no-network class is adapted; its real checks run first.
        monkeypatch.setattr(FakeBroker, "place_orders", filled)
        with simulated_execution(store=get_store(), account="DU1") as broker:
            yield broker
