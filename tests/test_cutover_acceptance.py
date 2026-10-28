"""Shadow-period end-to-end acceptance (tasks 13.4–13.5).

13.4 and 13.5 fail in the same place — an order whose chain nobody can read —
so they are tested together here rather than split across two files where each
would pass with the other's evidence.

The property that matters most is the one that is easiest to get wrong:

**`unreadable` is not `clean`.** A chain that could not be read produces a
verdict of `unreadable` and blocks the switch, because "I could not check" and
"there is nothing wrong" call for opposite responses. Collapsing them is how an
empty or broken ledger becomes a clean report — the same failure mode as the
empty-dispatch-ledger one, and the reason group 10 refuses to read "no
execution record" as "did not execute".

The other is 13.5's asymmetry: **`trades` staying clean is not the evidence.**
A ledger that was never written to cannot tell you whether an order reached the
broker and came back, so a broker receipt with no local row is a stop condition
in its own right.
"""

import json

import pytest

from ats.workflow import cutover_acceptance as ca


# --- fixtures -------------------------------------------------------------- #

def _chain(*, snapshot=True, revisions=("dh1",), reviews=("dh1",),
           approvals=("dh1",), decision="approved"):
    """A decision chain as `read_chain` returns it.

    Every list is parameterised so a test can remove exactly one link and watch
    the verdict change — which is the only way to show the check has teeth rather
    than reporting whatever it is handed.
    """
    return {
        "cycle": {"cycle_id": "c1",
                  "research_snapshot": json.dumps(
                      {"items": [{"task_id": "t1"}]}) if snapshot else None},
        "revisions": [{"decision_hash": h, "revision_no": i + 1}
                      for i, h in enumerate(revisions)],
        "reviews": [{"decision_hash": h, "review_id": f"r{i}"}
                    for i, h in enumerate(reviews)],
        "approvals": [{"decision_hash": h, "approval_id": f"a{i}",
                       "decision": decision} for i, h in enumerate(approvals)],
    }


def _intent(**overrides):
    row = {"intent_id": "i1", "cycle_id": "c1", "symbol": "MU",
           "decision_hash": "dh1", "approval_id": "a0"}
    row.update(overrides)
    return row


def _reader(chain, *, error: Exception | None = None):
    def read(cycle_id: str):
        if error is not None:
            raise error
        return chain
    return read


# --- 13.4 traceability ----------------------------------------------------- #

def test_a_fully_linked_order_is_traceable():
    report = ca.verify_traceability(
        chain_reader=_reader(_chain()), intents=[_intent()])
    assert report.traceable and not report.untraceable
    assert report.compliant_switch is True
    trace = report.traces[0]
    for link in ca.REQUIRED_LINKS:
        assert trace.links[link]["present"] is True


@pytest.mark.parametrize("drop,absent", [
    ("snapshot", ca.LINK_SNAPSHOT),
    ("revision", ca.LINK_REVISION),
    ("review", ca.LINK_REVIEW),
    ("approval", ca.LINK_APPROVAL),
])
def test_each_missing_link_alone_makes_the_order_untraceable(drop, absent):
    """One absent link is enough. A chain is only as strong as its weakest part,
    and a partial chain reads as traceable in any summary that lists counts."""
    chain = _chain()
    if drop == "snapshot":
        chain["cycle"]["research_snapshot"] = None
    elif drop == "revision":
        chain["revisions"] = []
    elif drop == "review":
        chain["reviews"] = []
    else:
        chain["approvals"] = []

    report = ca.verify_traceability(
        chain_reader=_reader(chain), intents=[_intent()])
    assert report.untraceable, f"dropping the {drop} link must fail the trace"
    assert absent in report.traces[0].missing
    assert report.compliant_switch is False


def test_a_rejected_approval_is_not_a_approval():
    """The subtle one: the link exists, the row exists, the answer is no."""
    report = ca.verify_traceability(
        chain_reader=_reader(_chain(decision="rejected")), intents=[_intent()])
    assert report.untraceable
    assert ca.LINK_APPROVAL in report.traces[0].missing
    detail = report.traces[0].links[ca.LINK_APPROVAL]["detail"]
    assert "rejection is not an approval" in detail


def test_a_revision_from_a_different_hash_does_not_satisfy_the_link():
    chain = _chain(revisions=("dh2",), reviews=("dh2",), approvals=("dh2",))
    report = ca.verify_traceability(
        chain_reader=_reader(chain), intents=[_intent()])
    assert {ca.LINK_REVISION, ca.LINK_REVIEW, ca.LINK_APPROVAL} <= set(
        report.traces[0].missing)


