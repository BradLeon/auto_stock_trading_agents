"""A failed frontier benchmark route is visible, and the two routes that failed silently work again.

2026-11-27: from 2026-10-23 LiveBench answered 404 (its repository was removed) and Toolathlon answered
Markdown to the adapter's Accept header, while every job stayed `succeeded` and green. All network traffic
here is faked.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from ats.data import persistent_queue, refresh
from ats.data.adapters.structured import registry
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import DiscoveryStatus, FetchRequest, IngestionStatus
from ats.data.pipelines.structured import discovery
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import frontier_ai_capability as product
from ats.data.sources.frontier_ai_capability import PublicBenchmarkAdapter, _parse_toolathlon_verified
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.runtime import collection_alerts as ca
from ats.runtime.dashboard import queries

SOURCE = "frontier_ai_capability"
DATASET = "frontier_ai_capability_benchmarks"
LIVEBENCH_URL = "https://livebench.ai/v1/table_2026_06_25.csv"
TOOLATHLON_URL = "https://toolathlon.xyz/docs/leaderboard"
NOW = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)          # Friday, after the 07:25 / 07:35 job times


def _table(*rows: tuple[str, str, str]) -> str:
    body = "".join(f'<tr><td data-label="Model">{model}</td><td data-label="Pass@1">{score} ± 1.0</td>'
                   f'<td data-label="Date">{day}</td></tr>' for model, score, day in rows)
    return f"<table>{body}</table>"


CURRENT = ("Claude Opus 5", "61.0", "2026-08-30")
ARCHIVED = ("Grok 4", "27.5", "2025-10-28")
TOOLATHLON_HTML = (f"<html>{_table(CURRENT)}<h2>Previous Toolathlon leaderboard</h2>"
                   f"{_table(ARCHIVED)}</html>").encode()
TOOLATHLON_MARKDOWN = b"# Leaderboard\n\n| Model | Pass@1 |\n|---|---|\n| Claude Opus 5 | 61.0 |\n"
LIVEBENCH_CSV = b"model,math,code\nDeepSeek V4 Flash,80,70\n"


class _Response:
    def __init__(self, body: bytes):
        self.body, self.headers = body, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


class _Web:
    """URL -> bytes, an exception, or a list consumed one answer per request."""

    def __init__(self, answers: dict):
        self.answers, self.requests = dict(answers), []

    def __call__(self, request, timeout=0):
        self.requests.append(request)
        answer = self.answers[request.full_url]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Response(answer)


def _not_found(url: str) -> HTTPError:
    return HTTPError(url, 404, "Not Found", None, None)


GOOD = {LIVEBENCH_URL: LIVEBENCH_CSV, TOOLATHLON_URL: TOOLATHLON_HTML}
BROKEN_TOOLATHLON = {**GOOD, TOOLATHLON_URL: TOOLATHLON_MARKDOWN}
ALL_BROKEN = {LIVEBENCH_URL: _not_found(LIVEBENCH_URL), TOOLATHLON_URL: TOOLATHLON_MARKDOWN}
SCOPE = {"benchmarks": ["livebench", "toolathlon_verified"]}


@pytest.fixture(autouse=True)
def public_routes_open(monkeypatch):
    for name in ("ATS_FRONTIER_AI_PUBLIC_ROUTES", "ATS_FRONTIER_AI_USE_AA", "ATS_FRONTIER_AI_DISABLED_BENCHMARKS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES", "1")
    monkeypatch.setenv("ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED", "1")
    monkeypatch.setenv("ATS_FRONTIER_AI_PUBLIC_HTML_BENCHMARKS", "toolathlon_verified")


def _adapter(web: _Web, at: datetime = NOW) -> PublicBenchmarkAdapter:
    adapter = PublicBenchmarkAdapter(api_key="", opener=web, clock=lambda: at)
    adapter.retry_delay_seconds = 0
    return adapter


def _request(scope: dict | None = None) -> FetchRequest:
    return FetchRequest(source_id=SOURCE, dataset_id=DATASET, query_scope=scope or SCOPE)


def _repo(tmp_path) -> SQLiteStructuredRepository:
    repo = SQLiteStructuredRepository(tmp_path / "data.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def _scored(batch) -> set[str]:
    return {record.dimensions["benchmark_id"] for record in batch.records}


# --------------------------------------------------------------------------- #
# the two routes
# --------------------------------------------------------------------------- #
def test_both_routes_ask_for_what_their_parser_reads():
    web = _Web(GOOD)
    batch = _adapter(web).fetch(_request())
    assert (batch.status, batch.failures) == (IngestionStatus.SUCCEEDED, [])
    assert [r.full_url for r in web.requests] == [LIVEBENCH_URL, TOOLATHLON_URL]
    assert web.requests[1].get_header("Accept") == "text/html,application/xhtml+xml"
    by_benchmark = {r.dimensions["benchmark_id"]: r for r in batch.records}
    assert by_benchmark["livebench"].dimensions["source_transport"] == "official_csv"
    assert by_benchmark["livebench"].period == "2026-06-25"
    assert by_benchmark["toolathlon_verified"].value == 61.0


def test_the_archived_toolathlon_table_is_not_part_of_the_verified_series():
    rows, meta = _parse_toolathlon_verified(TOOLATHLON_HTML, source_url=TOOLATHLON_URL, fetched_date="2026-11-27")
    assert [(r["model_name"], r["score_as_of"]) for r in rows] == [("Claude Opus 5", "2026-08-30")]
    assert (meta["record_count"], meta["excluded_before_series_start"], meta["parser_drift"]) == (1, 1, False)
    # an archive alone means the current table went missing
    rows, meta = _parse_toolathlon_verified(_table(ARCHIVED).encode(), source_url=TOOLATHLON_URL,
                                            fetched_date="2026-11-27")
    assert rows == [] and meta["parser_drift"] is True


def test_archived_rows_already_in_the_store_are_hidden_from_the_product():
    def row(benchmark, day):
        return {"period": day, "dimensions": {"benchmark_id": benchmark, "score_as_of": day}}

    stored = [row("toolathlon_verified", "2026-05-19"), row("toolathlon_verified", "2026-06-30"),
              row("livebench", "2026-06-25")]
    products = SimpleNamespace(structured=SimpleNamespace(observations=lambda **kwargs: stored))
    assert product._rows(products) == stored[1:]


# --------------------------------------------------------------------------- #
# route failures
# --------------------------------------------------------------------------- #
def test_one_transport_fault_is_retried_and_a_lasting_one_fails_only_its_route():
    flaky = _Web({**GOOD, LIVEBENCH_URL: [URLError("EOF in violation of protocol"), LIVEBENCH_CSV]})
    batch = _adapter(flaky).fetch(_request())
    assert batch.failures == [] and len(flaky.requests) == 3

    down = _Web({**GOOD, LIVEBENCH_URL: TimeoutError("read timed out")})
    batch = _adapter(down).fetch(_request())
    assert len(down.requests) == 3 and _scored(batch) == {"toolathlon_verified"}
    assert [(f.status, f.message.split(":")[1]) for f in batch.failures] == [
        (IngestionStatus.UNREACHABLE, "livebench")]


def test_an_http_error_is_not_retried():
    web = _Web({**GOOD, LIVEBENCH_URL: _not_found(LIVEBENCH_URL)})
    batch = _adapter(web).fetch(_request())
    assert len(web.requests) == 2
    assert batch.status == IngestionStatus.PARTIAL and _scored(batch) == {"toolathlon_verified"}
    assert batch.failures[0].message.startswith("route_failed:livebench:public_source_http_404:")


@pytest.mark.parametrize("answers, failed, detail", [
    (BROKEN_TOOLATHLON, "toolathlon_verified", "parser_drift"),
    ({**GOOD, LIVEBENCH_URL: b"model,math,code\n"}, "livebench", "no_rows"),
])
def test_a_route_that_yields_no_score_fails_without_taking_the_others(answers, failed, detail):
    batch = _adapter(_Web(answers)).fetch(_request())
    assert batch.status == IngestionStatus.PARTIAL and batch.records
    assert [(f.status, f.message) for f in batch.failures] == [
        (IngestionStatus.PARSE_FAILED, f"route_failed:{failed}:{detail}")]
    assert failed not in _scored(batch)


def test_a_closed_policy_gate_is_reported_as_access(monkeypatch):
    monkeypatch.delenv("ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED")
    batch = _adapter(_Web(GOOD)).fetch(_request())
    assert [f.status for f in batch.failures] == [IngestionStatus.ACCESS_REQUIRED]


def test_a_route_switched_off_on_purpose_is_neither_fetched_nor_a_failure(monkeypatch):
    monkeypatch.setenv("ATS_FRONTIER_AI_DISABLED_BENCHMARKS", " toolathlon_verified ")
    monkeypatch.delenv("ATS_FRONTIER_AI_PUBLIC_HTML_BENCHMARKS")     # removed from the allowlist as well
    web = _Web(GOOD)
    batch = _adapter(web).fetch(_request())
    assert (batch.status, batch.failures) == (IngestionStatus.SUCCEEDED, [])
    assert [r.full_url for r in web.requests] == [LIVEBENCH_URL] and _scored(batch) == {"livebench"}
    assert {"benchmark_id": "toolathlon_verified", "status": "disabled"} in batch.provider_metadata["sources"]


def test_a_mistyped_disabled_route_stops_the_run_before_any_request(monkeypatch):
    monkeypatch.setenv("ATS_FRONTIER_AI_DISABLED_BENCHMARKS", "toolathlon")
    web = _Web(GOOD)
    batch = _adapter(web).fetch(_request())
    assert web.requests == [] and batch.records == []
    assert batch.failures[0].message == "unknown_disabled_benchmarks:toolathlon"


def test_a_job_whose_routes_are_all_switched_off_does_not_pass_as_healthy(scheduled, monkeypatch):
    monkeypatch.setenv("ATS_FRONTIER_AI_DISABLED_BENCHMARKS", "livebench,toolathlon_verified")
    row = scheduled("frontier_ai_capability_benchmark_probe_p7d", GOOD, NOW)
    assert row["status"] == "failed"


def test_a_partial_run_names_the_route_in_its_note(tmp_path):
    repo = _repo(tmp_path)
    result = IngestionPipeline(repo).run(_adapter(_Web(BROKEN_TOOLATHLON)), _request())
    assert (result["status"], result["accepted"]) == ("partial", 1)
    note = repo.ingestion_history(source_id=SOURCE)[0]["note"]
    assert note == "route_failed:toolathlon_verified:parser_drift"


def test_release_check_ingests_every_partial_result_even_with_an_unchanged_payload(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    clock = {"at": NOW}

    def build(source_id, **kwargs):
        adapter = _adapter(_Web(BROKEN_TOOLATHLON), clock["at"])
        fetch = adapter.fetch

        def same_payload_every_time(request):
            batch = fetch(request)
            return batch.model_copy(update={"provider_metadata": {**batch.provider_metadata,
                                                                  "payload_sha256": "unchanged"}})

        adapter.fetch = same_payload_every_time
        return adapter, _request()

    monkeypatch.setattr(discovery, "build_ingestion", build)
    monkeypatch.setattr(registry, "build_ingestion", build)
    for week in (0, 7):
        clock["at"] = NOW + timedelta(days=week)
        packet = discovery.release_check(repo, source_ids=[SOURCE], ingest_new=True, force=True)
        assert packet["status"] == "partial"
        outcome = packet["sources"][0]
        assert outcome["status"] == DiscoveryStatus.PARTIAL.value
        assert outcome["ingestion"]["status"] == "partial"
    assert len(repo.ingestion_history(source_id=SOURCE)) == 2


# --------------------------------------------------------------------------- #
# ledger -> dashboard light -> alert
# --------------------------------------------------------------------------- #
@pytest.fixture
def scheduled(tmp_path, monkeypatch):
    """Run one weekly job against faked sites: real adapter, pipeline and ledger, a fake queue."""
    var = tmp_path / "var"
    var.mkdir()
    monkeypatch.setenv("ATS_DASHBOARD_VAR", str(var))
    repo = _repo(var)
    state: dict = {}

    class _Queue:
        def __init__(self, *a, **k):
            pass

        def enqueue(self, **kwargs):
            return f"task-{kwargs['trigger_ref']}", True

        def run_one(self, **kwargs):
            result = IngestionPipeline(repo).run(_adapter(_Web(state["answers"]), state["at"]), _request())
            return {"stdout": {"status": result["status"]}, "reported_status": result["status"],
                    "exit_code": 0, "result": {"stdout_sha256": "x"}}

        def record_lineage(self, *a, **k):
            pass

    monkeypatch.setattr(persistent_queue, "PersistentIngestionQueue", _Queue)
    monkeypatch.setattr(refresh, "_utc_now", lambda: state["at"])
    full = refresh.load_plan()

    def run(job_id: str, answers: dict, at: datetime) -> dict:
        state.update(answers=answers, at=at)
        plan = replace(full, jobs=tuple(j for j in full.jobs if j.id == job_id),
                       ledger_path=var / "data_refresh.sqlite")
        assert plan.jobs
        return refresh.run_tick(plan, now=at, data_db=var / "data.sqlite")[0]

    run.ledger = var / "data_refresh.sqlite"
    return run


@pytest.mark.parametrize("job_id", [
    "frontier_ai_capability_benchmark_probe_p7d",       # ats data ingest
    "frontier_ai_capability_full_audit_p7d",            # ats data frontier-capability-audit
])
def test_the_ledger_keeps_the_run_and_names_the_failed_route(scheduled, job_id):
    row = scheduled(job_id, BROKEN_TOOLATHLON, NOW)
    assert (row["status"], row["reason"]) == ("succeeded", "partial_route_failed:toolathlon_verified")


def test_every_route_failing_is_still_a_failed_run(scheduled):
    row = scheduled("frontier_ai_capability_benchmark_probe_p7d", ALL_BROKEN, NOW)
    assert row["status"] == "failed"


def test_light_goes_yellow_then_red_then_recovers_and_each_change_is_pushed(scheduled):
    job_id = "frontier_ai_capability_benchmark_probe_p7d"
    sent: list[tuple[str, str]] = []

    def week(number: int, answers: dict):
        at = NOW + timedelta(weeks=number)
        scheduled(job_id, answers, at)
        rows = [r for r in queries.source_rows(queries.DashboardPaths.from_env(), at) if r.job_id == job_id]
        sent.clear()
        ca.after_tick(ledger=scheduled.ledger, rows=rows, now=at.replace(hour=23),     # 07:00 Taipei: no digest
                      push=lambda title, body: sent.append((title, body)))
        return rows[0]

    assert week(0, GOOD).light == "green" and sent == []          # the first alert run only seeds the state
    first = week(1, BROKEN_TOOLATHLON)
    assert (first.light, first.light_reason) == ("yellow", "部分路由失败：toolathlon_verified")
    assert "部分路由失败：toolathlon_verified" in sent[0][1] and "需注意" in sent[0][0]
    second = week(2, {**BROKEN_TOOLATHLON, LIVEBENCH_URL: b"model,math,code\nDeepSeek V4 Flash,81,70\n"})
    assert (second.light, second.light_reason) == ("red", "部分路由连续 2 次失败：toolathlon_verified")
    assert "异常" in sent[0][0]
    third = week(3, GOOD)
    assert (third.light, third.consecutive_partial) == ("green", 0)
    assert "已恢复" in sent[0][0]


def test_partial_streak_ranks_below_staleness_and_repeated_failures():
    healthy = NOW - timedelta(days=1)
    assert queries.assess(True, 10, healthy, NOW, consecutive_failures=2, consecutive_partial=2,
                          failed_routes="x")[1] == "连续失败 2 次"
    assert queries.assess(True, 10, NOW - timedelta(days=11), NOW, consecutive_partial=1,
                          failed_routes="x")[1].startswith("已超 SLO")
    assert queries.assess(True, 10, healthy, NOW, consecutive_failures=1, consecutive_partial=2,
                          failed_routes="x") == ("red", "部分路由连续 2 次失败：x")
