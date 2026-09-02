"""Focused SEC transport and 10-Q/10-K asset tests; no other source is exercised."""

import json

import httpx

from ats.data import defeatbeta, document_assets, documents, sec
from ats.memory import get_store


def _filing_body(form: str = "10-Q") -> str:
    return (f"AMD Advanced Micro Devices quarterly report FORM {form}. Revenue, assets, "
            f"liabilities, cash flows and financial statements. " * 100).strip()


def _failure(stage: str, url: str = "https://www.sec.gov/test"):
    return sec.SecFetchFailure(stage, url, "ConnectError", "connection failed")


def test_sec_request_retries_are_bounded_and_failures_are_retained(monkeypatch):
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        if len(calls) < 3:
            raise httpx.ConnectError("temporary", request=httpx.Request("GET", url))
        return httpx.Response(200, text="available", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    text, failures = sec._request_text(
        "https://www.sec.gov/retry", stage="filing_index", attempts=3)

    assert text == "available" and len(calls) == 3
    assert len(failures) == 2
    assert {failure.stage for failure in failures} == {"filing_index"}


def test_exhibit_uses_complete_submission_when_index_is_unreachable(monkeypatch):
    body = ("AMD Q2 2026 financial results. Revenue, net income, earnings per share "
            "and guidance. " * 100)
    submission = ("<SEC-DOCUMENT><DOCUMENT><TYPE>EX-99.1\n<FILENAME>q2release.htm\n"
                  f"<TEXT><html><body>{body}</body></html></TEXT></DOCUMENT></SEC-DOCUMENT>")

    def request(url, *, stage, attempts=3):
        if stage == "filing_index":
            return "", (_failure(stage, url),)
        assert stage == "complete_submission"
        return submission, ()

    monkeypatch.setattr(sec, "_request_text", request)

    result = sec.exhibit_result("2488", "0000002488-26-000001")

    assert result.status == "succeeded" and result.stage == "complete_submission"
    assert result.url.endswith("q2release.htm") and "financial results" in result.text
    assert result.errors[0].stage == "filing_index"


def test_release_exhibit_is_selected_by_document_role_not_largest_size(monkeypatch):
    index = """
    <table>
      <tr><td>1</td><td><a href="press-release.htm">Press Release</a></td>
          <td>press-release.htm</td><td>EX-99.1</td><td>12000</td></tr>
      <tr><td>2</td><td><a href="presentation.htm">Quarterly presentation</a></td>
          <td>presentation.htm</td><td>EX-99.2</td><td>70000</td></tr>
      <tr><td>3</td><td><a href="statutory-report.htm">Statutory interim report</a></td>
          <td>statutory-report.htm</td><td>EX-99.4</td><td>250000</td></tr>
    </table>
    """
    release = ("ASML reports second quarter 2026 financial results. Revenue, net "
               "income, earnings per share and guidance. " * 100)
    presentation = "ASML quarterly results presentation slides. " * 100
    statutory = "ASML statutory interim report and condensed financial statements. " * 300

    def request(url, *, stage, attempts=3):
        if stage == "filing_index":
            return index, ()
        if stage == "exhibit_document" and url.endswith("press-release.htm"):
            return f"<html><body>{release}</body></html>", ()
        if stage == "exhibit_document" and url.endswith("presentation.htm"):
            return f"<html><body>{presentation}</body></html>", ()
        if stage == "exhibit_document" and url.endswith("statutory-report.htm"):
            return f"<html><body>{statutory}</body></html>", ()
        raise AssertionError(f"unexpected request: {stage} {url}")

    monkeypatch.setattr(sec, "_request_text", request)

    result = sec.exhibit_result("937966", "0000937966-26-000001")

    assert result.status == "succeeded"
    assert result.url.endswith("press-release.htm")
    assert result.role == "earnings_release"
    assert result.description == "Press Release"


def test_role_classifier_uses_filename_and_rejects_incidental_financial_wording():
    kla_role, _ = sec._classify_document_role(
        "EX-99.1 exhibit991earningsrelease7.htm",
        "KLA reports fiscal 2026 fourth quarter and full year results. Revenue was up.",
    )
    investment_role, _ = sec._classify_document_role(
        "6-K Document",
        "SK hynix new facility investment approved. The investment equals 5.8% of "
        "equity. Reference is made to financial statements in the annual report.",
    )

    assert kla_role == "earnings_release"
    assert investment_role == "other"


def test_6k_regulatory_form_passes_official_admission_gate():
    from ats.data.admission import CandidateDocument

    candidate = CandidateDocument(
        expected_entity="ASML", claimed_entity="ASML",
        target_period="Q2 FY2026", claimed_period="Q2 FY2026",
        expected_semantic="regulatory_filing", claimed_semantic="regulatory_filing",
        text="ASML statutory interim report condensed consolidated financial statements " * 100,
        source="sec", source_url="https://www.sec.gov/interim.htm",
        title="SEC 6-K filing (2026-07-15)", completeness="full",
        metadata={"form_type": "6-K"},
    )

    assert documents.official_document_issues(candidate) == []


def test_foreign_6k_primary_body_can_be_release_when_no_ex99(monkeypatch):
    index = """
    <table><tr><td>1</td><td><a href="skhy-6k.htm">Report of foreign issuer</a></td>
      <td>skhy-6k.htm</td><td>6-K</td><td>28000</td></tr></table>
    """
    body = ("SK hynix reports second quarter 2026 financial results. Revenue and net "
            "income increased, earnings per share improved and guidance was updated. " * 100)

    def request(url, *, stage, attempts=3):
        if stage == "filing_index":
            return index, ()
        if stage == "exhibit_document" and url.endswith("skhy-6k.htm"):
            return f"<html><body>{body}</body></html>", ()
        raise AssertionError(f"unexpected request: {stage} {url}")

    monkeypatch.setattr(sec, "_request_text", request)

    result = sec.exhibit_result(
        "1261436", "0001193125-26-000001", form_type="6-K")

    assert result.status == "succeeded"
    assert result.role == "earnings_release"
    assert result.declared_type == "6-K"
    assert result.url.endswith("skhy-6k.htm")


def test_filing_regime_is_determined_from_issuer_history():
    assert sec._filing_regime_from_forms(["8-K", "10-Q", "10-K"]) == "domestic"
    assert sec._filing_regime_from_forms(["6-K", "20-F"]) == "foreign_20f"
    assert sec._filing_regime_from_forms(["6-K", "40-F"]) == "foreign_40f"
    assert sec._filing_regime_from_forms(["6-K"]) == "foreign"


def test_direct_6k_preliminary_results_are_a_regulatory_financial_report():
    body = (
        "Preliminary Results of Operations. Basis: Consolidated. Current Period, "
        "Second quarter of 2026. Revenue. Operating Profit. Profit for the Period. "
        "Quarterly Results. Prepared under K-IFRS."
    )

    assert sec._direct_6k_financial_report(body)
    assert not sec._direct_6k_financial_report(
        "New facility investment approved. Investment amount and total equity.")


def test_foreign_regulatory_document_is_separate_from_release_exhibit(monkeypatch):
    index = """
    <table>
      <tr><td>1</td><td><a href="release.htm">Press Release</a></td>
        <td>release.htm</td><td>EX-99.1</td><td>12000</td></tr>
      <tr><td>2</td><td><a href="interim.htm">Statutory interim report</a></td>
        <td>interim.htm</td><td>EX-99.4</td><td>250000</td></tr>
    </table>
    """
    release = "ASML reports second quarter 2026 financial results and guidance. " * 100
    interim = ("ASML statutory interim report, consolidated financial statements, "
               "balance sheets, statements of operations and cash flows. " * 200)

    def request(url, *, stage, attempts=3):
        if stage == "filing_index":
            return index, ()
        if url.endswith("release.htm"):
            return f"<html><body>{release}</body></html>", ()
        if url.endswith("interim.htm"):
            return f"<html><body>{interim}</body></html>", ()
        raise AssertionError(f"unexpected request: {stage} {url}")

    monkeypatch.setattr(sec, "_request_text", request)

    result = sec.foreign_regulatory_result("937966", "0000937966-26-000001")

    assert result.status == "succeeded"
    assert result.role == "regulatory_filing"
    assert result.url.endswith("interim.htm")
    assert result.description == "Statutory interim report"


def test_foreign_interim_route_uses_6k_after_earnings_event(monkeypatch):
    identity = defeatbeta.Filing(
        symbol="TSM", cik="1046179", accession="release-6k", form_type="6-K",
        filing_date="2026-07-16", url="")
    regulatory = defeatbeta.Filing(
        symbol="TSM", cik="1046179", accession="financial-6k", form_type="6-K",
        filing_date="2026-08-14", url="", report_date="2026-06-30")
    queries = []

    def filings(*_args, **kwargs):
        queries.append(kwargs)
        if kwargs.get("forms") == ("10-Q",):
            return defeatbeta.FilingResults(status="missing")
        return defeatbeta.FilingResults([identity])

    monkeypatch.setattr(defeatbeta, "filings", filings)
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("1046179", "succeeded", ()))
    monkeypatch.setattr(
        sec, "issuer_filing_regime", lambda cik: ("foreign_20f", "succeeded", ()))

    def official(cik, *, symbol, forms, near, window_days):
        assert forms == ("6-K",) and window_days >= 45
        return [regulatory], "succeeded", ()

    monkeypatch.setattr(sec, "_submission_filing_metadata", official)
    monkeypatch.setattr(
        sec, "foreign_regulatory_result",
        lambda *_a, **_k: sec.SecFetchResult(
            text="TSM consolidated interim financial statements balance sheets cash flows " * 100,
            url="https://www.sec.gov/financial-6k/interim.htm", status="succeeded",
            stage="filing_index", role="regulatory_filing"),
    )

    result = sec.periodic_filing_result(
        "TSM", near="2026-07-16", period="Q2 FY2026")

    assert result.status == "succeeded"
    assert result.record["accession"] == "financial-6k"
    assert result.record["form_type"] == "6-K"
    assert result.record["filing_regime"] == "foreign_20f"


