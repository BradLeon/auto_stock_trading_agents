#!/usr/bin/env python
"""Assemble and validate production-mode ``projection_read`` evidence bundles (Phase F 8.5).

Runs ``tests/test_phase_f_entry_evidence.py`` against a read-only restore of the control
plane, matches each applicable call point to the executed test cases through the
properties the cases emitted themselves (entry, exact identity, path kind), builds one
bundle per exact scope and checks it with the real validator.

It never registers anything. ``record_enforcement`` writes the production cutover
database and needs its own explicit authorization after the bundles were reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from ats.config import REPO_ROOT
from ats.workflow import boundary_evidence as be
from ats.workflow import cutover as plane
from ats.workflow.scoped_intake import check_freeze
from ats.workflow.scoped_routes import RouteIdentity, canonical_json

TEST_FILE = REPO_ROOT / "tests/test_phase_f_entry_evidence.py"
MODE = "production"


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_tests(junit: Path, restore_from: Path | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("ATS_RUN_MODE", None)
    if restore_from is not None:
        env["ATS_ENTRY_EVIDENCE_RESTORE_FROM"] = str(restore_from)
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_FILE), "-q", "-p", "no:cacheprovider",
         "-o", "junit_family=xunit1", f"--junitxml={junit}"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def executed_cases(junit: Path) -> dict[tuple[str, str, str], str]:
    """(entry, canonical identity, path kind) -> test id, for passing cases only."""
    root = ET.parse(junit).getroot()
    cases = {}
    for case in root.iter("testcase"):
        if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
            raise SystemExit(f"entry evidence case not passing: {case.get('name')}")
        props = {p.get("name"): p.get("value") for p in case.iter("property")}
        key = (props.get("entry_exercised"), props.get("identity_json"), props.get("path_kind"))
        if None in key:
            continue
        if key in cases:
            raise SystemExit(f"duplicate evidence for {key[0]} {key[2]}: {case.get('name')}")
        cases[key] = f"{case.get('classname')}.{case.get('name')}"
    return cases


def stop_latch_events(junit: Path) -> dict[str, int]:
    """Control-plane stop latches the refusal paths produced, per entry (reported, not hidden)."""
    totals: dict[str, int] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        props = {p.get("name"): p.get("value") for p in case.iter("property")}
        if props.get("path_kind") == "negative" and "stop_latch_events" in props:
            site = props["entry_exercised"]
            totals[site] = totals.get(site, 0) + int(props["stop_latch_events"])
    return dict(sorted(totals.items()))


def bundle_for(who: RouteIdentity, cases, junit: Path) -> dict:
    required = {p.site: p for p in be.CALL_POINTS[plane.PROJECTION_READ] if MODE in p.modes
                and ("*" in p.consumers or who.consumer_id in p.consumers)}
    identity_json = canonical_json(who.as_row())
    proofs = {}
    for site, point in sorted(required.items()):
        contract = be.entry_scope_contract(point, who)
        negative = cases.get((site, identity_json, "negative"))
        positive = cases.get((site, identity_json, "positive"))
        if negative is None or (contract["compatible"] and positive is None):
            raise SystemExit(f"missing executed evidence: {who.consumer_id}/{who.scope['id']} {site} "
                             f"compatible={contract['compatible']}")
        if not contract["compatible"] and positive is not None:
            raise SystemExit(f"success evidence for a contract-incompatible entry: {site}")
        tests = (positive, negative) if contract["compatible"] else (negative,)
        source, _ = be.resolve_site(site)
        needed = {source, REPO_ROOT / "src/ats/workflow/cutover_wiring.py", Path(be.__file__),
                  REPO_ROOT / "src/ats/workflow/scoped_routes.py",
                  *(REPO_ROOT / "tests" / (t.removeprefix("tests.").split(".")[0] + ".py") for t in tests),
                  *(Path(p) for p in contract["dependencies"])}
        proof = {"writer": point.writer, "entry_exercised": site, "entry_contract": contract,
                 "negative_test": negative, "negative_side_effect_count": 0,
                 "junit_path": str(junit), "junit_sha256": sha256(junit),
                 "source_hashes": {str(Path(p).resolve()): sha256(p) for p in sorted(needed, key=str)}}
        if contract["compatible"]:
            proof["positive_test"] = positive
        proofs[site] = proof
    return {"identity": who.as_row(), "mode": MODE, "call_points": proofs}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scopes", required=True, help="JSON list of exact RouteIdentity rows")
    parser.add_argument("--freeze", required=True, help="current final implementation freeze")
    parser.add_argument("--restore-from", help="directory of production SQLite files (read-only source)")
    parser.add_argument("--output", required=True, help="new directory; must not exist")
    args = parser.parse_args(argv)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    check_freeze(json.loads(Path(args.freeze).read_text()))
    junit = out / "entry_evidence.junit.xml"
    result = run_tests(junit, Path(args.restore_from) if args.restore_from else None)
    (out / "pytest.log").write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        print(result.stdout[-2000:])
        return 1
    cases = executed_cases(junit)
    scopes = [RouteIdentity(**row) for row in json.loads(Path(args.scopes).read_text())]
    (out / "bundles").mkdir()
    summary = []
    for who in scopes:
        bundle = bundle_for(who, cases, junit)
        be._validate_bundle(plane.PROJECTION_READ, who, MODE, bundle)
        name = f"{who.consumer_id}-{who.scope['id']}.json"
        (out / "bundles" / name).write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
        summary.append({"identity": who.as_row(), "bundle": f"bundles/{name}",
                        "bundle_sha256": sha256(out / "bundles" / name),
                        "call_points": {site: ("positive+negative" if "positive_test" in proof else "refusal-only")
                                        for site, proof in bundle["call_points"].items()}})
    check_freeze(json.loads(Path(args.freeze).read_text()))
    manifest = {"assembled_at": datetime.now(UTC).isoformat(), "mode": MODE, "boundary": plane.PROJECTION_READ,
                "freeze": str(Path(args.freeze).resolve()), "freeze_sha256": sha256(args.freeze),
                "junit": str(junit), "junit_sha256": sha256(junit), "executed_cases": len(cases),
                "stop_latch_events_by_entry": stop_latch_events(junit),
                "validated_without_registration": len(summary), "registered": False,
                "restored_from": args.restore_from or None, "scopes": summary}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({"validated": len(summary), "executed_cases": len(cases), "registered": False,
                      "manifest": str(out / "manifest.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
