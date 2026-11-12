"""A refresh job's queue lease must name the source its command writes (2026-11-03: L1 observers refused daily)."""
import re

from ats.data.refresh import load_plan


def test_chain_source_collect_jobs_lease_the_source_they_collect():
    checked = 0
    for job in load_plan().jobs:
        command = " ".join(job.command) if not isinstance(job.command, str) else job.command
        found = re.search(r"chain-source-collect --source (\S+)", command)
        if found:
            checked += 1
            assert found.group(1) in job.sources, (job.id, found.group(1), job.sources)
    assert checked >= 6