def test_foreign_annual_route_uses_40f_for_canadian_regime(monkeypatch):
    identity = defeatbeta.Filing(
        symbol="COHR", cik="21510", accession="release-6k", form_type="6-K",
        filing_date="2026-08-01", url="")
    annual = defeatbeta.Filing(
        symbol="COHR", cik="21510", accession="annual-40f", form_type="40-F",
        filing_date="2026-09-15", url="https://www.sec.gov/annual.htm",
        report_date="2026-06-30")

    def filings(*_args, **kwargs):
        if kwargs.get("forms") == ("10-K",):
            return defeatbeta.FilingResults(status="missing")
        return defeatbeta.FilingResults([identity])

    monkeypatch.setattr(defeatbeta, "filings", filings)
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("21510", "succeeded", ()))
    monkeypatch.setattr(
        sec, "issuer_filing_regime", lambda cik: ("foreign_40f", "succeeded", ()))

    def official(cik, *, symbol, forms, near, window_days):
        assert forms == ("40-F",) and window_days == 150
        return [annual], "succeeded", ()

    monkeypatch.setattr(sec, "_submission_filing_metadata", official)
    monkeypatch.setattr(
        sec, "primary_filing_result",
        lambda *_a, **_k: sec.SecFetchResult(
            text="COHR annual report FORM 40-F revenue assets liabilities cash flows " * 100,
            url="https://www.sec.gov/annual.htm", status="succeeded",
            stage="primary_document", role="regulatory_filing"),
    )

    result = sec.periodic_filing_result(
        "COHR", near="2026-08-01", period="Q4 FY2026")

    assert result.status == "succeeded"
    assert result.record["form_type"] == "40-F"
    assert result.record["filing_regime"] == "foreign_40f"


