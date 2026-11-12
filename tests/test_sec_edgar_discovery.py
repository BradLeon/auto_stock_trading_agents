"""EDGAR filing discovery for due earnings, and the machine-wide SEC pace (hermetic: no network).

Found 2026-10-29: GOOG and MSFT printed 10-28 16:00 New York and their 8-Ks were on EDGAR minutes later, but the only
discovery source was the DefeatBeta copy of EDGAR, which shows a filing the next day ~08:35 UTC: the post-earnings
review would have started ~22 hours after the print. The EDGAR submissions list closes that gap for due entities.
"""

import functools
import json
from datetime import date, datetime, timezone

import pytest

from ats.data import sec_rate
from ats.data.pipelines.unstructured import document_ingest as fixed_sources
from ats.data.pipelines.unstructured.sec import SECSourceUnavailable, validate_filing
from ats.data.refresh import DEFAULT_CONFIG, load_plan
from ats.data.sec import SecFetchResult
from ats.data.sources import sec_submissions
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository

NOW = datetime(2026, 10, 28, 22, 0, tzinfo=timezone.utc)          # 06:00 Taipei on the 29th
GOOG_8K = "0001652044-26-000080"


def _submissions(*filings) -> dict:
    columns = {"accessionNumber": [], "form": [], "filingDate": [], "reportDate": [],
               "acceptanceDateTime": [], "primaryDocument": []}
    for accession, form, filed in filings:
        for name, value in zip(columns, (accession, form, filed, "2026-09-30",
                                         f"{filed}T20:05:11.000Z", "goog-8k.htm"), strict=True):
            columns[name].append(value)
    return {"name": "Alphabet Inc.", "filings": {"recent": columns}}


class _Http:
    def __init__(self, answers):
        self.answers = dict(answers)
        self.urls = []

    def __call__(self, url, *, timeout):
        self.urls.append(url)
        status, payload = self.answers[url.rsplit("/", 1)[-1]]
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


# --- the pace -------------------------------------------------------------------------------------------------------

def test_requests_from_any_process_share_one_slot_sequence(monkeypatch):
    monkeypatch.setattr(sec_rate, "_interval", lambda: 0.25)
    slept = []
    for _ in range(3):
        sec_rate.wait(sleep=slept.append, clock=lambda: 1000.0)      # three callers at the same instant
    assert slept == [0.25, 0.5]                                     # 4 per second, never a burst


