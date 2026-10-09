"""Boundary enforcement at write points and migration-range ownership (5.3, 5.5).

Declaring a boundary is half of 5.2; the other half is a check where the write
actually happens. Without it, a config can say Phase F is live while
`record_approval` keeps writing where it always wrote.

5.5 is the quieter failure. A migration range that both sides assume the other
owns produces no error at all — only silence, weeks later.
"""

import pytest

from ats.workflow import cutover as co
from ats.workflow import cutover_wiring as cw


@pytest.fixture
def plane(tmp_path):
    path = str(tmp_path / "cutover.sqlite")
    cw.bootstrap_wired(actor="test", path=path)
    return path


# --------------------------------------------------------------------------- #
# 5.3 — write-point enforcement
# --------------------------------------------------------------------------- #

def test_an_approval_write_is_refused_when_the_boundary_is_disabled(plane):
    """The requirement's case. Refused, not redirected.

    A redirect would make the mistake look like it worked, and the approval would
    land somewhere the audit trail does not read.
    """
    co.set_route(co.APPROVAL_LIFECYCLE, co.ROUTE_DISABLED, actor="op",
                 reason="freeze approvals during the cutover", path=plane)

    with pytest.raises(cw.BoundaryWriteRefused) as excinfo:
        cw.guard_approval_write(route_reader=lambda b: co.read_boundary(b, plane).route)

    assert excinfo.value.boundary == co.APPROVAL_LIFECYCLE
    assert excinfo.value.reason_code == "boundary_disabled"


def test_an_approval_write_is_allowed_on_both_live_routes(plane):
    """A boundary still on legacy must keep working.

    Checking only "is it on target" would break the system before the cutover even
    starts.
    """
    legacy = cw.guard_approval_write(
        route_reader=lambda b: co.read_boundary(b, plane).route)
    assert legacy.allowed and legacy.route == co.ROUTE_LEGACY

    co.set_route(co.APPROVAL_LIFECYCLE, co.ROUTE_TARGET, actor="op",
                 reason="migrated", path=plane)
    target = cw.guard_approval_write(
        route_reader=lambda b: co.read_boundary(b, plane).route)
    assert target.allowed and target.route == co.ROUTE_TARGET


def test_a_ledger_publication_bypassing_the_clerk_boundary_is_refused(plane):
    """The requirement's second case.

    A publication that skips the Clerk puts rows in the ledger that no
    reconciliation pass will ever look at.
    """
    co.set_route(co.CLERK_PUBLICATION, co.ROUTE_DISABLED, actor="op",
                 reason="freeze publications", path=plane)

    with pytest.raises(cw.BoundaryWriteRefused) as excinfo:
        cw.guard_clerk_publication(
            route_reader=lambda b: co.read_boundary(b, plane).route)
    assert excinfo.value.boundary == co.CLERK_PUBLICATION


def test_an_analyst_output_write_is_refused_when_disabled(plane):
    co.set_route(co.ANALYST_OUTPUT, co.ROUTE_DISABLED, actor="op",
                 reason="freeze", path=plane)
    with pytest.raises(cw.BoundaryWriteRefused) as excinfo:
        cw.guard_analyst_output(route_reader=lambda b: co.read_boundary(b, plane).route)
    assert excinfo.value.boundary == co.ANALYST_OUTPUT


def test_a_refusal_names_where_it_had_nowhere_to_go(plane):
    """The operator needs to know whether to enable the boundary or fix a caller."""
    co.set_route(co.ANALYST_OUTPUT, co.ROUTE_DISABLED, actor="op", reason="x",
                 path=plane)
    with pytest.raises(cw.BoundaryWriteRefused) as excinfo:
        cw.guard_analyst_output(what="a macro review",
                                 route_reader=lambda b: co.read_boundary(b, plane).route)
    assert "a macro review" in str(excinfo.value)
    assert "enable it explicitly" in str(excinfo.value)


def test_the_three_guarded_boundaries_are_independent(plane):
    """Freezing approvals must not freeze analyst output or the Clerk."""
    co.set_route(co.APPROVAL_LIFECYCLE, co.ROUTE_DISABLED, actor="op", reason="x",
                 path=plane)
    reader = lambda b: co.read_boundary(b, plane).route  # noqa: E731

    assert cw.guard_clerk_publication(route_reader=reader).allowed is True
    assert cw.guard_analyst_output(route_reader=reader).allowed is True


def test_a_guard_defaults_to_reading_the_persistent_plane(plane):
    """No injected reader: the guard consults the authority, not the caller.

    Passing the reader is for tests; production must read the shared state or the
    boundary is only enforced where somebody remembered to look.
    """
    co.set_route(co.CLERK_PUBLICATION, co.ROUTE_DISABLED, actor="op", reason="x",
                 path=plane)
    import ats.workflow.cutover as module

    original = module.default_cutover_db_path
    module.default_cutover_db_path = lambda: plane
    try:
        with pytest.raises(cw.BoundaryWriteRefused):
            cw.guard_clerk_publication()
    finally:
        module.default_cutover_db_path = original