def test_complete_submission_filename_retains_accession_hyphens(monkeypatch):
    requested = []

    def request(url, *, stage, attempts=3):
        requested.append((url, stage, attempts))
        return "submission", ()

    monkeypatch.setattr(sec, "_request_text", request)

    body, url, failures = sec._complete_submission(
        "https://www.sec.gov/Archives/edgar/data/2488/000000248826000123",
        "000000248826000123", stage="complete_submission",
    )

    assert body == "submission" and failures == ()
    assert url.endswith("/0000002488-26-000123.txt")
    assert requested == [(url, "complete_submission", 3)]


def test_filing_index_falls_back_to_htm_suffix(monkeypatch):
    requested = []

    def request(url, *, stage, attempts=3):
        requested.append(url)
        if url.endswith("-index.htm"):
            return "<html>filing index</html>", ()
        return "", (_failure(stage, url),)

    monkeypatch.setattr(sec, "_request_text", request)

    page, url, failures = sec._filing_index_page(
        "https://www.sec.gov/Archives/edgar/data/2488/000000248826000123",
        "0000002488-26-000123",
    )

    assert page == "<html>filing index</html>"
    assert url.endswith("0000002488-26-000123-index.htm")
    assert requested[0].endswith("-index.html")
    assert len(failures) == 1


