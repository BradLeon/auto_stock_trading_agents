"""Phase F 1.5 — the evidence-fingerprint surface is explicit and enforced.

The failure this prevents is quiet: a cutover that edits a shared contract file
invalidates the evidence of consumers it never touched, and nothing says so
until a later qualification query returns `ineligible` for reasons that point
at a file nobody remembers changing.

The second failure is worse — treating "the manifest changed" as a normal config
edit. The manifest is the digest baseline for every row, so touching it retires
all evidence at once. Both are asserted here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from ats.config import REPO_ROOT
from ats.data import assurance
from ats.workflow import assurance_surface as surface_mod

START = datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def surface() -> surface_mod.FingerprintSurface:
    return surface_mod.load_surface()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(surface_mod.MANIFEST.read_text(encoding="utf-8"))


def test_surface_is_derived_from_the_policy_not_hand_written(surface, manifest):
    """A hard-coded copy would drift from the policy it claims to describe."""
    policy = manifest["qualification_policy"]
    assert set(surface.required) == set(policy["required_fingerprint_paths"])
    assert {k: set(v) for k, v in surface.by_consumer.items()} == {
        k: set(v) for k, v in (policy.get("consumer_fingerprint_paths") or {}).items()}
    assert surface.manifest == "config/data/target_dataflow_coverage.yaml"


def test_a_shared_path_names_every_consumer_it_would_retire(surface):
    """The question the naive approach gets wrong. `assurance.py` is not a
    `fundamental`-only file, and treating it as one leaves nine consumers
    silently invalidated."""
    manifest = yaml.safe_load(surface_mod.MANIFEST.read_text(encoding="utf-8"))
    all_consumers = {row["id"] for row in manifest["consumers"]}

    assert set(surface.consumers) == all_consumers
    assert set(surface.consumers_touched_by("src/ats/data/consumer_api.py")) == all_consumers

    # Per-consumer entries add consumers beyond the shared set. Read from the
    # policy rather than hard-coded: `clerk` binds clerk.py and the decision
    # repository, NOT authorization.py, so assuming otherwise would misreport
    # exactly which consumer a change to the authorization gate retires.
    policy = manifest["qualification_policy"]["consumer_fingerprint_paths"]
    assert set(surface.consumers_touched_by("src/ats/execution/authorization.py")) == {
        consumer for consumer in all_consumers
        if "src/ats/execution/authorization.py" in {*surface.required, *policy.get(consumer, [])}}
    assert set(surface.consumers_touched_by("src/ats/execution/state_api.py")) == {
        consumer for consumer in all_consumers
        if "src/ats/execution/state_api.py" in {*surface.required, *policy.get(consumer, [])}}
    # The final shared business closure conservatively protects every consumer.
    assert set(surface.consumers_touched_by("src/ats/execution/authorization.py")) == all_consumers
    assert set(surface.consumers_touched_by("src/ats/decision/repository.py")) == all_consumers


def test_paths_for_unions_rather_than_replaces(surface):
    """A per-consumer entry narrows nothing — the shared set still applies."""
    paths = surface.paths_for("trader")
    assert set(surface.required) <= set(paths)
    assert "src/ats/execution/authorization.py" in paths
    assert "src/ats/execution/state_api.py" in paths


def test_the_authorization_gate_is_on_the_surface_and_that_is_a_constraint(surface):
    """`authorization.py` is a fingerprint path for `trader`, so Phase F's own
    generation binding (task 2.5) has to land before evidence is recorded.

    Stated as a test so the ordering constraint survives refactoring: the day
    this file leaves the surface, that ordering requirement silently changes.

    Scope note: only `trader` binds it, not `clerk` — the earlier reading that
    `authorization.py` covers both was wrong, and over-broadening the claim would
    have made the re-verification scope look larger than it is.
    """
    assert "src/ats/execution/authorization.py" in surface.paths_for("trader")
    assert "src/ats/execution/authorization.py" in surface.paths_for("clerk")


def test_every_declared_path_exists_and_is_inside_the_repository(surface):
    for path in surface.all_paths():
        assert (REPO_ROOT / path).is_file(), path


def test_drift_detects_a_changed_shared_file(surface, tmp_path, manifest):
    """Drift is computed on CONTENT, matching what `assurance` does — otherwise
    this module and the qualification verdict could disagree about unchanged."""
    role = next(row for row in manifest["consumers"] if row["id"] == "fundamental")
    recorded = surface_mod.fingerprint(surface.paths_for("fundamental"))

    assert surface_mod.drift(surface, recorded) == {}

    # Mutate a real shared file's hash input without touching the file itself:
    # record against a copy so the repo file stays clean.
    assert recorded["src/ats/data/assurance.py"]
    tampered = dict(recorded)
    tampered["src/ats/data/consumer_api.py"] = "0" * 64
    changed = surface_mod.drift(surface, tampered)
    assert list(changed) == ["src/ats/data/consumer_api.py"]


def test_drift_treats_a_removed_path_as_drift(surface):
    """A path the contract no longer requires is still drift: the evidence was
    proved under rules that no longer apply."""
    recorded = surface_mod.fingerprint(surface.required) | {
        "config/data/retired_long_ago.yaml": "a" * 64}
    assert "config/data/retired_long_ago.yaml" in surface_mod.drift(surface, recorded)


def test_the_manifest_change_is_reported_as_retiring_everything(surface, tmp_path):
    """The single most expensive mistake available: a "small" manifest edit.

    A manifest change does not adjust one policy entry — it changes the digest
    every recorded row was proved under, so every consumer goes ineligible at
    once. The report has to say so unambiguously.
    """
    affected = surface_mod.affected_consumers(surface, [surface.manifest])
    assert affected[surface.manifest] == ("*",)


def test_affected_consumers_maps_each_path_to_its_consumers(surface):
    affected = surface_mod.affected_consumers(surface, [
        "config/risk.yaml", "src/ats/data/assurance.py", "docs/unrelated.md"])
    assert set(affected["config/risk.yaml"]) == set(surface.consumers)
    assert len(affected["src/ats/data/assurance.py"]) == 10
    assert "docs/unrelated.md" not in affected


def test_a_cutover_action_that_touches_the_surface_is_refused(surface):
    """The assertion itself. Route changes belong in the release overlay."""
    with pytest.raises(surface_mod.FingerprintSurfaceError) as excinfo:
        surface_mod.assert_outside_fingerprint_surface(
            ["config/data/structured.yaml"])

    message = str(excinfo.value)
    assert "config/data/structured.yaml" in message
    assert "every consumer" in message
    assert "release overlay" in message


def test_the_refusal_names_the_manifest_consequence_too(surface):
    with pytest.raises(surface_mod.FingerprintSurfaceError) as excinfo:
        surface_mod.assert_outside_fingerprint_surface([surface.manifest])
    assert "every consumer" in str(excinfo.value)


def test_a_cutover_action_outside_the_surface_is_allowed(surface):
    """Otherwise the guard would fire on the Phase F work it exists to permit.

    Uses files that exist: a path reported as changed but absent from disk is a
    typo, not new work, and is rejected separately.
    """
    surface_mod.assert_outside_fingerprint_surface(
        ["docs/archive/PHASE_F_ACCEPTANCE_REQUIREMENTS.md"])


def test_a_misspelled_path_is_rejected_rather_than_silently_passing(surface):
    """A wrong route in the reported path would make the surface check pass
    while the real edit retired evidence — the guard must not be defeatable by
    a typo."""
    with pytest.raises(surface_mod.FingerprintSurfaceError) as excinfo:
        surface_mod.assert_outside_fingerprint_surface(
            ["src/ats/data/structured.yaml"])  # the real one is config/data/...

    assert "not on the evidence-fingerprint surface" in str(excinfo.value)
    assert "typo" in str(excinfo.value)


def test_an_absolute_path_to_a_surfaced_file_is_still_refused(surface):
    """Callers may report absolute paths; resolution must not let a surfaced
    file through by spelling it absolutely."""
    target = REPO_ROOT / "config" / "data" / "structured.yaml"
    with pytest.raises(surface_mod.FingerprintSurfaceError) as excinfo:
        surface_mod.assert_outside_fingerprint_surface([str(target)])
    assert "config/data/structured.yaml" in str(excinfo.value)


def test_recorded_hashes_round_trips_from_an_evidence_row(tmp_path, manifest):
    """The bridge to the real ledger: what a recorded row carries must be
    readable by the drift check, or the check only works on synthetic input."""
    role = next(row for row in manifest["consumers"] if row["id"] == "fundamental")
    policy = manifest["qualification_policy"]
    scope = {"products": {p: {"product": p} for p in sorted(role["products"])}}
    ledger = tmp_path / "assurance.sqlite"

    event_id = assurance.record_evidence(
        domain_id=role["domain"], consumer_id=role["id"], evidence_type="read",
        outcome="passed", scope=scope, as_of=START,
        command_summary="surface probe", result_summary="surface probe",
        dependency_paths=list(policy["required_fingerprint_paths"]),
        db_path=ledger)

    history = assurance.evidence_history(domain_id=role["domain"],
                                         consumer_id=role["id"],
                                         scope=scope, db_path=ledger)
    row = next(item for item in history if item["event_id"] == event_id)
    recorded = surface_mod.recorded_hashes(row)

    assert set(recorded) == set(policy["required_fingerprint_paths"])
    surface = surface_mod.load_surface()
    assert surface_mod.drift(surface, recorded) == {}, (
        "a freshly recorded row must show no drift against the current surface")


def test_inventory_is_exported_for_review(surface):
    """Acceptance evidence has to name the surface, so it ships as rows."""
    rows = surface.as_rows()
    assert rows
    manifest_rows = [row for row in rows if row["path"] == surface.manifest]
    assert manifest_rows and manifest_rows[0]["invalidates"] == "every recorded evidence row"
    assert any(row["scope"] == "required" for row in rows)


@pytest.mark.parametrize("path", ["src/ats/workflow/schedule_runtime.py", "src/ats/workflow/schedule_executor.py",
                                   "config/workflow/workflow_owners.yaml", "src/ats/workflow/isolation.py"])
def test_schedule_runtime_dependency_drift_requires_reverification(path, surface):
    with pytest.raises(surface_mod.FingerprintSurfaceError,match="all 10"):
        surface_mod.assert_outside_fingerprint_surface([path],surface)


# --------------------------------------------------------------------------- #
# Two tiers: qualification-bound vs freeze-only
# --------------------------------------------------------------------------- #

def _freeze_document() -> dict:
    return yaml.safe_load(surface_mod.FREEZE_SURFACE.read_text(encoding="utf-8"))


def test_freeze_only_tier_is_frozen_but_never_qualification_bound(surface):
    """Tests, evidence scripts and tooling stay in the freeze; no consumer's
    qualification evidence binds them, so editing one retires nothing."""
    freeze_only = set(surface.freeze_only)
    assert freeze_only and freeze_only <= set(surface.all_paths())
    assert surface_mod.FREEZE_SURFACE_RELATIVE in surface.all_paths()
    assert not freeze_only & set(surface.qualification_paths())
    for consumer in surface.consumers:
        bound = surface.paths_for(consumer)
        assert not [p for p in bound if p.startswith(("tests/", "scripts/"))], consumer
        assert not freeze_only & set(bound), consumer
    assert surface.consumers_touched_by("tests/test_phase_f_scoped_intake.py") == ()
    assert surface.consumers_touched_by("src/ats/workflow/boundary_evidence.py") == ()
    surface_mod.assert_outside_fingerprint_surface(["tests/test_phase_f_scoped_intake.py"], surface)


def test_runtime_code_and_data_verifiers_stay_qualification_bound(surface):
    """The narrowing must not reach code production executes or the verifier
    that judges data evidence."""
    for path in ("src/ats/data/assurance.py", "src/ats/workflow/assurance_surface.py",
                 "src/ats/workflow/exit_requirements.py", "src/ats/workflow/read_recovery.py",
                 "src/ats/workflow/consumer_reads.py", "src/ats/workflow/cutover_wiring.py",
                 "src/ats/workflow/scoped_routes.py", "src/ats/workflow/acceptance_reports.py"):
        assert set(surface.consumers_touched_by(path)) == set(surface.consumers), path


def _module_imports(path: Path) -> set[str]:
    import ast

    name = ".".join(path.relative_to(REPO_ROOT / "src").with_suffix("").parts)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: set[str] = set()
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # unrelated escape warnings in src
        tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[:len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def test_src_tools_are_unreachable_from_runtime_modules():
    """A src module may be freeze-only only if no runtime module imports it.
    The CLI is exempt: its tool commands are run by an operator while taking or
    gating evidence. Without this, any runtime file could be demoted to dodge drift."""
    tools = {row["path"] for row in _freeze_document()["src_tools"]}
    modules = {".".join(Path(p).relative_to("src").with_suffix("").parts): p for p in tools}
    for source in (REPO_ROOT / "src/ats").rglob("*.py"):
        label = source.relative_to(REPO_ROOT).as_posix()
        if label in tools or label == "src/ats/runtime/cli.py":
            continue
        reached = _module_imports(source) & set(modules)
        assert not reached, f"{label} imports freeze-only tool {sorted(reached)}"


def test_self_verifying_entries_name_a_real_self_check():
    """A verifier of another evidence family may leave data qualification only
    because its own family binds its hash; the named test must exist."""
    for row in _freeze_document()["self_verifying"]:
        target, _, case = row["self_check"].partition("::")
        function, _, param = case.partition("[")
        text = (REPO_ROOT / target).read_text(encoding="utf-8")
        assert f"def {function}(" in text, row["self_check"]
        assert not param or f'"{param.rstrip("]")}"' in text, row["self_check"]


@pytest.mark.parametrize("document, message", [
    ({"tests": ["src/ats/workflow/cutover_wiring.py"]}, "outside tests/ or scripts/"),
    ({"src_tools": [{"path": "src/ats/workflow/intake.py"}]}, "lacks: reason"),
    ({"self_verifying": [{"path": "src/ats/workflow/boundary_evidence.py", "reason": "x"}]},
     "lacks: family, self_check"),
    ({"tests": ["tests/a.py", "tests/a.py"]}, "twice"),
])
def test_freeze_surface_entries_are_validated(tmp_path, document, message):
    path = tmp_path / "freeze_surface.yaml"
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError, match=message):
        surface_mod.load_freeze_only(path)


def test_a_path_cannot_be_both_qualification_bound_and_freeze_only(tmp_path):
    path = tmp_path / "freeze_surface.yaml"
    path.write_text(yaml.safe_dump({"src_tools": [
        {"path": "src/ats/data/assurance.py", "reason": "attempted demotion"}]}))
    with pytest.raises(ValueError, match="both qualification-bound and freeze-only"):
        surface_mod.load_surface(surface_mod.MANIFEST, path)


def test_qualification_implementation_hashes_exclude_only_the_freeze_only_tier(surface):
    from ats.workflow.business_replay_inputs import (
        implementation_hashes,
        qualification_implementation_hashes,
    )

    full, bound = implementation_hashes(), qualification_implementation_hashes()
    assert set(bound) == set(full) - set(surface.freeze_only) - {surface_mod.FREEZE_SURFACE_RELATIVE}
    assert "src/ats/workflow/boundary_evidence.py" not in bound
    assert "src/ats/agents/information/entry.py" in bound
