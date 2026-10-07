"""Phase F 8.1–8.5 — the batch CLI.

The exit code is the load-bearing part here. A gate script has to be able to fail
on a blocked batch, so `dry-run` and `show` exit non-zero unless every checked
batch is switchable — and the read-only actions must be provably read-only, since
an operator will run them in a loop before deciding.
"""

from __future__ import annotations

import json

import pytest

from ats.runtime import cli
from ats.workflow import batch_manifest as bm
from ats.workflow import cutover as plane


@pytest.fixture
def wired(tmp_path):
    """A wired control plane plus an empty manifest, both in throwaway files."""
    from ats.workflow import cutover_wiring as wiring

    cutover_db = str(tmp_path / "cutover.sqlite")
    manifest_db = str(tmp_path / "batches.sqlite")
    wiring.bootstrap_wired(actor="test", path=cutover_db)
    return cutover_db, manifest_db


def _declare(manifest_db, batch_id="b-research", batch_class=bm.RESEARCH_READ,
             **kw):
    """Declare a batch through the public API, not the CLI."""
    defaults = dict(
        owner="ats.data.products", old_route="legacy", new_route="target",
        observation_window="2 trading days", success_criteria="no divergence",
        stop_conditions="any unaccepted divergence",
        fallback_route="legacy", fallback_proof="valid", fallback_retired="no",
        fallback_available="yes", fallback_drill_ref="drill-1")
    defaults.update(kw)
    return bm.declare_batch(
        bm.CutoverBatch(batch_id=batch_id, batch_class=batch_class, **defaults),
        path=manifest_db)


def _run(capsys, argv):
    code = cli.main(argv)
    out = capsys.readouterr().out
    try:
        return code, json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return code, out


def _batch_argv(action, manifest_db, cutover_db, *extra):
    # `batch` is the subcommand and `action` its positional; putting the action
    # first makes argparse read it as the top-level command.
    return ["batch", action, "--db", manifest_db, "--cutover-db", cutover_db,
            *extra]


# --------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------- #

def test_batch_is_its_own_command():
    with pytest.raises(SystemExit):
        cli.main(["batch", "--help"])


def test_every_batch_class_is_accepted_by_the_parser(wired, capsys):
    """A CLI list that drifts from the module's makes argparse reject a class the
    code actually supports — the failure reads as a code bug, not a CLI one.

    Asserted on the REFUSAL TEXT: every class here is refused for a missing field
    (never for being an unknown choice), which is what proves the parser knows it.
    """
    _, manifest_db = wired
    for name in bm.BATCH_CLASSES:
        code, payload = _run(capsys, [
            "batch", "declare", "--batch-class", name, "--batch-id", f"x-{name}",
            "--db", manifest_db])
        assert code == 1, name
        assert "invalid choice" not in payload.get("error", ""), name
        assert payload["declared"] is False
        assert "must declare a" in payload["error"], name


def test_list_on_an_empty_manifest_says_so_rather_than_crashing(capsys, wired):
    cutover_db, manifest_db = wired
    code, text = _run(capsys, _batch_argv("list", manifest_db, cutover_db))
    assert code == 0
    assert "逐批切流清单" in text


# --------------------------------------------------------------------------- #
# declare
# --------------------------------------------------------------------------- #

def test_declare_prints_the_stored_record(capsys, wired):
    cutover_db, manifest_db = wired
    code, payload = _run(capsys, _batch_argv(
        "declare", manifest_db, cutover_db,
        "--batch-id", "b-cli", "--batch-class", bm.RESEARCH_READ,
        "--owner", "o", "--old-route", "legacy", "--new-route", "target",
        "--observation-window", "w", "--success-criteria", "s",
        "--stop-reason", "x", "--fallback-route", "legacy",
        "--fallback-proof", "valid", "--fallback-drill", "d1"))
    assert code == 0
    assert payload["batch_id"] == "b-cli"
    assert payload["fallback_drill_ref"] == "d1"


