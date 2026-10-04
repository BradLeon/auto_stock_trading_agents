"""Hermetic data-only acceptance gates for third-party unstructured sources."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from ats.data.pipelines.unstructured import source_acceptance as acceptance
from ats.schemas.chain import ArticleRef, ArticleSourceDef


NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)


def _source(source_id="trendforce_news", *, adapter="trendforce", minimum=800):
    return ArticleSourceDef(
        id=source_id, label=source_id, adapter=adapter, entity="PUBLISHER",
        stance="regulator", doc_type="article", pages=1, max_per_run=10,
        min_body_chars=minimum, match=[],
    )


def _ref(slug="article-1"):
    return ArticleRef(url=f"https://example.test/news/{slug}?utm=source", slug=slug,
                      title="A complete article", published_at=date(2026, 8, 28))


def _adapter(*, refs=None, body="full body ", status=None, provenance=None):
    refs = refs if refs is not None else [_ref()]
    status = status or {"status": "succeeded"}
    return SimpleNamespace(
        discover_with_status=lambda **_: (refs, status),
        fetch_body=lambda _url: body,
        provenance=lambda ref: provenance or {"native_id": f"native:{ref.slug}",
                                               "canonical_url": ref.url},
    )


def _wire(monkeypatch, source, adapter):
    monkeypatch.setattr(acceptance, "_load_article_sources", lambda: {source.id: source})
    monkeypatch.setattr(acceptance, "_adapter", lambda _source: adapter)


def test_trendforce_acceptance_records_provenance_and_is_separate_from_dram(monkeypatch):
    _wire(monkeypatch, _source(), _adapter(body="x" * 900))

    result = acceptance.assess_article_source("trendforce_news", now=NOW)

    assert result["platform_eligible"] is True
    assert result["classification"] == "equivalent"
    assert result["scope"]["separate_dataset"] == "industry_dram_contract_price"
    candidate = result["candidates"][0]
    assert candidate["native_id"] == "native:article-1"
    assert candidate["canonical_url"] == "https://example.test/news/article-1"
    assert candidate["content_hash"]
    assert result["side_effects"] == {
        "llm": 0, "agent": 0, "workflow": 0, "orders": 0, "trades": 0, "persistence": 0,
    }


def test_unreadable_body_is_a_partial_gap_and_cannot_be_published(monkeypatch, tmp_path):
    _wire(monkeypatch, _source(), _adapter(body=""))
    result = acceptance.assess_article_source("trendforce_news", now=NOW)

    assert result["outcome"] == "partial"
    assert result["platform_eligible"] is False
    assert result["counts"]["unreadable"] == 1
    with pytest.raises(ValueError, match="acceptance failed"):
        acceptance.publish_source(result, path=tmp_path / "release.yaml")
    assert not (tmp_path / "release.yaml").exists()


def test_transient_body_failure_is_retried_before_it_becomes_a_gap(monkeypatch):
    calls = {"count": 0}
    adapter = _adapter(body="x" * 900)

    def fetch(_url):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("temporary")
        return "x" * 900

    adapter.fetch_body = fetch
    _wire(monkeypatch, _source(), adapter)

    result = acceptance.assess_article_source("trendforce_news", now=NOW)

    assert result["platform_eligible"] is True
    assert result["candidates"][0]["body_attempts"] == 2


def test_ibkr_true_empty_success_is_distinct_from_provider_entitlement_failure(monkeypatch):
    source = _source("ibkr_news", adapter="ibkr_news", minimum=600)
    _wire(monkeypatch, source, _adapter(refs=[], status={"status": "succeeded", "providers": ["DJ-N"]}))

    empty = acceptance.assess_article_source("ibkr_news", now=NOW)

    assert empty["outcome"] == "no_change"
    assert empty["platform_eligible"] is True

    _wire(monkeypatch, source, _adapter(refs=[], status={"status": "unauthorized",
                                                          "error": "news_provider_entitlement_missing"}))
    failed = acceptance.assess_article_source("ibkr_news", now=NOW)
    assert failed["outcome"] == "unreachable"
    assert failed["platform_eligible"] is False


def test_failed_ibkr_slice_is_partial_even_when_another_slice_has_articles(monkeypatch):
    source = _source("ibkr_news", adapter="ibkr_news", minimum=600)
    _wire(monkeypatch, source, _adapter(
        body="x" * 700,
        status={"status": "succeeded", "failed_slices": [{"symbol": "NVDA", "stage": "historical_news"}]},
    ))

    result = acceptance.assess_article_source("ibkr_news", now=NOW)

    assert result["outcome"] == "partial"
    assert result["platform_eligible"] is False
    assert next(row for row in result["checks"] if row["check"] == "slice_completeness")["passed"] is False


def test_ibkr_fallback_only_activates_for_unavailability_or_failed_slices():
    policy = {"policy": {"fallback": {
        "source_id": "yfinance_live_news",
        "activate_on": ["unreachable", "provider_unavailable", "unauthorized"],
    }}}

    quiet = acceptance.fallback_plan(
        source_id="ibkr_news", outcome="no_change", discovery={"status": "succeeded"}, policy=policy)
    unavailable = acceptance.fallback_plan(
        source_id="ibkr_news", outcome="unreachable",
        discovery={"status": "provider_unavailable"}, policy=policy)
    slice_failure = acceptance.fallback_plan(
        source_id="ibkr_news", outcome="partial", discovery={"status": "succeeded",
        "failed_slices": [{"symbol": "NVDA", "stage": "historical_news"}]}, policy=policy)

    assert quiet["activate"] is False
    assert quiet["reason"] == "primary_completed_including_zero_news"
    assert unavailable == {"configured": True, "activate": True,
                           "source_id": "yfinance_live_news", "scope": "source",
                           "reason": "provider_unavailable"}
    assert slice_failure["activate"] is True
    assert slice_failure["scope"] == "failed_slices"
    assert slice_failure["entities"] == ["NVDA"]


def test_ibkr_fallback_executes_yahoo_only_for_the_declared_failure_scope(monkeypatch):
    calls = []

    def assess(source_id, **kwargs):
        calls.append((source_id, kwargs.get("adapter_params")))
        if source_id == "ibkr_news":
            return {"fallback": {"activate": True, "source_id": "yfinance_live_news",
                                 "scope": "failed_slices", "entities": ["NVDA"]}}
        return {"source_id": source_id, "platform_eligible": False}

    monkeypatch.setattr(acceptance, "assess_article_source", assess)
    result = acceptance.assess_ibkr_news_with_fallback(now=NOW)

    assert calls == [("ibkr_news", None), ("yfinance_live_news", {"symbols": ["NVDA"]})]
    assert result["fallback"]["attempted"] is True


def test_ibkr_zero_news_never_calls_yahoo(monkeypatch):
    calls = []

    def assess(source_id, **kwargs):
        calls.append(source_id)
        return {"fallback": {"activate": False, "source_id": "yfinance_live_news",
                             "scope": "none", "reason": "primary_completed_including_zero_news"}}

    monkeypatch.setattr(acceptance, "assess_article_source", assess)
    result = acceptance.assess_ibkr_news_with_fallback(now=NOW)

    assert calls == ["ibkr_news"]
    assert result["fallback"]["attempted"] is False


def test_ibkr_body_dedupe_uses_normalized_title_and_content_hash(monkeypatch):
    source = _source("ibkr_news", adapter="ibkr_news", minimum=600)
    refs = [_ref("dj-1"), _ref("br-2")]
    adapter = _adapter(refs=refs, body="x" * 700)
    adapter.provenance = lambda ref: {
        "native_id": ref.slug, "canonical_url": ref.url,
        "entity_association": "title_verified", "queried_entities": "NVDA",
        "title_verified_entities": "NVDA", "dedup_title": "same wire headline",
        "dedup_time": "2026-08-28T10:00:00",
    }
    _wire(monkeypatch, source, adapter)
    monkeypatch.setattr(acceptance, "load_policy", lambda *_args, **_kwargs: {
        "domain": "unstructured", "adapter": "ibkr_news",
        "request_budget": {"max_body_requests": 2},
        "policy": {"minimum_body_chars": 600, "lookback_days": 7,
                   "require_entity_verified": True},
    })

    result = acceptance.assess_article_source("ibkr_news", now=NOW)

    assert result["counts"]["accepted"] == 1
    assert result["counts"]["duplicate"] == 1
    assert result["candidates"][1]["duplicate_of"] == "dj-1"


def test_body_request_budget_keeps_full_headline_ledger_without_fetching_every_body(monkeypatch):
    source = _source("ibkr_news", adapter="ibkr_news", minimum=600)
    refs = [_ref(f"article-{index}") for index in range(3)]
    calls = []
    adapter = _adapter(refs=refs, body="x" * 700)
    adapter.fetch_body = lambda url: calls.append(url) or "x" * 700
    _wire(monkeypatch, source, adapter)
    monkeypatch.setattr(acceptance, "load_policy", lambda *_args, **_kwargs: {
        "domain": "unstructured", "adapter": "ibkr_news",
        "request_budget": {"max_body_requests": 1},
        "policy": {"minimum_body_chars": 600, "lookback_days": 7},
    })

    result = acceptance.assess_article_source("ibkr_news", now=NOW)

    assert len(calls) == 1
    assert result["counts"]["deferred"] == 2
    assert result["scope"]["body_requests_used"] == 1
    assert result["platform_eligible"] is True


def test_acceptance_window_comes_from_source_policy_not_only_request_budget(monkeypatch):
    source = _source("ibkr_news", adapter="ibkr_news", minimum=600)
    _wire(monkeypatch, source, _adapter(body="x" * 700))
    monkeypatch.setattr(acceptance, "load_policy", lambda *_args, **_kwargs: {
        "domain": "unstructured", "adapter": "ibkr_news",
        "request_budget": {"lookback_days": 1, "max_body_requests": 1},
        "policy": {"minimum_body_chars": 600, "lookback_days": 7},
    })

    result = acceptance.assess_article_source("ibkr_news", now=NOW)

    assert result["scope"]["lookback_days"] == 7


def test_release_overlay_is_explicit_and_reversible(monkeypatch, tmp_path):
    _wire(monkeypatch, _source(), _adapter(body="x" * 900))
    result = acceptance.assess_article_source("trendforce_news", now=NOW)
    path = tmp_path / "releases.yaml"

    published = acceptance.publish_source(result, path=path, mode="platform", actor="test")

    assert published["previous_mode"] == ""
    overlay = acceptance.load_release_overlay(path)
    assert overlay["sources"]["trendforce_news"] == "platform"
    assert overlay["history"][-1]["actor"] == "test"


def test_declared_partial_newsletter_body_can_publish_only_when_source_policy_allows_it(monkeypatch, tmp_path):
    source = _source("semianalysis", adapter="semianalysis", minimum=2000)
    _wire(monkeypatch, source, _adapter(
        body="x" * 5000,
        provenance={"native_id": "imap:<partial@example.test>",
                    "canonical_url": "https://semianalysis.com/p/partial",
                    "completeness": "partial"},
    ))

    monkeypatch.setattr(acceptance, "load_policy", lambda *_args, **_kwargs: {
        "domain": "unstructured", "adapter": "semianalysis",
        "policy": {"allow_partial_bodies": True, "minimum_body_chars": 2000},
    })
    result = acceptance.assess_article_source("semianalysis", now=NOW)

    assert result["platform_eligible"] is True
    assert result["outcome"] == "succeeded"
    assert result["classification"] == "partial"
    assert result["candidates"][0]["reason"] == "declared_partial_body"
    assert result["candidates"][0]["content_hash"]
    assert acceptance.publish_source(result, path=tmp_path / "release.yaml")["mode"] == "platform"


def test_entity_rejected_yahoo_recommendation_is_visible_but_not_fetched_or_publishable(monkeypatch):
    source = _source("yfinance_live_news", adapter="yfinance_live_news", minimum=800)
    calls = []
    adapter = _adapter(
        body="x" * 1000,
        provenance={"native_id": "yahoo-1", "canonical_url": "https://example.test/wrong",
                    "entity_association": "association_rejected", "queried_entities": "MRVL"},
    )
    adapter.fetch_body = lambda url: calls.append(url) or "x" * 1000
    _wire(monkeypatch, source, adapter)
    monkeypatch.setattr(acceptance, "load_policy", lambda *_args, **_kwargs: {
        "domain": "unstructured", "adapter": "yfinance_live_news",
        "request_budget": {"max_body_requests": 2},
        "policy": {"minimum_body_chars": 800, "lookback_days": 2,
                   "require_entity_verified": True,
                   "release_requires_human_title_url_review": True},
    })

    result = acceptance.assess_article_source("yfinance_live_news", now=NOW)

    assert calls == []
    assert result["counts"]["association_rejected"] == 1
    assert result["platform_eligible"] is False
    assert result["candidates"][0]["status"] == "association_rejected"


def test_yahoo_source_needs_explicit_title_url_review_before_release(monkeypatch, tmp_path):
    source = _source("yfinance_live_news", adapter="yfinance_live_news", minimum=800)
    _wire(monkeypatch, source, _adapter(
        body="x" * 1000,
        provenance={"native_id": "yahoo-1", "canonical_url": "https://example.test/right",
                    "entity_association": "title_verified", "queried_entities": "NVDA"},
    ))
    monkeypatch.setattr(acceptance, "load_policy", lambda *_args, **_kwargs: {
        "domain": "unstructured", "adapter": "yfinance_live_news",
        "request_budget": {"max_body_requests": 2},
        "policy": {"minimum_body_chars": 800, "lookback_days": 2,
                   "require_entity_verified": True,
                   "release_requires_human_title_url_review": True},
    })

    pending = acceptance.assess_article_source("yfinance_live_news", now=NOW)
    assert pending["platform_eligible"] is False
    accepted = acceptance.assess_article_source(
        "yfinance_live_news", now=NOW, human_review_approved=True)
    assert accepted["platform_eligible"] is True
    assert acceptance.publish_source(accepted, path=tmp_path / "release.yaml")["mode"] == "platform"
