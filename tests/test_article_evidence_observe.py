"""Article witnesses extracted from already-ingested articles (article-witness-observations 2.1–2.3, 2026-11-13).

The extraction stage reads admitted article versions only, reads each version once, skips articles outside the
publisher's witnessed claims without a model call, and stops at a per-run request cap.
"""

from __future__ import annotations

import pytest

from ats.agents.evidence import observer
from ats.chain import articles
from ats.data import document_assets


class FakeStore:
    def __init__(self, docs):
        self.docs = {d["document_id"]: dict(d) for d in docs}
        self.versions = {d["document_id"]: "v1" for d in docs}
        self.bodies = {d["document_id"]: d.pop("body", "") for d in self.docs.values()}
        self.runs: dict[tuple[str, str, str], str] = {}   # (version_id, consumer, processor) -> status
        self.notes: dict[tuple[str, str, str], str] = {}

    def documents(self, entity=None, *, ok_only=True, published_since=None, limit=200, **_):
        return [d for d in self.docs.values() if d["entity"] == entity
                and (not published_since or str(d.get("published_at", "")) >= published_since)]

    def latest_document_version(self, document_id):
        return {"version_id": f"{document_id}@{self.versions[document_id]}"}

    def document_processing(self, document_id, *, consumer, processor_version, limit=20):
        vid = self.latest_document_version(document_id)["version_id"]
        status = self.runs.get((vid, consumer, processor_version))
        note = self.notes.get((vid, consumer, processor_version), "")
        return [{"version_id": vid, "status": status, "note": note}] if status else []

    def begin_document_processing(self, document_id, consumer, processor_version="v1", *, retry_failed=False, **_):
        vid = self.latest_document_version(document_id)["version_id"]
        if self.runs.get((vid, consumer, processor_version)) == "succeeded":
            return None
        self.runs[(vid, consumer, processor_version)] = "running"
        return vid

    def finish_document_processing(self, version_id, consumer, processor_version="v1", *, ok, outputs=0, note="", **_):
        self.runs[(version_id, consumer, processor_version)] = "succeeded" if ok else "failed"
        self.notes[(version_id, consumer, processor_version)] = note


def _doc(doc_id, entity, title, body, published="2026-11-12"):
    return {"document_id": doc_id, "entity": entity, "doc_type": "research_article", "title": title,
            "body": body, "published_at": published, "source_url": f"https://example.test/{doc_id}"}


@pytest.fixture
def calls(monkeypatch):
    seen: list[str] = []

    def fake_observe(symbol, document_id, text, **_):
        seen.append(document_id)
        return {"new": 2, "saved": 2, "failure": ""}

    monkeypatch.setattr(observer, "observe_document", fake_observe)
    monkeypatch.setattr(articles, "discover", lambda *a, **k: pytest.fail("extraction must not discover"))
    return seen


@pytest.fixture
def store(monkeypatch):
    def build(docs):
        s = FakeStore(docs)
        monkeypatch.setattr(document_assets, "read_document", lambda doc_id, store=None: s.bodies[doc_id])
        return s
    return build


def test_scope_follows_witness_declarations_across_layers():
    sources = {s.id: s for s in articles.load_article_sources()}
    trendforce = articles.witness_scope(sources["trendforce_news"])
    dowjones = articles.witness_scope(sources["ibkr_news"])
    assert len(trendforce["claims"]) > len(dowjones["claims"]) > 0
    # TrendForce witnesses memory claims (L6), so a memory company is in its scope; publishers never are.
    assert "MU" in trendforce["entities"]
    assert not {"TRENDFORCE", "DOWJONES", "SEMIANALYSIS"} & trendforce["entities"]


def test_reads_admitted_versions_once_and_never_discovers(store, calls):
    s = store([_doc("TRENDFORCE:a", "TRENDFORCE", "HBM supply tightens as DRAM prices climb", "x" * 900)])
    first = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    second = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    assert first["status"] == "succeeded" and first["extracted"] == 1 and first["observations"] == 2
    assert second["status"] == "no_change" and second["already_extracted"] == 1
    assert calls == ["TRENDFORCE:a"]


def test_a_revised_article_body_is_read_again(store, calls):
    s = store([_doc("TRENDFORCE:a", "TRENDFORCE", "DRAM contract prices rise", "x" * 900)])
    articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    s.versions["TRENDFORCE:a"] = "v2"
    again = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    assert again["extracted"] == 1 and calls == ["TRENDFORCE:a", "TRENDFORCE:a"]