def test_sec_failure_is_staged_unreachable_not_silent_missing(monkeypatch):
    monkeypatch.setattr(
        sec, "_request_text",
        lambda url, *, stage, attempts=3: ("", (_failure(stage, url),)),
    )

    result = sec.primary_filing_result("2488", "0000002488-26-000001", "10-Q")

    assert result.status == "unreachable"
    assert result.stage == "complete_submission"
    assert [failure.stage for failure in result.errors] == [
        "filing_index", "filing_index", "complete_submission"]


def test_primary_filing_uses_declared_form_from_index(monkeypatch):
    index = """
    <table>
      <tr><td>1</td><td><a href="amd-20260630.htm">Quarterly report</a></td>
          <td>amd-20260630.htm</td><td>10-Q</td><td>123456</td></tr>
      <tr><td>2</td><td><a href="ex991.htm">Release</a></td>
          <td>ex991.htm</td><td>EX-99.1</td><td>999999</td></tr>
    </table>
    """

    def request(url, *, stage, attempts=3):
        if stage == "filing_index":
            return index, ()
        if stage == "primary_document":
            assert url.endswith("amd-20260630.htm")
            return f"<html><body>{_filing_body()}</body></html>", ()
        raise AssertionError(f"unexpected fallback: {stage}")

    monkeypatch.setattr(sec, "_request_text", request)

    result = sec.primary_filing_result("2488", "0000002488-26-000001", "10-Q")

    assert result.status == "succeeded" and result.stage == "filing_index"
    assert result.url.endswith("amd-20260630.htm")
    assert "FORM 10-Q" in result.text


