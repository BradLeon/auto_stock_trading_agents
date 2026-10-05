"""Phase F 2.1–2.3 — the route registry and the write grant.

The property under test is cross-process single-active: at any moment at most one
route may submit real orders. An in-process flag cannot express that (the resident
scheduler and a one-shot CLI are separate processes), and a route id alone cannot
either — after A→B→A the id is the same but the authority is not.

The negative cases are the point. A guard whose positive case passes proves only
that the happy path works; what matters is that a grant issued before a cutover
stops working after it, and that a grant for one account cannot drive another.
"""

import sqlite3

import pytest

from ats.execution import broker_write_guard as guard
from ats.execution import route_registry as registry


@pytest.fixture(autouse=True)
def _clean_guard():
    guard.reset_for_tests()
    yield
    guard.reset_for_tests()


@pytest.fixture
def store(tmp_path):
    return str(tmp_path / "routes.sqlite")


def _install(store, route="A", generation=1, environment="paper", account="DU1"):
    return registry.install_route(route, generation=generation,
                                  environment=environment, account=account,
                                  actor="test", reason="fixture", path=store)


# --- 2.1 the registry ------------------------------------------------------- #

def test_no_route_is_a_fail_closed_error(store):
    """Absence is not permission. A fresh install must not be writable."""
    with pytest.raises(registry.RouteRegistryError, match="no active trade route"):
        registry.read_state(store)
    assert registry.try_read_state(store) is None


def test_install_records_the_route_and_generation(store):
    state = _install(store)
    assert (state.route_id, state.generation) == ("A", 1)
    assert registry.read_state(store).same_as(state)


def test_install_refuses_to_overwrite_an_existing_route(store):
    """Re-installing on every process start would reset the generation and
    silently defeat the mechanism."""
    _install(store)
    with pytest.raises(registry.RouteRegistryError, match="already installed"):
        _install(store, route="B", generation=9)


def test_switch_moves_to_the_next_generation(store):
    _install(store)
    switched = registry.switch_route(1, "B", environment="paper", account="DU1",
                                     actor="ops", reason="cutover", path=store)
    assert (switched.route_id, switched.generation) == ("B", 2)


def test_switch_refuses_a_stale_expected_generation(store):
    """The compare-and-set: two operators racing cannot both succeed."""
    _install(store)
    registry.switch_route(1, "B", path=store, reason="first")
    with pytest.raises(registry.RouteGenerationConflict, match="generation moved"):
        registry.switch_route(1, "C", path=store, reason="second")


def test_a_route_id_alone_cannot_identify_an_authority(store):
    """A→B→A: the second A is a different authority from the first.

    This is the reason a generation exists at all — comparing route ids would let
    an authorization minted before the round trip look valid afterwards.
    """
    first = _install(store, route="A", generation=1)
    registry.switch_route(1, "B", path=store, reason="to B")
    back = registry.switch_route(2, "A", path=store, reason="back to A")

    assert back.route_id == first.route_id
    assert back.generation == 3
    assert not back.same_as(first)


def test_history_records_every_generation(store):
    _install(store)
    registry.switch_route(1, "B", path=store, reason="one")
    registry.switch_route(2, "A", path=store, reason="two")

    entries = registry.history(store)
    assert [(row["from_generation"], row["to_generation"]) for row in entries] == [
        (2, 3), (1, 2), (0, 1)]


def test_generation_must_be_positive(store):
    with pytest.raises(registry.RouteRegistryError, match="generation must be"):
        _install(store, generation=0)


def test_route_id_is_required(store):
    with pytest.raises(registry.RouteRegistryError, match="route_id is required"):
        registry.install_route("  ", path=store)


def test_schema_is_additive_over_an_existing_database(store):
    """An installation that predates the table keeps working."""
    with sqlite3.connect(store) as conn:
        conn.execute("CREATE TABLE legacy (x TEXT)")
        conn.execute("INSERT INTO legacy VALUES ('kept')")

    _install(store)
    with sqlite3.connect(store) as conn:
        assert conn.execute("SELECT x FROM legacy").fetchone()[0] == "kept"


# --- 2.2 the grant is re-verified at every submission ------------------------ #

def test_a_grant_allows_a_matching_authority(store):
    _install(store)
    guard.grant_write("A", 1, environment="paper", account="DU1")
    guard.check_grant(operation="place_orders", caller="t",
                      state_reader=lambda: registry.read_state(store),
                      account="DU1", environment="paper")


def test_no_grant_is_a_refusal(store):
    """'Nobody granted this' must not read as 'anything goes'."""
    _install(store)
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        guard.check_grant(operation="place_orders", caller="t",
                          state_reader=lambda: registry.read_state(store),
                          account="DU1")
    assert exc.value.reason_code == guard.REASON_NO_GRANT


def test_a_grant_from_a_previous_generation_is_refused(store):
    """The core cross-process property: a cutover revokes the old authority for
    processes that were already running."""
    _install(store)
    guard.grant_write("A", 1, account="DU1")
    reader = lambda: registry.read_state(store)          # noqa: E731
    guard.check_grant(operation="place_orders", caller="t", state_reader=reader,
                      account="DU1")

    registry.switch_route(1, "B", environment="paper", account="DU1",
                          reason="cutover", path=store)

    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        guard.check_grant(operation="place_orders", caller="t", state_reader=reader,
                          account="DU1")
    assert exc.value.reason_code == guard.REASON_GENERATION_STALE


