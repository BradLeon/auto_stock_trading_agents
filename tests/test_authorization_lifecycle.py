"""Phase F 2.4 — authorization lifecycle is derived, not recorded.

The route switch in task 11.7 has to answer "is any authorization valid and
unfinished?" and the model had no way to answer it: `ExecutionAuthorization` is
constructed on demand from review and approval rows, with no id, no expiry and no
completion state.

These tests pin the derivation's asymmetry, which is the part that matters. The
danger is not a bug in the happy path — it is an order that was accepted by the
broker while the local write failed, disappearing from the drain check and letting
a route switch strand a live order on a route that no longer accepts submissions.
So an unrecognised status blocks; it does not pass.
"""

import pytest

from ats.execution.authorization_lifecycle import (
    LIVE_ORDER_STATUSES,
    TERMINAL_CYCLE_STATUSES,
    TERMINAL_ORDER_STATUSES,
    AuthorizationLifecycle,
    lifecycle_for_cycle,
    unsettled_orders_for_cycle,
)


def _row(order_id="o1", status="submitted", **kw):
    return {"order_id": order_id, "status": status, **kw}


# --- status classification -------------------------------------------------- #

@pytest.mark.parametrize("status", sorted(TERMINAL_ORDER_STATUSES))
def test_terminal_statuses_are_finished(status):
    assert AuthorizationLifecycle.classify_status(status) == "terminal"


@pytest.mark.parametrize("status", sorted(LIVE_ORDER_STATUSES))
def test_live_statuses_are_unfinished(status):
    assert AuthorizationLifecycle.classify_status(status) == "live"


@pytest.mark.parametrize("status", ["", None, "weird", "FILLED_x", 3])
def test_an_unrecognised_status_is_unknown_not_finished(status):
    """The load-bearing case. `place_orders` polls for three seconds and returns,
    so "submitted" routinely settles later — and a row whose status we cannot read
    is still an order somebody may hold."""
    assert AuthorizationLifecycle.classify_status(status) == "unknown"


def test_status_matching_is_case_and_space_insensitive():
    assert AuthorizationLifecycle.classify_status("  Filled ") == "terminal"
    assert AuthorizationLifecycle.classify_status("SUBMITTED") == "live"


# --- unfinished sets --------------------------------------------------------- #

def test_a_submitted_order_is_unfinished():
    lifecycle = AuthorizationLifecycle(cycle_status="executed",
                                       order_rows=[_row(status="submitted")])
    assert lifecycle.has_unfinished() is True
    assert lifecycle.unfinished()[0]["_lifecycle"] == "live"


def test_a_filled_order_is_finished_even_while_the_cycle_is_open():
    lifecycle = AuthorizationLifecycle(cycle_status="executing",
                                       order_rows=[_row(status="filled")])
    assert lifecycle.has_unfinished() is False
    assert lifecycle.blocks_route_switch() is True, "the live cycle still blocks"


def test_a_partially_filled_order_is_unfinished():
    lifecycle = AuthorizationLifecycle(cycle_status="executed",
                                       order_rows=[_row(status="partial")])
    assert lifecycle.unfinished()[0]["_lifecycle"] == "live"


def test_an_unknown_status_keeps_the_order_in_the_unfinished_set():
    lifecycle = AuthorizationLifecycle(cycle_status="executed",
                                       order_rows=[_row(status="")])
    assert lifecycle.has_unfinished() is True
    assert lifecycle.unknown_status_orders()[0]["order_id"] == "o1"


def test_terminal_and_live_orders_are_counted_separately():
    lifecycle = AuthorizationLifecycle(cycle_status="executed", order_rows=[
        _row("a", "filled"), _row("b", "submitted"), _row("c", "cancelled"),
        _row("d", ""),
    ])
    summary = lifecycle.blocking_summary()
    assert summary["order_count"] == 4
    assert summary["unfinished_count"] == 2
    assert {row["order_id"] for row in summary["unfinished_orders"]} == {"b", "d"}
    assert summary["blocks_switch"] is True


# --- cycle status ------------------------------------------------------------ #

@pytest.mark.parametrize("status", sorted(TERMINAL_CYCLE_STATUSES))
def test_terminal_cycles_are_not_live(status):
    assert AuthorizationLifecycle(cycle_status=status, order_rows=[]).cycle_is_live() is False


@pytest.mark.parametrize("status", ["executing", "pending_approval", "risk_gate", "", None])
def test_a_live_or_unknown_cycle_is_treated_as_live(status):
    """Fail closed: an unrecognised or absent status must not read as finished."""
    assert AuthorizationLifecycle(cycle_status=status, order_rows=[]).cycle_is_live() is True


def test_an_unreachable_audit_trail_blocks_the_switch(tmp_path):
    """Deriving from a store with no cycles yields an unknown status, which blocks."""
    lifecycle = lifecycle_for_cycle("c-unknown", store=object())
    assert lifecycle.cycle_is_live() is True
    assert lifecycle.blocks_route_switch() is True


# --- derivation from a store -------------------------------------------------- #

class _FakeStore:
    """Minimal stand-in exposing the readers the derivation probes for."""

    def __init__(self, rows=(), cycle=None):
        self._rows = list(rows)
        self._cycle = cycle

    def recent_trades(self, symbol=None, limit=10):
        return list(self._rows)

    def orders_for_cycle(self, cycle_id):
        return [row for row in self._rows if row.get("cycle_id") == cycle_id]


def test_orders_are_filtered_to_the_cycle_in_question():
    store = _FakeStore(rows=[
        _row("a", "submitted", cycle_id="c-1"),
        _row("b", "filled", cycle_id="c-2"),
    ])
    lifecycle = lifecycle_for_cycle("c-1", store=store)
    assert [row["order_id"] for row in lifecycle.orders()] == ["a"]
    assert lifecycle.has_unfinished() is True


def test_a_cycle_whose_orders_are_all_finished_does_not_block_on_orders():
    store = _FakeStore(rows=[_row("a", "filled", cycle_id="c-1")])
    assert unsettled_orders_for_cycle("c-1", store=store) == []


def test_a_store_without_any_reader_yields_no_orders_and_still_blocks():
    """No reader is not permission. The live unknown cycle carries the decision."""
    lifecycle = lifecycle_for_cycle("c-x", store=object())
    assert lifecycle.orders() == []
    assert lifecycle.blocks_route_switch() is True


def test_the_derived_view_is_the_same_one_reconciliation_reads():
    """One implementation of "is this finished", so the drain check and
    reconciliation cannot disagree about the same row."""
    store = _FakeStore(rows=[_row("a", "submitted", cycle_id="c-1")])
    lifecycle = lifecycle_for_cycle("c-1", store=store)
    assert lifecycle.unfinished()[0]["order_id"] == \
        unsettled_orders_for_cycle("c-1", store=store)[0]["order_id"]
