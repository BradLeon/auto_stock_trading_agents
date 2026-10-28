"""Shadow trade ledger isolation (Phase F 3.7).

The load-bearing property is not "the data is kept separately" — it is that a
shadow run's mistaken write to the real `trades` table **fails loudly** instead of
being redirected somewhere harmless. Redirecting is the tempting alternative and
it is worse: the mistake looks like it worked, so it recurs outside the shadow
window and writes to production for real.
"""

import sqlite3

import pytest

from ats.execution import broker_write_guard as guard
from ats.workflow import shadow_ledger as sl


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "shadow-orders.sqlite"


def _intent(intent_id: str = "i1", **overrides) -> sl.ShadowIntent:
    defaults = {
        "intent_id": intent_id, "run_id": "run-1", "cycle_id": "c1",
        "symbol": "MU", "action": "buy", "quantity": 100.0,
        "revision_no": 1, "sequence": 0,
        "decision_hash": "dh", "approval_id": "a1",
        "route_id": "A", "route_generation": 1,
    }
    defaults.update(overrides)
    return sl.ShadowIntent(**defaults)


class _RealLedger:
    """Just enough of the store for the leak check."""

    def __init__(self):
        self.rows: list[tuple] = []

    class _Conn:
        def __init__(self, outer):
            self.outer = outer

        def execute(self, sql, params=()):
            return _Result(self.outer.rows)

    @property
    def conn(self):
        return self._Conn(self)

    def add(self, cycle_id: str, source: str, context: str = "") -> None:
        self.rows.append((cycle_id, source, context))


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


# --------------------------------------------------------------------------- #
# intents live in the shadow ledger
# --------------------------------------------------------------------------- #

