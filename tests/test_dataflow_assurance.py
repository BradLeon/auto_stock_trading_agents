"""Phase F 1.3 — the dataflow assurance mechanism as a regression suite.

`ats.data.assurance` decides whether a consumer may be cut over, and until now
it had no pytest coverage at all: its behaviour was asserted only by
`scripts/verify_dataflow_qualification.py`, which returns exit codes rather than
reporting which case failed. Phase F makes cutover routing depend on this module,
so "the script passed once" is not a foundation.

These tests are the same probes, restructured so a failure names the mechanism.
The script stays as end-to-end evidence; when the two disagree, the spec is the
authority and the script is the thing to fix (see 1.4).

Everything here runs against a throwaway ledger under `tmp_path` and asserts
against a *copy* of the coverage manifest wherever drift is the subject — a test
that edits the checked-in manifest would invalidate every other test in the
repository.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from ats.config import REPO_ROOT
from ats.data import assurance

COVERAGE = REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml"

# `as_of` must be recent: the policy's TTLs run from the evidence's own stamp,
# and the shortest is 1 day (`technical`, `trader`, `clerk`). A fixed past date
# — or even "yesterday" — would push those consumers into `stale:*` and assert
# nothing about the mechanism under test. Date-bearing fixtures derive from this
# instead of hard-coding, and the TTL behaviour gets its own explicit test.
START = datetime.now(timezone.utc) - timedelta(seconds=5)

CONSUMER = "fundamental"
OTHER_CONSUMER = "sector"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(COVERAGE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def policy(manifest: dict) -> dict:
    return manifest["qualification_policy"]


@pytest.fixture(scope="module")
def role(manifest: dict) -> dict:
    return next(row for row in manifest["consumers"] if row["id"] == CONSUMER)


@pytest.fixture(scope="module")
def other_role(manifest: dict) -> dict:
    return next(row for row in manifest["consumers"] if row["id"] == OTHER_CONSUMER)


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    return tmp_path / "assurance.sqlite"


@pytest.fixture
def paths(manifest: dict, policy: dict) -> list[str]:
    return list(policy["required_fingerprint_paths"]) + [
        "src/ats/data/contract_validation.py",
    ]


@pytest.fixture
def scope(role: dict) -> dict:
    """Scope in the shape `_rollback_failures` expects.

    `products` maps each product to its own sub-scope, because the rollback
    drill's `scope_hash` is computed over `scope["products"][product]` — a flat
    list would make every drill hash wrong for reasons that have nothing to do
    with what is being tested.
    """
    return {"products": {product: {"product": product, "vintage": "2026-06-30"}
                         for product in sorted(role["products"])},
            "purpose": "pytest-mechanism", "data_as_of": START.isoformat()}


@pytest.fixture
def other_scope(other_role: dict) -> dict:
    return {"products": {product: {"product": product, "vintage": "2026-06-30"}
                         for product in sorted(other_role["products"])},
            "purpose": "pytest-mechanism"}


@pytest.fixture
def rollback_proof(policy: dict, role: dict, scope: dict) -> list[dict]:
    """A rollback drill row per product, shaped from the policy.

    Route strings, the `scope_hash` derivation and the required fields all come
    from the contract rather than being hard-coded, so a policy change surfaces
    as a failure here instead of as a fixture that quietly stops qualifying.
    """
    import hashlib
    import json

    stamp = datetime.now(timezone.utc).isoformat()
    proofs = []
    for product in sorted(role["products"]):
        product_scope = scope["products"][product]
        proofs.append({
            "product": product,
            "route": policy["rollback_routes"][product],
            "status": "passed",
            "read_only": True,
            "input_refs": [f"ref-{product}"],
            "payload_hash": hashlib.sha256(
                json.dumps({"product": product}, sort_keys=True).encode()).hexdigest(),
            "scope_hash": hashlib.sha256(
                json.dumps(product_scope, sort_keys=True).encode()).hexdigest(),
            "native_packet_equal": True,
            "reason": "", "as_of": stamp,
        })
    return proofs


@pytest.fixture
def required_inputs(manifest: dict, policy: dict) -> list[dict]:
    return [{"input_id": name, "source_status": "succeeded",
             "run_id": "synthetic-source-run", "checked_at": START.isoformat(),
             "input_refs": ["synthetic-accepted-version"]}
            for name in policy["required_inputs"][CONSUMER]]


@pytest.fixture
def sec_failure(manifest: dict, policy: dict) -> dict:
    return {
        "input_id": "sec_edgar_filing_body", "source_status": "failed",
        "run_id": "synthetic-sec-failure-run", "task_id": "synthetic-sec-task",
        "checked_at": START.isoformat(),
        "reason": "synthetic_official_body_transport_failure",
        "stage": "official_body_fetch", "input_refs": [],
        "checks": {name: True for name in
                   policy["optional_inputs"]["sec_edgar_filing_body"]["required_checks"]},
    }


class Recorder:
    """Thin wrapper mirroring the script's `record`/`query` closures."""

    def __init__(self, ledger: Path, paths: list[str], policy: dict,
                 scope: dict, role: dict) -> None:
        self.ledger, self.paths, self.policy = ledger, paths, policy
        self.scope, self.role = scope, role

    def record(self, kind: str, *, who: dict | None = None, at: datetime = START,
               outcome: str = "passed", details: dict | None = None,
               prereqs: list[str] | None = None, selected_scope: dict | None = None,
               dependencies: list[str] | None = None) -> str:
        who = who or self.role
        chosen = selected_scope or self.scope
        return assurance.record_evidence(
            domain_id=who["domain"], consumer_id=who["id"], evidence_type=kind,
            outcome=outcome, scope=chosen, as_of=at,
            command_summary="pytest mechanism replay; no qualification asserted",
            result_summary="synthetic probe",
            prerequisite_event_ids=prereqs, details=details,
            dependency_paths=(dependencies or self.paths) + list(
                self.policy.get("consumer_fingerprint_paths", {}).get(who["id"], [])),
            db_path=self.ledger)

    def query(self, *, who: dict | None = None, selected_scope: dict | None = None,
              **kwargs) -> dict:
        who = who or self.role
        return assurance.qualification(
            domain_id=who["domain"], consumer_id=who["id"],
            contract_version=who["contract_version"],
            scope=selected_scope or self.scope,
            db_path=kwargs.pop("db_path", self.ledger), **kwargs)

    def qualify_all(self, *, who: dict | None = None, selected_scope: dict | None = None,
                    inputs: list[dict] | None = None, sec: dict | None = None,
                    proofs: list[dict] | None = None) -> dict[str, str]:
        """Record every required evidence type; returns the event ids by type."""
        who = who or self.role
        ids: dict[str, str] = {}
        for kind in who["required_evidence"]:
            if kind == "completeness":
                details = {"inputs": (inputs if inputs is not None else [])
                           + ([sec] if sec else [])}
            elif kind == "rollback":
                details = {"rollback": proofs} if proofs is not None else {}
            else:
                details = {}
            ids[kind] = self.record(kind, who=who, selected_scope=selected_scope,
                                    details=details)
        return ids


