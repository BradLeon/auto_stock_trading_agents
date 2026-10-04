from datetime import datetime, timezone
from types import SimpleNamespace

from ats.data.persistent_queue import PersistentIngestionQueue, enqueue_cache_miss
from ats.data import persistent_queue
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository
from ats.runtime import scheduler
from ats.chain import sources as chain_sources
from ats.data import research, yahoo_news
from ats.data.pipelines import factset_earnings_insight
from ats.data.pipelines.unstructured.article_ingest import _article_sources
from ats.data.catalog.loader import DataCatalog
from ats.data import calendar_refresh
from ats.data.stores.schedule_calendar import ScheduleCalendarStore
from ats.data.runtime import market_data
from ats.trader import portfolio
from ats.runtime import cli


def test_consensus_cache_miss_is_idempotently_queued_not_executed(tmp_path, monkeypatch):
    queue_path = tmp_path / "persistent-queue.sqlite"
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(queue_path))
    now = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    command = ["ats", "data", "ingest", "--source", "yfinance_consensus",
               "--entity", "MSFT"]

    first = enqueue_cache_miss(
        source_id="yfinance_consensus", dataset_id="market_consensus",
        entity="msft", command=command, now=now)
    duplicate = enqueue_cache_miss(
        source_id="yfinance_consensus", dataset_id="market_consensus",
        entity="MSFT", command=command, now=now)

    assert first is not None and first["created"] is True
    assert duplicate is not None and duplicate["created"] is False
    task = PersistentIngestionQueue(queue_path).get(first["task_id"])
    assert task["status"] == "queued"
    assert task["trigger_kind"] == "cache_miss"
    assert task["command"] == command


def test_unregistered_cache_miss_policy_cannot_enqueue(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(tmp_path / "queue.sqlite"))
    result = enqueue_cache_miss(
        source_id="runtime_quote", dataset_id="runtime_quotes", entity="MSFT",
        command=["ats", "data", "ingest", "--source", "runtime_quote"])
    assert result is None
    assert PersistentIngestionQueue(tmp_path / "queue.sqlite").list() == []


def test_unstructured_production_store_rejects_write_without_worker_lease(
        tmp_path, monkeypatch):
    data_path = tmp_path / "production-like-data.sqlite"
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(data_path))
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(tmp_path / "queue.sqlite"))
    repo = PlatformUnstructuredRepository(data_path, writable=True)
    try:
        try:
            repo.register_data_source(SimpleNamespace(
                id="trendforce_news", label="TrendForce", adapter="trendforce_news",
                cadence="weekly", entity=""))
        except PermissionError as exc:
            assert "managed-queue lease" in str(exc)
        else:
            raise AssertionError("production unstructured write bypassed queue lease")
    finally:
        repo.close()


def test_factset_schedule_submits_one_weekly_queue_task(monkeypatch):
    calls = {}

    class Queue:
        def enqueue(self, **kwargs):
            calls.update(kwargs)
            return "task-1", True

        def run_one(self, *, task_id):
            calls["run_task_id"] = task_id
            return {"status": "succeeded", "stdout": {"status": "succeeded"}}

    monkeypatch.setattr("ats.data.persistent_queue.PersistentIngestionQueue", Queue)
    scheduler._factset_weekly_ingest()

    assert calls["source_id"] == "factset_earnings_insight_doc"
    assert calls["trigger_kind"] == "scheduled"
    assert calls["trigger_ref"].startswith("factset-weekly:")
    assert "factset_earnings_insight_metrics" in calls["scope"]["sources"]
    assert calls["command"][:3] == ["ats", "data", "factset-refresh"]
    assert calls["run_task_id"] == "task-1"


def test_direct_factset_pipeline_rejects_before_provider_access(tmp_path, monkeypatch):
    data_path = tmp_path / "production-data.sqlite"
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(data_path))
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(tmp_path / "queue.sqlite"))
    monkeypatch.setattr(factset_earnings_insight, "fetch_report", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("FactSet provider called before queue validation")))
    pipeline = factset_earnings_insight.FactSetWeeklyPipeline(
        SimpleNamespace(path=data_path), SimpleNamespace())

    try:
        pipeline.run()
    except PermissionError as exc:
        assert "managed-queue lease" in str(exc)
    else:
        raise AssertionError("direct FactSet pipeline bypassed the managed queue")


