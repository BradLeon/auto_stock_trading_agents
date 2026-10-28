"""Which filings the SEC body job spends its daily budget on (hermetic: no network).

Found 2026-10-26 (G0 U06): two daily runs in a row admitted nothing. Of the 20 filings each run took, 8 were already in
the library, 8 were permanent 404s retried every day (their recency never moved, because identical failures are stored
idempotently), and the newest ones (SKHY's 6-K of 10-23) were left at the end of the line with the budget gone.
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ats.data.pipelines.unstructured import document_ingest as fixed_sources
from ats.data.pipelines.unstructured.sec import SECTransport
from ats.data.sec import SecFetchResult
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
FILED = "2026-10-23"


def _filing(n: int, symbol: str = "NVDA", filed: str = FILED) -> dict:
    accession = f"0001045810-26-{n:06d}"
    return {"symbol": symbol, "cik": "1045810", "accession_number": accession,
            "company_name": "NVIDIA Corporation", "form_type": "10-Q", "filing_date": filed,
            "filing_url": f"https://www.sec.gov/Archives/edgar/data/1045810/{accession.replace('-', '')}/nvda.htm"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_DOCS_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test research@example.com")
    store = PlatformUnstructuredRepository(tmp_path / "fixed.sqlite", writable=True)
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: {
        "revision": "a" * 40, "spec_sha256": "b" * 64, "file_sha256": "c" * 64,
        "file_updated_at": NOW.isoformat(), "source_url": "https://huggingface.co/example.parquet"})
    state = {"filings": [], "missing": set(), "spent": 0, "tried": []}
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda *_a, **_k: list(state["filings"]))

    def fetch(row, transport):
        state["tried"].append(row["accession_number"])
        if row["accession_number"] in state["missing"]:
            return SecFetchResult(status="missing", stage="complete_submission"), "regulatory_filing"
        transport.remaining -= 1                      # a successful request is what uses the budget
        return SecFetchResult(text=f"NVIDIA Corporation filing {row['accession_number']}. " * 80,
                              url=row["filing_url"], status="succeeded"), "regulatory_filing"

    monkeypatch.setattr(fixed_sources, "fetch_filing", fetch)
    original = fixed_sources._rows

    def rows():
        data = original()
        data["sec_edgar_filing_body"] = {**data["sec_edgar_filing_body"], "request_budget": {
            **data["sec_edgar_filing_body"]["request_budget"], "max_requests_per_run": state.get("budget", 20)}}
        return data

    monkeypatch.setattr(fixed_sources, "_rows", rows)

    def run(at=NOW):
        state["tried"].clear()
        return fixed_sources.ingest_documents("sec_edgar_filing_body", entity="NVDA", now=at, store=store)

    def index():
        return fixed_sources.ingest_documents("defeatbeta_sec_filing_index", entity="NVDA", now=NOW, store=store)

    state.update(store=store, run=run, index=index)
    yield state
    store.close()


def test_an_admitted_filing_is_not_fetched_again_until_the_weekly_recheck(world):
    world["filings"] = [_filing(1)]
    world["index"]()
    assert world["run"]()["accepted"] == 1
    assert world["run"]()["discovered"] == 0 and world["tried"] == []
    assert world["run"](NOW + timedelta(days=8))["discovered"] == 1            # revised bodies are still caught


def test_a_filing_that_keeps_missing_is_given_up_after_three_days_of_tries(world):
    world["filings"] = [_filing(1), _filing(2)]
    world["missing"] = {_filing(1)["accession_number"]}
    world["index"]()
    results = [world["run"](NOW + timedelta(days=i)) for i in range(5)]
    assert [r["discovered"] for r in results] == [2, 1, 1, 0, 0]               # the good one is admitted on day one
    attempts = world["store"].conn.execute(
        "SELECT COUNT(*) FROM data_fixed_source_attempts WHERE natural_key=? AND outcome!='accepted'",
        (_filing(1)["accession_number"],)).fetchone()[0]
    assert attempts == 3                                                      # tried on three days, then left alone


def test_runs_within_one_day_count_as_one_try(world):
    world["filings"] = [_filing(1)]
    world["missing"] = {_filing(1)["accession_number"]}
    world["index"]()
    for hour in range(6):
        world["run"](NOW + timedelta(hours=hour))                             # six runs, one day
    assert world["run"](NOW + timedelta(days=1))["discovered"] == 1          # still being tried


def test_the_budget_is_spent_by_successes_and_the_rest_are_deferred_not_failed(world):
    world["budget"] = 2
    world["filings"] = [_filing(n, filed=f"2026-10-{10 + n}") for n in range(1, 5)]
    world["index"]()
    first = world["run"]()
    assert first["accepted"] == 2
    codes = json.loads(world["store"].conn.execute(
        "SELECT reason_codes FROM data_ingestion_runs WHERE source_id='sec_edgar_filing_body' "
        "ORDER BY started_at DESC LIMIT 1").fetchone()[0])
    assert codes.get("deferred_budget") == 2 and "source_unavailable" not in codes
    ledger = world["store"].conn.execute("SELECT COUNT(*) FROM data_fixed_source_attempts").fetchone()[0]
    assert ledger == 2                                                         # deferred ones left no attempt behind
    second = world["run"]()
    assert second["accepted"] == 2                                             # and are first in line next time
    assert {*first["document_ids"], *second["document_ids"]} and len(
        {*first["document_ids"], *second["document_ids"]}) == 4


def test_a_filing_that_was_never_tried_goes_before_one_that_failed(world):
    world["filings"] = [_filing(1, filed="2026-10-20"), _filing(2, filed="2026-10-22")]
    world["missing"] = {_filing(2)["accession_number"]}
    world["index"]()
    world["budget"] = 1
    world["run"]()                                    # tries the newer one first; it is missing
    world["filings"].append(_filing(3, filed="2026-10-25"))
    world["index"]()
    world["run"](NOW + timedelta(hours=1))
    assert world["tried"][0] == _filing(3)["accession_number"]                # newest, never tried
    assert _filing(2)["accession_number"] not in world["tried"][:1]


class _Response:
    def __init__(self, status):
        self.status = status
        self.is_redirect = False
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("x", request=None, response=self)

    @property
    def status_code(self):
        return self.status

    def iter_bytes(self):
        yield b"body"


class _Client:
    statuses: list[int] = []

    def __init__(self, *_a, **_k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def stream(self, _method, _url):
        status = self.statuses.pop(0)
        if status == "connect":
            raise httpx.ConnectError("unreachable")

        class _Ctx:
            def __enter__(self_inner):
                return _Response(status)

            def __exit__(self_inner, *_a):
                return False

        return _Ctx()


def test_failed_requests_do_not_use_the_request_budget(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test research@example.com")
    monkeypatch.setattr("ats.data.pipelines.unstructured.sec.httpx.Client", _Client)
    monkeypatch.setattr("ats.data.pipelines.unstructured.sec.time.sleep", lambda *_: None)
    transport = SECTransport({"max_requests_per_run": 1, "max_failed_requests_per_run": 3})
    row = _filing(7)
    request = transport.for_filing({"accession_number": row["accession_number"], "cik": row["cik"]})
    url = row["filing_url"]

    _Client.statuses = [404]
    assert request(url, stage="filing_index")[0] == ""                          # a 404 ...
    assert transport.remaining == 1 and transport.failures_remaining == 3       # ... is an answer, costs nothing
    _Client.statuses = [200]
    assert request(url, stage="complete_submission")[0] == "body"
    assert transport.remaining == 0 and transport.exhausted
    _, failures = request(url, stage="complete_submission")
    assert [f.error_type for f in failures] == ["RequestBudgetExceeded"]


def test_failures_have_their_own_ceiling(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test research@example.com")
    monkeypatch.setattr("ats.data.pipelines.unstructured.sec.httpx.Client", _Client)
    monkeypatch.setattr("ats.data.pipelines.unstructured.sec.time.sleep", lambda *_: None)
    transport = SECTransport({"max_requests_per_run": 5, "max_failed_requests_per_run": 2})
    row = _filing(8)
    request = transport.for_filing({"accession_number": row["accession_number"], "cik": row["cik"]})
    _Client.statuses = ["connect", "connect", "connect"]
    request(row["filing_url"], stage="a")                                       # two tries, both flaky
    assert transport.failures_remaining == 0 and transport.exhausted and transport.remaining == 5
    assert [f.error_type for f in request(row["filing_url"], stage="b")[1]] == ["RequestBudgetExceeded"]


def test_a_filing_that_yields_no_body_gives_its_requests_back(monkeypatch):
    from ats.data.pipelines.unstructured import sec

    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test research@example.com")
    transport = SECTransport({"max_requests_per_run": 20})

    def nothing_found(*_a, **_k):                       # two successful requests, then no press-release exhibit
        transport.remaining -= 2
        return SecFetchResult(status="missing", stage="complete_submission")

    monkeypatch.setattr(sec.extractor, "exhibit_result", nothing_found)
    result, _ = sec.fetch_filing(_filing(9) | {"form_type": "8-K"}, transport)
    assert result.status == "missing" and transport.remaining == 20