# --------------------------------------------------------------------------- #
# the declared wiring matches the code
# --------------------------------------------------------------------------- #

def test_every_boundary_declares_at_least_one_call_site(plane):
    assert co.unwired_boundaries(plane) == []


def test_the_declared_call_sites_exist_in_the_source():
    """A declared call site that does not exist is worse than none.

    It reads as wiring, so the pre-flight passes, and the boundary is not actually
    enforced anywhere. Handles both module-level functions and class methods, since
    the two forms appear in the same table.
    """
    import importlib

    for boundary, sites in cw.DECLARED_BOUNDARY_WIRING.items():
        for call_site, _authority, _semantics in sites:
            parts = call_site.split(".")
            resolved = None
            for split in range(len(parts) - 1, 0, -1):
                module_name = ".".join(parts[:split])
                try:
                    resolved = importlib.import_module(module_name)
                except ImportError:
                    continue
                target = resolved
                for attribute in parts[split:]:
                    target = getattr(target, attribute, None)
                    if target is None:
                        break
                break
            assert resolved is not None and target is not None, (
                f"{boundary} declares {call_site}, which does not resolve to a "
                f"module attribute")


def test_the_approval_boundary_names_the_repository_write_points(plane):
    """Both write methods, not just the guard.

    Declaring only the guard would let a caller reach `record_approval` directly.
    """
    sites = {w.call_site for w in co.wiring_of(co.APPROVAL_LIFECYCLE, plane)}
    assert any("record_approval" in site for site in sites)
    assert any("record_review" in site for site in sites)


def test_the_trader_boundary_names_the_submission_layer(plane):
    sites = {w.call_site for w in co.wiring_of(co.LIVE_TRADER, plane)}
    assert any("check_grant" in site for site in sites)
    assert any("validate_authorization" in site for site in sites)


def test_bootstrap_wires_all_six_at_once():
    import tempfile
    from pathlib import Path

    path = str(Path(tempfile.mkdtemp()) / "cutover.sqlite")
    cw.bootstrap_wired(path=path)
    assert set(co.all_boundaries(path)) == set(co.SIX_BOUNDARIES)
    assert co.unwired_boundaries(path) == []


def test_bootstrap_alone_leaves_boundaries_unwired():
    """The two are separable: an unwired plane is valid for a codebase that has not
    implemented a boundary, which is not this one."""
    import tempfile
    from pathlib import Path

    path = str(Path(tempfile.mkdtemp()) / "cutover.sqlite")
    co.bootstrap(path=path)
    assert len(co.unwired_boundaries(path)) == 6


# --------------------------------------------------------------------------- #
# 5.5 — migration-range ownership
# --------------------------------------------------------------------------- #

def test_both_migration_ranges_are_declared():
    """Chief projection rendering and the Sector CLI legacy read."""
    names = {b.slice_name for b in cw.OWNER_BOUNDARIES}
    assert names == {"chief_projection_rendering", "sector_cli_legacy_read"}


def test_each_range_names_its_migration_task():
    """A handover described but never scheduled is unowned in substance."""
    for boundary in cw.OWNER_BOUNDARIES:
        assert boundary.migration_task.strip(), boundary.slice_name
        assert boundary.done_when.strip(), boundary.slice_name


def test_an_undeclared_range_is_reported_unowned():
    assert set(cw.unowned_slices({})) == {
        "chief_projection_rendering", "sector_cli_legacy_read"}


def test_a_partially_declared_set_still_reports_the_missing_one():
    """The realistic state: one range was assigned and the other was forgotten."""
    unowned = cw.unowned_slices({"chief_projection_rendering": "alice"})
    assert unowned == ["sector_cli_legacy_read"]


def test_a_fully_declared_set_has_no_unowned_range():
    declared = {b.slice_name: "owner" for b in cw.OWNER_BOUNDARIES}
    assert cw.unowned_slices(declared) == []


def test_asserting_no_unowned_range_names_the_slice_and_its_task():
    """The refusal has to be actionable: which range, and who was supposed to take it."""
    with pytest.raises(cw.BoundaryWriteRefused) as excinfo:
        cw.assert_no_unowned_slice({"chief_projection_rendering": "alice"})

    message = str(excinfo.value)
    assert "sector_cli_legacy_read" in message
    assert "9.6" in message
    assert excinfo.value.reason_code == "migration_range_unowned"


def test_asserting_no_unowned_range_passes_when_declared():
    declared = {b.slice_name: "owner" for b in cw.OWNER_BOUNDARIES}
    cw.assert_no_unowned_slice(declared)