def test_declare_requires_a_batch_id(capsys, wired):
    cutover_db, manifest_db = wired
    with pytest.raises(SystemExit):
        cli.main(["batch", "declare", "--batch-class", bm.RESEARCH_READ,
                  "--db", manifest_db])
    capsys.readouterr()


def test_declare_refuses_a_batch_missing_its_review_fields(capsys, wired):
    """A batch with no observation window / success criteria / stop conditions is
    refused through the CLI as a plain message, not a traceback — an operator who
    left a field blank should be told which one, and the batch must not be stored.
    """
    cutover_db, manifest_db = wired
    code, payload = _run(capsys, _batch_argv(
        "declare", manifest_db, cutover_db,
        "--batch-id", "b-thin", "--batch-class", bm.RESEARCH_READ,
        "--owner", "o", "--old-route", "legacy", "--new-route", "target"))
    assert code == 1
    assert payload["declared"] is False
    assert "observation_window" in payload["error"]
    assert bm.list_batches(path=manifest_db) == []


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #

def test_list_shows_every_declared_batch(capsys, wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    _declare(manifest_db, "b-two", batch_class=bm.SCHEDULE)
    code, payload = _run(capsys, _batch_argv("list", manifest_db, cutover_db,
                                            "--json"))
    assert code == 0
    assert {row["batch_id"] for row in payload} == {"b-one", "b-two"}


def test_list_filters_by_class(capsys, wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    _declare(manifest_db, "b-two", batch_class=bm.SCHEDULE)
    _, payload = _run(capsys, _batch_argv(
        "list", manifest_db, cutover_db, "--batch-class", bm.SCHEDULE, "--json"))
    assert [row["batch_id"] for row in payload] == ["b-two"]


def test_list_renders_a_table_when_not_asked_for_json(capsys, wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    _, text = _run(capsys, _batch_argv("list", manifest_db, cutover_db))
    assert "`b-one`" in text
    assert "drill-1" in text


# --------------------------------------------------------------------------- #
# dry-run — the exit code is the point
# --------------------------------------------------------------------------- #

def test_dry_run_exits_non_zero_when_a_batch_is_blocked(capsys, wired):
    """A gate script has to be able to fail here."""
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-blocked", fallback_proof="missing")
    code, _ = _run(capsys, _batch_argv("dry-run", manifest_db, cutover_db))
    assert code == 1


def test_dry_run_exits_zero_when_everything_is_switchable(capsys, wired,
                                                          monkeypatch):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-ok")
    _declare(manifest_db, "b-collection", batch_class=bm.COLLECTION_PUBLISH,
             old_route="", new_route="", direct_verification=True,
             fallback_route="", fallback_proof="", fallback_retired="",
             fallback_available="", fallback_drill_ref="")
    _stub_eligible(monkeypatch)
    code, _ = _run(capsys, _batch_argv("dry-run", manifest_db, cutover_db))
    assert code == 0


def _stub_eligible(monkeypatch):
    """Make live qualification report every consumer eligible.

    The production verdict is 0/10, so without this every CLI test would be
    asserting the ineligible path — which is a real test, but not the one that
    checks the exit-code semantics.
    """
    from ats.data import assurance

    monkeypatch.setattr(assurance, "qualification",
                        lambda **kwargs: {"status": "eligible", "reasons": []})


def test_dry_run_changes_no_route(capsys, wired, monkeypatch):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    before = {b: s.route for b, s in plane.all_boundaries(cutover_db).items()}
    history_before = sum(len(plane.boundary_history(b, cutover_db))
                         for b in plane.SIX_BOUNDARIES)

    _run(capsys, _batch_argv("dry-run", manifest_db, cutover_db))

    assert {b: s.route for b, s in plane.all_boundaries(cutover_db).items()} \
        == before
    assert sum(len(plane.boundary_history(b, cutover_db))
               for b in plane.SIX_BOUNDARIES) == history_before


def test_dry_run_reports_the_real_verdict_with_names(capsys, wired):
    """The live state is 0/10 eligible, so the report must name the consumers
    rather than say "some qualification is missing"."""
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-research")
    _, text = _run(capsys, _batch_argv("dry-run", manifest_db, cutover_db))
    assert "`b-research`" in text
    # The batch HAS a proven, drilled fallback, so the live blocker is
    # qualification — 0/10 today. Naming that is the point of the report.
    assert "blocked" in text
    assert "not eligible" in text
    assert "layer" in text or "macro" in text


def test_dry_run_json_carries_the_check_breakdown(capsys, wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    _, payload = _run(capsys, _batch_argv("dry-run", manifest_db, cutover_db,
                                          "--json"))
    assert payload[0]["checks"]["fallback_safe"] is True, (
        "the default batch declares a proven, drilled fallback")
    assert payload[0]["checks"]["fallback_drill_recorded"] is True
    assert payload[0]["checks"]["qualified"] is False, (
        "live qualification is 0/10 today; the report must say so rather than "
        "letting a dry-run look passable")
    assert payload[0]["outcome"] == bm.BLOCKED


def test_dry_run_states_that_it_moves_nothing(capsys, wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    _, text = _run(capsys, _batch_argv("dry-run", manifest_db, cutover_db))
    assert "不改变任何路由" in text


# --------------------------------------------------------------------------- #
# show
# --------------------------------------------------------------------------- #

def test_show_prints_the_batch_and_its_dry_run(capsys, wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    code, payload = _run(capsys, _batch_argv(
        "show", manifest_db, cutover_db, "--batch-id", "b-one"))
    assert payload["batch"]["batch_id"] == "b-one"
    assert "outcome" in payload["dry_run"]
    assert code == 1  # blocked in the live state


def test_show_exits_zero_for_a_switchable_batch(capsys, wired, monkeypatch):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    _stub_eligible(monkeypatch)
    code, _ = _run(capsys, _batch_argv("show", manifest_db, cutover_db,
                                       "--batch-id", "b-one"))
    assert code == 0


def test_show_requires_a_batch_id(capsys, wired):
    cutover_db, manifest_db = wired
    with pytest.raises(SystemExit):
        cli.main(_batch_argv("show", manifest_db, cutover_db))
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# drift
# --------------------------------------------------------------------------- #

def test_drift_reports_a_stop_for_a_withdrawn_qualification(capsys, wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    code, payload = _run(capsys, _batch_argv(
        "drift", manifest_db, cutover_db, "--batch-id", "b-one",
        "--qualification-status", "ineligible", "--serving", "yes"))
    assert payload["action"] == bm.STOP
    assert code == 1


def test_drift_holds_when_the_batch_is_not_serving(capsys, wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    code, payload = _run(capsys, _batch_argv(
        "drift", manifest_db, cutover_db, "--batch-id", "b-one",
        "--qualification-status", "degraded", "--serving", "no"))
    assert payload["action"] == bm.HOLD
    assert code == 0


def test_drift_never_says_fall_back_for_a_withdrawal(capsys, wired):
    """The self-defeating case: the fallback is gated on the qualification that
    just disappeared."""
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    _, payload = _run(capsys, _batch_argv(
        "drift", manifest_db, cutover_db, "--batch-id", "b-one",
        "--qualification-status", "ineligible", "--serving", "yes"))
    assert payload["action"] != bm.FALL_BACK
    assert "stops rather than falls back" in payload["reason"]


def test_drift_falls_back_only_for_a_degraded_verdict_while_serving(capsys,
                                                                    wired):
    cutover_db, manifest_db = wired
    _declare(manifest_db, "b-one")
    code, payload = _run(capsys, _batch_argv(
        "drift", manifest_db, cutover_db, "--batch-id", "b-one",
        "--qualification-status", "degraded", "--serving", "yes"))
    assert payload["action"] == bm.FALL_BACK
    assert code == 1
