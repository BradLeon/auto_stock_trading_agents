"""Recovery-window and production-exit checks (report re-measurement retired with acceptance_*, r5 8.4)."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from ats.workflow import exit_requirements as exit



def test_recovery_drill_receipt_binds_the_qualification_tier_not_tooling(tmp_path):
    """The rollback evidence cites this receipt, so it must retire with runtime
    code but not with tests or evidence scripts (those are held by the freeze)."""
    import hashlib

    from ats.workflow.assurance_surface import (
        implementation_hashes,
        qualification_implementation_hashes,
    )
    raw = tmp_path / 'drill.junit.xml'
    raw.write_text('<testsuite><testcase classname="drill" name="stop"/></testsuite>')
    receipt = {'kind':'junit','status':'passed','reference':str(raw),
        'sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'cases':['drill::stop'],
        'implementation':qualification_implementation_hashes()}
    assert exit._artifact_verified(receipt)
    assert not [p for p in receipt['implementation'] if p.startswith(('tests/', 'scripts/'))]
    # A receipt pinned to the full tree (tests included) is a different binding.
    assert exit._artifact_verified({**receipt, 'implementation':implementation_hashes()}) is False
    runtime = dict(receipt['implementation'], **{'src/ats/workflow/consumer_reads.py':'0'*64})
    assert exit._artifact_verified({**receipt, 'implementation':runtime}) is False