@pytest.fixture
def recorder(ledger, paths, policy, scope, role) -> Recorder:
    return Recorder(ledger, paths, policy, scope, role)


# --- fail-closed basics ------------------------------------------------------ #

def test_missing_ledger_is_ineligible(recorder):
    """No evidence, no qualification. A missing table must not read as a pass."""
    assert recorder.query()["status"] == "ineligible"


def test_all_required_evidence_yields_eligible(recorder, required_inputs, sec_failure,
                                               rollback_proof):
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure,
                         proofs=rollback_proof)
    result = recorder.query()
    assert result["status"] == "eligible", result["reasons"]
    assert result["read_only"] is True
    assert result["contract_version"] == "target-dataflow-v1"


def test_each_missing_evidence_type_blocks(recorder, required_inputs, sec_failure,
                                           rollback_proof):
    """Every required type is load-bearing, not decorative."""
    for kind in recorder.role["required_evidence"]:
        recorder.qualify_all(inputs=required_inputs, sec=sec_failure,
                             proofs=rollback_proof)
        before = recorder.query()["status"]
        assert before == "eligible"
        # A failed outcome for that type is the cheapest way to remove it.
        recorder.record(kind, outcome="failed")
        assert recorder.query()["status"] == "ineligible", kind


# --- SEC optional exception -------------------------------------------------- #

def test_sec_body_failure_is_a_nonblocking_gap_not_an_ineligibility(
        recorder, required_inputs, sec_failure, rollback_proof):
    result = recorder.qualify_all(inputs=required_inputs, sec=sec_failure,
                                  proofs=rollback_proof) and recorder.query()
    assert result["status"] == "eligible", result["reasons"]
    gap = result["accepted_nonblocking_gaps"][0]
    assert gap["source_status"] == "failed"
    assert gap["blocking"] is False
    assert gap["source_verified"] is False, "an accepted gap must not mark the source verified"
    assert gap["policy_version"] == "sec-body-optional-v1"
    assert gap["run_id"] and gap["task_id"]