def test_direct_schedule_calendar_refresh_rejects_before_provider_access(
        tmp_path, monkeypatch):
    data_path = tmp_path / "production-data.sqlite"
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(data_path))
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(tmp_path / "queue.sqlite"))
    monkeypatch.setattr(calendar_refresh, "_fetch_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("calendar provider called before queue validation")))
    try:
        calendar_refresh.refresh_schedule_calendar(
            source_ids={"federal_reserve_fomc"},
            store=ScheduleCalendarStore(data_path))
    except PermissionError as exc:
        assert "managed-queue lease" in str(exc)
    else:
        raise AssertionError("direct calendar refresh bypassed the managed queue")


def test_schedule_calendar_producer_queues_sources_and_finalizer(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(tmp_path / "queue.sqlite"))
    now = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    tasks = calendar_refresh.enqueue_schedule_calendar_refresh(
        trigger_kind="scheduled", trigger_ref="calendar-test:2026-09-25T12:00Z",
        now=now)

    assert tasks[-1]["source_id"] == "calendar_maintenance"
    assert tasks[-1]["status"] == "queued"
    assert all(task["status"] == "queued" for task in tasks)
    assert {task["source_id"] for task in tasks[:-1]} >= {
        "federal_reserve_fomc", "bls_release_calendar", "bea_release_schedule"}
    assert all(PersistentIngestionQueue().get(task["task_id"])["status"] == "queued"
               for task in tasks)


def test_runtime_batch_prices_keep_per_symbol_status_and_never_enqueue(
        tmp_path, monkeypatch):
    import pandas as pd

    queue_path = tmp_path / "queue.sqlite"
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(queue_path))
    frame = pd.DataFrame(
        {"MSFT": [100.0, 101.0]},
        index=pd.to_datetime(["2026-09-24", "2026-09-25"]))
    monkeypatch.setattr(market_data, "_download_close_frame",
                        lambda *_args, **_kwargs: (frame, ["MSFT", "TSLA"]))
    rows = market_data.fetch_close_history_many(["MSFT", "TSLA"])

    assert rows["MSFT"].status == "succeeded"
    assert rows["MSFT"].bar_as_of.isoformat() == "2026-09-25"
    assert rows["MSFT"].queried_at.tzinfo is not None
    assert rows["TSLA"].status == "no_data"
    assert PersistentIngestionQueue(queue_path).list() == []


def test_broker_portfolio_read_bypasses_ingestion_queue(tmp_path, monkeypatch):
    queue_path = tmp_path / "queue.sqlite"
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(queue_path))
    expected = SimpleNamespace(as_of="2026-09-25T12:00:00+00:00")

    class Broker:
        def __init__(self, **_kwargs):
            pass

        def get_portfolio(self):
            return expected

    monkeypatch.setattr(portfolio, "IBKRBroker", Broker)
    monkeypatch.setattr(portfolio, "_sector_map", lambda: {})

    assert portfolio.snapshot() is expected
    assert PersistentIngestionQueue(queue_path).list() == []


def test_managed_worker_supplies_live_lease_and_completes_successfully(
        tmp_path, monkeypatch):
    queue_path = tmp_path / "queue.sqlite"
    queue = PersistentIngestionQueue(queue_path)
    task_id, _ = queue.enqueue(
        source_id="calendar_maintenance",
        scope={"sources": ["calendar_maintenance"]},
        trigger_kind="manual", trigger_ref="calendar-maintenance-test",
        command=["ats", "data", "calendar-finalize"],
        policy_fingerprint="test", requested_as_of=datetime.now(timezone.utc).isoformat())
    seen = {}

    class Process:
        returncode = 0

        def communicate(self, timeout=None):
            seen["timeout"] = timeout
            return b'{"status":"succeeded"}', b""

        def terminate(self):
            raise AssertionError("successful worker must not be terminated")

        def kill(self):
            raise AssertionError("successful worker must not be killed")

    def launch(argv, **kwargs):
        seen["argv"] = argv
        env = kwargs["env"]
        seen["lease_valid"] = queue.valid_source_lease(
            env["ATS_PERSISTENT_QUEUE_TASK_ID"],
            env["ATS_PERSISTENT_QUEUE_LEASE_OWNER"],
            env["ATS_PERSISTENT_QUEUE_SOURCE_ID"],
        )
        return Process()

    monkeypatch.setattr(persistent_queue.subprocess, "Popen", launch)
    result = queue.run_one(worker_id="test-worker", task_id=task_id)

    assert seen["lease_valid"] is True
    assert seen["argv"][-2:] == ["data", "calendar-finalize"]
    assert result["status"] == "succeeded"
    assert queue.get(task_id)["status"] == "succeeded"


