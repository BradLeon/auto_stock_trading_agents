import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from ats.data.articles import ibkr_news


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


class Stock:
    def __init__(self, symbol, *_):
        self.symbol = symbol
        self.conId = 0


def _headline(article_id="42", provider="DJ-N", title="NVIDIA signs new supply agreement"):
    return SimpleNamespace(
        articleId=article_id, providerCode=provider, headline=title,
        time=datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )


class FakeIB:
    def __init__(self, *, providers=None, responses=None, fail_symbols=()):
        self.providers = providers if providers is not None else [SimpleNamespace(code="DJ-N")]
        self.responses = list(responses or [])
        self.fail_symbols = set(fail_symbols)
        self.calls = []

    def reqNewsProviders(self):
        return self.providers

    def qualifyContracts(self, contract):
        if contract.symbol in self.fail_symbols:
            raise TimeoutError(contract.symbol)
        contract.conId = {"NVDA": 1, "AMD": 2}.get(contract.symbol, 3)

    def reqHistoricalNews(self, con_id, providers, start, end, limit):
        self.calls.append((con_id, providers, start, end, limit))
        response = self.responses.pop(0) if self.responses else []
        if isinstance(response, Exception):
            raise response
        return response


def _run(monkeypatch, fake, **kwargs):
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)
    return ibkr_news.discover(
        symbols=["NVDA"], pages=1, lookback_days=3, now=NOW, **kwargs)


def test_ibkr_uses_utc_wire_format_and_none_is_a_local_empty_slice(monkeypatch):
    fake = FakeIB(responses=[None])

    assert _run(monkeypatch, fake) == []
    _, _, start, end, _ = fake.calls[0]
    assert start == "20260820-11:50:00"
    assert end == "20260823-12:00:00"


def test_one_slice_timeout_does_not_abort_older_slices(monkeypatch):
    fake = FakeIB(responses=[TimeoutError("newest"), [_headline()]])
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)

    rows = ibkr_news.discover(
        symbols=["NVDA"], pages=2, lookback_days=6, now=NOW)

    assert len(fake.calls) == 2
    assert [row.slug for row in rows] == ["DJ-N-42"]


def test_bad_symbol_and_duplicate_cross_symbol_article_are_localized(monkeypatch):
    fake = FakeIB(
        responses=[[_headline()], [_headline()]],
        fail_symbols={"NVDA"},
    )
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)

    rows = ibkr_news.discover(
        symbols=["NVDA", "AMD", "TSM"], pages=1, lookback_days=3, now=NOW)

    assert len(rows) == 1
    assert rows[0].url == "ibkr-news://DJ-N/42"


def test_missing_or_failed_provider_lookup_is_zero_results(monkeypatch):
    assert _run(monkeypatch, FakeIB(providers=[])) == []
    failing = FakeIB()
    failing.reqNewsProviders = lambda: (_ for _ in ()).throw(TimeoutError("providers"))
    assert _run(monkeypatch, failing) == []


def test_fetch_body_handles_none_binary_and_provider_provenance(monkeypatch):
    class Articles:
        def __init__(self, value):
            self.value = value

        def reqNewsArticle(self, provider, article_id):
            assert (provider, article_id) == ("DJ-N", "42")
            return self.value

    monkeypatch.setattr(ibkr_news, "_client", lambda: Articles(None))
    assert ibkr_news.fetch_body("ibkr-news://DJ-N/42") == ""
    monkeypatch.setattr(
        ibkr_news, "_client", lambda: Articles(SimpleNamespace(articleType=1, articleText="x")))
    assert ibkr_news.fetch_body("ibkr-news://DJ-N/42") == ""
    monkeypatch.setattr(
        ibkr_news, "_client",
        lambda: Articles(SimpleNamespace(articleType=0, articleText="<p>Full body</p>")),
    )
    assert ibkr_news.fetch_body("ibkr-news://DJ-N/42") == "Full body"