def test_an_intent_is_recorded_with_its_causal_chain(ledger):
    """Everything needed to answer "was this order authorised", weeks later."""
    sl.record_intent(_intent(), path=ledger)

    rows = sl.intents(cycle_id="c1", path=ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["decision_hash"] == "dh"
    assert row["approval_id"] == "a1"
    assert row["route_id"] == "A"
    assert row["route_generation"] == 1


def test_intents_are_append_only(ledger):
    sl.record_intent(_intent(), path=ledger)
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(ledger) as conn:
            conn.execute("UPDATE shadow_order_intents SET quantity=0")


def test_the_shadow_ledger_is_not_the_real_ledger(ledger, tmp_path):
    """Separate file, separate env var.

    Phase E's `ATS_SHADOW_DB_PATH` holds workflow runs and triggers; putting order
    intents there would put them behind the same access rule as scheduling state.
    """
    import os

    from ats.config import REPO_ROOT

    default = sl.default_shadow_ledger_path()
    assert "orders.sqlite" in default
    assert "phase-e.sqlite" not in default
    assert default != str(REPO_ROOT / "var" / "ats.sqlite")

    os.environ["ATS_SHADOW_ORDER_DB"] = str(tmp_path / "custom.sqlite")
    try:
        assert sl.default_shadow_ledger_path().endswith("custom.sqlite")
    finally:
        os.environ.pop("ATS_SHADOW_ORDER_DB", None)


# --------------------------------------------------------------------------- #
# the refusal, not the redirection
# --------------------------------------------------------------------------- #

def test_a_real_ledger_write_from_a_shadow_run_raises(ledger):
    with pytest.raises(sl.ShadowLedgerWriteRefused):
        sl.refuse_trades_write(run_id="run-1", detail="save_trades", path=ledger)


def test_the_refusal_explicitly_rules_out_redirection(ledger):
    """The reason matters: someone will otherwise "fix" it by redirecting."""
    with pytest.raises(sl.ShadowLedgerWriteRefused) as excinfo:
        sl.refuse_trades_write(run_id="run-1", detail="save_trades", path=ledger)
    assert "refused rather than redirected" in str(excinfo.value)


def test_a_refused_write_records_nothing_in_the_shadow_ledger(ledger):
    """A refused real write must not appear as a shadow intent either.

    Otherwise the mistake is merely relabelled: the ledger would show an intent
    that the run never actually formed.
    """
    with pytest.raises(sl.ShadowLedgerWriteRefused):
        sl.refuse_trades_write(run_id="run-1", path=ledger)
    assert sl.intents(path=ledger) == []


def test_the_leak_check_catches_a_write_that_got_through(ledger):
    """The prevention must be verifiable, not just asserted.

    `assert_real_ledger_not_written` exists because a prohibition that is never
    checked is one nobody can rely on.
    """
    real = _RealLedger()
    real.add("c1", "shadow:run-1", "shadow intent")

    with pytest.raises(sl.ShadowLedgerWriteRefused) as excinfo:
        sl.assert_real_ledger_not_written(real, run_id="run-1", path=ledger)
    assert "run-1" in str(excinfo.value)


def test_the_leak_check_passes_when_nothing_leaked(ledger):
    real = _RealLedger()
    real.add("c9", "trader", "a real, unrelated cycle")

    result = sl.assert_real_ledger_not_written(real, run_id="run-1", path=ledger)
    assert result["leaked_rows"] == 0
    assert result["trades_rows_checked"] == 1


def test_the_leak_check_catches_a_shadow_source_without_a_run_id(ledger):
    """Attribution may be missing entirely; the source column still says shadow."""
    real = _RealLedger()
    real.add("c1", "shadow:unknown-run", "")

    with pytest.raises(sl.ShadowLedgerWriteRefused):
        sl.assert_real_ledger_not_written(real, path=ledger)


# --------------------------------------------------------------------------- #
# attribution rebuild
# --------------------------------------------------------------------------- #

def test_attribution_rebuilds_from_the_ledger_alone(ledger):
    """Deliberately independent of any live store.

    If rebuilding needed the process that produced it, the ledger would not be
    evidence.
    """
    sl.record_intent(_intent("i1", sequence=0), path=ledger)
    sl.record_intent(_intent("i2", symbol="AMD", action="sell", sequence=1),
                     path=ledger)

    rebuilt = sl.rebuild_attribution("c1", path=ledger)
    assert rebuilt["order_count"] == 2
    assert [o["symbol"] for o in rebuilt["orders"]] == ["MU", "AMD"]
    assert rebuilt["every_order_authorized"] is True


def test_rebuilding_does_not_change_the_original_records(ledger):
    sl.record_intent(_intent(), path=ledger)
    before = sl.intents(cycle_id="c1", path=ledger)

    sl.rebuild_attribution("c1", path=ledger)

    assert sl.intents(cycle_id="c1", path=ledger) == before


def test_rebuilt_attribution_reports_whether_each_order_reached_the_broker(ledger):
    sl.record_intent(_intent("i1"), path=ledger)
    sl.record_submit_attempt(intent_id="i1", accepted=False,
                             refusal_code=guard.REASON_SHADOW_RUN,
                             refusal_id="r-1", path=ledger)

    rebuilt = sl.rebuild_attribution("c1", path=ledger)
    assert rebuilt["any_reached_broker"] is False
    assert rebuilt["refused_count"] == 1
    assert rebuilt["orders"][0]["refusal_codes"] == [guard.REASON_SHADOW_RUN]


def test_an_order_that_reached_the_broker_is_visible_as_such(ledger):
    """Would mean the prohibition failed, so it must not be smoothed over."""
    sl.record_intent(_intent("i1"), path=ledger)
    sl.record_submit_attempt(intent_id="i1", accepted=True, path=ledger)

    rebuilt = sl.rebuild_attribution("c1", path=ledger)
    assert rebuilt["submitted_count"] == 1
    assert rebuilt["any_reached_broker"] is True


# --------------------------------------------------------------------------- #
# the attestation
# --------------------------------------------------------------------------- #

def test_the_attestation_records_that_the_prohibition_was_exercised(ledger):
    """A shadow run that never reached a submit cannot claim the guard works.

    Absence of attempts is not a pass, and the attestation says so in a field a
    caller can check rather than leaving it to be inferred.
    """
    sl.record_intent(_intent("i1"), path=ledger)
    sl.record_submit_attempt(intent_id="i1", accepted=False,
                             refusal_code=guard.REASON_SHADOW_RUN,
                             refusal_id="r-1", path=ledger)

    attestation = sl.shadow_attestation(run_id="run-1", path=ledger)
    assert attestation["prohibition_exercised"] is True
    assert attestation["no_order_reached_broker"] is True


def test_an_attestation_from_a_run_with_no_attempts_says_so(ledger):
    sl.record_intent(_intent("i1"), path=ledger)

    attestation = sl.shadow_attestation(run_id="run-1", path=ledger)
    assert attestation["submit_attempts"] == 0
    assert attestation["prohibition_exercised"] is False


def test_the_attestation_flags_an_accepted_submit(ledger):
    sl.record_intent(_intent("i1"), path=ledger)
    sl.record_submit_attempt(intent_id="i1", accepted=True, path=ledger)

    attestation = sl.shadow_attestation(run_id="run-1", path=ledger)
    assert attestation["no_order_reached_broker"] is False
    assert attestation["accepted_attempts"] == 1


def test_the_attestation_includes_the_real_ledger_check(ledger):
    real = _RealLedger()
    real.add("c9", "trader", "unrelated")

    sl.record_intent(_intent("i1"), path=ledger)
    sl.record_submit_attempt(intent_id="i1", accepted=False,
                             refusal_code=guard.REASON_SHADOW_RUN, path=ledger)

    attestation = sl.shadow_attestation(run_id="run-1", store=real, path=ledger)
    assert attestation["real_ledger"]["leaked_rows"] == 0
