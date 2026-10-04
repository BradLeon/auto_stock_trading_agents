import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Make the src layout importable without installing.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ats.schemas.channel import ApprovalRequest, Notification, ReportBundle  # noqa: E402
from ats.schemas.decision import BossApproval  # noqa: E402
from ats.schemas.memory import TradeLogEntry  # noqa: E402

# --- managed-queue lease for tests -------------------------------------------
#
# Persistent writes are gated: `persistent_queue.require_queue_worker(source_id)`
# rejects any write unless the process holds a live managed-queue lease, and the
# production shape of that is `run_worker` claiming a task, injecting four env
# vars, and spawning `python -m ats.runtime.cli` to do the write. Tests are not
# that worker, so every test that writes through the data layer needs a lease of
# its own — without this the suite fails with `PermissionError: persistent
# ingestion for <source> requires an active managed-queue lease` in 158 places.
#
# Two things make the lease honest rather than a blanket bypass:
#
# 1. The lease is a REAL queue task in a throwaway database. `valid_source_lease`
#    still checks status, owner and expiry; the test is genuinely a leased
#    worker, so a test that corrupts the lease still fails the gate.
# 2. Its scope is an explicit list, not a wildcard. A write to an uncovered
#    source still raises, which is what makes a new source id visible instead of
#    silently tolerated.
TEST_ONLY_PERSISTENT_SOURCES: frozenset[str] = frozenset({
    # Identifiers the test suite passes to the collection helpers. They are NOT
    # catalog sources, so the lease has to name them. A new one surfaces as a
    # PermissionError in the test that uses it — add it here, do not widen the
    # check.
    "tf", "tw_ic_exports", "kr", "ibkr", "model_price",
})


def _grant_write_lease(tmp_path, monkeypatch) -> None:
    """Isolate the queue database and lease it to this test.

    The lease source id is exported as `ATS_PERSISTENT_QUEUE_SOURCE_ID` because
    that is how the platform repository resolves the source for its own write
    path (`platform.py` reads the env var, not the data). Exporting it makes the
    repository's write match the lease, exactly as `run_worker` does in
    production, rather than requiring a scope entry for an empty string.

    The lease lives in its OWN database, never in the queue file a test is
    inspecting. Tests that assert on queue contents (`list() == []`, task counts,
    dedup behaviour) point `ATS_PERSISTENT_QUEUE_PATH` at their own file and would
    otherwise see this fixture's lease sitting in it — which is both a false
    failure and a false signal about the queue.
    """
    from ats.data.catalog.structured import StructuredCatalog
    from ats.data.persistent_queue import PersistentIngestionQueue

    queue_path = tmp_path / "write-lease.sqlite"
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(queue_path))

    catalog = StructuredCatalog.load().raw or {}
    persistent = sorted(
        source_id for source_id, source in (catalog.get("sources") or {}).items()
        if (source or {}).get("persistence") == "persistent")
    scope_sources = sorted({*persistent, *TEST_ONLY_PERSISTENT_SOURCES, ""})

    queue = PersistentIngestionQueue(queue_path)
    task_id, _created = queue.enqueue(
        source_id="test-lease",
        scope={"sources": scope_sources},
        trigger_kind="manual",
        trigger_ref="conftest",
        # Must be an allowlisted ingestion command: the queue rejects anything
        # else at enqueue time, which is the point of the allowlist.
        command=["ats", "data", "ingest"],
        policy_fingerprint="test-lease",
    )
    worker_id = "test-worker"
    assert queue.claim(worker_id, task_id=task_id), "test lease must be claimable"

    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_TASK_ID", task_id)
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_LEASE_OWNER", worker_id)
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_SOURCE_ID", "test-lease")


