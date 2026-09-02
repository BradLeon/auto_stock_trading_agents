import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from ats.data.articles import ibkr_news


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


class Stock:
    def __init__(self, symbol, *_):
        self.symbol = symbol
        self.conId = 0


def _headline(article_id="42", provider="DJ-N", title="NVIDIA signs new supply agreement",
              when=None):
    return SimpleNamespace(
        articleId=article_id, providerCode=provider, headline=title,
        time=when if when is not None else datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
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


def test_provider_lookup_retries_an_initially_empty_tws_response_without_blocking(monkeypatch):
    fake = FakeIB(providers=[])
    responses = [[], [SimpleNamespace(code="DJ-N")]]
    fake.reqNewsProviders = lambda: responses.pop(0)
    sleeps = []
    fake.sleep = lambda seconds: sleeps.append(seconds)
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)

    _rows, status = ibkr_news.discover_with_status(
        symbols=["NVDA"], pages=1, lookback_days=3, now=NOW,
        provider_lookup_attempts=2, provider_lookup_retry_seconds=0.1)

    assert status["provider_lookup"]["attempts"] == 2
    assert sleeps == [0.1]


def test_provider_empty_after_retries_is_not_labeled_an_entitlement_failure(monkeypatch):
    fake = FakeIB(providers=[])
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)

    _rows, status = ibkr_news.discover_with_status(
        symbols=["NVDA"], now=NOW, provider_lookup_attempts=1)

    assert status["status"] == "provider_unavailable"
    assert status["error"] == "news_provider_lookup_empty_after_retries"


def test_explicit_diagnostic_can_probe_recent_provider_when_enumeration_is_empty(monkeypatch):
    fake = FakeIB(providers=[], responses=[[_headline()]])
    fake.isConnected = lambda: True
    fake.client = SimpleNamespace(serverVersion=lambda: 178)
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)

    result = ibkr_news.diagnose(
        symbol="NVDA", providers=["DJ-N"], now=NOW,
        provider_lookup_attempts=1)

    assert result["status"] == "historical_news_available"
    assert result["selected_providers"] == ["DJ-N"]
    assert result["selected_but_not_currently_enumerated"] == ["DJ-N"]
    assert result["probes"][0]["status"] == "headlines_received"


def test_diagnose_reports_timeout_without_mislabeling_it_zero_news(monkeypatch):
    fake = FakeIB(responses=[None])
    fake.isConnected = lambda: True
    fake.client = SimpleNamespace(serverVersion=lambda: 178)
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)

    result = ibkr_news.diagnose(symbol="NVDA", providers=["DJ-N"], now=NOW)

    assert result["status"] == "historical_news_no_callback"
    assert result["con_id"] == 1
    assert result["probes"][0]["status"] == "timeout_without_response"
    assert result["probes"][0]["headlines"] is None


def test_provider_subscription_error_has_a_distinct_classification():
    assert ibkr_news._request_failure_status([
        {"code": 321, "message": "Not subscribed for 'DJ-N' provider"},
    ]) == "provider_not_subscribed"
    assert ibkr_news._request_failure_status([
        {"code": 321, "message": "invalid request"},
    ]) == "request_rejected"


def _run(monkeypatch, fake, **kwargs):
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)
    return ibkr_news.discover(
        symbols=["NVDA"], pages=1, lookback_days=3, now=NOW, **kwargs)


def test_ibkr_uses_utc_wire_format_and_none_is_an_explicit_failed_slice(monkeypatch):
    fake = FakeIB(responses=[None])

    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)
    rows, status = ibkr_news.discover_with_status(
        symbols=["NVDA"], pages=1, lookback_days=3, now=NOW)
    assert rows == []
    assert status["failed_slices"][0]["error"] == "empty_response_timeout_or_provider_failure"
    _, _, start, end, _ = fake.calls[0]
    assert start == datetime(2026, 8, 20, 11, 50, tzinfo=timezone.utc)
    assert end == NOW


def test_low_level_historical_news_path_uses_tws_wire_datetime_format():
    assert ibkr_news._historical_wire_time(NOW) == "20260823 12:00:00 UTC"


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


def test_cross_provider_headline_retains_exact_time_entity_gate_and_native_ids(monkeypatch):
    naive_tws_time = datetime(2026, 8, 23, 8, 0)
    fake = FakeIB(
        providers=[SimpleNamespace(code="DJ-N"), SimpleNamespace(code="BRFG")],
        responses=[[
            _headline("dj-42", "DJ-N", "NVIDIA launches a new platform -- WSJ", naive_tws_time),
            _headline("dj-99", "DJ-N", "A retailer opens a new store -- WSJ", naive_tws_time),
        ], [
            _headline("br-7", "BRFG", "NVIDIA launches a new platform -- WSJ", naive_tws_time),
        ]],
    )
    monkeypatch.setitem(sys.modules, "ib_async", SimpleNamespace(Stock=Stock))
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)

    rows = ibkr_news.discover(
        symbols=["NVDA", "AMD"], pages=1, lookback_days=3, now=NOW)

    assert len(rows) == 2
    nvidia = next(row for row in rows if "NVIDIA" in row.title)
    irrelevant = next(row for row in rows if "retailer" in row.title)
    provenance = ibkr_news.provenance(nvidia)
    assert provenance["published_at_exact"] == "2026-08-23T08:00:00"
    assert provenance["published_at_timezone"] == "tws_session_timezone_unreported"
    assert provenance["provider_article_ids"] == "BRFG:br-7, DJ-N:dj-42"
    assert provenance["title_verified_entities"] == "NVDA"
    assert provenance["association_rejected_entities"] == "AMD"
    assert provenance["entity_association"] == "title_verified"
    assert ibkr_news.provenance(irrelevant)["entity_association"] == "association_rejected"


def test_publisher_uses_the_final_wire_suffix():
    assert ibkr_news._publisher_from_headline(
        "Micron Stock -- Tech Stocks -- MarketWatch", "DJ-RTA") == "MarketWatch"


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


def test_fetch_body_retries_a_transient_empty_response(monkeypatch):
    class Articles:
        def __init__(self):
            self.responses = [None, SimpleNamespace(articleType=0, articleText="<p>Recovered</p>")]
            self.sleeps = []

        def reqNewsArticle(self, _provider, _article_id):
            return self.responses.pop(0)

        def sleep(self, seconds):
            self.sleeps.append(seconds)

    fake = Articles()
    monkeypatch.setattr(ibkr_news, "_client", lambda: fake)

    assert ibkr_news.fetch_body("ibkr-news://DJ-N/42") == "Recovered"
    assert fake.sleeps == [ibkr_news._ARTICLE_FETCH_RETRY_SECONDS]


def test_source_owned_timeout_uses_public_method_for_test_double():
    fake = FakeIB(responses=[[_headline()]])

    rows = ibkr_news._historical_news(fake, 1, "DJ-N", NOW, NOW, 100)

    assert len(rows) == 1
    assert fake.calls[0][1] == "DJ-N"
