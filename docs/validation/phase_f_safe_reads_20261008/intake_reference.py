"""Exact pre-session module bytes; other code/tests/config stay current."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest


class PreSessionSources:
    def pytest_sessionstart(self, session):
        from ats.workflow import intake_verification as iv
        root = Path(__file__).resolve().parent
        before = json.loads((root / "before.json").read_text())
        relative = "src/ats/workflow/intake_verification.py"
        with zipfile.ZipFile(root / "before_sources.zip") as archive:
            source = archive.read(relative)
        assert hashlib.sha256(source).hexdigest() == before["files"][relative]["sha256"]
        exec(compile(source, str(iv.__file__), "exec"), iv.__dict__)
        scheduler = "src/ats/runtime/scheduler.py"
        current = Path(scheduler).read_bytes()
        assert hashlib.sha256(current).hexdigest() == before["files"][scheduler]["sha256"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([
        "-q", "--tb=short",
        "tests/test_intake_verification.py::test_a_complete_run_may_enter_the_decision_cycle",
        "tests/test_intake_verification.py::test_a_disagreement_between_the_two_contracts_is_reported_not_hidden",
        "tests/test_intake_verification.py::test_a_fully_consistent_run_reports_no_disagreement",
        "tests/test_workflow_data_cutover.py::test_scheduler_data_accesses_use_unified_runtime_products_and_pipelines",
        "--junitxml=" + str(Path(__file__).with_name("limited-baseline.junit.xml"))],
        plugins=[PreSessionSources()]))