def test_the_rate_setting_cannot_approach_secs_limit(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("ATS_SEC_MAX_RPS", "50")
    assert sec_rate._interval() == pytest.approx(1 / 8)


def test_a_throttling_answer_stops_every_caller_for_the_cooldown():
    assert not sec_rate.observe(403, b"<html>Not Found</html>")      # an ordinary 403 is an answer
    assert sec_rate.observe(403, b"Request Rate Threshold Exceeded")
    with pytest.raises(sec_rate.SECRateLimited):
        sec_rate.wait()
    clock = lambda: 10**12                                            # noqa: E731 - long after the cooldown
    sec_rate.wait(clock=clock)


def test_a_429_is_a_throttle():
    assert sec_rate.is_throttled(429)


# --- discovery ------------------------------------------------------------------------------------------------------

def test_recent_filings_are_index_rows_the_body_job_accepts():
    rows = sec_submissions.recent_filings("GOOG", "1652044", _submissions(
        (GOOG_8K, "8-K", "2026-10-28"), ("0001652044-26-000070", "4", "2026-10-27"),
        ("0001652044-26-000010", "10-Q", "2026-07-25")), since=date(2026, 10, 21))
    assert [row["accession_number"] for row in rows] == [GOOG_8K]    # forms and age filtered
    assert validate_filing(rows[0]) == ("GOOG", GOOG_8K)


def test_only_due_entities_are_looked_up_and_known_ciks_save_the_ticker_file(tmp_path):
    http = _Http({"CIK0001652044.json": (200, _submissions((GOOG_8K, "8-K", "2026-10-28")))})
    rows, missing = sec_submissions.discover(
        ["GOOG"], now=NOW, known_ciks={"GOOG": "1652044"}, budget={"max_requests_per_run": 5},
        cache=tmp_path / "tickers.json", http=http, wait=lambda: None)
    assert [row["accession_number"] for row in rows] == [GOOG_8K] and missing == []
    assert len(http.urls) == 1


def test_a_throttle_ends_the_run_without_further_requests(tmp_path):
    http = _Http({"CIK0001652044.json": (429, b""), "CIK0000789019.json": (200, _submissions())})
    with pytest.raises(SECSourceUnavailable, match="rate_limited"):
        sec_submissions.discover(["GOOG", "MSFT"], now=NOW, known_ciks={"GOOG": "1652044", "MSFT": "789019"},
                                 budget={"max_requests_per_run": 5}, cache=tmp_path / "t.json", http=http)
    assert len(http.urls) == 1
    with pytest.raises(sec_rate.SECRateLimited):
        sec_rate.wait()


def test_the_window_is_earnings_events_around_today():
    class Calendar:
        def latest_events(self, limit):
            def event(entity, day, status="planned"):
                return {"event_type": "earnings", "status": status, "event_date": day,
                        "payload": {"metadata": {"entity": entity}}}
            return [event("GOOG", "2026-10-28"), event("SKHY", "2026-10-26"), event("NVDA", "2026-11-25"),
                    event("COHR", "2026-10-29"), event("LRCX", "2026-10-21"), event("MU", "2026-10-28", "cancelled"),
                    {"event_type": "fomc", "status": "planned", "event_date": "2026-10-28", "payload": {}}]
    assert sec_submissions.due_entities(NOW, days_before=1, days_after=3, calendar_store=Calendar()) == [
        "COHR", "GOOG", "SKHY"]


# --- the body job reads both indexes --------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_DOCS_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test research@example.com")
    store = PlatformUnstructuredRepository(tmp_path / "fixed.sqlite", writable=True)
    http = _Http({"CIK0001652044.json": (200, _submissions((GOOG_8K, "8-K", "2026-10-28")))})
    monkeypatch.setattr(fixed_sources, "_due_entities", lambda *_a, **_k: ["GOOG"])
    monkeypatch.setattr(fixed_sources, "_known_ciks", lambda _store: {"GOOG": "1652044"})
    monkeypatch.setattr(fixed_sources, "_edgar_discover",
                        functools.partial(sec_submissions.discover, http=http, wait=lambda: None))
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: {
        "revision": "a" * 40, "spec_sha256": "b" * 64, "file_sha256": "c" * 64,
        "file_updated_at": NOW.isoformat(), "source_url": "https://huggingface.co/example.parquet"})
    state = {"hf": [], "tried": [], "store": store, "http": http}
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda *_a, **_k: list(state["hf"]))

    def fetch(row, transport):
        state["tried"].append(row["accession_number"])
        return SecFetchResult(text=f"Alphabet announces third quarter results {row['accession_number']}. " * 60,
                              url=row["filing_url"], status="succeeded"), "company_release"

    monkeypatch.setattr(fixed_sources, "fetch_filing", fetch)
    yield state
    store.close()


def test_a_print_found_on_edgar_is_fetched_the_same_morning(world):
    found = fixed_sources.ingest_documents("sec_edgar_submissions_index", now=NOW, store=world["store"])
    assert found["status"] == "succeeded" and found["accepted"] == 1
    body = fixed_sources.ingest_documents("sec_edgar_filing_body", now=NOW, store=world["store"])
    assert body["accepted"] == 1 and world["tried"] == [GOOG_8K]


def test_the_same_filing_in_both_indexes_is_fetched_once(world):
    fixed_sources.ingest_documents("sec_edgar_submissions_index", now=NOW, store=world["store"])
    world["hf"] = [{**sec_submissions.recent_filings("GOOG", "1652044", _submissions(
        (GOOG_8K, "8-K", "2026-10-28")), since=date(2026, 10, 1))[0]}]
    fixed_sources.ingest_documents("defeatbeta_sec_filing_index", entity="GOOG", now=NOW, store=world["store"])
    first = fixed_sources.ingest_documents("sec_edgar_filing_body", now=NOW, store=world["store"])
    again = fixed_sources.ingest_documents("sec_edgar_filing_body", now=NOW, store=world["store"])
    assert first["discovered"] == 1 and again["discovered"] == 0 and world["tried"] == [GOOG_8K]


def test_a_repeated_discovery_run_records_nothing_new(world):
    fixed_sources.ingest_documents("sec_edgar_submissions_index", now=NOW, store=world["store"])
    again = fixed_sources.ingest_documents("sec_edgar_submissions_index", now=NOW, store=world["store"])
    assert again["status"] == "no_change"


# --- the schedule ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("shift", ["06:00", "18:00"])
def test_each_shift_runs_discovery_body_and_finalize_in_that_order_in_one_tick(shift):
    plan = load_plan(str(DEFAULT_CONFIG))
    chain = [job for job in plan.jobs if job.sources[0] in {
        "sec_edgar_submissions_index", "sec_edgar_filing_body", "calendar_maintenance"}
        and job.at.strftime("%H:%M") == shift and job.timezone_name == "Asia/Taipei"]
    assert [job.sources[0] for job in chain] == [
        "sec_edgar_submissions_index", "sec_edgar_filing_body", "calendar_maintenance"]
    assert len({job.window(NOW)[1] for job in chain}) == 1