def test_manual_structured_ingest_cli_only_enqueues(monkeypatch, capsys):
    calls = {}

    class Queue:
        def __init__(self):
            pass

        def enqueue(self, **kwargs):
            calls.update(kwargs)
            return "manual-task", True

        def run_one(self, *, task_id):
            calls["run_task_id"] = task_id
            return None

        def get(self, task_id):
            return {"status": "queued"}

    monkeypatch.setattr("ats.data.persistent_queue.PersistentIngestionQueue", Queue)
    code = cli.main(["data", "ingest", "--source", "yfinance_consensus", "--entity", "MSFT"])

    assert code == 0
    assert calls["source_id"] == "yfinance_consensus"
    assert calls["command"] == ["ats", "data", "ingest", "--source",
                                 "yfinance_consensus", "--entity", "MSFT"]
    assert "run_task_id" in calls
    assert '"status": "queued"' in capsys.readouterr().out


def test_governed_article_sources_come_from_authoritative_unstructured_registry():
    sources = _article_sources()
    catalog = DataCatalog.load()
    target_ids = {item.id for item in catalog.target_unstructured_sources()}

    assert {"factset_earnings_insight_doc", "trendforce_news", "semianalysis",
            "ibkr_news", "yfinance_live_news"} <= set(sources)
    assert set(sources) <= target_ids
    assert target_ids - set(sources) == {
        "defeatbeta_sec_filing_index", "sec_edgar_filing_body",
        "defeatbeta_earnings_transcript", "ai_hardware_knowledge_corpus"}
    assert "kr_semiconductor_exports" not in target_ids
    assert sources["trendforce_news"].max_per_run == 16
    assert sources["trendforce_news"].entity == "TRENDFORCE"
    assert sources["ibkr_news"].params["symbols"]
    assert "sources" not in catalog.raw["domains"]
    assert "news_sources" not in catalog.raw["domains"]
    assert catalog.validate().valid


def test_legacy_chain_collection_rejects_adapter_call_without_queue_lease(monkeypatch):
    class Store:
        def register_data_source(self, *_args, **_kwargs):
            raise AssertionError("write stage must not run before queue validation")

    source = SimpleNamespace(id="kr_semiconductor_exports", concepts={"supply_tightness"})
    monkeypatch.setattr(chain_sources, "load_sources", lambda: [source])
    try:
        chain_sources.collect(Store())
    except PermissionError as exc:
        assert "managed-queue lease" in str(exc)
    else:
        raise AssertionError("legacy Chain collection bypassed the managed queue")


def test_legacy_research_and_yahoo_producers_fail_before_provider_access(monkeypatch):
    monkeypatch.setattr(research, "fetch_batch", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("newsletter provider called before queue validation")))
    try:
        research.ingest_batch(datetime.now(timezone.utc))
    except PermissionError:
        pass
    else:
        raise AssertionError("legacy newsletter ingest bypassed the managed queue")

    monkeypatch.setattr(yahoo_news, "fetch_many", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("Yahoo provider called before retirement validation")))
    try:
        yahoo_news.backfill(["MSFT"], datetime.now(timezone.utc))
    except PermissionError as exc:
        assert "retired for production" in str(exc)
    else:
        raise AssertionError("unregistered standalone Yahoo backfill remained writable")
