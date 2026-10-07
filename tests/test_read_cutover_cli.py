"""CLI surface for the read-path executor (task 9.1–9.5).

The CLI is where the authorisation gate is easiest to defeat, so most of these
assert what it *refuses*:

- a switch with no `--authorisation`
- a switch citing an authorisation nobody recorded
- an authorisation recorded without an authoriser, an issuer or an expiry

and one assertion on the exit code, because a partial switch must not read as
success to a script: `plan` exits 0, `switch` exits non-zero unless every scope
moved. Without that, an operator's shell reports success on a batch where half
the consumers stayed behind.
"""

import json

import pytest

from ats.runtime import cli
from ats.workflow import batch_manifest as bm
from ats.workflow import cutover as plane
from ats.workflow import cutover_wiring as wiring

FUTURE = "2099-01-01T00:00:00+00:00"


@pytest.fixture
def dbs(tmp_path):
    manifest = str(tmp_path / "batches.sqlite")
    cutover = str(tmp_path / "cutover.sqlite")
    wiring.bootstrap_wired(actor="test", path=cutover)
    bm.declare_batch(bm.CutoverBatch(
        batch_id="b-research", batch_class=bm.RESEARCH_READ, owner="o",
        scope={"consumers": ["layer", "sector"]}, old_route="legacy",
        new_route="target", observation_window="w", success_criteria="s",
        stop_conditions="x", fallback_route="legacy", fallback_proof="valid",
        fallback_retired="no", fallback_available="yes",
        fallback_drill_ref="drill-1"), path=manifest)
    return manifest, cutover


def _run(argv) -> int:
    try:
        return cli.main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)


def _argv(action, manifest, cutover, *extra):
    return ["read", action, "--db", manifest, "--cutover-db", cutover, *extra]


def _authorize(manifest, cutover, *, scope="layer,sector") -> int:
    """Record an authorisation; returns its exit code.

    Callers that assert on a *later* command's stdout pass `capsys` and call
    `capsys.readouterr()` themselves — this helper deliberately does not touch
    capsys, so a test that forgets to clear sees the write's JSON prepended to
    the next command's output rather than a mysteriously unparseable string.
    """
    return _run(_argv("authorize", manifest, cutover,
                      "--authorisation", "DEP-1", "--authorised-by", "owner",
                      "--issued-by", "owner", "--scope", scope,
                      "--valid-until", FUTURE))


# --- the gate ----------------------------------------------------------------

def test_switch_without_an_authorisation_refuses(dbs, capsys):
    """The state in which an unauthorised change looks most reasonable."""
    manifest, cutover = dbs
    code = _run(_argv("switch", manifest, cutover, "--batch-id", "b-research",
                      "--apply"))
    out = capsys.readouterr().out
    assert "refusing" in out
    assert "--authorisation is required" in out
    assert plane.all_boundaries(cutover)[plane.PROJECTION_READ].route == "legacy"


def test_switch_citing_an_unrecorded_authorisation_refuses(dbs, capsys):
    """An authorisation passed only on the command line cannot be audited later."""
    manifest, cutover = dbs
    code = _run(_argv("switch", manifest, cutover, "--batch-id", "b-research",
                      "--authorisation", "NEVER-RECORDED", "--apply"))
    out = capsys.readouterr().out
    assert code != 0
    assert "no recorded authorisation" in out
    assert plane.all_boundaries(cutover)[plane.PROJECTION_READ].route == "legacy"


@pytest.mark.parametrize("missing", ["--authorised-by", "--issued-by",
                                     "--valid-until"])
def test_an_authorisation_missing_an_auditable_field_is_not_stored(dbs, capsys, missing):
    """Incomplete means unauditable, not weaker-but-valid.

    Asserted on stderr because that is where argparse's usage errors go; the
    substantive check is the second half — nothing reached the store.
    """
    manifest, cutover = dbs
    fields = {"--authorisation": "DEP-1", "--authorised-by": "owner",
              "--issued-by": "owner", "--scope": "layer",
              "--valid-until": FUTURE}
    del fields[missing]

    argv = _argv("authorize", manifest, cutover)
    for flag, value in fields.items():
        argv += [flag, value]

    code = _run(argv)
    captured = capsys.readouterr()
    assert code != 0
    # argparse names the flag it is missing, so the message is actionable without
    # this test having to restate it.
    assert f"{missing} is required" in captured.err

    from ats.workflow import read_cutover as rc

    assert rc.read_authorisation("DEP-1", path=manifest) is None, (
        "an unauditable authorisation reached the store")


