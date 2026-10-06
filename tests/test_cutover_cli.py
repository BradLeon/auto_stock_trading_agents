"""Cutover CLI entry points (task 5.12).

The requirement is that the output is machine-parseable and that the pre-flight
changes nothing. Both matter: the pre-flight is what an operator runs before every
decision, and it is also the thing most likely to be run in a loop.
"""

import json

import pytest

from ats.runtime import cli
from ats.workflow import cutover as co
from ats.workflow import cutover_wiring as cw


@pytest.fixture
def db(tmp_path, capsys):
    """A bootstrapped, wired plane.

    The bootstrap output is swallowed here so a test's own `capsys` read sees only
    its command — otherwise the two accumulate and `json.loads` fails on "extra
    data", which reads like a CLI bug rather than a fixture one.
    """
    path = str(tmp_path / "cutover.sqlite")
    code = cli.main(["cutover", "state", "--bootstrap", "--db", path])
    assert code == 0
    capsys.readouterr()
    return path


def _run(capsys, argv):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else {}


# --------------------------------------------------------------------------- #
# the entry point exists
# --------------------------------------------------------------------------- #

def test_cutover_is_its_own_command():
    with pytest.raises(SystemExit):
        cli.main(["cutover", "--help"])


def test_state_prints_all_six_boundaries(capsys, db):
    _, payload = _run(capsys, ["cutover", "state", "--db", db])
    assert set(payload) == set(co.SIX_BOUNDARIES)
    for boundary, state in payload.items():
        assert state["boundary"] == boundary
        assert state["route"] in co.VALID_ROUTES


def test_the_output_is_json_a_script_can_parse(capsys, db):
    """No prose, no trailing output — a runbook script pipes this into `jq`."""
    code = cli.main(["cutover", "state", "--db", db])
    raw = capsys.readouterr().out
    parsed = json.loads(raw)
    assert code == 0
    assert isinstance(parsed, dict)
    assert set(parsed) == set(co.SIX_BOUNDARIES)


def test_wiring_prints_the_declared_call_sites(capsys, db):
    _, payload = _run(capsys, ["cutover", "wiring", "--db", db])
    approval_sites = {w["call_site"]
                      for w in payload[co.APPROVAL_LIFECYCLE]}
    assert any("record_approval" in site for site in approval_sites)


# --------------------------------------------------------------------------- #
# preflight is read-only
# --------------------------------------------------------------------------- #

def test_preflight_exits_zero_on_a_wired_plane(capsys, db):
    code, payload = _run(capsys, ["cutover", "preflight", "--db", db])
    assert code == 0
    assert payload["ok"] is True


def test_preflight_exits_one_when_something_blocks(capsys, tmp_path):
    empty = str(tmp_path / "unwired.sqlite")
    co.bootstrap(path=empty)
    code, payload = _run(capsys, ["cutover", "preflight", "--db", empty])
    assert code == 1
    assert payload["ok"] is False


def test_preflight_changes_no_route(capsys, db):
    """The requirement's second clause, and the reason it is worth a test."""
    _, before = _run(capsys, ["cutover", "state", "--db", db])

    _run(capsys, ["cutover", "preflight", "--db", db])
    _run(capsys, ["cutover", "preflight", "--db", db])

    _, after = _run(capsys, ["cutover", "state", "--db", db])
    assert after == before


def test_preflight_with_an_activation_request_checks_the_boundary(capsys, db):
    code, payload = _run(capsys, [
        "cutover", "preflight", "--boundary", co.PROJECTION_READ,
        "--consumer-id", "macro", "--scope-json", '{"consumer": "macro"}',
        "--db", db])
    assert code == 0
    assert "activation_request" in payload["checked"]


def test_preflight_warns_about_the_disabled_live_trader(capsys, db):
    """5.9: trading off does not stop research, so this is a warning not a problem."""
    _, payload = _run(capsys, ["cutover", "preflight", "--db", db])
    assert payload["ok"] is True
    assert any("live trader is 'disabled'" in w for w in payload["warnings"])


# --------------------------------------------------------------------------- #
# route changes
# --------------------------------------------------------------------------- #

# The compatibility pairs, as the plane declares them. A cutover has to move a whole
# connected set, so the helper walks the chain rather than one edge: cutting the
# approval lifecycle also requires the Clerk publication to move with it.
_REQUIRED_PARTNERS = {
    co.ANALYST_OUTPUT: co.APPROVAL_LIFECYCLE,
    co.APPROVAL_LIFECYCLE: co.CLERK_PUBLICATION,
    co.CLERK_PUBLICATION: co.APPROVAL_LIFECYCLE,
    co.PROJECTION_READ: co.DISPATCHER_SCHEDULE,
    co.DISPATCHER_SCHEDULE: co.PROJECTION_READ,
}


