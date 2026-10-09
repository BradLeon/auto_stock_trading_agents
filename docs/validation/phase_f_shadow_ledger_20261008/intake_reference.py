"""Limited intake-source baseline: HEAD source matches the captured pre-session hash.

Other modules and tests stay current. This is not a full pre-Phase-F reconstruction.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class PreSessionIntake:
    def pytest_sessionstart(self, session):
        from ats.workflow import intake_verification as iv

        relative = "src/ats/workflow/intake_verification.py"
        source = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT)
        baseline = json.loads(Path(__file__).with_name("before.json").read_text())
        assert hashlib.sha256(source).hexdigest() == baseline["source_sha256"][relative]
        exec(compile(source, "pre-session:" + relative, "exec"), iv.__dict__)


if __name__ == "__main__":
    raise SystemExit(pytest.main([
        "-q", "--tb=short",
        "tests/test_intake_verification.py::test_a_complete_run_may_enter_the_decision_cycle",
        "tests/test_intake_verification.py::test_a_disagreement_between_the_two_contracts_is_reported_not_hidden",
        "tests/test_intake_verification.py::test_a_fully_consistent_run_reports_no_disagreement",
        "--junitxml=" + str(Path(__file__).with_name("intake-baseline.junit.xml"))],
        plugins=[PreSessionIntake()]))