def test_a_switch_that_cannot_move_everything_exits_non_zero(dbs, capsys,
                                                             monkeypatch):
    """A partial switch is not the requested outcome and must not read as success
    to a script."""
    manifest, cutover = dbs
    _authorize(manifest, cutover)
    capsys.readouterr()

    from ats.workflow import read_cutover as rc

    monkeypatch.setattr(rc, "_scope_eligibility",
                        lambda cid, scope, qualification_reader: {
                            "status": "eligible" if cid == "layer" else "ineligible"})

    code = _run(_argv("switch", manifest, cutover, "--batch-id", "b-research",
                      "--authorisation", "DEP-1", "--apply"))
    out = capsys.readouterr().out
    assert code != 0, "a partial switch exited 0"
    assert "partially_switched" in out
    assert "`sector`" in out, "the holdout was not named"


# --- the happy path, minus the permission problem ----------------------------

def test_a_fully_qualified_batch_switches_and_exits_zero(dbs, capsys, monkeypatch):
    manifest, cutover = dbs
    _authorize(manifest, cutover)
    capsys.readouterr()

    from ats.workflow import read_cutover as rc

    monkeypatch.setattr(rc, "_scope_eligibility",
                        lambda cid, scope, qualification_reader: {"status": "eligible"})

    code = _run(_argv("switch", manifest, cutover, "--batch-id", "b-research",
                      "--authorisation", "DEP-1", "--apply"))
    assert code == 0
    states = plane.all_boundaries(cutover)
    for boundary in rc._switch_chain(plane.PROJECTION_READ):
        assert states[boundary].route == "target"


def test_decide_mode_is_the_default_and_moves_nothing(dbs, capsys, monkeypatch):
    """9.1's runbook has an operator re-run this in a loop before deciding."""
    manifest, cutover = dbs
    _authorize(manifest, cutover)
    capsys.readouterr()

    from ats.workflow import read_cutover as rc

    monkeypatch.setattr(rc, "_scope_eligibility",
                        lambda cid, scope, qualification_reader: {"status": "eligible"})

    code = _run(_argv("switch", manifest, cutover, "--batch-id", "b-research",
                      "--authorisation", "DEP-1"))
    out = capsys.readouterr().out
    assert code == 0
    assert plane.all_boundaries(cutover)[plane.PROJECTION_READ].route == "legacy"
    assert "实际改动路由：**否**" in out, (
        "the report must state that nothing changed — an operator reading "
        "'switched' beside an unchanged route would be misled")


# --- reading records back ----------------------------------------------------

def test_a_recorded_authorisation_is_readable_back(dbs, capsys):
    manifest, cutover = dbs
    _authorize(manifest, cutover)
    capsys.readouterr()

    code = _run(_argv("show-auth", manifest, cutover, "--authorisation", "DEP-1"))
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["authorised_by"] == "owner"
    assert payload["scope"] == "layer,sector"


def test_history_reports_the_attempt_even_when_nothing_moved(dbs, capsys):
    """"We looked and it was blocked" is the reading an operator needs when the
    blocker is later resolved."""
    manifest, cutover = dbs
    _run(_argv("switch", manifest, cutover, "--batch-id", "b-research",
               "--apply"))  # no authorisation -> refused
    capsys.readouterr()

    code = _run(_argv("history", manifest, cutover, "--json"))
    assert code == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["changed"] == 0
    assert rows[0]["batch_id"] == "b-research"


def test_the_live_trader_cannot_be_switched_through_the_read_cli(dbs, capsys,
                                                                monkeypatch):
    """Otherwise the 11.1 live authorisation gate is decorative."""
    manifest, cutover = dbs
    bm.declare_batch(bm.CutoverBatch(
        batch_id="b-live", batch_class=bm.LIVE_TRADER, owner="o",
        scope={"consumers": ["trader"]}, old_route="disabled", new_route="target",
        observation_window="w", success_criteria="s", stop_conditions="x",
        fallback_route="disabled", fallback_proof="valid", fallback_retired="no",
        fallback_available="yes", fallback_drill_ref="drill-1"), path=manifest)
    _authorize(manifest, cutover, scope="trader")

    from ats.workflow import read_cutover as rc

    monkeypatch.setattr(rc, "_scope_eligibility",
                        lambda cid, scope, qualification_reader: {"status": "eligible"})

    code = _run(_argv("switch", manifest, cutover, "--batch-id", "b-live",
                      "--authorisation", "DEP-1", "--apply"))
    assert code != 0
    assert plane.all_boundaries(cutover)[plane.LIVE_TRADER].route == "disabled"