def test_periodic_filing_is_a_separate_catalog_asset_with_health(monkeypatch):
    store = get_store()
    filing_text = "amd:document " + ("xbrl context taxonomy member " * 600) + _filing_body()
    filing = defeatbeta.Filing(
        symbol="AMD", cik="2488", accession="0000002488-26-000099",
        form_type="10-Q", filing_date="2026-08-05",
        url="https://www.sec.gov/filing", report_date="2026-06-30",
    )
    monkeypatch.setattr(defeatbeta, "filings", lambda *a, **k: [filing])
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("", "missing", ()))
    monkeypatch.setattr(
        sec, "primary_filing_result",
        lambda *_a, **_k: sec.SecFetchResult(
            # Real inline-XBRL documents can put the cover/form marker after a long
            # taxonomy preamble (AMD Q2 2026 places it beyond character 20k).
            text=filing_text,
            url="https://www.sec.gov/Archives/edgar/data/2488/primary.htm",
            status="succeeded", stage="filing_index"),
    )
    monkeypatch.setattr(documents, "_from_folder", lambda *a: [])
    monkeypatch.setattr(
        documents, "sec_8k_release",
        lambda *_a, **_k: sec.SecRecordResult(status="missing", stage="exhibit"),
    )
    monkeypatch.setattr(
        documents, "_tavily_deck",
        lambda *_: (_ for _ in ()).throw(AssertionError("deck must not run")),
    )

    gathered = documents.gather(
        "AMD", period="Q2 FY2026", report_date="2026-08-04", store=store,
        include_deck=False)

    assert gathered == [("SEC 10-Q filing (2026-08-05)", filing_text)]
    rows = store.documents(entity="AMD")
    assert len(rows) == 1 and rows[0]["doc_type"] == "regulatory_filing"
    assert rows[0]["external_id"] == "0000002488-26-000099"
    assert document_assets.read_document(rows[0]["document_id"], store=store) == filing_text
    alias = store.document_aliases(rows[0]["document_id"])[0]
    assert json.loads(alias["metadata_json"]) == {
        "cik": "2488", "claimed_period": "Q2 FY2026", "form_type": "10-Q",
        "report_date": "2026-06-30", "filing_regime": "domestic",
    }
    health = {row["source_id"]: row for row in store.data_source_health()}
    assert health["sec_official:periodic_filing:AMD"]["status"] == "succeeded"
    assert health["sec_official:periodic_filing:AMD"]["accepted"] == 1
    assert health["sec_official:earnings_release:AMD"]["status"] == "missing"


def test_one_6k_body_can_be_cataloged_under_release_and_regulatory_roles(monkeypatch):
    store = get_store()
    body = ("SKHY preliminary results of operations. Current Period Second quarter "
            "of 2026. Revenue, operating profit, profit for the period, quarterly "
            "results, K-IFRS. " * 100)
    common = {
        "text": body, "filed": None,
        "source_url": "https://www.sec.gov/skhy-6k.htm",
        "accession": "0001193125-26-321989", "cik": "2120882",
        "form_type": "6-K", "claimed_period": "Q2 FY2026",
    }
    release = {**common, "label": "SEC 6-K earnings release (2026-07-29)",
               "document_role": "company_release"}
    filing = {**common, "label": "SEC 6-K filing (2026-07-29)",
              "document_role": "regulatory_filing", "filing_regime": "foreign_20f",
              "report_date": "2026-07-29"}

    monkeypatch.setattr(documents, "_from_folder", lambda *a: [])
    monkeypatch.setattr(
        documents, "sec_8k_release",
        lambda *_a, **_k: sec.SecRecordResult(
            record=release, status="succeeded", stage="filing_index"),
    )
    monkeypatch.setattr(
        documents, "sec_periodic_filing",
        lambda *_a, **_k: sec.SecRecordResult(
            record=filing, status="succeeded", stage="filing_index"),
    )

    gathered = documents.gather(
        "SKHY", period="Q2 FY2026", report_date="2026-07-29", store=store,
        include_deck=False)

    assert len(gathered) == 2
    rows = store.documents(entity="SKHY")
    assert {row["doc_type"] for row in rows} == {
        "company_release", "regulatory_filing"}
    health = {row["source_id"]: row for row in store.data_source_health()}
    assert health["sec_official:earnings_release:SKHY"]["status"] == "succeeded"
    assert health["sec_official:periodic_filing:SKHY"]["status"] == "succeeded"


