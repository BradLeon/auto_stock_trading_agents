"""Registration filings (S-1, S-1/A, 424B4): local file entry, one-off SEC backfill, version chain."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from ats.data import document_assets
from ats.data.catalog.loader import DataCatalog
from ats.data.persistent_queue import PersistentIngestionQueue
from ats.data.pipelines.unstructured import registration
from ats.data.products.unstructured import admitted_documents, earnings_document_package
from ats.data.sec import SecFetchFailure
from ats.data.sources import sec_submissions
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
CIK = "9999001"
FILLER = "<p>Our business depends on compute commitments and customer retention.</p>" * 600


def _s1(registrant="Anthropic, PBC", *, filed="October 26, 2026", amendment="", body=FILLER, closed=True,
        wrapper="S-1"):
    stamp = f"<p>As filed with the Securities and Exchange Commission on {filed}.</p>" if filed else ""
    html = (f"<html><body>{stamp}<p>Registration No.&#160;333-</p>"
            f"<div>{amendment}</div><div><span>FORM S-1</span></div>"
            "<div>REGISTRATION STATEMENT <span>UNDER THE SE</span><span>CURITIES</span> ACT OF 1933</div>"
            f"<h1>{registrant}</h1><div>(Exact name of registrant as specified in its charter)</div>{body}")
    html += "</body></html>" if closed else ""
    if not wrapper:
        return html
    tail = "\n</TEXT>\n</DOCUMENT>\n" if closed else ""
    return f"<DOCUMENT>\n<TYPE>{wrapper}\n<SEQUENCE>1\n<TEXT>\n{html}{tail}"


def _424b4(registrant="Anthropic, PBC"):
    return ("<html><body><p>Filed Pursuant to Rule 424(b)(4)</p><p>Registration No. 333-300000</p>"
            f"<p>PROSPECTUS</p><h1>{registrant}</h1><p>Class A Common Stock</p>{FILLER}</body></html>")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_DOCS_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test research@example.com")
    monkeypatch.setattr(registration, "_TICKER_CACHE", tmp_path / "tickers.json")
    repository = PlatformUnstructuredRepository(tmp_path / "registration.sqlite", writable=True)
    yield repository
    repository.close()


def _file(tmp_path, text, name="s1.html"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _versions(store):
    return store.conn.execute("SELECT COUNT(*) FROM data_document_versions").fetchone()[0]


def test_registration_sources_are_registered_and_not_scheduled():
    catalog = DataCatalog.load()
    assert catalog.validate().valid
    sources = {source.id: source for source in catalog.target_unstructured_sources()}
    assert set(registration.SOURCE_IDS) <= set(sources)
    from ats.data.refresh import load_plan

    assert not [job for job in load_plan().jobs if set(job.sources) & set(registration.SOURCE_IDS)]
    for action in ("registration-ingest", "registration-backfill"):
        PersistentIngestionQueue._validate_command(["ats", "data", action, "--entity", "ANT"], trigger_kind="manual")


def test_text_joins_inline_spans_and_decodes_entities():
    text = registration.filing_text(
        "<div><span>SE</span><span>CURITIES</span>&#160;of $10<span>.8</span>&#160;billion &amp; Co&#8217;s</div>"
        "<table><tr><td>2026</td><td>2027</td></tr></table>")
    assert text == "SECURITIES of $10.8 billion & Co’s 2026 2027"


def test_local_file_is_admitted_with_cover_registrant_date_and_provenance(tmp_path, store):
    path = _file(tmp_path, _s1())

    result = registration.ingest_file("ANT", "S-1", path, store=store, now=NOW)

    assert result["status"] == "succeeded", result
    assert result["registrant"] == "Anthropic, PBC" and result["filing_date"] == "2026-10-26"
    assert result["provided_by"] == "本地提供" and len(result["raw_sha256"]) == 64
    [document] = admitted_documents(entities=["ANT"], repository=store)
    assert document.document_id == result["document_id"]
    assert document.document_type == "registration_statement"
    assert document.source == "local_registration_file" and document.published_at == "2026-10-26"
    assert "SECURITIES ACT OF 1933" in document.text and "&#160;" not in document.text
    [filing] = registration.registration_filings("ANT", repository=store)
    assert filing["form_type"] == "S-1" and filing["provided_by"] == "本地提供"
    assert filing["raw_sha256"] == result["raw_sha256"]


def test_another_registrant_is_quarantined_as_identity_mismatch(tmp_path, store):
    # The body talking about Anthropic does not make it Anthropic's filing.
    body = "<p>We compete with Anthropic, PBC and its Claude models.</p>" + FILLER
    path = _file(tmp_path, _s1("Ant Group Co., Ltd.", body=body))

    result = registration.ingest_file("ANT", "S-1", path, store=store, now=NOW)

    assert result["status"] == "quarantined" and result["document_id"] == ""
    assert result["reason_codes"] == ["identity_mismatch"]
    assert admitted_documents(entities=["ANT"], repository=store) == []
    assert registration.latest_registration_filing("ANT", repository=store) is None
    row = store.conn.execute("SELECT status,reason_codes FROM data_document_candidates").fetchone()
    assert row[0] == "quarantined" and "identity_mismatch" in row[1]


def test_resubmitting_the_same_file_returns_the_held_document(tmp_path, store):
    path = _file(tmp_path, _s1())
    first = registration.ingest_file("ANT", "S-1", path, store=store, now=NOW)
    versions = _versions(store)

    again = registration.ingest_file("ANT", "S-1", path, store=store, now=NOW.replace(day=27))

    assert again["status"] == "no_change" and again["document_id"] == first["document_id"]
    assert again["reason_codes"] == ["same_file_already_held"]
    assert _versions(store) == versions and len(registration.registration_filings("ANT", repository=store)) == 1


def test_another_copy_of_a_held_filing_is_not_added(tmp_path, store):
    first = registration.ingest_file("ANT", "S-1", _file(tmp_path, _s1()), store=store, now=NOW)
    resaved = _file(tmp_path, _s1(body=FILLER + "<p>saved again</p>"), "resaved.html")

    again = registration.ingest_file("ANT", "S-1", resaved, store=store, now=NOW)

    assert again["status"] == "no_change" and again["document_id"] == first["document_id"]
    assert again["reason_codes"] == ["same_filing_already_held"]


@pytest.mark.parametrize("text, form, extra, reason", [
    (_s1(), "S-1/A", {}, "form_type_mismatch"),
    (_s1(wrapper=""), "424B4", {"filing_date": "2026-10-26"}, "form_type_mismatch"),
    (_s1(closed=False), "S-1", {}, "completeness_truncated"),
    (_s1(body="<p>cover only</p>"), "S-1", {}, "body_too_short"),
    (_s1(filed=""), "S-1", {}, "filing_date_unresolved"),
    (_s1(), "S-1", {"filing_date": "2026-10-27"}, "filing_date_mismatch"),
])
def test_unusable_files_are_quarantined(tmp_path, store, text, form, extra, reason):
    result = registration.ingest_file("ANT", form, _file(tmp_path, text), store=store, now=NOW, **extra)

    assert result["status"] == "quarantined", result
    assert reason in result["reason_codes"]
    assert admitted_documents(entities=["ANT"], repository=store) == []


def test_amendment_cover_is_an_amendment_only(tmp_path, store):
    text = _s1(amendment="Amendment No. 1 to", wrapper="S-1/A", filed="November 10, 2026")

    result = registration.ingest_file("ANT", "S-1/A", _file(tmp_path, text), store=store, now=NOW)

    assert result["status"] == "succeeded", result


def test_undeclared_issuer_is_refused(tmp_path, store):
    with pytest.raises(ValueError, match="registration_issuer_not_declared:GOOG"):
        registration.ingest_file("GOOG", "S-1", _file(tmp_path, _s1()), store=store, now=NOW)


def test_production_write_requires_the_queue_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(tmp_path / "queue.sqlite"))
    with pytest.raises(PermissionError):
        registration.ingest_file("ANT", "S-1", _file(tmp_path, _s1()), now=NOW)
    with pytest.raises(PermissionError):
        registration.backfill("ANT", now=NOW, http=lambda *_a, **_k: pytest.fail("provider called without lease"))


def _listing(filings, name="Anthropic, PBC"):
    columns = ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime", "primaryDocument")
    return {"name": name, "filings": {"recent": {
        column: [row[i] for row in filings] for i, column in enumerate(columns)}}}


def _row(number, form, filed):
    return (f"0000999900-26-{number:06d}", form, filed, "", f"{filed}T20:00:00.000Z", f"doc{number}.htm")


def test_daily_discovery_never_lists_registration_forms():
    payload = _listing([_row(1, "S-1", "2026-11-25"), _row(2, "424B4", "2026-11-25"),
                        _row(3, "S-1/A", "2026-11-25"), _row(4, "8-K", "2026-11-25")])
    for symbol in ("GOOG", "ANT"):
        rows = sec_submissions.recent_filings(symbol, CIK, payload, since=date(2026, 11, 1))
        assert [row["form_type"] for row in rows] == ["8-K"]
    assert not set(registration.FORMS) & set(sec_submissions.FORMS)


class _Sec:
    """The submissions listing and the primary documents; records what was asked."""

    def __init__(self, filings, bodies, name="Anthropic, PBC"):
        self.listing, self.bodies, self.fetched = _listing(filings, name), bodies, []

    def http(self, url, *, timeout):
        if url == sec_submissions.COMPANY_TICKERS:
            return 200, json.dumps({"0": {"ticker": "ANT", "cik_str": int(CIK)}}).encode()
        assert url.endswith(f"CIK{int(CIK):010d}.json")
        return 200, json.dumps(self.listing).encode()

    def for_filing(self, row):
        def request(url, *, stage, attempts=3):
            self.fetched.append(url.rsplit("/", 1)[-1])
            body = self.bodies.get(url.rsplit("/", 1)[-1], "")
            return body, () if body else (SecFetchFailure(stage, url, "HTTPStatusError", "attempt 1 failed"),)

        return request

    def backfill(self, store, now=NOW):
        return registration.backfill("ANT", store=store, now=now, http=self.http, wait=lambda: None, transport=self)


FILINGS = [
    _row(4, "424B4", "2026-12-04"), _row(3, "S-1/A", "2026-11-20"), _row(2, "S-1/A", "2026-11-09"),
    _row(1, "S-1", "2026-10-26"), _row(5, "8-K", "2026-12-10"),
]
BODIES = {
    "doc2.htm": _s1(amendment="Amendment No. 1 to", filed="November 9, 2026", wrapper="",
                    body="<p>This Amendment is being filed solely for the purpose of filing Exhibit 10.1.</p>"),
    "doc3.htm": _s1(amendment="Amendment No. 2 to", filed="November 19, 2026", wrapper=""),
    "doc4.htm": _424b4(),
}


def test_backfill_builds_the_version_chain_beside_the_local_first_version(tmp_path, store):
    local = registration.ingest_file("ANT", "S-1", _file(tmp_path, _s1()), store=store, now=NOW)
    sec = _Sec(FILINGS, BODIES)

    result = sec.backfill(store)

    assert result["status"] == "succeeded", result
    states = {(item["form_type"], item["filing_date"]): item["status"] for item in result["filings"]}
    assert states == {("S-1", "2026-10-26"): "no_change", ("S-1/A", "2026-11-09"): "not_applicable",
                      ("S-1/A", "2026-11-20"): "succeeded", ("424B4", "2026-12-04"): "succeeded"}
    assert sec.fetched == ["doc2.htm", "doc3.htm", "doc4.htm"]      # the held S-1 and the 8-K are not fetched
    chain = registration.registration_filings("ANT", repository=store)
    assert [item["form_type"] for item in chain] == ["S-1", "S-1/A", "424B4"]
    assert chain[0]["supersedes"] == "" and chain[2]["supersedes"] == chain[1]["document_id"]
    latest = registration.latest_registration_filing("ANT", repository=store)
    assert latest["form_type"] == "424B4" and latest["accession_number"] == "0000999900-26-000004"
    # SEC's listing date is the filing date, even when the cover says the evening before.
    assert chain[1]["filing_date"] == "2026-11-20"
    assert registration.latest_registration_filing(
        "ANT", as_of=date(2026, 11, 30), repository=store)["form_type"] == "S-1/A"
    assert registration.latest_registration_filing("ANT", as_of=date(2026, 10, 25), repository=store) is None
    # The first version stays readable, and none of this is earnings material.
    assert "FORM S-1" in document_assets.read_document(local["document_id"], store=store)
    assert earnings_document_package(store, entity="ANT", period="FY26Q3", event_date="2026-12-04").documents == ()
    assert {d.document_type for d in admitted_documents(entities=["ANT"], repository=store)} == {
        "registration_statement"}


ALL_BODIES = {**BODIES, "doc1.htm": _s1(wrapper="")}


def test_backfill_rerun_fetches_nothing(store):
    first = _Sec(FILINGS, ALL_BODIES).backfill(store)
    assert first["status"] == "succeeded" and first["counts"]["succeeded"] == 3
    versions = _versions(store)
    again = _Sec(FILINGS, ALL_BODIES)

    result = again.backfill(store, now=NOW.replace(day=27))

    assert result["status"] == "no_change", result
    assert again.fetched == [] and _versions(store) == versions


def test_backfill_reports_a_failed_download_and_retries_only_that_one(store):
    result = _Sec(FILINGS, {k: v for k, v in ALL_BODIES.items() if k != "doc4.htm"}).backfill(store)
    assert result["status"] == "partial"
    assert [item["form_type"] for item in result["filings"] if item["status"] == "failed"] == ["424B4"]
    retry = _Sec(FILINGS, ALL_BODIES)

    assert retry.backfill(store, now=NOW.replace(day=27))["status"] == "succeeded"
    assert retry.fetched == ["doc4.htm"]


def test_backfill_quarantines_a_foreign_cover(store):
    sec = _Sec([_row(4, "424B4", "2026-12-04")], {"doc4.htm": _424b4("Ant Group Co., Ltd.")})

    result = sec.backfill(store)

    assert result["status"] == "quarantined"
    assert result["filings"][0]["reason_codes"] == ["identity_mismatch"]
    assert registration.latest_registration_filing("ANT", repository=store) is None


def test_backfill_stops_when_the_ticker_resolves_to_somebody_else(store):
    sec = _Sec(FILINGS, BODIES, name="Antero Something Corp")

    result = sec.backfill(store)

    assert result["status"] == "failed" and result["reason"].startswith("registration_cik_name_mismatch")
    assert sec.fetched == []