def _connected_set(boundary: str) -> list[str]:
    """Every boundary reachable from `boundary` through the compatibility pairs."""
    seen = {boundary}
    queue = [boundary]
    while queue:
        current = queue.pop()
        partner = _REQUIRED_PARTNERS.get(current)
        if partner and partner not in seen:
            seen.add(partner)
            queue.append(partner)
    return sorted(seen)


def _move(capsys, db, boundary, reason="qualified", actor="alice"):
    """`set-route` through the CLI, moving the whole required set.

    Moving one boundary of a required pair always leaves the plane briefly
    incompatible, and `set-route` reports that as a refusal — which is the point.
    So the set is moved in an order where each step is accepted: partners first,
    the boundary under test last.
    """
    group = _connected_set(boundary)
    partners = [b for b in group if b != boundary]
    for other in partners:
        _run(capsys, ["cutover", "set-route", "--boundary", other,
                      "--route", co.ROUTE_TARGET, "--actor", actor,
                      "--reason", reason, "--db", db])
    code, payload = _run(capsys, [
        "cutover", "set-route", "--boundary", boundary,
        "--route", co.ROUTE_TARGET, "--actor", actor, "--reason", reason,
        "--db", db])
    assert code == 0, payload
    return code, payload


def test_set_route_moves_one_boundary_and_records_the_reason(capsys, db):
    code, payload = _move(capsys, db, co.ANALYST_OUTPUT,
                          reason="qualified for macro")
    assert code == 0
    assert payload["route"] == co.ROUTE_TARGET

    _, history = _run(capsys, ["cutover", "history", "--boundary",
                               co.ANALYST_OUTPUT, "--db", db])
    assert history[0]["actor"] == "alice"
    assert "qualified" in history[0]["reason"]


def test_set_route_requires_a_reason(capsys, db):
    with pytest.raises(SystemExit):
        cli.main(["cutover", "set-route", "--boundary", co.ANALYST_OUTPUT,
                  "--route", co.ROUTE_TARGET, "--db", db])


def test_set_route_refuses_an_incompatible_combination(capsys, db):
    """The move happens, then compatibility is checked so the refusal names both.

    Reported as JSON plus a non-zero exit rather than a traceback: a runbook script
    has to be able to read WHICH combination conflicted.
    """
    code, payload = _run(capsys, [
        "cutover", "set-route", "--boundary", co.PROJECTION_READ,
        "--route", co.ROUTE_TARGET, "--actor", "op", "--reason", "reads",
        "--db", db])
    assert code == 1
    assert payload["compatible"] is False
    assert "dispatcher_schedule" in payload["problem"]

    _, preflight_payload = _run(capsys, ["cutover", "preflight", "--db", db])
    assert preflight_payload["ok"] is False


def test_an_unknown_boundary_is_rejected_by_the_parser(capsys, db):
    """The CLI's boundary list is read from the module, so it cannot drift."""
    with pytest.raises(SystemExit):
        cli.main(["cutover", "set-route", "--boundary", "not_a_boundary",
                  "--route", co.ROUTE_TARGET, "--actor", "op", "--reason", "x",
                  "--db", db])


# --------------------------------------------------------------------------- #
# activation
# --------------------------------------------------------------------------- #

def test_activate_registers_a_scope(capsys, db):
    co.set_route(co.PROJECTION_READ, co.ROUTE_TARGET, actor="op",
                 reason="migrated with the schedule", path=db)
    co.set_route(co.DISPATCHER_SCHEDULE, co.ROUTE_TARGET, actor="op",
                 reason="migrated with the read", path=db)

    code, payload = _run(capsys, [
        "cutover", "activate", "--boundary", co.PROJECTION_READ,
        "--consumer-id", "macro", "--scope-json", '{"consumer": "macro"}',
        "--actor", "op", "--db", db])
    assert code == 0
    assert payload["activation_id"]

    _, active = _run(capsys, ["cutover", "active", "--boundary",
                              co.PROJECTION_READ, "--db", db])
    assert len(active) == 1


def test_activate_refuses_while_the_combination_is_incompatible(capsys, db):
    """A half-migrated pair blocks activation as well as `set-route`."""
    _run(capsys, ["cutover", "set-route", "--boundary", co.PROJECTION_READ,
                  "--route", co.ROUTE_TARGET, "--actor", "op", "--reason", "reads",
                  "--db", db])

    code, payload = _run(capsys, [
        "cutover", "activate", "--boundary", co.PROJECTION_READ,
        "--consumer-id", "macro", "--scope-json", '{"consumer": "macro"}',
        "--actor", "op", "--db", db])
    assert code == 1
    assert payload["activated"] is False


