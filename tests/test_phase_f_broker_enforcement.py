"""Actual startup -> IBKRBroker -> fake transport, including separate processes."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from phase_f_broker_harness import FakeIB, broker_for, record_boundary_proof

from ats.execution import broker_write_guard as guard
from ats.execution import route_registry as rr
from ats.execution import route_switch as rs
from ats.execution.authorization_lifecycle import AuthorizationLifecycle
from ats.schemas.decision import TradeDecision


@pytest.fixture(autouse=True)
def clean_guard():
    guard.reset_for_tests()
    yield
    guard.reset_for_tests()


@pytest.fixture
def authority(tmp_path, monkeypatch):
    path = tmp_path / "routes.sqlite"
    monkeypatch.setenv("ATS_ROUTE_REGISTRY_PATH", str(path))
    rr.install_route("A", environment="paper", account="DU1")
    guard.grant_write("A", 1, environment="paper", account="DU1")
    return path


def submit(ib, cycle="cycle", *, expected="DU1", qty=1):
    return broker_for(ib, expected=expected).place_orders(
        [(TradeDecision(symbol="AMD", action="buy", qty=qty), qty)],
        cycle, wait=0, revision_no=1)


@pytest.mark.parametrize("mode", ["unset", "revoked", "grant_without_mode"])
def test_direct_broker_cannot_connect_without_initialised_capability(authority, mode):
    if mode == "unset":
        guard.reset_for_tests()
    elif mode == "revoked":
        guard.revoke_grant()
    else:
        guard._STATE.mode = guard.UNSET
    broker = broker_for(FakeIB())
    broker.session = lambda: pytest.fail("unauthorised session opened")
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        broker.place_orders([(TradeDecision(symbol="AMD", action="buy"), 1)], "c")
    assert exc.value.reason_code == guard.REASON_NO_GRANT
    assert guard.refusal_rows()


@pytest.mark.parametrize("accounts,expected", [
    (["DU2"], "DU1"), ([], "DU1"), (["DU1", "DU2"], ""),
    (RuntimeError("not connected"), "DU1"), (["U1"], "U1"),
])
def test_actual_session_must_match_not_just_configuration(authority, accounts, expected):
    ib = FakeIB(accounts=accounts)
    with pytest.raises(guard.BrokerWriteProhibited):
        submit(ib, expected=expected)
    assert ib.accepted == []
    assert rr.submission_receipts() == []


@pytest.mark.parametrize("grant_env,grant_account,route_env,route_account,session", [
    ("", "DU1", "paper", "DU1", "DU1"),
    ("paper", "", "paper", "DU1", "DU1"),
    ("paper", "DU1", "live", "DU1", "DU1"),
    ("paper", "DU1", "paper", "DU2", "DU1"),
    ("paper", "X1", "paper", "X1", "X1"),
])
def test_incomplete_or_inconsistent_four_way_binding_refuses(
        authority, grant_env, grant_account, route_env, route_account, session):
    rr.switch_route(1, "B", environment=route_env, account=route_account)
    guard.grant_write("B", 2, environment=grant_env, account=grant_account)
    ib = FakeIB(accounts=[session])
    with pytest.raises(guard.BrokerWriteProhibited):
        submit(ib, expected=session)
    assert not ib.accepted


def test_valid_session_sets_explicit_order_account_and_preserves_reads(authority, record_property):
    ib = FakeIB(accounts=["DU2", "DU1"])
    entries = submit(ib)
    assert entries[0].status == "filled"
    assert ib.accepted[0]["account"] == "DU1"
    assert rr.submission_receipts()[0]["status"] == "filled"
    guard.reset_for_tests()
    assert broker_for(ib).connected_account(ib) == "DU1"
    record_boundary_proof(record_property, "ats.broker.ibkr.IBKRBroker._submit", "positive")


def test_freeze_between_batch_check_and_actual_write_is_rechecked(authority, record_property):
    ib = FakeIB(before_qualify=lambda: rr.freeze_submissions(actor="race"))
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        submit(ib)
    assert exc.value.reason_code == guard.REASON_FROZEN
    assert not ib.accepted
    record_boundary_proof(record_property, "ats.broker.ibkr.IBKRBroker._submit", "negative")


def test_session_changed_during_contract_qualification_is_rechecked(authority):
    ib = FakeIB()
    ib.before_qualify = lambda: setattr(ib, "accounts", ["DU2"])
    with pytest.raises(guard.BrokerWriteProhibited):
        submit(ib)
    assert not ib.accepted


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_actual_authority_failure_is_audited_before_connect(authority, fault):
    authority.unlink()
    if fault == "corrupt":
        authority.write_bytes(b"not sqlite")
    ib = FakeIB()
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        submit(ib)
    assert exc.value.reason_code == guard.REASON_AUTHORITY_UNREADABLE
    assert not ib.accepted
    if fault == "missing":
        assert not authority.exists()


def test_duplicate_identity_refuses_even_if_payload_changes_or_generation_moves(authority):
    ib = FakeIB()
    submit(ib)
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        submit(ib, qty=2)
    assert exc.value.reason_code == "duplicate_order_intent"
    rr.switch_route(1, "B", environment="paper", account="DU1")
    guard.grant_write("B", 2, environment="paper", account="DU1")
    with pytest.raises(guard.BrokerWriteProhibited):
        submit(ib)
    assert len(ib.accepted) == 1
    submit(ib, cycle="different")
    assert len(ib.accepted) == 2


def test_timeout_after_acceptance_remains_unknown_and_blocks_drain(authority):
    def timeout():
        raise TimeoutError("accepted but reply lost")
    ib = FakeIB(on_place=timeout)
    assert submit(ib)[0].status == "error"
    assert len(ib.accepted) == 1
    assert rr.submission_receipts()[0]["status"] == "unknown"
    report = rs.perform_switch("B", expected_generation=1,
                               lifecycle=AuthorizationLifecycle(cycle_status="executed", order_rows=[]))
    assert not report.succeeded and "unknown" in " ".join(report.reasons)
    assert rr.read_state().generation == 1


_CHILD = textwrap.dedent('''
    import json, os, sys
    from ats.execution import broker_write_guard as guard
    from ats.execution import route_registry as rr
    from ats.schemas.decision import TradeDecision
    from phase_f_broker_harness import FakeIB, broker_for
    mode, generation, route, cycle, accepted_file = sys.argv[1:]
    guard.grant_write(route, int(generation), environment="paper", account="DU1")
    print("READY", flush=True)
    sys.stdin.readline()
    try:
        if mode == "switch":
            from ats.execution import route_switch as rs
            from ats.execution.authorization_lifecycle import AuthorizationLifecycle
            original = rr.read_state
            def initial_read(path=None):
                state = original(path)
                rr.read_state = original
                print("READ_BASE", flush=True)
                sys.stdin.readline()
                return state
            rr.read_state = initial_read
            report = rs.perform_switch(cycle, expected_generation=1,
                                       lifecycle=AuthorizationLifecycle(cycle_status="executed", order_rows=[]),
                                       environment="paper", account="DU1")
            out = report.as_row()
        elif mode == "freeze":
            rr.freeze_submissions(actor=f"pid-{os.getpid()}")
            out = {"frozen": True}
        else:
            def on_place():
                if mode == "crash":
                    os._exit(73)
                if mode == "blocked_submit":
                    print("AT_BROKER", flush=True)
                    sys.stdin.readline()
            ib = FakeIB(accepted_file=accepted_file, on_place=on_place)
            entries = broker_for(ib).place_orders(
                [(TradeDecision(symbol="AMD", action="buy", qty=1), 1)], cycle,
                revision_no=1, wait=0)
            out = {"accepted": len(ib.accepted), "status": entries[0].status}
    except guard.BrokerWriteProhibited as exc:
        out = {"accepted": 0, "reason": exc.reason_code, "audit": guard.refusal_rows()}
    print(json.dumps(out), flush=True)
''')


def child(mode, generation, route, cycle, accepted_file):
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), str(root / "tests")])
    proc = subprocess.Popen([sys.executable, "-c", _CHILD, mode, str(generation),
                             route, cycle, str(accepted_file)], env=env, text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    assert proc.stdout.readline().strip() == "READY"
    return proc


def release(proc):
    proc.stdin.write("go\n")
    proc.stdin.flush()


def result(proc):
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err
    return json.loads(out.strip().splitlines()[-1])


def test_two_processes_race_same_intent_only_one_transport_accepts(authority, tmp_path):
    accepted = tmp_path / "accepted.jsonl"
    first = child("submit", 1, "A", "same", accepted)
    second = child("submit", 1, "A", "same", accepted)
    release(first)
    release(second)
    results = [result(first), result(second)]
    assert sorted(r["accepted"] for r in results) == [0, 1]
    assert len(accepted.read_text().splitlines()) == 1
    refused = next(r for r in results if not r["accepted"])
    assert refused["reason"] == "duplicate_order_intent"
    assert "prior_pid=" in refused["audit"][-1]["detail"]


def test_live_old_process_delayed_until_after_cutover_cannot_submit(authority, tmp_path):
    accepted = tmp_path / "accepted.jsonl"
    old = child("submit", 1, "A", "old", accepted)
    rr.switch_route(1, "B", environment="paper", account="DU1")
    current = child("submit", 2, "B", "new", accepted)
    release(old)
    release(current)
    assert result(old)["reason"] == guard.REASON_GENERATION_STALE
    assert result(current)["accepted"] == 1
    assert len(accepted.read_text().splitlines()) == 1


def test_cross_process_freeze_waits_for_actual_write_mutex(authority, tmp_path):
    accepted = tmp_path / "accepted.jsonl"
    writer = child("blocked_submit", 1, "A", "c", accepted)
    freezer = child("freeze", 1, "A", "unused", accepted)
    release(writer)
    assert writer.stdout.readline().strip() == "AT_BROKER"
    release(freezer)
    with pytest.raises(subprocess.TimeoutExpired):
        freezer.wait(timeout=0.2)
    assert not rr.read_freeze().frozen
    release(writer)
    assert result(writer)["accepted"] == 1
    assert result(freezer)["frozen"]
    later = child("submit", 1, "A", "later", accepted)
    release(later)
    assert result(later)["reason"] == guard.REASON_FROZEN
    assert len(accepted.read_text().splitlines()) == 1


def test_dead_process_leaves_unknown_intent_no_retry_or_unsafe_switch(authority, tmp_path):
    accepted = tmp_path / "accepted.jsonl"
    crashed = child("crash", 1, "A", "c", accepted)
    release(crashed)
    crashed.communicate(timeout=15)
    assert crashed.returncode == 73
    retry = child("submit", 1, "A", "c", accepted)
    release(retry)
    assert result(retry)["reason"] == "duplicate_order_intent"
    report = rs.perform_switch("B", expected_generation=1,
                               lifecycle=AuthorizationLifecycle(cycle_status="executed", order_rows=[]))
    assert not report.succeeded
    assert len(accepted.read_text().splitlines()) == 1
    assert rr.submission_receipts()[0]["status"] == "unknown"


def test_two_current_processes_can_submit_different_intents(authority, tmp_path):
    accepted = tmp_path / "accepted.jsonl"
    first = child("submit", 1, "A", "first", accepted)
    second = child("submit", 1, "A", "second", accepted)
    release(first)
    release(second)
    assert result(first)["accepted"] == result(second)["accepted"] == 1
    assert len(accepted.read_text().splitlines()) == 2


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_resident_process_rechecks_actual_authority_after_fault(authority, tmp_path, fault):
    accepted = tmp_path / "accepted.jsonl"
    resident = child("submit", 1, "A", "c", accepted)
    authority.unlink()
    if fault == "corrupt":
        authority.write_bytes(b"corrupt authority")
    release(resident)
    out = result(resident)
    assert out["reason"] == guard.REASON_AUTHORITY_UNREADABLE
    assert out["audit"][-1]["pid"] == resident.pid
    assert not accepted.exists()


def test_competing_switches_cannot_both_advance_from_same_prefreeze_snapshot(authority, tmp_path):
    accepted = tmp_path / "unused.jsonl"
    first = child("switch", 1, "A", "B", accepted)
    second = child("switch", 1, "A", "C", accepted)
    release(first)
    release(second)
    assert first.stdout.readline().strip() == "READ_BASE"
    assert second.stdout.readline().strip() == "READ_BASE"
    release(first)
    release(second)
    outcomes = [result(first), result(second)]
    assert sorted(out["succeeded"] for out in outcomes) == [False, True]
    assert rr.read_state().generation == 2
    assert not rr.read_freeze().frozen


def test_shadow_prohibition_also_covers_cancellation(authority):
    guard.startup(caller="shadow", mode="shadow")
    broker = broker_for(FakeIB())
    broker.session = lambda: pytest.fail("cancel session opened in shadow")
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        broker.cancel_all()
    assert exc.value.reason_code == guard.REASON_SHADOW_RUN


def test_actual_broker_after_a_b_a_rejects_original_a_process(authority, tmp_path):
    accepted = tmp_path / "accepted.jsonl"
    old = child("submit", 1, "A", "old", accepted)
    lifecycle = AuthorizationLifecycle(cycle_status="executed", order_rows=[])
    assert rs.perform_switch("B", expected_generation=1, lifecycle=lifecycle,
                             environment="paper", account="DU1").succeeded
    assert rs.perform_switch("A", expected_generation=2, lifecycle=lifecycle,
                             environment="paper", account="DU1").succeeded
    current = child("submit", 3, "A", "current", accepted)
    release(old)
    release(current)
    assert result(old)["reason"] == guard.REASON_GENERATION_STALE
    assert result(current)["accepted"] == 1
    assert len(accepted.read_text().splitlines()) == 1


def test_echoed_order_account_mismatch_stays_unknown_and_blocks_switch(authority):
    ib = FakeIB()
    place = ib.placeOrder
    def wrong_account(contract, order):
        trade = place(contract, order)
        trade.order.account = "DU2"
        return trade
    ib.placeOrder = wrong_account
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        submit(ib)
    assert exc.value.reason_code == guard.REASON_ACCOUNT_MISMATCH
    assert rr.submission_receipts()[0]["status"] == "unknown"


def test_second_order_is_rechecked_after_first_order_and_freeze(authority):
    ib = FakeIB()
    qualify = ib.qualifyContracts
    count = 0
    def before_second(*contracts):
        nonlocal count
        count += 1
        if count == 2:
            rr.freeze_submissions(actor="freeze-between-orders")
        return qualify(*contracts)
    ib.qualifyContracts = before_second
    items = [(TradeDecision(symbol=symbol, action="buy", qty=1), 1)
             for symbol in ("AMD", "NVDA")]
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        broker_for(ib).place_orders(items, "batch", wait=0, revision_no=1)
    assert exc.value.reason_code == guard.REASON_FROZEN
    assert [row["symbol"] for row in ib.accepted] == ["AMD"]
    assert len(rr.submission_receipts()) == 1