@pytest.mark.parametrize("failing", ["company_financials", "defeatbeta_earnings_transcript",
                                     "defeatbeta_sec_filing_index"])
def test_sec_exception_does_not_waive_a_required_input(recorder, required_inputs,
                                                       sec_failure, rollback_proof,
                                                       failing):
    """The exception is scoped to the SEC body, not a general waiver."""
    changed = [{**row, "source_status": "failed", "reason": "synthetic_failure"}
               if row["input_id"] == failing else row for row in required_inputs]
    recorder.qualify_all(inputs=changed, sec=sec_failure, proofs=rollback_proof)
    assert recorder.query()["status"] == "ineligible"


def test_sec_input_without_verified_checks_still_blocks(recorder, required_inputs,
                                                        sec_failure, rollback_proof):
    """Accepting a failed SEC body is only safe if the consumer proved it handles
    the empty case. Empty checks = unproven = blocking."""
    recorder.qualify_all(inputs=required_inputs,
                         sec={**sec_failure, "checks": {}}, proofs=rollback_proof)
    assert recorder.query()["status"] == "ineligible"


def test_missing_sec_status_is_not_silently_treated_as_optional(recorder, required_inputs,
                                                                rollback_proof):
    """Dropping the SEC row entirely must not read as "the exception applied"."""
    recorder.qualify_all(inputs=required_inputs, proofs=rollback_proof)
    assert recorder.query()["status"] == "ineligible"


# --- rollback evidence ------------------------------------------------------- #

def test_passed_label_without_rollback_proof_is_rejected(recorder, required_inputs,
                                                         sec_failure, rollback_proof):
    """A `rollback` evidence row that carries no drill proves nothing."""
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure,
                         proofs=rollback_proof)
    recorder.record("rollback")
    assert recorder.query()["status"] == "ineligible"


def test_one_product_drill_does_not_qualify_the_whole_role(recorder, required_inputs,
                                                            sec_failure, rollback_proof):
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    recorder.record("rollback", details={"rollback": rollback_proof[:1]})
    assert recorder.query()["status"] == "ineligible"


def test_rollback_for_the_wrong_scope_cannot_be_reused(recorder, required_inputs,
                                                       sec_failure, rollback_proof):
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    recorder.record("rollback", details={"rollback": [
        {**row, "scope_hash": "0" * 64} for row in rollback_proof]})
    assert recorder.query()["status"] == "ineligible"


# --- evidence ordering, revocation, prerequisites --------------------------- #

def test_latest_failed_evidence_overrides_an_earlier_pass(recorder, required_inputs,
                                                          sec_failure, rollback_proof):
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    recorder.record("read", outcome="failed")
    assert recorder.query()["status"] == "ineligible"


def test_prerequisite_revocation_is_checked_recursively(recorder, required_inputs,
                                                        sec_failure, rollback_proof):
    """A revoked prerequisite invalidates the evidence that depended on it, even
    though the dependent row itself is untouched and passing."""
    ids = recorder.qualify_all(inputs=required_inputs, sec=sec_failure,
                               proofs=rollback_proof)
    recorder.record("read", prereqs=[ids["admission"]])
    recorder.record("admission")
    assurance.revoke_evidence(ids["admission"], reason="pytest prereq revoke",
                              db_path=recorder.ledger)
    result = recorder.query()
    assert any(r.startswith("prerequisite:revoked") for r in result["reasons"]), result


def test_revocation_does_not_contaminate_another_consumer(
        recorder, paths, policy, scope, role, other_role, other_scope,
        required_inputs, sec_failure, rollback_proof):
    """Revoking one consumer's evidence must not make another's ineligible —
    otherwise one role's problem silently freezes the whole cutover."""
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    other = Recorder(recorder.ledger, paths, policy, other_scope, other_role)
    other.qualify_all(who=other_role, selected_scope=other_scope,
                      proofs=rollback_proof)
    assert other.query(who=other_role, selected_scope=other_scope)["status"] == "eligible"

    last_read = assurance.evidence_history(
        domain_id=role["domain"], consumer_id=role["id"], scope=scope,
        db_path=recorder.ledger)[0]["event_id"]
    assurance.revoke_evidence(last_read, reason="pytest isolation",
                              db_path=recorder.ledger)

    assert recorder.query()["status"] == "ineligible"
    assert other.query(who=other_role, selected_scope=other_scope)["status"] == "eligible"


# --- fingerprints, TTL, scope ------------------------------------------------ #