def test_an_unreadable_chain_is_unreadable_not_clean():
    """The property the whole module is arranged around."""
    report = ca.verify_traceability(
        chain_reader=_reader(None, error=RuntimeError("database is locked")),
        intents=[_intent()])

    assert report.traces[0].verdict == ca.UNREADABLE
    assert report.compliant_switch is False
    assert "c1" in report.unreadable_cycles
    assert "failure to check" in report.traces[0].reason, (
        "the message must distinguish 'we could not check' from 'there is "
        "nothing wrong', or an operator reads a broken ledger as a clean one")


def test_an_order_with_no_cycle_cannot_be_traced_at_all():
    """'We could not tell which cycle it belongs to' is a finding."""
    report = ca.verify_traceability(
        chain_reader=_reader(_chain()), intents=[_intent(cycle_id="")])
    assert report.untraceable
    assert report.traces[0].missing == ca.REQUIRED_LINKS


def test_an_empty_intent_list_is_not_a_pass():
    report = ca.verify_traceability(chain_reader=_reader(_chain()), intents=[])
    assert report.traces == []
    assert "空账本" in report.note, (
        "an empty ledger reported as 'all orders traceable' is the failure this "
        "note exists to name")


def test_an_empty_snapshot_establishes_nothing():
    chain = _chain()
    chain["cycle"]["research_snapshot"] = json.dumps({"items": []})
    report = ca.verify_traceability(
        chain_reader=_reader(chain), intents=[_intent()])
    assert ca.LINK_SNAPSHOT in report.traces[0].missing


def test_one_untraceable_order_fails_the_whole_switch():
    """Compliance is a property of the switch, not of the average order."""
    report = ca.verify_traceability(
        chain_reader=_reader(_chain()),
        intents=[_intent(intent_id="i1"), _intent(intent_id="i2", cycle_id="")])
    assert len(report.traceable) == 1
    assert report.compliant_switch is False


def test_the_chain_is_read_once_per_cycle():
    """Re-reading per order would let a revocation between two reads produce two
    different verdicts for the same cycle — neither of them a finding."""
    calls: list[str] = []

    def counting(cycle_id: str):
        calls.append(cycle_id)
        return _chain()

    ca.verify_traceability(chain_reader=counting, intents=[
        _intent(intent_id="i1"), _intent(intent_id="i2"),
        _intent(intent_id="i3")])
    assert calls == ["c1"]


# --- 13.5 no unapproved real order ------------------------------------------ #

def _attempt(*, accepted=False, code="shadow_run_prohibited"):
    return {"intent_id": "i1", "accepted": accepted, "refusal_code": code}


class _Result:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows


class _FakeConn:
    """Module level, because a class body is not a closure.

    A `_Conn` nested inside `_FakeLedger` is invisible to `_FakeLedger.conn`'s
    body — the name resolves as a global and raises `NameError`. That is not a
    style point: it made three "the ledger is clean" tests fail for a reason that
    had nothing to do with the ledger.
    """

    def __init__(self, outer): self.outer = outer

    def execute(self, sql, params=()):
        text = str(sql)
        if text.startswith("PRAGMA table_info(trades)"):
            return _Result([(0, "order_id"), (1, "cycle_id"), (2, "symbol"),
                            (3, "source"), (4, "context")])
        if text.startswith("PRAGMA table_info(fills)"):
            return _Result([(0, "exec_id"), (1, "symbol"), (2, "order_id"),
                            (3, "origin")])
        if "FROM trades" in text:
            return _Result(self.outer._trades)
        if "FROM fills" in text:
            return _Result(self.outer._fills)
        return _Result([])


class _FakeLedger:
    """A store shaped like the real one for the queries 13.5 makes.

    Two details are load-bearing and were got wrong on the first attempt:

    - `PRAGMA table_info` has to answer, because the attribution check asks
      whether the `origin` column exists rather than assuming it.
    - `origin` lives on **fills**, not `trades`. A fake that put it on `trades`
      passed against a schema that does not exist, and the real query raised on
      every real store — turning "the ledger is clean" into "the check could not
      run".
    """

    def __init__(self, trades=(), fills=()):
        self._trades = [tuple(r) for r in trades]
        self._fills = [tuple(r) for r in fills]

    @property
    def conn(self): return _FakeConn(self)


def test_a_refused_submit_attempt_is_the_clean_case():
    report = ca.verify_no_unapproved_orders(
        run_id="r1", attempts=[_attempt()])
    assert report.verdict == ca.CLEAN
    assert report.refused_attempts == 1
    assert report.refusal_codes == ["shadow_run_prohibited"]


def test_no_attempt_at_all_is_not_a_pass():
    """Nothing tested the prohibition, so nothing may be claimed about it."""
    report = ca.verify_no_unapproved_orders(run_id="r1", attempts=[])
    assert report.verdict == ca.UNEXERCISED
    assert any("never exercised" in c for c in report.stop_conditions)