def test_periodic_filing_unreachable_is_recorded_without_asset(monkeypatch):
    store = get_store()
    monkeypatch.setattr(documents, "_from_folder", lambda *a: [])
    monkeypatch.setattr(
        documents, "sec_8k_release",
        lambda *_a, **_k: sec.SecRecordResult(status="missing", stage="exhibit"),
    )
    failure = _failure("complete_submission")
    monkeypatch.setattr(
        documents, "sec_periodic_filing",
        lambda *_a, **_k: sec.SecRecordResult(
            status="unreachable", stage="complete_submission", errors=(failure,),
            discovered=1),
    )

    assert documents.gather(
        "AMD", period="Q2 FY2026", report_date="2026-08-04", store=store,
        include_deck=False) == []
    assert store.documents(entity="AMD") == []
    health = {row["source_id"]: row for row in store.data_source_health()}
    filing_health = health["sec_official:periodic_filing:AMD"]
    assert filing_health["status"] == "unreachable"
    assert json.loads(filing_health["reason_codes"]) == {
        "complete_submission:ConnectError": 1}


def test_filing_metadata_mirror_outage_is_not_reported_as_missing(monkeypatch):
    monkeypatch.setattr(
        defeatbeta, "filings",
        lambda *a, **k: defeatbeta.FilingResults(
            status="unreachable", error="parquet transport failed",
            source_uri="https://huggingface.test/stock_sec_filing.parquet"),
    )
    ticker_failure = _failure("company_tickers")
    monkeypatch.setattr(
        sec, "_ticker_cik", lambda symbol: ("", "unreachable", (ticker_failure,)))

    release = sec.earnings_release_result(
        "AMD", near="2026-08-04", period="Q2 FY2026")
    filing = sec.periodic_filing_result(
        "AMD", near="2026-08-04", period="Q2 FY2026")

    assert release.status == filing.status == "unreachable"
    assert release.stage in {"filing_metadata", "company_tickers"}
    assert filing.stage in {"filing_metadata", "company_submissions", "company_tickers"}
    assert {error.error_type for error in release.errors} == {
        "ConnectError", "MetadataSourceError"}


def test_sec_official_discovery_survives_filing_mirror_outage(monkeypatch):
    outage = defeatbeta.FilingResults(
        status="unreachable", error="mirror unavailable", source_uri="https://mirror.test")
    release_filing = defeatbeta.Filing(
        symbol="AMZN", cik="1018724", accession="official-release", form_type="8-K",
        filing_date="2026-07-30", url="", items="2.02,9.01",
        primary_document="amzn.htm")
    periodic_filing = defeatbeta.Filing(
        symbol="AMZN", cik="1018724", accession="official-10q", form_type="10-Q",
        filing_date="2026-07-31", url="https://www.sec.gov/amzn10q.htm",
        report_date="2026-06-30", primary_document="amzn10q.htm")
    release_text = ("AMZN reports second quarter 2026 financial results. Revenue, net "
                    "income, earnings per share and guidance. " * 100)

    monkeypatch.setattr(defeatbeta, "filings", lambda *a, **k: outage)
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("1018724", "succeeded", ()))
    monkeypatch.setattr(
        sec, "issuer_filing_regime", lambda cik: ("domestic", "succeeded", ()))

    def official(cik, *, symbol, forms, near, window_days):
        return ([release_filing], "succeeded", ()) if forms == defeatbeta.EARNINGS_FORMS \
            else ([periodic_filing], "succeeded", ())

    monkeypatch.setattr(sec, "_submission_filing_metadata", official)
    monkeypatch.setattr(
        sec, "exhibit_text", lambda *_a, **_k: (release_text, "https://www.sec.gov/release"))
    monkeypatch.setattr(
        sec, "primary_filing_result",
        lambda *_a, **_k: sec.SecFetchResult(
            text="AMZN quarterly report FORM 10-Q revenue assets cash flows " * 100,
            url="https://www.sec.gov/amzn10q.htm", status="succeeded",
            stage="primary_document"),
    )

    release = sec.earnings_release_result(
        "AMZN", near="2026-07-30", period="Q2 FY2026")
    filing = sec.periodic_filing_result(
        "AMZN", near="2026-07-30", period="Q2 FY2026")

    assert release.status == filing.status == "succeeded"
    assert release.record["accession"] == "official-release"
    assert filing.record["accession"] == "official-10q"