def test_an_article_outside_the_witnessed_claims_costs_no_call_and_is_checked_once(store, calls, monkeypatch):
    s = store([_doc("DOWJONES:x", "DOWJONES", "Costco reports holiday sales", "Costco said shoppers " * 80)])
    first = articles.observe_ingested_articles(s, source_id="ibkr_news", since="2026-11-01")
    assert first["irrelevant"] == 1 and first["status"] == "no_change" and calls == []
    monkeypatch.setattr(articles, "is_relevant", lambda *a, **k: pytest.fail("re-checked an irrelevant article"))
    second = articles.observe_ingested_articles(s, source_id="ibkr_news", since="2026-11-01")
    assert second["irrelevant"] == 1 and calls == []


def test_a_member_named_in_the_headline_with_a_keyword_in_the_text_is_read(store, calls):
    body = "Lawmakers pushed for stricter export rules on advanced chips. " + "Analysts said orders " * 60
    s = store([_doc("DOWJONES:h", "DOWJONES", "Nvidia Scandal Tests the Resilience of the Global AI Boom", body)])
    result = articles.observe_ingested_articles(s, source_id="ibkr_news", since="2026-11-01")
    assert result["extracted"] == 1, result


def test_a_non_l5_trendforce_article_is_not_dropped_for_being_off_l5(store, calls):
    s = store([_doc("TRENDFORCE:m", "TRENDFORCE", "NAND flash contract prices to rise in 1Q27", "y" * 900)])
    result = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    assert result["extracted"] == 1


def test_the_request_cap_leaves_the_rest_as_a_successful_partial(store, calls):
    docs = [_doc(f"TRENDFORCE:{i}", "TRENDFORCE", f"HBM capacity update {i}", "z" * 900, f"2026-11-1{i}")
            for i in range(3)]
    s = store(docs)
    result = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01", max_requests=2)
    assert result["status"] == "partial" and result["extracted"] == 2 and result["deferred"] == 1
    assert result["reason"].startswith("partial_budget_deferred")
    assert calls == ["TRENDFORCE:2", "TRENDFORCE:1"]          # newest first


def test_the_controller_counts_the_capped_partial_as_success_with_its_reason():
    from ats.data import refresh

    assert "article-evidence-observe" in refresh._SAFE_DERIVED_ACTIONS
    assert "article-evidence-observe" in refresh._PARTIAL_OK_DERIVED


def test_the_persistent_queue_accepts_the_extraction_command():
    """2026-11-13: the first backfill was refused at enqueue because the queue allowlist did not name it."""
    from ats.data.persistent_queue import PersistentIngestionQueue

    tokens = PersistentIngestionQueue._validate_command(
        ["ats", "data", "article-evidence-observe", "--source", "ibkr_news", "--max-requests", "40"],
        trigger_kind="manual")
    assert tokens[2] == "article-evidence-observe"


def test_an_article_with_nothing_extractable_is_read_once(store, monkeypatch):
    """A market wrap the model reads and finds no claim facts in is done, not a failure to retry."""
    calls: list[str] = []

    def empty(symbol, document_id, text, **_):
        calls.append(document_id)
        return {"new": 0, "saved": 0, "failure": "市场行情快讯，没有可核对的经营数字"}

    monkeypatch.setattr(observer, "observe_document", empty)
    s = store([_doc("TRENDFORCE:w", "TRENDFORCE", "HBM market wrap", "w" * 900)])
    first = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    second = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    assert first["status"] == "succeeded" and first["nothing_extractable"] == 1 and first["failures"] == 0
    assert second["already_extracted"] == 1 and calls == ["TRENDFORCE:w"]


def test_a_failed_model_call_is_retried(store, monkeypatch):
    outcomes = iter([{"new": 0, "saved": 0, "failure": "窗口 1 调用失败：timeout"},
                     {"new": 3, "saved": 3, "failure": ""}])
    monkeypatch.setattr(observer, "observe_document", lambda *a, **k: next(outcomes))
    s = store([_doc("TRENDFORCE:t", "TRENDFORCE", "DRAM prices", "t" * 900)])
    first = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    second = articles.observe_ingested_articles(s, source_id="trendforce_news", since="2026-11-01")
    assert first["status"] == "failed" and first["failures"] == 1
    assert second["extracted"] == 1 and second["observations"] == 3