def test_an_accepted_submit_is_a_stop_condition():
    report = ca.verify_no_unapproved_orders(
        run_id="r1", attempts=[_attempt(accepted=True, code="")])
    assert report.verdict == ca.STOP
    assert any("ACCEPTED" in c for c in report.stop_conditions)


def test_a_broker_receipt_with_no_local_ledger_is_a_stop_condition():
    """The failure `trades`-stays-clean cannot see: the broker has the order and
    the ledger does not."""
    report = ca.verify_no_unapproved_orders(
        run_id="r1", attempts=[_attempt()],
        broker_receipts=[{"order_id": "b1", "symbol": "MU"}])
    assert report.verdict == ca.STOP
    assert report.unreconciled_broker_receipts
    assert any("cannot be ruled out" in c for c in report.stop_conditions)


def test_broker_receipts_with_a_real_ledger_are_checked_against_it():
    ledger = _FakeLedger(trades=[("c1", "shadow", "manual")])
    report = ca.verify_no_unapproved_orders(
        run_id="r1", attempts=[_attempt()], real_ledger=ledger,
        broker_receipts=[{"order_id": "b1"}])
    assert report.trades_rows_checked == 1
    assert report.verdict == ca.CLEAN, report.stop_conditions


def test_a_polluted_real_ledger_is_a_stop_condition():
    ledger = _FakeLedger(trades=[("r1", "shadow-run", "")])
    report = ca.verify_no_unapproved_orders(
        run_id="r1", attempts=[_attempt()], real_ledger=ledger)
    assert report.verdict == ca.STOP
    # Reported as a LEAK, not as a failure to read. The leak check raises when it
    # finds something, so a blanket `except` would file a detected breach under
    # "the ledger could not be checked" — and the second hides the first.
    assert any("was written to by this run" in c for c in report.stop_conditions)
    assert not any("could not be checked" in c for c in report.stop_conditions)


def test_a_legacy_fill_and_a_late_fill_are_excluded_by_attribution():
    """Both are expected during a shadow window. What is not expected is one of
    them being attributed to the shadow run — and both stay visible."""
    ledger = _FakeLedger(
        trades=[("c9", "manual-fill", ""), ("c8", "late-fill", "")],
        fills=[("e1", "MU", "o1", "manual"),
               ("e2", "NVDA", "o2", "unattributed"),
               ("e3", "AMD", "o3", "")])
    report = ca.verify_no_unapproved_orders(
        run_id="r1", attempts=[_attempt()], real_ledger=ledger)

    assert report.verdict == ca.CLEAN, report.stop_conditions
    excluded = {row["origin"]: row["count"] for row in report.attributed_excluded}
    # A blank origin lands in `unattributed` rather than being counted as system
    # or dropped — §11.1: unknown is neither system nor manual.
    assert excluded == {"manual": 1, "unattributed": 2}


def test_a_ledger_with_no_origin_column_reports_nothing_rather_than_claiming_clean():
    """No attribution column means the buckets are unknown, not empty."""
    class _NoOriginConn(_FakeConn):
        def execute(self, sql, params=()):
            if str(sql).startswith("PRAGMA table_info(fills)"):
                return _Result([(0, "exec_id"), (1, "symbol")])
            return _FakeConn.execute(self, sql, params)

    class _NoOrigin(_FakeLedger):
        @property
        def conn(self): return _NoOriginConn(self)

    report = ca.verify_no_unapproved_orders(
        run_id="r1", attempts=[_attempt()], real_ledger=_NoOrigin())
    assert report.attributed_excluded == []
    assert report.trades_rows_checked == 0


def test_capability_checks_and_simulated_receipts_are_carried_into_the_report():
    report = ca.verify_no_unapproved_orders(
        run_id="r1", attempts=[_attempt()],
        capability_checks=[{"route_id": "A", "generation": 2, "verdict": "stale"}],
        simulated_receipts=[{"order_id": "s1", "accepted": False}])
    assert report.capability_checks[0]["verdict"] == "stale"
    assert report.simulated_receipts == 1


def test_both_reports_state_what_they_did_not_prove():
    trace = ca.verify_traceability(chain_reader=_reader(_chain()),
                                   intents=[_intent()])
    unapproved = ca.verify_no_unapproved_orders(run_id="r1",
                                                attempts=[_attempt()])
    combined = ca.render_acceptance_report(trace, unapproved)
    assert "未**证明" in combined
    assert "独立" in combined, (
        "the report must say that `trades` is independent acceptance, or a "
        "clean ledger reads as the primary evidence")