def test_q4_event_queries_10k_with_annual_filing_window(monkeypatch):
    queries = []

    def filings(*_args, **kwargs):
        queries.append(kwargs)
        return defeatbeta.FilingResults(status="missing")

    monkeypatch.setattr(defeatbeta, "filings", filings)
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("", "missing", ()))

    result = sec.periodic_filing_result(
        "MSFT", near="2026-07-29", period="Q4 FY2026")

    assert result.status == "missing"
    assert queries[0]["forms"] == ("10-K",)
    assert queries[0]["window_days"] == 45


def test_sec_submissions_fills_lagging_periodic_metadata(monkeypatch):
    release = defeatbeta.Filing(
        symbol="AMD", cik="0000002488", accession="0000002488-26-000121",
        form_type="8-K", filing_date="2026-08-04", url="", report_date="2026-08-04")

    def filings(*_args, **kwargs):
        if kwargs.get("forms") == ("10-Q",):
            return defeatbeta.FilingResults(status="missing")
        return defeatbeta.FilingResults([release])

    submissions = json.dumps({"filings": {"recent": {
        "accessionNumber": ["0000002488-26-000123"],
        "filingDate": ["2026-08-05"], "reportDate": ["2026-06-27"],
        "form": ["10-Q"], "primaryDocument": ["amd-20260627.htm"],
    }}})
    monkeypatch.setattr(defeatbeta, "filings", filings)
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("2488", "succeeded", ()))
    monkeypatch.setattr(
        sec, "_request_text",
        lambda url, *, stage, attempts=3: (submissions, ())
        if stage == "company_submissions" else ("", ()),
    )
    monkeypatch.setattr(
        sec, "primary_filing_result",
        lambda *_a, **_k: sec.SecFetchResult(
            text=_filing_body(),
            url="https://www.sec.gov/Archives/edgar/data/2488/primary.htm",
            status="succeeded", stage="filing_index"),
    )

    result = sec.periodic_filing_result(
        "AMD", near="2026-08-04", period="Q2 FY2026")

    assert result.status == "succeeded"
    assert result.record["accession"] == "0000002488-26-000123"
    assert result.record["form_type"] == "10-Q"
    assert result.record["report_date"] == "2026-06-27"
    assert result.record["source_url"].endswith("primary.htm")


def test_release_discovery_uses_official_items_to_select_earnings_8k(monkeypatch):
    mirror = defeatbeta.Filing(
        symbol="AMZN", cik="1018724", accession="mirror-row",
        form_type="8-K", filing_date="2026-07-30", url="")
    submissions = json.dumps({"filings": {"recent": {
        "accessionNumber": ["0001018724-26-000023", "0001018724-26-000024"],
        "filingDate": ["2026-07-29", "2026-07-30"],
        "reportDate": ["2026-07-29", "2026-07-30"],
        "form": ["8-K", "8-K"],
        "items": ["5.02,9.01", "2.02,9.01"],
        "primaryDocument": ["amzn-20260729.htm", "amzn-20260730.htm"],
    }}})
    fetched_accessions = []
    release = ("AMZN Amazon.com reports second quarter 2026 financial results. Revenue, net "
               "income, earnings per share and guidance. " * 100)

    monkeypatch.setattr(
        defeatbeta, "filings",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("defeatbeta must not run when SEC metadata succeeds")),
    )
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("1018724", "succeeded", ()))
    monkeypatch.setattr(
        sec, "_request_text",
        lambda url, *, stage, attempts=3: (submissions, ())
        if stage == "company_submissions" else ("", ()),
    )

    def exhibit(cik, accession):
        fetched_accessions.append(accession)
        return release, f"https://www.sec.gov/{accession}/release.htm"

    monkeypatch.setattr(sec, "exhibit_text", exhibit)

    result = sec.earnings_release_result(
        "AMZN", near="2026-07-30", period="Q2 FY2026")

    assert result.status == "succeeded"
    assert result.record["accession"] == "0001018724-26-000024"
    assert fetched_accessions == ["0001018724-26-000024"]
