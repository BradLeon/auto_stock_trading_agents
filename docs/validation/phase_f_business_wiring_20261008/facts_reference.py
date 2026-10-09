"""Limited HEAD-TradingMemory comparison for the unchanged fact-reprocessing case."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class HeadStore:
    def pytest_sessionstart(self, session):
        from ats import memory
        from ats.memory import store

        source = subprocess.run(["git", "show", "HEAD:src/ats/memory/store.py"],
                                cwd=ROOT, check=True, text=True, capture_output=True).stdout
        namespace = dict(store.__dict__)
        exec(compile(source, "HEAD:src/ats/memory/store.py", "exec"), namespace)  # noqa: S102
        store.TradingMemory = memory.TradingMemory = namespace["TradingMemory"]
        memory.reset_store_cache()


if __name__ == "__main__":
    raise SystemExit(pytest.main([
        "-q", str(ROOT / "tests/test_fact_projections.py"),
        "--junitxml=" + str(Path(__file__).with_name("head-facts.junit.xml"))],
        plugins=[HeadStore()]))
