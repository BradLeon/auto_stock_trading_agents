"""The managed-queue lease is what lets tests write at all — and still gates them.

`persistent_queue.require_queue_worker` rejects any persistent write without a live
lease. That gate is correct: it is what makes the managed queue the single writer.
It also means a test either holds a lease or cannot write, so the lease fixture in
`conftest.py` is load-bearing infrastructure for the whole suite.

These tests pin both halves. Without the first, a future change that quietly
disables the gate would look like a pass. Without the second, a stricter gate would
turn 158 tests red with an error that reads like a product bug.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

# `tests/` is not an importable package (no __init__.py), so conftest's constants
# are read by path rather than by import.
_CONFTEST_SPEC = importlib.util.spec_from_file_location(
    "ats_test_conftest", Path(__file__).with_name("conftest.py"))
_CONFTEST = importlib.util.module_from_spec(_CONFTEST_SPEC)
_CONFTEST_SPEC.loader.exec_module(_CONFTEST)
TEST_ONLY_PERSISTENT_SOURCES = _CONFTEST.TEST_ONLY_PERSISTENT_SOURCES

from ats.data.persistent_queue import PersistentIngestionQueue, require_queue_worker
from ats.memory import get_store
from ats.schemas.chain import Observation

NOW = datetime.now(timezone.utc)


def _obs(**kw) -> Observation:
    base = dict(document_id="lease-probe-doc", entity="MU", metric="hbm_capacity",
                period="FY26Q3", observation_type="guidance", stance="supplier",
                direction="up", evidence_span="2027 产能大部分已预售", observed_at=NOW)
    return Observation(**{**base, **kw})


@pytest.fixture
def queue_db(tmp_path) -> str:
    return str(tmp_path / "queue.sqlite")


def test_the_gate_refuses_a_write_without_a_lease(monkeypatch, queue_db):
    """The gate must still bite. A test fixture that satisfies it by disabling it
    would leave the production single-writer property unverified."""
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", queue_db)
    monkeypatch.delenv("ATS_PERSISTENT_QUEUE_TASK_ID", raising=False)
    monkeypatch.delenv("ATS_PERSISTENT_QUEUE_LEASE_OWNER", raising=False)

    with pytest.raises(PermissionError, match="managed-queue lease"):
        require_queue_worker("lease-probe-doc")


def test_the_conftest_lease_admits_a_write_in_both_source_forms(monkeypatch, queue_db):
    """The fixture covers both ways a source reaches the gate: the exported env var
    (which is how the platform repository resolves its own write) and a source named
    directly by a caller."""
    # The suite's own lease points at the conftest database; these tests use their
    # own queue file, so the lease identity is re-established against it here.
    task_id, _ = PersistentIngestionQueue(queue_db).enqueue(
        source_id="probe", scope={"sources": ["", "covered"]},
        trigger_kind="manual", trigger_ref="probe",
        command=["ats", "data", "ingest"], policy_fingerprint="probe")
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", queue_db)
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_TASK_ID", task_id)
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_LEASE_OWNER", "probe-worker")
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_SOURCE_ID", "probe")
    assert PersistentIngestionQueue(queue_db).claim("probe-worker", task_id=task_id)

    require_queue_worker("")            # the empty sentinel platform.py passes
    require_queue_worker("covered")     # named explicitly in the scope
    with pytest.raises(PermissionError):
        require_queue_worker("not-covered")


def test_tests_hold_a_working_lease_by_default():
    """If this fails, every test that writes through the data layer is failing for
    one reason and the suite looks like a product regression."""
    require_queue_worker("")


def test_a_test_can_actually_persist_an_observation(publish_document):
    """End to end through the delegated write path, which is where both gates sit.

    The document must be published first: a neutral fact that cites material which
    does not exist is exactly what `neutral_fact_requires_published_document_version`
    exists to prevent, so the test publishes one rather than bypassing the gate.
    """
    store = get_store()
    publish_document(store, "lease-probe-doc")
    assert store.save_observation(_obs()) is True
    assert len(store.observations(entity="MU")) == 1


def test_a_fact_citing_an_unpublished_document_is_still_rejected(monkeypatch,
                                                                  publish_document):
    """The second gate must keep biting after the fixture exists.

    Auto-publish is disabled here: this test is about the rejection, and the hook
    would otherwise publish the missing document and turn it into a pass.
    """
    monkeypatch.setenv("ATS_TEST_NO_AUTO_PUBLISH", "1")
    store = get_store()
    publish_document(store, "lease-probe-doc")

    with pytest.raises(ValueError, match="neutral_fact_requires_published_document_version"):
        store.save_observation(_obs(document_id="never-published"))


def test_a_fact_cannot_cite_a_document_published_after_it(monkeypatch, publish_document):
    """The temporal half of the same gate: material that did not exist when the
    fact was observed cannot support it."""
    monkeypatch.setenv("ATS_TEST_NO_AUTO_PUBLISH", "1")
    store = get_store()
    later = datetime.now(timezone.utc)
    publish_document(store, "lease-probe-doc", fetched_at=later.isoformat())
    past = _obs(observed_at=datetime.fromtimestamp(
        later.timestamp() - 3600, tz=timezone.utc))

    with pytest.raises(ValueError, match="neutral_fact_cannot_cite_future_document_version"):
        store.save_observation(past)


def test_the_declared_test_only_sources_are_real_identifiers():
    """The lease scope names identifiers the collection helpers accept. They are
    not catalog sources, so this list is the only place they are declared — a typo
    here would surface as a PermissionError in whichever test uses it."""
    from ats.data.catalog.structured import StructuredCatalog
    registered = (StructuredCatalog.load().raw or {}).get("sources") or {}
    assert TEST_ONLY_PERSISTENT_SOURCES, "the list must not be emptied to dodge failures"
    for source_id in TEST_ONLY_PERSISTENT_SOURCES:
        assert source_id == source_id.strip().lower(), source_id
        assert source_id not in registered, (
            f"{source_id} is a catalog source now — drop it from the test-only list "
            "so the catalog stays the single authority on persistent sources")


def test_persistent_sources_come_from_the_catalog_not_a_hand_list(monkeypatch, queue_db):
    """The lease covers what the catalog declares persistent, so a newly registered
    persistent source is admitted without editing the fixture."""
    from ats.data.catalog.structured import StructuredCatalog

    catalog = StructuredCatalog.load().raw or {}
    persistent = [source_id for source_id, source in (catalog.get("sources") or {}).items()
                  if (source or {}).get("persistence") == "persistent"]
    assert persistent, "catalog declares no persistent source — fixture would be empty"

    task_id, _ = PersistentIngestionQueue(queue_db).enqueue(
        source_id="probe", scope={"sources": list(persistent)},
        trigger_kind="manual", trigger_ref="probe",
        command=["ats", "data", "ingest"], policy_fingerprint="probe")
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", queue_db)
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_TASK_ID", task_id)
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_LEASE_OWNER", "probe-worker")
    PersistentIngestionQueue(queue_db).claim("probe-worker", task_id=task_id)

    for source_id in persistent:
        require_queue_worker(source_id)