def test_activate_requires_an_actor(capsys, db):
    with pytest.raises(SystemExit):
        cli.main(["cutover", "activate", "--boundary", co.PROJECTION_READ,
                  "--consumer-id", "macro", "--db", db])


def test_activate_refuses_a_report_that_is_not_citable(capsys, db, tmp_path):
    """The evidence must cover THIS scope, or the activation rests on nothing."""
    report_db = str(tmp_path / "reports.sqlite")
    code, payload = _run(capsys, [
        "cutover", "activate", "--boundary", co.PROJECTION_READ,
        "--consumer-id", "macro", "--scope-json", '{"consumer": "macro"}',
        "--actor", "op", "--report-id", "typo", "--report-db", report_db,
        "--db", db])
    assert code == 1
    assert payload["activated"] is False
    assert any("typo" in problem for problem in payload["problems"])


def test_release_records_a_reason_and_drops_it_from_active(capsys, db):
    co.set_route(co.PROJECTION_READ, co.ROUTE_TARGET, actor="op", reason="x",
                 path=db)
    co.set_route(co.DISPATCHER_SCHEDULE, co.ROUTE_TARGET, actor="op", reason="x",
                 path=db)
    _run(capsys, ["cutover", "activate", "--boundary", co.PROJECTION_READ,
                  "--consumer-id", "macro", "--scope-json", '{"consumer": "macro"}',
                  "--actor", "op", "--db", db])

    code, payload = _run(capsys, [
        "cutover", "release", "--boundary", co.PROJECTION_READ,
        "--scope-json", '{"consumer": "macro"}', "--actor", "op",
        "--reason", "qualification revoked", "--db", db])
    assert code == 0
    assert payload["released"] is True

    _, active = _run(capsys, ["cutover", "active", "--boundary",
                              co.PROJECTION_READ, "--db", db])
    assert active == []


def test_release_exits_one_when_there_was_nothing_to_release(capsys, db):
    code, payload = _run(capsys, [
        "cutover", "release", "--boundary", co.PROJECTION_READ,
        "--scope-json", '{"consumer": "macro"}', "--actor", "op",
        "--reason", "x", "--db", db])
    assert code == 1
    assert payload["released"] is False


# --------------------------------------------------------------------------- #
# fallback and re-verification
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("argv_tail", [
    ["--fallback-proof", "missing"],
    ["--fallback-retired", "yes"],
    ["--fallback-available", "no"],
])
def test_fallback_exits_zero_only_when_all_three_hold(capsys, db, argv_tail):
    """Each condition alone is enough to refuse."""
    ok_code, ok_payload = _run(capsys, [
        "cutover", "fallback", "--route", "legacy", "--fallback-proof", "valid",
        "--fallback-retired", "no", "--fallback-available", "yes", "--db", db])
    assert ok_code == 0
    assert ok_payload["verdict"] == "ok"

    code, payload = _run(capsys, ["cutover", "fallback", "--route", "legacy",
                                  "--db", db, *argv_tail])
    assert code == 1, argv_tail
    assert payload["verdict"] != "ok", argv_tail


def test_fallback_distinguishes_retired_from_unavailable(capsys, db):
    _, retired = _run(capsys, [
        "cutover", "fallback", "--route", "legacy", "--fallback-proof", "valid",
        "--fallback-retired", "yes", "--db", db])
    _, broken = _run(capsys, [
        "cutover", "fallback", "--route", "legacy", "--fallback-proof", "valid",
        "--fallback-available", "no", "--db", db])

    assert retired["verdict"] == "blocked"
    assert broken["verdict"] == "unavailable"
    assert retired["reason_code"] != broken["reason_code"]


def test_reverify_exits_one_when_the_evidence_is_not_there(capsys, db):
    """Nothing is qualified yet, so the honest answer is a refusal."""
    code, payload = _run(capsys, [
        "cutover", "reverify", "--consumer-id", "macro",
        "--scope-json", '{"consumer": "macro"}', "--db", db])
    assert code == 1
    assert payload["approved"] is False
    assert payload["reasons"]


def test_a_malformed_scope_is_reported(capsys, db):
    with pytest.raises(SystemExit):
        cli.main(["cutover", "reverify", "--consumer-id", "macro",
                  "--scope-json", "{oops", "--db", db])


def test_the_boundary_choices_come_from_the_module():
    """A CLI list that drifts makes `--boundary` accept values the plane rejects."""
    from ats.runtime.cli import _cutover_boundary_names

    assert _cutover_boundary_names() == list(co.SIX_BOUNDARIES)


def test_the_declared_wiring_and_the_cli_agree(capsys, db):
    """The runbook's switch names and the CLI's must be the module's."""
    _, payload = _run(capsys, ["cutover", "wiring", "--db", db])
    assert set(payload) == set(cw.DECLARED_BOUNDARY_WIRING)
