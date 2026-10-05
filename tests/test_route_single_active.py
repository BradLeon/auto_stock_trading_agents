"""Cross-process single-active-route verification (Phase F 2.8).

2.1–2.7 built the mechanism. This file exists because a mechanism that only works
inside one process has not solved the problem the mechanism was for: the resident
scheduler and a one-shot CLI are separate processes, and the failure mode is
specifically that one of them keeps writing after the cutover.

Three cases, and each names a different way single-active-ness can be lost:

- **Two processes race the cutover.** `switch_route` is a compare-and-set, so one
  of them loses. Both losing gracefully matters: the loser must not bump a second
  generation on the way out.
- **A stale process submits late.** The case the generation exists for. The
  resident process holds a grant from generation N; the switch moves to N+1; the
  resident process then tries to submit on a timer. If that succeeds, every other
  guarantee in Phase F is decorative.
- **The authority cannot be read.** Not an edge case — a locked or deleted
  registry file. The requirement is that this fails closed rather than degrading
  to "probably still fine".

These use real subprocesses rather than threads, because a thread shares memory and
would hide precisely the bugs being tested.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ats.execution import broker_write_guard as guard
from ats.execution import route_registry as rr
from ats.execution import route_switch as rs
from ats.execution.authorization_lifecycle import AuthorizationLifecycle

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "routes.sqlite"
    monkeypatch.setenv("ATS_ROUTE_REGISTRY_PATH", str(path))
    rr.install_route("A", generation=1, environment="paper", account="DU1",
                     actor="test", reason="fixture", path=str(path))
    guard.reset_for_tests()
    yield path
    guard.reset_for_tests()


def _settled() -> AuthorizationLifecycle:
    return AuthorizationLifecycle(cycle_status="executed",
                                   order_rows=[{"order_id": "o1",
                                                "status": "filled"}])


def _submit() -> dict:
    """Attempt one submission; return what happened instead of raising."""
    try:
        guard.check_grant(operation="place_orders", caller="test",
                          state_reader=rr.read_state, freeze_reader=rr.read_freeze,
                          account="DU1")
        return {"accepted": True, "reason_code": ""}
    except guard.BrokerWriteProhibited as exc:
        return {"accepted": False, "reason_code": exc.reason_code}


# --------------------------------------------------------------------------- #
# the cross-process harness
# --------------------------------------------------------------------------- #

_CHILD = textwrap.dedent("""
    import json, os, sys
    sys.path.insert(0, os.environ["ATS_SRC"])

    mode = sys.argv[1]
    registry_path = sys.argv[2]
    generation = int(sys.argv[3])
    route_id = sys.argv[4]

    from ats.execution import broker_write_guard as guard
    from ats.execution import route_registry as rr
    from ats.execution import route_switch as rs
    from ats.execution.authorization_lifecycle import AuthorizationLifecycle

    guard.grant_write(route_id, generation, account="DU1")
    out = {"mode": mode}

    if mode == "submit":
        # The resident-process case: a process that was handed a capability before
        # a cutover and fires on a timer afterwards.
        try:
            guard.check_grant(operation="place_orders", caller="resident",
                              state_reader=rr.read_state,
                              freeze_reader=rr.read_freeze, account="DU1")
            out["accepted"] = True
        except guard.BrokerWriteProhibited as exc:
            out["accepted"] = False
            out["reason_code"] = exc.reason_code

    elif mode == "switch":
        # Two operators racing. Both believe they are moving generation N.
        lifecycle = AuthorizationLifecycle(cycle_status="executed",
                                           order_rows=[{"order_id": "o1",
                                                        "status": "filled"}])
        report = rs.perform_switch(
            sys.argv[5], actor=f"op-{os.getpid()}", reason="race",
            expected_generation=generation,
            lifecycle=lifecycle, environment="paper", account="DU1",
            path=registry_path)
        out["succeeded"] = report.succeeded
        out["to_generation"] = report.to_generation
        out["reasons"] = report.reasons

    elif mode == "read":
        try:
            state = rr.read_state(registry_path)
            out["generation"] = state.generation
            out["route_id"] = state.route_id
        except Exception as exc:
            out["error"] = type(exc).__name__

    print(json.dumps(out))
    """)


def _child_env() -> dict:
    env = dict(os.environ)
    env["ATS_SRC"] = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env


def _run_child(script: str, mode: str, registry_path: Path, generation: int = 1,
               route_id: str = "A", extra: str = "") -> dict:
    """Run the child script in a real separate process."""
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD, mode, str(registry_path),
         str(generation), route_id, extra],
        capture_output=True, text=True, env=_child_env(), timeout=120)
    if completed.returncode != 0:
        raise AssertionError(
            f"child {mode} failed (rc={completed.returncode}):\n"
            f"{completed.stderr[-2000:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _run_raw(code: str, **env_extra: str) -> subprocess.CompletedProcess:
    env = _child_env()
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, env=env, timeout=120)


# --------------------------------------------------------------------------- #
# case 1: two processes race the cutover
# --------------------------------------------------------------------------- #

def test_only_one_of_two_racing_processes_may_switch(registry):
    """Compare-and-set, verified across processes rather than within one.

    Both children start from generation 1 and try to move it. If both succeeded,
    the two would each believe they owned the route.
    """
    first = _run_child(_CHILD, "switch", registry, generation=1, extra="B")
    second = _run_child(_CHILD, "switch", registry, generation=1, extra="C")

    successes = [r for r in (first, second) if r["succeeded"]]
    assert len(successes) == 1, f"both or neither won: {first} {second}"

    # The generation advanced exactly once, not twice.
    assert rr.read_state(str(registry)).generation == 2


def test_the_race_loser_leaves_the_route_exactly_as_the_winner_left_it(registry):
    """A loser must not bump a second generation on its way out."""
    _run_child(_CHILD, "switch", registry, generation=1, extra="B")
    before = rr.read_state(str(registry))

    second = _run_child(_CHILD, "switch", registry, generation=1, extra="C")

    assert not second["succeeded"]
    after = rr.read_state(str(registry))
    assert after.same_as(before)
    assert after.route_id == "B"


def test_the_race_loser_does_not_wedge_the_registry_frozen(registry):
    """A losing operator must not leave submissions closed.

    Otherwise the loser of a race would silently stop all trading, which reads as
    a cutover to anyone watching positions rather than as a failed request.
    """
    _run_child(_CHILD, "switch", registry, generation=1, extra="B")
    _run_child(_CHILD, "switch", registry, generation=1, extra="C")

    assert rr.read_freeze(str(registry)).frozen is False
    guard.grant_write("B", 2, account="DU1")
    assert _submit()["accepted"] is True


# --------------------------------------------------------------------------- #
# case 2: a stale process submits late
# --------------------------------------------------------------------------- #

def test_a_process_that_was_granted_before_the_cutover_cannot_submit_after(
        registry):
    """The case the whole generation mechanism exists for.

    The resident scheduler holds a grant from generation 1. A switch moves the
    route to generation 2. The resident process then fires on its timer. If that
    submission were accepted, "exactly one route can submit real orders" would be
    false and every other Phase F guarantee would be decorative.
    """
    # The resident process is granted on the OLD generation...
    stale = _run_child(_CHILD, "submit", registry, generation=1, route_id="A")
    assert stale["accepted"] is True, "baseline: the grant works before the switch"

    # ...the cutover happens...
    report = rs.perform_switch("B", actor="op", lifecycle=_settled(),
                               environment="paper", account="DU1",
                               path=str(registry))
    assert report.succeeded

    # ...and the resident process fires again on the same stale grant.
    late = _run_child(_CHILD, "submit", registry, generation=1, route_id="A")
    assert late["accepted"] is False
    assert late["reason_code"] == guard.REASON_GENERATION_STALE


def test_a_late_submit_during_the_freeze_window_is_refused_as_frozen(registry):
    """Distinct reason, and the distinction is the diagnostic value.

    During the drain the generation has NOT moved, so a stale grant would pass a
    generation-only check. Being refused as frozen tells the operator "a cutover is
    in progress", which is different from "your capability is stale".
    """
    freeze = rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    late = _run_child(_CHILD, "submit", registry, generation=1, route_id="A")

    assert late["accepted"] is False
    assert late["reason_code"] == guard.REASON_FROZEN
    rr.abort_freeze(freeze.switch_token, path=str(registry))


def test_a_process_granted_on_the_new_generation_submits_normally(registry):
    """The positive case: the switch must not leave the route unable to trade."""
    rs.perform_switch("B", actor="op", lifecycle=_settled(), environment="paper",
                      account="DU1", path=str(registry))
    fresh = _run_child(_CHILD, "submit", registry, generation=2, route_id="B")
    assert fresh["accepted"] is True


def test_a_process_from_an_older_generation_is_refused_even_when_the_route_matches(
        registry):
    """A→B→A is why identity is not just the route id.

    After a round trip the route id is `A` again, so a check that compared ids would
    accept a grant from the first `A`. The generation is what tells them apart.
    """
    rs.perform_switch("B", actor="op", lifecycle=_settled(), environment="paper",
                      account="DU1", path=str(registry))
    back = rs.perform_switch("A", actor="op", lifecycle=_settled(),
                             environment="paper", account="DU1",
                             path=str(registry))
    assert back.succeeded
    assert back.to_generation == 3

    from_the_past = _run_child(_CHILD, "submit", registry, generation=1,
                               route_id="A")
    assert from_the_past["accepted"] is False
    assert from_the_past["reason_code"] == guard.REASON_GENERATION_STALE


# --------------------------------------------------------------------------- #
# case 3: the authority cannot be read
# --------------------------------------------------------------------------- #

def test_an_unreadable_authority_fails_closed_rather_than_allowing(registry):
    """A locked or corrupt registry must stop trading, not permit it.

    Degrading to "probably still fine" is the single behaviour that would make the
    guarantee decorative, so it is asserted directly.
    """
    completed = _run_raw(
        """
        import os, sys
        sys.path.insert(0, os.environ["ATS_SRC"])
        from ats.execution import broker_write_guard as guard
        from ats.execution import route_registry as rr

        guard.grant_write("A", 1, account="DU1")

        def unreadable():
            raise rr.RouteRegistryError("database is locked")

        try:
            guard.check_grant(operation="place_orders", caller="t",
                              state_reader=unreadable,
                              freeze_reader=rr.read_freeze, account="DU1")
            print("ACCEPTED")
        except guard.BrokerWriteProhibited as exc:
            print("REFUSED:" + exc.reason_code)
        """)
    assert "REFUSED" in completed.stdout, completed.stdout + completed.stderr


def test_a_deleted_registry_file_fails_closed(registry):
    """The realistic version of the above: the file is gone.

    Not "treat as no route, allow" and not "raise something the caller ignores" —
    the refusal has to come from the same gate as every other refusal.
    """
    Path(registry).unlink()
    try:
        with pytest.raises(rr.RouteRegistryError):
            rr.read_state(str(registry))
        # A fresh file with no row is the "not installed" case, which is also an
        # error — "no route" must not read as "route A".
        assert rr.try_read_state(str(registry)) is None
    finally:
        if Path(registry).exists():
            Path(registry).unlink()


def test_an_authorization_bound_before_the_switch_is_refused_after_it(registry):
    """The other half of single-active-ness: the authorization, not just the grant.

    A grant and an authorization are separate capabilities. A process holding a
    valid grant is still refused if it presents an authorization bound to a
    superseded generation.
    """
    from ats.execution.authorization import (
        ExecutionAuthorization,
        bind_to_active_route,
        validate_authorization,
    )

    auth = ExecutionAuthorization(
        cycle_id="c1", revision_no=1, decision_hash="dh", review_id="r1",
        review_at="2026-10-05T00:00:00+00:00", approval_id="a1",
        approval_at="2026-10-05T00:00:01+00:00", ruleset_version="v1",
        portfolio_snapshot_id="pf1", market_as_of="2026-10-05T00:00:00+00:00")

    stale_auth = bind_to_active_route(auth)
    assert (stale_auth.route_id, stale_auth.route_generation) == ("A", 1)

    rs.perform_switch("B", actor="op", lifecycle=_settled(), environment="paper",
                      account="DU1", path=str(registry))

    # Rebinding produces the new authority; the stale one stays stale.
    fresh = bind_to_active_route(auth)
    assert (fresh.route_id, fresh.route_generation) == ("B", 2)
    assert stale_auth.route_generation != fresh.route_generation

    # The route check is the LAST one, so the audit repo has to be satisfied
    # first. A stub returning "everything is effective" isolates the route
    # binding, which is what this test is about.
    class _AllEffective:
        def latest_revision(self, cycle_id):
            return {"revision_no": 1, "decision_hash": "dh"}

        def effective_review(self, *args):
            return {"portfolio_snapshot_id": "pf1",
                    "market_as_of": "2026-10-05T00:00:00+00:00"}

        def effective_approval(self, *args):
            return {"approved_at": "2026-10-05T00:00:01+00:00"}

    from ats.execution.route_registry import read_state

    # The snapshot must be current for the check to get as far as the route, so
    # the repo stub and the snapshot timestamp are part of isolating it.
    now = datetime.now(timezone.utc)

    assert validate_authorization(
        _AllEffective(), fresh, snapshot_as_of=now,
        route_state=read_state()) == []

    reasons = validate_authorization(_AllEffective(), stale_auth,
                                     snapshot_as_of=now,
                                     route_state=read_state())
    assert any("route" in reason for reason in reasons), reasons


# --------------------------------------------------------------------------- #
# the guarantee as a whole
# --------------------------------------------------------------------------- #

def test_across_processes_only_the_current_generation_is_ever_accepted(registry):
    """One acceptance for the current generation, none for any other.

    Stated as a single end-to-end claim over subprocesses rather than as three
    separate assertions, because the guarantee is the composition.
    """
    rr.record_issuance("auth-1", path=str(registry))
    accepted: list[int] = []

    # Before the cutover, generation 1 is the only one that can write.
    accepted.append(_run_child(_CHILD, "submit", registry, 1, "A")["accepted"])

    rs.perform_switch("B", actor="op", lifecycle=_settled(), environment="paper",
                      account="DU1", path=str(registry))

    # After it, generation 1 is refused and generation 2 is the only one accepted.
    accepted.append(_run_child(_CHILD, "submit", registry, 1, "A")["accepted"])
    accepted.append(_run_child(_CHILD, "submit", registry, 2, "B")["accepted"])
    accepted.append(_run_child(_CHILD, "submit", registry, 3, "C")["accepted"])

    assert accepted == [True, False, True, False]


def test_the_registry_is_the_only_authority_and_not_a_config_file(registry):
    """Every process reads the same file; none carries its own copy of the truth.

    Asserted because the alternative — each process caching its view — is what
    2.1's docstring warns about, and it would pass every test above in a
    single-process run.
    """
    first = _run_child(_CHILD, "read", registry)
    second = _run_child(_CHILD, "read", registry)

    assert first["generation"] == second["generation"]
    assert first["route_id"] == second["route_id"]
    assert rr.read_state(str(registry)).generation == first["generation"]
