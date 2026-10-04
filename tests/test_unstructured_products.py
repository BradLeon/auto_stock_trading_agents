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