def test_incomplete_fingerprint_registry_is_rejected(recorder, required_inputs,
                                                     sec_failure, rollback_proof):
    """Evidence that fingerprints only part of the policy surface would survive a
    change to the un-recorded files."""
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    recorder.record("read", dependencies=["src/ats/data/assurance.py"])
    assert recorder.query()["status"] == "ineligible"


def test_dependency_content_change_invalidates_the_evidence(recorder, required_inputs,
                                                            sec_failure, rollback_proof,
                                                            tmp_path):
    """The fingerprint is over content, not over the file's existence."""
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    dependency = tmp_path / "proof.txt"
    dependency.write_text("v1", encoding="utf-8")
    recorder.record("read", dependencies=recorder.paths + [str(dependency)])
    assert recorder.query()["status"] == "eligible"

    dependency.write_text("v2", encoding="utf-8")
    assert recorder.query()["status"] == "ineligible"


def test_evidence_expires_on_its_own_ttl_not_on_report_age(recorder, required_inputs,
                                                           sec_failure, rollback_proof):
    """A TTL of 30 days must not be extended by the absence of newer reports."""
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    assert recorder.query()["status"] == "eligible"
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert recorder.query(now=later)["status"] == "ineligible"


def test_scope_must_match_exactly(recorder, required_inputs, sec_failure, rollback_proof):
    """Qualification is per scope; a broader or narrower scope is a different
    question and must not inherit the answer."""
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    assert recorder.query()["status"] == "eligible"

    narrowed = {**recorder.scope, "entity": "AMD"}
    assert recorder.query(selected_scope=narrowed)["status"] == "ineligible"

    widened = {**recorder.scope,
               "products": {**recorder.scope["products"], "MARKET_DATA": {"product": "MARKET_DATA"}}}
    assert recorder.query(selected_scope=widened)["status"] == "ineligible"


def test_manifest_drift_is_fail_closed(recorder, required_inputs, sec_failure,
                                       rollback_proof, tmp_path):
    """Editing the consumer contract manifest invalidates every recorded proof.

    This is the constraint that makes Phase F's own code changes order-dependent:
    the manifest is the digest baseline, so touching it retires the evidence.
    """
    recorder.qualify_all(inputs=required_inputs, sec=sec_failure, proofs=rollback_proof)
    assert recorder.query()["status"] == "eligible"

    drifted = tmp_path / "coverage.yaml"
    drifted.write_text(COVERAGE.read_text(encoding="utf-8") + "\n# drift\n",
                       encoding="utf-8")
    assert recorder.query(coverage_path=drifted)["status"] == "ineligible"


# --- immutability and read-only ---------------------------------------------- #

@pytest.mark.parametrize("statement", [
    "UPDATE dataflow_assurance_events SET outcome='passed'",
    "DELETE FROM dataflow_assurance_events",
])
def test_recorded_evidence_cannot_be_mutated(ledger, statement, paths, role, scope):
    """Append-only is enforced by the database, not by convention: an auditor
    must be able to trust that today's file differs from yesterday's only by
    additions.

    A row must exist first — the immutability triggers are per-row, so an empty
    table would pass this test for the wrong reason.
    """
    event_id = assurance.record_evidence(
        domain_id=role["domain"], consumer_id=role["id"], evidence_type="read",
        outcome="passed", scope=scope, as_of=START,
        command_summary="immutability probe", result_summary="row present for update attempt",
        dependency_paths=paths, db_path=ledger)
    assert event_id

    with sqlite3.connect(ledger) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement)


def test_qualification_connection_is_read_only(ledger):
    """A qualification query must not be able to write, so querying cannot
    become a way to fix up the evidence it is judging."""
    with assurance._connect(ledger, writable=True):
        pass
    with assurance._connect(ledger, writable=False) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE cannot_write(x)")


# --- input validation -------------------------------------------------------- #

def test_unknown_prerequisite_is_rejected(recorder):
    with pytest.raises(ValueError):
        recorder.record("read", prereqs=["nonexistent"])


def test_cross_scope_prerequisite_is_rejected(recorder, required_inputs, sec_failure,
                                              rollback_proof):
    """A prerequisite proved for one scope cannot authorise another scope."""
    ids = recorder.qualify_all(inputs=required_inputs, sec=sec_failure,
                               proofs=rollback_proof)
    with pytest.raises(ValueError):
        recorder.record("read", prereqs=[ids["admission"]],
                        selected_scope={**recorder.scope, "entity": "AMD"})


