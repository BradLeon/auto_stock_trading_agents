"""Financial releases, filings and decks enter the shared document asset layer."""

from datetime import date

from ats.data import document_assets, documents
from ats.memory import get_store


def _body(marker: str) -> str:
    return (f"{marker} revenue guidance and operating results. " * 80).strip()


def test_remote_release_and_deck_are_catalogued_then_reused_without_network(monkeypatch):
    store = get_store()
    calls = {"sec": 0, "deck": 0}

    def sec(_symbol):
        calls["sec"] += 1
        return {
            "label": "SEC 8-K earnings release (2026-08-19)",
            "text": _body("release"), "filed": date(2026, 8, 19),
            "source_url": "https://www.sec.gov/Archives/edgar/data/1/ex991.htm",
            "accession": "000000000126000001",
        }

    def deck(_symbol):
        calls["deck"] += 1
        return ("investor presentation (tavily:https://ir.example.test/q2.pdf)",
                _body("deck"))

    monkeypatch.setattr(documents, "_from_folder", lambda *a: [])
    monkeypatch.setattr(documents, "sec_8k_release", sec)
    monkeypatch.setattr(documents, "_tavily_deck", deck)

    first = documents.gather("AMD", period="2026Q2", store=store)
    assert len(first) == 2 and calls == {"sec": 1, "deck": 1}
    rows = store.documents(entity="AMD")
    assert {r["doc_type"] for r in rows} == {"release", "deck"}
    release = next(r for r in rows if r["doc_type"] == "release")
    assert release["source_url"].startswith("https://www.sec.gov/")
    assert release["external_id"] == "000000000126000001"

    monkeypatch.setattr(
        documents, "sec_8k_release",
        lambda *_: (_ for _ in ()).throw(AssertionError("release refetched")))
    monkeypatch.setattr(
        documents, "_tavily_deck",
        lambda *_: (_ for _ in ()).throw(AssertionError("deck refetched")))
    second = documents.gather("AMD", period="2026Q2", store=store)
    assert [body for _, body in second] == [body for _, body in first]


def test_curated_filing_and_announcement_are_separate_assets(monkeypatch):
    store = get_store()
    folder = [
        ("doc:AMD-2026-10-Q.pdf", _body("10-Q filing")),
        ("doc:customer-partnership-announcement.md", _body("announcement")),
    ]
    monkeypatch.setattr(documents, "_from_folder", lambda *a: folder)
    monkeypatch.setattr(documents, "sec_8k_release", lambda *_: None)
    monkeypatch.setattr(documents, "_tavily_deck", lambda *_: None)

    assert documents.gather("AMD", period="2026Q2", store=store) == folder

    rows = store.documents(entity="AMD")
    assert {r["doc_type"] for r in rows} == {"filing", "announcement"}
    for row in rows:
        assert document_assets.read_document(row["document_id"], store=store)
