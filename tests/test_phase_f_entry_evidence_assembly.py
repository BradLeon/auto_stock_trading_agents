"""The assembled production-mode bundles must reject incomplete or forged evidence."""
import copy
import hashlib
import importlib.util
import json
import xml.etree.ElementTree as ET

import pytest

from ats.config import REPO_ROOT
from ats.workflow import boundary_evidence as be
from ats.workflow import cutover as plane
from ats.workflow import runtime_reads as rr
from ats.workflow.scoped_routes import canonical_json

POINT = "2026-10-14T02:46:31.536419+00:00"
SYMBOLS = ("MRVL", "MSFT", "NVDA", "TSM")
EXPECTED_SITES = {
    "information": {"ats.workflow.dispatcher.Dispatcher.dispatch", "ats.runtime.cli.run_information_pass",
                    "ats.data.consumer_api.read_input", "ats.workflow.ownership.run_owned_workflow"},
    "fundamental": {"ats.workflow.dispatcher.Dispatcher.dispatch", "ats.runtime.cli.run_pead",
                    "ats.runtime.cli._run_analyst_fundamental", "ats.data.consumer_api.read_input",
                    "ats.workflow.ownership.run_owned_workflow"}}


def load_assembler():
    spec = importlib.util.spec_from_file_location(
        "assemble_phase_f_entry_evidence", REPO_ROOT / "scripts/assemble_phase_f_entry_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def assembled(tmp_path_factory):
    assembler = load_assembler()
    junit = tmp_path_factory.mktemp("entry-evidence") / "evidence.junit.xml"
    result = assembler.run_tests(junit, None)
    assert result.returncode == 0, result.stdout[-1500:]
    return assembler, junit, assembler.executed_cases(junit)


def who(consumer="information", symbol="MRVL"):
    return rr.business_identity(consumer, kind="entity", scope_id=symbol, entities=[symbol], as_of=POINT)


def validate(bundle, identity):
    be._validate_bundle(plane.PROJECTION_READ, identity, "production", bundle)


@pytest.mark.parametrize("consumer", sorted(EXPECTED_SITES))
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_every_exact_scope_has_a_valid_complete_bundle(assembled, consumer, symbol):
    assembler, junit, cases = assembled
    identity = who(consumer, symbol)
    bundle = assembler.bundle_for(identity, cases, junit)
    assert set(bundle["call_points"]) == EXPECTED_SITES[consumer]
    validate(bundle, identity)
    cli = bundle["call_points"].get("ats.runtime.cli.run_information_pass")
    if cli:  # the portfolio-level CLI cannot succeed for an entity scope
        assert "positive_test" not in cli and cli["entry_contract"]["compatible"] is False


def test_missing_negative_or_positive_evidence_is_refused(assembled):
    assembler, junit, cases = assembled
    identity = who("fundamental")
    key = canonical_json(identity.as_row())
    for kind in ("negative", "positive"):
        trimmed = {k: v for k, v in cases.items()
                   if not (k[0] == "ats.workflow.dispatcher.Dispatcher.dispatch" and k[1] == key and k[2] == kind)}
        with pytest.raises(SystemExit, match="missing executed evidence"):
            assembler.bundle_for(identity, trimmed, junit)


def test_success_evidence_for_an_incompatible_entry_is_refused(assembled):
    assembler, junit, cases = assembled
    identity = who("information")
    site = "ats.runtime.cli.run_information_pass"
    invented = {**cases, (site, canonical_json(identity.as_row()), "positive"): "tests.made.up"}
    with pytest.raises(SystemExit, match="incompatible"):
        assembler.bundle_for(identity, invented, junit)


@pytest.mark.parametrize("fault", ["mode", "side_effect", "foreign_identity", "path_kind", "drift"])
def test_forged_or_drifted_evidence_is_rejected(assembled, tmp_path, fault):
    assembler, junit, cases = assembled
    identity = who("fundamental", "MRVL")
    bundle = copy.deepcopy(assembler.bundle_for(identity, cases, junit))
    site = "ats.data.consumer_api.read_input"
    proof = bundle["call_points"][site]
    if fault == "drift":
        proof["source_hashes"][next(iter(proof["source_hashes"]))] = "0" * 64
    else:
        tree = ET.parse(junit)
        case = next(c for c in tree.iter("testcase")
                    if f"{c.get('classname')}.{c.get('name')}" == proof["negative_test"])
        props = {p.get("name"): p for p in case.iter("property")}
        if fault == "mode":
            props["mode"].set("value", "isolated")
        elif fault == "side_effect":
            props["negative_side_effect_count"].set("value", "1")
        elif fault == "path_kind":
            props["path_kind"].set("value", "positive")
        else:
            props["identity_json"].set("value", canonical_json(who("fundamental", "MSFT").as_row()))
        changed = tmp_path / "forged.xml"
        tree.write(changed)
        proof.update(junit_path=str(changed), junit_sha256=hashlib.sha256(changed.read_bytes()).hexdigest())
    with pytest.raises(plane.CutoverError):
        validate(bundle, identity)


def test_a_bundle_cannot_be_reused_for_another_scope_or_mode(assembled):
    assembler, junit, cases = assembled
    bundle = assembler.bundle_for(who("fundamental", "MRVL"), cases, junit)
    with pytest.raises(plane.CutoverError):
        validate(bundle, who("fundamental", "MSFT"))
    with pytest.raises(plane.CutoverError):
        be._validate_bundle(plane.PROJECTION_READ, who("fundamental", "MRVL"), "isolated", bundle)


def test_the_assembler_never_registers_evidence():
    source = (REPO_ROOT / "scripts/assemble_phase_f_entry_evidence.py").read_text()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", '"""')))
    assert "record_enforcement(" not in code
