"""Limited HEAD-Chief counterfactual; never edits the workspace implementation.

Only Chief source is reverted in this subprocess. Other code and current tests
stay identical. This is not a pre-Phase-F or full-suite baseline reconstruction.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class HeadChief:
    def pytest_sessionstart(self, session):
        from ats.graph import chief

        source = subprocess.run(["git", "show", "HEAD:src/ats/graph/chief.py"],
                                cwd=ROOT, check=True, text=True, capture_output=True).stdout
        exec(compile(source, "HEAD:src/ats/graph/chief.py", "exec"), chief.__dict__)  # noqa: S102


if __name__ == "__main__":
    raise SystemExit(pytest.main([
        "-q", str(ROOT / "tests/test_chief_graph.py"),
        "--junitxml=" + str(Path(__file__).with_name("head-chief.junit.xml"))],
        plugins=[HeadChief()]))
