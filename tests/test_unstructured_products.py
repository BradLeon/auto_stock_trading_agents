from __future__ import annotations

from pathlib import Path

from datetime import datetime, timedelta, timezone

from ats.data.products.unstructured import earnings_document_package, platform_news_items


class _Repository:
    def __init__(self, rows, versions):
        self.rows = rows
        self.versions = versions

    def documents(self, entity=None, **_kwargs):
        return [row for row in self.rows if row["entity"] == entity]

    def latest_document_version(self, document_id):
        return self.versions.get(document_id)


def _row(document_id: str, doc_type: str) -> dict:
    return {
        "document_id": document_id,
        "entity": "NVDA",
        "doc_type": doc_type,
        "source": "sec",
        "source_url": "https://www.sec.gov/example",
        "published_at": "2026-08-26",
        "title": document_id,
    }


def test_event_package_selects_only_exact_period_and_immutable_versions(tmp_path: Path) -> None:
    release = tmp_path / "release.md"
    filing = tmp_path / "filing.md"
    transcript = tmp_path / "transcript.md"
    release.write_text("release body", encoding="utf-8")
    filing.write_text("filing body", encoding="utf-8")
    transcript.write_text("transcript body", encoding="utf-8")
    rows = [
        _row("NVDA:Q2 FY2027:company_release", "company_release"),
        _row("NVDA:Q2 FY2027:regulatory_filing", "regulatory_filing"),
        _row("NVDA:Q2 FY2027:earnings_transcript", "earnings_transcript"),
        _row("NVDA:unknown:release", "release"),
        _row("NVDA:Q1 FY2027:transcript", "transcript"),
    ]
    versions = {
        "NVDA:Q2 FY2027:company_release": {
            "version_id": "release-v1", "local_path": str(release), "source_url": "https://sec/release"},
        "NVDA:Q2 FY2027:regulatory_filing": {
            "version_id": "filing-v1", "local_path": str(filing), "source_url": "https://sec/filing"},
        "NVDA:Q2 FY2027:earnings_transcript": {
            "version_id": "transcript-v1", "local_path": str(transcript), "source_url": ""},
    }

    package = earnings_document_package(_Repository(rows, versions), entity="NVDA", period="Q2 FY2027")

    assert [item.role for item in package.documents] == [
        "earnings_release", "regulatory_filing", "earnings_transcript"]
    assert package.scoreable
    assert "release body" in package.official_text()
    assert package.transcript and package.transcript.text == "transcript body"
    assert all("unknown" not in item.document_id for item in package.documents)


def test_platform_news_product_requires_ticker_association(monkeypatch, tmp_path: Path) -> None:
    body = tmp_path / "news.md"
    body.write_text("NVIDIA demand update", encoding="utf-8")
    now = datetime.now(timezone.utc)

    class _Platform(_Repository):
        def close(self):
            pass

    platform = _Platform([{
        "document_id": "DOWJONES:DJ-N-42:article", "entity": "NVDA",
        "doc_type": "article", "source": "ibkr_news", "title": "NVIDIA demand update",
        "published_at": now.isoformat(), "fetched_at": now.isoformat(), "source_url": "ibkr-news://DJ-N/42",
    }], {"DOWJONES:DJ-N-42:article": {
        "version_id": "news-v1", "local_path": str(body), "source_url": "ibkr-news://DJ-N/42"}})
    monkeypatch.setattr("ats.data.stores.unstructured.get_platform_unstructured_repository", lambda: platform)

    items = platform_news_items(entity="NVDA", since=now - timedelta(days=1))

    assert [(item.id, item.source, item.tickers, item.summary) for item in items] == [
        ("DOWJONES:DJ-N-42:article", "platform:ibkr_news", ["NVDA"], "NVIDIA demand update")]


def _sec_body(document_id: str, filed: str, **extra) -> dict:
    return {**_row(document_id, "company_release"), "source": "sec_edgar_filing_body", "period": "",
            "published_at": filed, "title": f"ASML 6-K {document_id}", **extra}


def _version(path: Path, text: str) -> dict:
    path.write_text(text, encoding="utf-8")
    return {"version_id": f"{path.stem}-v1", "local_path": str(path), "source_url": "https://www.sec.gov/x"}


LAM_STYLE = ("Lam Research Corporation Reports Financial Results for the Quarter Ended September 27, 2026. "
             "Highlights: Revenue of $8.22 billion.")
RESULTS = "ASML reports third-quarter 2026 results: net sales of EUR 8.0 billion and net income of EUR 2.5 billion."


def test_unlabelled_sec_release_is_matched_by_filing_date_to_the_event(tmp_path: Path) -> None:
    """An 8-K/6-K body has no fiscal-period label; the event date is what links it to its earnings event."""
    rows = [_sec_body("ASML:sec-1", "2026-10-14")]
    rows[0]["entity"] = "ASML"
    repo = _Repository(rows, {"ASML:sec-1": _version(tmp_path / "a.md", RESULTS)})

    without = earnings_document_package(repo, entity="ASML", period="Q3 FY2026")
    assert without.documents == ()              # the period-only rule still cannot see it

    package = earnings_document_package(repo, entity="ASML", period="Q3 FY2026", event_date="2026-10-14")
    assert [item.role for item in package.documents] == ["earnings_release"]
    assert package.scoreable


def test_filing_date_matching_rejects_unrelated_or_misdated_bodies(tmp_path: Path) -> None:
    buyback = _sec_body("ASML:sec-buyback", "2026-10-14")
    late = _sec_body("ASML:sec-late", "2026-10-30")
    labelled_other = _sec_body("ASML:sec-q2", "2026-10-14", period="Q2 FY2026")
    other_source = _sec_body("ASML:sec-other", "2026-10-14", source="sec")
    rows = [buyback, late, labelled_other, other_source]
    for row in rows:
        row["entity"] = "ASML"
    versions = {
        "ASML:sec-buyback": _version(tmp_path / "b.md", "ASML share buyback programme: shares repurchased this week."),
        "ASML:sec-late": _version(tmp_path / "l.md", RESULTS),
        "ASML:sec-q2": _version(tmp_path / "q.md", RESULTS),
        "ASML:sec-other": _version(tmp_path / "o.md", RESULTS),
    }
    package = earnings_document_package(_Repository(rows, versions), entity="ASML", period="Q3 FY2026",
                                        event_date="2026-10-14")
    assert package.documents == ()
    # same filing one day before and three days after the event is still inside the window
    inside = _sec_body("ASML:sec-in", "2026-10-17")
    inside["entity"] = "ASML"
    package = earnings_document_package(
        _Repository([inside], {"ASML:sec-in": _version(tmp_path / "i.md", RESULTS)}),
        entity="ASML", period="Q3 FY2026", event_date="2026-10-14")
    assert [item.document_id for item in package.documents] == ["ASML:sec-in"]


def test_results_release_without_an_ordinal_quarter_is_recognised(tmp_path: Path) -> None:
    row = _sec_body("LRCX:sec-8k", "2026-10-21")
    row["entity"] = "LRCX"
    package = earnings_document_package(
        _Repository([row], {"LRCX:sec-8k": _version(tmp_path / "lam.md", LAM_STYLE)}),
        entity="LRCX", period="FY2027Q1", event_date="2026-10-21 16:00")
    assert [item.role for item in package.documents] == ["earnings_release"]
