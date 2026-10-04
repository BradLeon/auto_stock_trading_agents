import httpx
import pytest

from ats.data import sec as legacy
from ats.data.pipelines.unstructured import sec
from ats.data.pipelines.unstructured.transcripts import parse_transcript


def row(form="8-K"):
    return dict(symbol="AMD", cik="2488", accession_number="0000002488-26-000001",
                form_type=form, filing_url=
                "https://www.sec.gov/Archives/edgar/data/2488/000000248826000001")


class Replay:
    def __init__(self, fetch):
        self.fetch = fetch

    def for_filing(self, row):
        return self.fetch


def test_managed_reuses_ex99_and_submission_parser():
    body = "AMD financial results revenue net income earnings per share guidance. " * 100
    def fetch(url, *, stage, attempts):
        if stage == "complete_submission":
            return ("<DOCUMENT><TYPE>EX-99.1\n<FILENAME>results.htm\n"
                    "<DESCRIPTION>Earnings Release\n<TEXT><html>" + body +
                    "</html></TEXT></DOCUMENT>"), ()
        return "", (legacy.SecFetchFailure(stage, url, "ConnectError", "unavailable"),)
    result, role = sec.fetch_filing(row(), Replay(fetch))
    assert result.status == "succeeded"
    assert result.stage == "complete_submission"
    assert result.url.endswith("/results.htm")
    assert role == "company_release"
    assert legacy._transport.get() is None


def test_managed_primary_uses_declared_type_not_largest_file():
    index = """<table><tr><td>1</td><td>Report</td>
      <td><a href="report.htm">report.htm</a></td><td>10-Q</td><td>1000</td></tr>
      <tr><td>2</td><td>Presentation</td><td><a href="slides.htm">slides.htm</a></td>
      <td>EX-99.2</td><td>999999</td></tr></table>"""
    def fetch(url, *, stage, attempts):
        if stage == "filing_index":
            return index, ()
        assert url.endswith("/report.htm")
        return "<html>AMD financial statements revenue cash flows. " * 100, ()
    result, role = sec.fetch_filing(row("10-Q"), Replay(fetch))
    assert result.status == "succeeded" and role == "regulatory_filing"
    assert result.url.endswith("/report.htm")


def test_managed_6k_cover_is_not_release():
    def fetch(url, **kwargs):
        return ("<DOCUMENT><TYPE>6-K\n<FILENAME>cover.htm\n<TEXT>" +
                "UNITED STATES SECURITIES AND EXCHANGE COMMISSION cover page. " * 100 +
                "</TEXT></DOCUMENT>"), ()
    result, _ = sec.fetch_filing(row("6-K"), Replay(fetch))
    assert result.status != "succeeded"


def test_transport_context_restored_on_exception():
    def broken(*args, **kwargs):
        raise RuntimeError("unexpected")
    with pytest.raises(RuntimeError):
        sec.fetch_filing(row(), Replay(broken))
    assert legacy._transport.get() is None


def test_transport_enforces_budget_and_official_accession(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "research@example.com")
    t = sec.SECTransport({"max_requests_per_run": 0})
    request = t.for_filing(row())
    body, failures = request(row()["filing_url"] + "/report.htm", stage="body")
    assert not body and failures[-1].error_type == "RequestBudgetExceeded"
    body, failures = request("https://evil.test/report.htm", stage="body")
    assert not body and failures[-1].error_type == "InvalidOfficialURL"


@pytest.mark.parametrize("key", ["paragraph_number", "paragraph_order", "ordinal"])
def test_transcript_ordinal_aliases_and_gaps(key):
    r = dict(symbol="AMD", fiscal_year=2026, fiscal_quarter=2,
             transcripts=[{key: 0, "speaker": "CEO", "content": "Welcome"},
                          {key: 1, "speaker": "Analyst", "content": "Q&A"}])
    assert "## Questions and Answers" in parse_transcript(r)[2]
    r["transcripts"][1][key] = 2
    with pytest.raises(ValueError, match="order_gap"):
        parse_transcript(r)