@pytest.mark.parametrize("kwargs", [
    {"max_age_days": 0},
    {"environment_names": ["TOKEN=not-a-variable-name"]},
    {"details": {"raw_body": "not allowed"}},
])
def test_invalid_audit_metadata_is_rejected(ledger, paths, role, scope, kwargs):
    """The evidence row is an audit record: a zero TTL, a value smuggled in as an
    environment NAME, or an unvalidated payload all corrupt it."""
    with pytest.raises(ValueError):
        assurance.record_evidence(
            domain_id=role["domain"], consumer_id=role["id"], evidence_type="read",
            outcome="passed", scope=scope, as_of=START,
            command_summary="pytest probe", result_summary="validation probe",
            dependency_paths=paths, db_path=ledger, **kwargs)


def test_legacy_ledger_gets_additive_migration_without_qualification_upgrade(
        tmp_path, paths, role, scope):
    """A ledger written before `details_json` existed must open without losing
    rows — and must NOT thereby become qualified. Schema repair is not evidence.
    """
    legacy = tmp_path / "legacy.sqlite"
    with sqlite3.connect(legacy) as conn:
        conn.executescript(assurance._SCHEMA.replace(
            ", details_json TEXT NOT NULL DEFAULT '{}'", ""))

    event_id = assurance.record_evidence(
        domain_id=role["domain"], consumer_id=role["id"], evidence_type="read",
        outcome="passed", scope=scope, as_of=START,
        command_summary="legacy schema probe", result_summary="additive migration",
        dependency_paths=paths, db_path=legacy)

    history = assurance.evidence_history(domain_id=role["domain"],
                                         consumer_id=role["id"], db_path=legacy)
    assert history[0]["event_id"] == event_id
    assert history[0]["details"] == {}

    result = assurance.qualification(domain_id=role["domain"],
                                     consumer_id=role["id"],
                                     contract_version=role["contract_version"],
                                     scope=scope, db_path=legacy)
    assert result["status"] == "ineligible"


def test_unverified_fallback_evidence_cannot_qualify_a_consumer(
        ledger, manifest, paths, policy, tmp_path):
    """`technical`, `chief`, `risk`, `trader` and `clerk` have a `fallback`
    evidence type and no proven drill. Recording a passing label for it must not
    promote them — this is the mechanism that keeps them ineligible today.

    Uses a real fallback-bearing consumer rather than a synthetic one: the
    point is that the policy's own consumers stay blocked, so testing it against
    a consumer whose contract lacks the evidence type would assert nothing.
    """
    consumer = next(row for row in manifest["consumers"]
                    if "fallback" in row["required_evidence"])
    fallback_scope = {"products": {p: {"product": p, "vintage": "2026-06-30"}
                                    for p in sorted(consumer["products"])},
                      "purpose": "pytest-fallback"}
    recorder = Recorder(ledger, paths, policy, fallback_scope, consumer)
    recorder.qualify_all(who=consumer, selected_scope=fallback_scope)

    result = recorder.query(who=consumer, selected_scope=fallback_scope)
    assert result["status"] == "ineligible", result["reasons"]
    # The reason code differs by evidence type — `technical` carries `fallback`
    # and no `rollback`, so the unproven drill surfaces under whichever check
    # covers its type. Assert the shape (an unverified drill) rather than one
    # consumer's spelling.
    assert any(r.startswith(("fallback_unverified:", "rollback_unverified:"))
               for r in result["reasons"]), result["reasons"]


def test_the_five_unproven_consumers_all_remain_ineligible(ledger, manifest, paths,
                                                           policy):
    """Names the consumers this gate is currently holding closed, so a future
    change that quietly promotes one of them fails here rather than in a cutover
    report weeks later.
    """
    unproven = [row for row in manifest["consumers"]
                if "fallback" in row["required_evidence"]]
    assert {row["id"] for row in unproven} == {
        "technical", "chief", "risk", "trader", "clerk"}, \
        "the set of consumers gated on an unproven fallback changed — re-check " \
        "their cutover eligibility deliberately"

    for index, consumer in enumerate(unproven):
        scope = {"products": {p: {"product": p, "vintage": "2026-06-30"}
                              for p in sorted(consumer["products"])},
                 "purpose": f"pytest-unproven-{index}"}
        recorder = Recorder(ledger, paths, policy, scope, consumer)
        recorder.qualify_all(who=consumer, selected_scope=scope)
        assert recorder.query(who=consumer, selected_scope=scope)["status"] \
            == "ineligible", consumer["id"]


def test_pure_text_tool_environment_paths_stay_out_of_scope(manifest, policy):
    """Guard against the policy being widened to a path that is not a file inside
    the repo — `_dependencies` resolves and hashes whatever it is given."""
    for var in policy["required_fingerprint_paths"]:
        assert (REPO_ROOT / var).is_file(), var
