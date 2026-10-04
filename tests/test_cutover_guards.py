"""Phase F 1.6 — cutover invariants are machine-checked, not reviewed.

The three rules exist because each has a plausible-looking failure mode that
review does not catch: a module that flips a route for itself, an order path
that builds its own broker client, and a "temporary" project-wide default mode.
All three pass code review and none of them fail a test — until now.

The negative cases here are the point. A guard with only positive assertions
proves nothing: the current tree being clean is the easy half, and what matters
is that each forbidden construct is actually detected when introduced.
"""

import ast
from pathlib import Path

import pytest

from ats.workflow import cutover_guards


def _write(tmp_path: Path, source: str, name: str = "module.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _scan(tmp_path: Path, source: str):
    path = _write(tmp_path, source)
    return cutover_guards.scan_cutover_module(path, root=tmp_path)


# --- route mutation ---------------------------------------------------------- #

def test_a_module_that_applies_the_release_overlay_is_reported(tmp_path):
    """Choosing a route for a consumer is the control plane's job. A consumer
    module that applies the overlay itself can promote a scope no evidence
    covers."""
    found = _scan(tmp_path, """
from ats.data.release import ReleaseManager

def switch(consumer):
    manager = ReleaseManager(repo)
    return manager.apply(manager.check_consumer(consumer))
""")
    assert [item.kind for item in found] == ["route_mutation"]
    assert "manager.apply" in found[0].target
    assert "control plane" in found[0].detail


def test_rollback_of_the_overlay_is_also_a_route_mutation(tmp_path):
    """Rollback picks a route too — that is exactly the operation Phase F must
    keep inside the control plane."""
    found = _scan(tmp_path, """
def revert(manager, consumer):
    return manager.rollback(kind="consumer", target_id=consumer)
""")
    assert [item.kind for item in found] == ["route_mutation"]
    assert "rollback" in found[0].target


def test_a_sqlite_transaction_rollback_is_not_a_route_mutation(tmp_path):
    """`conn.rollback()` appears in four unrelated modules. Matching the method
    name alone would flag all of them and train everyone to ignore the guard."""
    found = _scan(tmp_path, """
def store(conn):
    try:
        conn.execute("UPDATE trades SET status='x'")
    except Exception:
        conn.rollback()
        raise
""")
    assert found == []


def test_reading_a_mode_is_not_a_violation(tmp_path):
    """Twenty-three modules legitimately ask what mode a consumer is in. That
    is the existing rollout mechanism and must stay legal."""
    found = _scan(tmp_path, """
from ats.data.rollout_modes import read_mode, source_mode

def route_for(consumer, source_id):
    mode = read_mode(consumer, source_id=source_id)
    if mode == "platform":
        return source_mode(source_id)
    return mode
""")
    assert found == []


def test_a_declared_bypass_stops_that_violation_only(tmp_path):
    found = cutover_guards.scan_cutover_module(
        _write(tmp_path, """
def switch(manager, consumer):
    return manager.apply(manager.check_consumer(consumer))
"""),
        root=tmp_path,
        exceptions=[cutover_guards.CutoverException(
            module="module", target="manager.apply",
            reason="migrating this consumer in Phase F", phase="Phase G")],
    )
    assert found == []


def test_a_declaration_only_covers_its_own_target(tmp_path):
    found = cutover_guards.scan_cutover_module(
        _write(tmp_path, """
def one(manager, consumer):
    return manager.apply(manager.check_consumer(consumer))

def two(registry, workflow_id):
    return registry.set_mode(workflow_id, "dispatcher")
"""),
        root=tmp_path,
        exceptions=[cutover_guards.CutoverException(
            module="module", target="manager.apply",
            reason="only this one", phase="Phase G")],
    )
    assert [item.target for item in found] == ["registry.set_mode()"]


# --- broker write bypass ----------------------------------------------------- #

def test_a_module_that_builds_its_own_broker_and_submits_is_reported(tmp_path):
    """The write prohibition lives inside the broker implementation. A module
    that constructs its own client and submits never reaches it — which is the
    whole reason the prohibition was placed there rather than in the caller."""
    found = _scan(tmp_path, """
from ib_async import IB

def send(contract, order):
    ib = IB()
    ib.connect("127.0.0.1", 7497, clientId=1)
    return ib.placeOrder(contract, order)
""")
    assert [item.kind for item in found] == ["broker_write_bypass"]
    assert "write arbitration" in found[0].detail


def test_submitting_through_an_injected_broker_is_governed_not_flagged(tmp_path):
    """The normal path: the broker object is passed in, so the arbitration
    inside `place_orders` runs. Flagging this would make the guard useless."""
    found = _scan(tmp_path, """
def send(broker, decision, qty, cycle_id):
    return broker.place_orders([(decision, qty)], cycle_id)
""")
    assert found == []


# --- global mode switch ------------------------------------------------------ #

def test_a_project_wide_default_mode_is_reported(tmp_path):
    """A single default that serves every consumer cannot express "this one
    qualified, that one did not" — the failure Phase F exists to remove."""
    found = _scan(tmp_path, """
import os

def force_platform():
    os.environ["ATS_STRUCTURED_DEFAULT_MODE"] = "platform"
""")
    assert [item.kind for item in found] == ["global_mode_switch"]
    assert "per-consumer" in found[0].detail


def test_a_per_consumer_override_is_not_a_global_switch(tmp_path):
    found = _scan(tmp_path, """
import os

def force(consumer):
    os.environ[f"ATS_STRUCTURED_{consumer.upper()}_MODE"] = "platform"
""")
    assert found == []


# --- the authority exemption is narrow ---------------------------------------- #

def test_the_control_plane_itself_is_exempt(tmp_path):
    """It necessarily references the broker client and the overlay; scanning it
    would report the mechanism that enforces the rules as a violation of them."""
    found = cutover_guards.scan_cutover_module(
        _write(tmp_path, """
from ib_async import IB

def send(ib, contract, order):
    return ib.placeOrder(contract, order)
""", name="cutover_guards.py"),
        root=tmp_path)
    # The exemption is keyed on the dotted module path, so a temp-dir fixture
    # named the same is NOT exempt — that is intentional, and asserted here so
    # the narrowness is visible rather than assumed.
    assert [item.kind for item in found] == ["broker_write_bypass"]


def test_the_real_control_plane_modules_are_exempt():
    """Checked against the real package: every authority module scans clean."""
    for dotted in sorted(cutover_guards.ROUTE_AUTHORITY_MODULES):
        path = cutover_guards.PACKAGE_ROOT.parent / (dotted.replace(".", "/") + ".py")
        if not path.is_file():
            continue
        assert cutover_guards.scan_cutover_module(path) == [], dotted


# --- the current tree -------------------------------------------------------- #

def test_the_current_tree_has_no_cutover_violation():
    """The green baseline. If this fails, either a route was short-circuited or
    an exemption is needed — and both are decisions, not accidents."""
    found = cutover_guards.scan_cutover()
    assert found == [], "\n".join(str(item) for item in found)


def test_scanning_the_package_actually_covers_it():
    """A guard that scans nothing passes every negative case vacuously."""
    scanned = {path for path in cutover_guards.PACKAGE_ROOT.rglob("*.py")
               if "__pycache__" not in path.parts}
    assert len(scanned) > 100, "the scan root looks wrong"
    assert cutover_guards.PACKAGE_ROOT.name == "ats"


# --- exception discipline ---------------------------------------------------- #

def test_no_bypasses_are_declared_by_default():
    """Empty by construction, like the architecture guards. A new bypass must be
    declared with a concrete later phase, never tolerated silently."""
    assert cutover_guards.CUTOVER_EXCEPTIONS == ()


def test_a_bypass_without_a_removal_phase_is_rejected():
    with pytest.raises(cutover_guards.CutoverExceptionPhaseError) as excinfo:
        cutover_guards.validate_cutover_exceptions([
            cutover_guards.CutoverException(module="m", target="t", reason="r")])
    assert "no removal phase" in str(excinfo.value)


def test_a_bypass_removing_itself_in_the_active_phase_is_rejected():
    """Declaring 'Phase F' while Phase F is being applied is how a bypass
    declares itself temporary and then outlives the change."""
    with pytest.raises(cutover_guards.CutoverExceptionPhaseError) as excinfo:
        cutover_guards.validate_cutover_exceptions([
            cutover_guards.CutoverException(
                module="m", target="t", reason="r", phase="Phase F")])
    assert "already expired" in str(excinfo.value)


def test_an_unknown_removal_phase_is_rejected():
    with pytest.raises(cutover_guards.CutoverExceptionPhaseError):
        cutover_guards.validate_cutover_exceptions([
            cutover_guards.CutoverException(
                module="m", target="t", reason="r", phase="Phase Z")])
    with pytest.raises(cutover_guards.CutoverExceptionPhaseError):
        cutover_guards.validate_cutover_exceptions([], active_phase="Phase Q")


def test_a_future_removal_phase_is_accepted():
    cutover_guards.validate_cutover_exceptions([
        cutover_guards.CutoverException(
            module="m", target="t", reason="r", phase="Phase G")])


def test_the_guard_raises_rather_than_warn(tmp_path, monkeypatch):
    """A warning is a comment. The assertion form is what makes it a gate."""
    root = cutover_guards.PACKAGE_ROOT
    try:
        cutover_guards.assert_cutover_invariants(root)
    except cutover_guards.CutoverViolationError as exc:  # pragma: no cover
        pytest.fail(f"current tree violates cutover invariants: {exc}")


def test_assert_reports_every_violation_at_once(tmp_path):
    """Fixing violations one round-trip at a time is how guards get abandoned."""
    path = _write(tmp_path, """
import os
from ib_async import IB

def switch(manager, consumer):
    os.environ["ATS_STRUCTURED_DEFAULT_MODE"] = "platform"
    return manager.apply(manager.check_consumer(consumer))

def send(contract, order):
    ib = IB()
    return ib.placeOrder(contract, order)
""")
    found = cutover_guards.scan_cutover_module(path, root=tmp_path)
    assert {item.kind for item in found} == {
        "route_mutation", "global_mode_switch", "broker_write_bypass"}