def test_a_grant_survives_an_unrelated_generation_move_only_against_its_own(store):
    """A grant issued at the current generation keeps working — the check is not a
    blanket 'any change invalidates'."""
    _install(store)
    registry.switch_route(1, "A", path=store, reason="same route, new generation")
    guard.grant_write("A", 2, account="DU1")
    guard.check_grant(operation="place_orders", caller="t",
                      state_reader=lambda: registry.read_state(store), account="DU1")


def test_revoking_the_grant_stops_writes(store):
    _install(store)
    guard.grant_write("A", 1, account="DU1")
    guard.revoke_grant("cutover")
    assert guard.active_grant() is None
    with pytest.raises(guard.BrokerWriteProhibited):
        guard.check_grant(operation="place_orders", caller="t",
                          state_reader=lambda: registry.read_state(store),
                          account="DU1")


def test_grant_requires_a_route_and_a_positive_generation():
    with pytest.raises(ValueError, match="route_id is required"):
        guard.grant_write("", 1)
    with pytest.raises(ValueError, match="generation must be"):
        guard.grant_write("A", 0)


# --- 2.3 the account and environment binding --------------------------------- #

def test_a_grant_for_one_account_cannot_drive_another(store):
    """This is what makes a paper grant useless against a live connection."""
    _install(store, account="DU1")
    guard.grant_write("A", 1, account="DU1")
    reader = lambda: registry.read_state(store)          # noqa: E731

    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        guard.check_grant(operation="place_orders", caller="t", state_reader=reader,
                          account="U12345")
    assert exc.value.reason_code == guard.REASON_ACCOUNT_MISMATCH


def test_an_unmatched_account_is_refused_even_when_the_grant_names_one(store):
    """A blank on either side means 'unstated', not 'matches' — otherwise an
    unnamed grant would satisfy any broker."""
    _install(store, account="DU1")
    guard.grant_write("A", 1, account="DU1")
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        guard.check_grant(operation="place_orders", caller="t",
                          state_reader=lambda: registry.read_state(store),
                          account="")
    assert exc.value.reason_code == guard.REASON_ACCOUNT_MISMATCH


def test_an_environment_mismatch_is_refused(store):
    _install(store, environment="paper", account="")
    guard.grant_write("A", 1, environment="live")
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        guard.check_grant(operation="place_orders", caller="t",
                          state_reader=lambda: registry.read_state(store),
                          environment="paper", account="")
    assert exc.value.reason_code == guard.REASON_ENVIRONMENT_MISMATCH


def test_an_unreadable_registry_is_a_refusal_not_a_pass(store):
    """Fail closed: if the authority cannot be read, submission stops."""
    _install(store)
    guard.grant_write("A", 1, account="DU1")

    def reader():
        raise registry.RouteRegistryError("database is locked")

    with pytest.raises(registry.RouteRegistryError):
        guard.check_grant(operation="place_orders", caller="t", state_reader=reader,
                          account="DU1")


# --- the submit layer enforces it --------------------------------------------- #

def test_the_broker_submit_layer_consults_the_grant(store, monkeypatch):
    """A grant that predates a cutover cannot be used through the broker either."""
    from ats.broker.ibkr import IBKRBroker

    _install(store, account="DU1")
    monkeypatch.setenv("ATS_ROUTE_REGISTRY_PATH", store)
    guard.grant_write("A", 1, account="DU1")
    registry.switch_route(1, "B", environment="paper", account="DU1",
                          reason="cutover", path=store)

    broker = IBKRBroker.__new__(IBKRBroker)
    broker.connected_account = lambda: "DU1"             # type: ignore[method-assign]
    from ats.schemas.decision import TradeDecision

    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        broker.place_orders([(TradeDecision(symbol="AMD", action="buy", qty=1), 1.0)],
                            cycle_id="c1")
    assert exc.value.reason_code == guard.REASON_GENERATION_STALE


def test_a_refused_submission_is_recorded_with_its_reason(store, monkeypatch):
    from ats.broker.ibkr import IBKRBroker

    _install(store, account="DU1")
    # The submit layer resolves the registry through the env var, so the test has
    # to point it at this test's database — otherwise it reads (and would create)
    # the real `var/phase_f_routes.sqlite`.
    monkeypatch.setenv("ATS_ROUTE_REGISTRY_PATH", store)
    guard.grant_write("A", 1, account="DU1")
    registry.switch_route(1, "B", environment="paper", account="DU1",
                          reason="cutover", path=store)

    broker = IBKRBroker.__new__(IBKRBroker)
    broker.connected_account = lambda: "DU1"             # type: ignore[method-assign]
    from ats.schemas.decision import TradeDecision

    with pytest.raises(guard.BrokerWriteProhibited):
        broker.place_orders([(TradeDecision(symbol="AMD", action="buy", qty=1), 1.0)],
                            cycle_id="c1")

    rows = guard.refusal_rows()
    assert rows and rows[-1]["reason_code"] == guard.REASON_GENERATION_STALE
    assert rows[-1]["caller"] == "IBKRBroker.place_orders"