class _PublishedDocument:
    """Minimal duck type for `save_document`, which reads these attributes only.

    The data layer accepts a document object rather than keyword arguments, and
    the only field that matters for versioning is `sha256` — without it no version
    row is written, which is the usual reason a "published" document turns out not
    to be one.
    """

    def __init__(self, *, document_id: str, symbol: str, period: str,
                 doc_type: str, source: str, text: str, fetched_at: str,
                 source_url: str = "", title: str = "") -> None:
        import hashlib

        self.document_id = document_id
        self.symbol = symbol
        self.period = period
        self.doc_type = doc_type
        self.source = source
        self.source_url = source_url
        self.title = title
        self.text = text
        self.fetched_at = fetched_at
        self.sha256 = hashlib.sha256(text.encode()).hexdigest()
        self.path = None
        self.version_path = None
        self.external_id = ""
        self.published_at = ""
        self.completeness = "full"
        self.truncation_reason = ""
        self.carrier_format = ""
        self.mime_source = ""


@pytest.fixture
def publish_document():
    """Publish a document version so neutral facts may cite it.

    `platform.save_evidence_observation` refuses a fact whose document has no
    published version (`neutral_fact_requires_published_document_version`), and
    refuses one that cites a version published AFTER the fact was observed
    (`neutral_fact_cannot_cite_future_document_version`) — a fact may not be
    supported by material that did not exist when it was seen. A test that writes
    observations therefore has to publish the document it cites, at a time no later
    than the observation, which is what this does rather than loosening either gate.
    """
    def _publish(store, document_id: str, *, symbol: str = "MU",
                 period: str = "FY26Q3", doc_type: str = "transcript",
                 source: str = "test", text: str | None = None,
                 fetched_at: str = "") -> str:
        # Default to a time safely BEFORE any observation the test will write:
        # `NOW`-style stamps are taken at test-body time, which is after the
        # document was published here, and citing a later version is the failure
        # this default avoids.
        stamp = fetched_at or (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        body = text if text is not None else f"published body for {document_id}"
        store.save_document(_PublishedDocument(
            document_id=document_id, symbol=symbol, period=period,
            doc_type=doc_type, source=source, text=body, fetched_at=stamp))
        return stamp

    return _publish


@pytest.fixture(autouse=True)
def _publish_documents_cited_by_facts(monkeypatch):
    """Publish the document a neutral fact cites, at write time, inside tests only.

    Why this exists: `save_evidence_observation` requires a fact's document to have
    a published version, and requires that version to predate the fact. Roughly 60
    tests write facts directly against a `document_id` they never published — they
    are testing the FACT behaviour (idempotence, retirement, discovery freeze), not
    the citation gate, and each would otherwise have to restate the same setup.

    This wraps the single write entry point (`TradingMemory.save_observation`) and,
    only when the cited document has no version yet, publishes a minimal one dated
    BEFORE the fact. Both gates therefore still run and still reject anything a
    test asserts about them — see the tests below that assert the rejections.

    It is deliberately NOT a product change: no production code path gains a
    bypass, and `publish_document` remains available for tests that want to be
    explicit about what they published.

    Disable it with `ATS_TEST_NO_AUTO_PUBLISH=1` — required by any test that
    asserts the citation gates REJECT something, since the hook would otherwise
    publish the missing document and turn an expected failure into a pass.
    """
    from ats.memory.store import TradingMemory

    original = TradingMemory.save_observation

    def save_observation(self, obs, **kwargs):
        # Checked per call, not at install time: a test sets the env var with
        # `monkeypatch.setenv` AFTER this fixture has already patched the class, so
        # an install-time check would enable a hook the test just disabled.
        if os.environ.get("ATS_TEST_NO_AUTO_PUBLISH") == "1":
            return original(self, obs, **kwargs)
        document_id = str(getattr(obs, "document_id", "") or "")
        if document_id and self.data_store().latest_document_version(document_id) is None:
            observed_at = getattr(obs, "observed_at", None)
            stamp = (observed_at - timedelta(minutes=1)).isoformat() if observed_at else ""
            _publish_cited_document(self, document_id, stamp)
        return original(self, obs, **kwargs)

    monkeypatch.setattr(TradingMemory, "save_observation", save_observation)
    yield


def _publish_cited_document(store, document_id: str, fetched_at: str) -> None:
    """Write a minimal published version for `document_id`."""
    stamp = fetched_at or (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    store.save_document(_PublishedDocument(
        document_id=document_id, symbol="MU", period="", doc_type="test",
        source="test", text=f"published body for {document_id}", fetched_at=stamp))


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Point Context Memory + checkpoints at throwaway DBs per test."""
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "mem.sqlite"))
    monkeypatch.delenv("ATS_STRUCTURED_DB_PATH", raising=False)
    monkeypatch.setenv("ATS_STRUCTURED_ARTIFACT_ROOT", str(tmp_path / "structured_artifacts"))
    monkeypatch.setenv("ATS_CHECKPOINT_DB", str(tmp_path / "ckpt.sqlite"))
    # The data-layer database is where neutral evidence facts, documents, candidates
    # and cursors now live (Workflow memory retires those tables), so it must be
    # throwaway too — otherwise a test would write into the real `var/data.sqlite`
    # and its outcome would depend on production data.
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(tmp_path / "data.sqlite"))
    monkeypatch.setenv("ATS_DATA_ARTIFACT_ROOT", str(tmp_path / "data_artifacts"))
    # The managed queue is a persistence surface like the others, and a test
    # without a lease cannot write at all.
    _grant_write_lease(tmp_path, monkeypatch)
    from ats.memory import reset_store_cache
    from ats.data.structured import reset_repository_cache

    reset_store_cache()
    reset_repository_cache()
    yield
    reset_store_cache()
    reset_repository_cache()


@pytest.fixture(autouse=True)
def _isolate_report_dir(tmp_path, monkeypatch):
    """Reports must never land in the real Obsidian vault during tests.

    Writers resolve the vault via a config loader's `output_dir` at call time (imports
    are inside functions), so patching the module attribute covers them all.

    BOTH loaders are redirected. Relying on each test to stub whatever happens to write
    has now failed twice: 2026-07-15 a chief test overwrote real vault documents, and
    2026-08-06 the chain-evidence weekly report landed in the vault because it reads
    `load_sector_config().output_dir` — not covered here at the time — from a scheduler
    test that had no reason to know a report writer sat downstream. Isolation belongs
    in this fixture, not in each caller's memory.
    """
    import ats.config as config

    real_macro = config.load_macro_config
    real_sector = config.load_sector_config

    def _macro(name: str = "macro"):
        return real_macro(name).model_copy(update={"output_dir": str(tmp_path)})

    def _sector(name: str = "ai_hardware"):
        return real_sector(name).model_copy(update={"output_dir": str(tmp_path)})

    monkeypatch.setattr(config, "load_macro_config", _macro)
    monkeypatch.setattr(config, "load_sector_config", _sector)
    # Primary-source cache (data.source_cache) reads AND WRITES under docs_root, which
    # in production is the same Obsidian vault. Without this a test that fetches a
    # document would deposit it in the user's notes — and, worse, would read whatever
    # is already there, so a test's outcome would depend on the vault's contents.
    monkeypatch.setenv("ATS_DOCS_ROOT", str(tmp_path / "sources"))


@pytest.fixture(autouse=True)
def _no_transcript_dataset(monkeypatch):
    """Tier-1 transcript lookup is off unless a test asks for it.

    It reads a 2.2GB remote parquet; left live, every test touching fetch_document
    would spend ~40s on the network. Tests that exercise this path monkeypatch
    `ats.data.defeatbeta.fetch` themselves.
    """
    from ats.data import defeatbeta

    monkeypatch.setattr(defeatbeta, "fetch", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _no_live_layer_analyst(monkeypatch):
    """No test may reach the live layer analyst or the rotation pass.

    Autouse because the failure mode is silent in the same way the adjudicator's is:
    `layer_review.run` and `rotation.run` both swallow exceptions and degrade (to a
    carried-forward verdict / to None), so a test that accidentally called out to a
    model would not error — it would spend money, take a minute, and still look green.
    That is exactly what happened on 2026-08-20: three tests written against the old
    single-synthesis path kept patching only `review.run_structured`, and when `run()`
    started defaulting to the layered path they silently began making real calls.

    Raising (rather than returning a stub) is deliberate: a test that needs these must
    say so by patching them, and one that does not should never be shaped by whatever a
    stub happened to return.
    """
    def _blocked(role, schema, context, **kw):
        raise AssertionError(
            f"test reached the live model for role {role!r} — patch "
            f"`layer_review.run_structured` / `rotation.run_structured` in this test")

    from ats.agents.layer import layer_review
    from ats.agents.sector import rotation

    monkeypatch.setattr(layer_review, "run_structured", _blocked)
    monkeypatch.setattr(rotation, "run_structured", _blocked)


@pytest.fixture(autouse=True)
def _stub_adjudicator(monkeypatch):
    """No test may reach the live cluster adjudicator.

    Patched at `run_structured` rather than at `judge`, so the adjudicator's own glue
    still runs — echoing cluster ids back, dropping invented ones, defaulting the
    unjudged to neutral. Polarity is expressed in the fixture data: a cluster whose
    evidence spans carry [[REFUTE]] or [[NEUTRAL]] gets that, everything else supports.

    Autouse because the failure mode is silent: `judge()` swallows exceptions and
    degrades to all-neutral, so a test that accidentally called out to a model would
    not error — it would just quietly assert against `unknown` and look green.
    """
    from ats.agents.evidence import adjudicator
    from ats.agents.evidence.outputs import (AdjudicationView, ClusterJudgementView,
                                          CrossSectionView, EntityReadingView)

    def _fake(role, schema, context, **kw):
        blocks: list[tuple[str, list[str]]] = []
        for line in context.splitlines():
            if " id=" in line:
                blocks.append((line.split(" id=", 1)[1].strip(), []))
            elif blocks:
                blocks[-1][1].append(line)
        out = []
        for key, body in blocks:
            text = "\n".join(body)
            polarity = ("refute" if "[[REFUTE]]" in text
                        else "neutral" if "[[NEUTRAL]]" in text else "support")
            out.append(ClusterJudgementView(cluster_key=key, polarity=polarity,
                                            reason=f"stub:{polarity}"))
        return AdjudicationView(judgements=out)

    def _fake_cross(role, schema, context, **kw):
        blocks: list[tuple[str, list[str]]] = []
        for line in context.splitlines():
            if line.startswith("### "):
                blocks.append((line[4:].strip(), []))
            elif blocks:
                blocks[-1][1].append(line)
        out = []
        for entity, body in blocks:
            text = "\n".join(body)
            standing = ("unknown" if "（本期无可用读数）" in text
                        else "weak" if "[[WEAK]]" in text
                        else "neutral" if "[[NEUTRAL]]" in text else "strong")
            # Cite the clusters whose direction matches the standing — the real
            # adjudicator is asked to name what it relied on, and a stub that cited
            # everything would hide the very defect `key_clusters` exists to catch.
            want = "down" if standing == "weak" else "up"
            cited = [ln.split("id=", 1)[1].strip()
                     for ln in body if " id=" in ln or ln.strip().startswith("· id=")]
            keys = [k for k in cited if f"|{want}|" in k] or cited
            out.append(EntityReadingView(entity=entity, standing=standing,
                                         reason=f"stub:{standing}", key_clusters=keys))
        return CrossSectionView(readings=out)

    def _dispatch(role, schema, context, **kw):
        return (_fake_cross if schema is CrossSectionView else _fake)(
            role, schema, context, **kw)

    monkeypatch.setattr(adjudicator, "run_structured", _dispatch)


class FakeBroker:
    """Records placed orders; fills everything at $100."""

    placed: list = []

    def __init__(self, *a, **k):
        pass

    def place_orders(self, items, cycle_id, wait=3.0, revision_no=0, chain=None):
        # Task 2.1: a real broker submission echoes the authorization chain
        # onto every entry, exactly like IBKRBroker._submit does.
        chain = chain or {}
        now = datetime.now(timezone.utc)
        FakeBroker.placed = list(items)
        return [TradeLogEntry(order_id="1", cycle_id=cycle_id, symbol=d.symbol, action=d.action,
                              qty=q, status="filled", submitted_at=now, filled_at=now,
                              avg_fill_price=100.0, rationale=d.rationale,
                              revision_no=revision_no, order_seq=i,
                              decision_hash=chain.get("decision_hash", ""),
                              approval_id=chain.get("approval_id", ""))
                for i, (d, q) in enumerate(items)]

    def get_fills(self):
        return [{"exec_id": "e1", "symbol": "NVDA", "side": "BOT", "shares": 5, "price": 100,
                 "time": datetime.now(timezone.utc).isoformat(), "realized_pnl": None,
                 "commission": 1.0, "order_id": "1"}]


def _fresh_portfolio() -> "PortfolioSnapshot":
    from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot

    now = datetime.now(timezone.utc)
    return PortfolioSnapshot(as_of=now, net_liquidation=1_000_000.0, cash=900_000.0,
                             gross_exposure=100_000.0, daily_pnl=0.0,
                             positions=[], exposure=ExposureBreakdown())


@pytest.fixture
def broker(monkeypatch):
    """Hermetic broker stack: FakeBroker, $100 last price, and a FRESH paper
    portfolio so the execution authorization gate (7.5) passes — a real order
    without a current snapshot is refused."""
    from ats.trader import execute as texec
    from ats.schemas.risk import RiskReview

    FakeBroker.placed = []
    monkeypatch.setattr(texec, "IBKRBroker", FakeBroker)
    monkeypatch.setattr(texec, "_last_price", lambda s: 100.0)
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: _fresh_portfolio())
    monkeypatch.setattr("ats.risk.assess.enrich_beta", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.enrich_options", lambda p: None)
    monkeypatch.setattr("ats.risk.assess.assess",
                        lambda p, **k: RiskReview(as_of=datetime.now(timezone.utc),
                                                  risk_state="normal"))
    return FakeBroker


class FakeAsyncChannel:
    """Async BossChannel stub: captures the approval request instead of sending."""

    is_async = True

    def __init__(self):
        self.thread_id = None
        self.request = None
        self.notifications = []

    def push(self, msg):
        self.notifications.append(msg)

    def send_approval_request(self, req, thread_id):
        self.request = req
        self.thread_id = thread_id

    def fetch_report_context(self, query):
        from ats.channel.context import build_report_bundle

        return build_report_bundle(query)


@pytest.fixture
def async_channel():
    return FakeAsyncChannel()


class FakeChannel:
    """Programmable BossChannel for tests: replays a scripted verdict."""

    def __init__(self, verdict: BossApproval):
        self.verdict = verdict
        self.requests: list[ApprovalRequest] = []
        self.notifications: list[Notification] = []

    def push(self, msg: Notification) -> None:
        self.notifications.append(msg)

    def request_approval(self, req: ApprovalRequest) -> BossApproval:
        self.requests.append(req)
        return self.verdict

    def fetch_report_context(self, query: str) -> ReportBundle:
        return ReportBundle(query=query)


@pytest.fixture
def approve_all():
    return FakeChannel(BossApproval(status="approved", reviewer="test",
                                    reviewed_at=datetime.now(timezone.utc)))


@pytest.fixture
def reject_all():
    return FakeChannel(BossApproval(status="rejected", reviewer="test",
                                    reviewed_at=datetime.now(timezone.utc)))
