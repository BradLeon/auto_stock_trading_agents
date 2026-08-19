"""Article sources — published prose into the evidence ledger (hermetic, no network).

The gates treat a publisher exactly like a company: same table, same dedup, same
adjudication. What differs is only that the body has to be located inside a page first,
and that step is where this pipeline can fail in ways a series never could.
"""

from datetime import date, datetime, timezone

import pytest

from ats.chain import articles
from ats.memory import get_store
from ats.schemas.chain import ArticleRef, ArticleSourceDef

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _source(**kw):
    base = dict(id="tf", label="TrendForce 新闻", adapter="trendforce",
                entity="TRENDFORCE", stance="regulator", doc_type="article",
                pages=2, max_per_run=12, min_body_chars=800,
                match=["cowos", "hbm", "packaging"])
    return ArticleSourceDef(**{**base, **kw})


def _ref(slug, *, day=13):
    return ArticleRef(url=f"https://x/news/2026/08/{day:02d}/{slug}/", slug=slug,
                      title=slug.replace("-", " "), published_at=date(2026, 8, day))


def _body(n=1200):
    return "TSMC said its back end is in shortage mode. " * (n // 44 + 1)


class _Adapter:
    """Stands in for a data/articles/<name>.py module."""

    def __init__(self, refs, bodies=None, *, raises=False):
        self.refs, self.bodies, self.raises = refs, bodies or {}, raises
        self.discover_calls, self.fetched = 0, []

    def discover(self, *, pages=3, **_):
        self.discover_calls += 1
        if self.raises:
            raise RuntimeError("index unreachable")
        return self.refs

    def fetch_body(self, url):
        self.fetched.append(url)
        return self.bodies.get(url, _body())


@pytest.fixture
def wire(monkeypatch):
    """Install one fake source + adapter, and count observer calls."""
    from ats.agents.evidence import observer

    def _install(source, adapter, *, observe_result=None):
        monkeypatch.setattr(articles, "load_article_sources", lambda: [source])
        monkeypatch.setattr(articles, "_adapter", lambda s: adapter)
        calls = []

        def _observe(symbol, document_id, text, **kw):
            calls.append({"symbol": symbol, "document_id": document_id,
                          "text": text, **kw})
            return observe_result or {"symbol": symbol, "saved": 3, "new": 3, "failure": ""}

        monkeypatch.setattr(observer, "observe_document", _observe)
        return calls

    return _install


def test_a_publisher_we_cannot_reach_is_a_gap_not_silence(wire):
    """"We could not read the index" and "the publisher had nothing to say" are
    different claims about the world — the same distinction `sources.collect` records
    with -1, and for the same reason."""
    adapter = _Adapter([], raises=True)
    wire(_source(), adapter)
    store = get_store()

    stats = articles.collect_articles(store, now=NOW)
    assert stats["tf"].unreachable and stats["tf"].code == -1
    assert [d for d in store.documents(entity="TRENDFORCE", ok_only=False) if not d["ok"]]


def test_a_quiet_week_is_not_the_same_zero_as_an_outage(wire):
    """A weekly job against a daily publisher normally finds everything already read.
    That is the healthy steady state, and it must not report as the outage above."""
    adapter = _Adapter([_ref("news-cowos-capacity-up")])
    wire(_source(), adapter)
    store = get_store()

    articles.collect_articles(store, now=NOW)          # first run reads it
    assert adapter.fetched                             # ...having fetched the body
    adapter.fetched.clear()

    second = articles.collect_articles(store, now=NOW)  # second run: nothing new
    assert second["tf"].code == 0
    assert not second["tf"].unreachable
    assert adapter.fetched == [], "an already-read article must not be re-fetched"


def test_the_keyword_filter_spends_nothing_on_an_off_topic_article(wire):
    """The filter exists to control COST, not correctness — so it has to actually stop
    the spending: no body fetch, no model call. (Correctness is already handled: the
    concept menu is closed, so an off-topic article lands wholly in the unmapped pool.)"""
    adapter = _Adapter([_ref("news-huawei-mate-90-launches-in-september")])
    calls = wire(_source(), adapter)
    store = get_store()

    stat = articles.collect_articles(store, now=NOW)["tf"]
    assert stat.scanned == 1 and stat.matched == 0
    assert adapter.fetched == [] and calls == []


def test_a_short_keyword_does_not_match_inside_a_longer_word(wire):
    """From the first live run: `ase` (the OSAT) matched "b-ase-d" and "b-ase", pulling
    in a Huawei phone piece and a gallium-arsenide solar piece. The wasted model calls
    were the small half of the damage — `max_per_run` was already reached, so the
    false positives EVICTED articles that belonged."""
    src = _source(match=["ase", "cowos", "2nm"])
    noise = [_ref("news-huawei-readies-mate-90-with-tau-scaling-based-chips"),
             _ref("news-xiamen-gaas-satellite-solar-cell-production-base")]
    real = [_ref("news-ase-spil-breaks-ground-to-boost-cowos-capacity"),
            _ref("news-tsmc-2nm-ramp-on-track")]
    adapter = _Adapter(noise + real)
    calls = wire(src, adapter)
    get_store()

    articles.collect_articles(get_store(), now=NOW)
    read = {c["document_id"] for c in calls}
    assert not any("huawei" in d or "gaas" in d for d in read), "substring match is back"
    assert len(read) == 2, "both genuinely-matching articles must still be read"


def test_a_page_with_no_article_body_never_reaches_the_model(wire):
    """The one that matters most.

    A paywalled page is not short — TrendForce's gated research page is the LONGEST
    document on the site and contains no article at all. If a body cannot be located,
    handing the page to the extraction model would manufacture evidence out of the
    navigation bar, which is far worse than missing the piece: it is invisible
    downstream. So: recorded as a gap, and the model is never called.
    """
    ref = _ref("news-cowos-capacity-behind-the-paywall")
    adapter = _Adapter([ref], bodies={ref.url: ""})
    calls = wire(_source(), adapter)
    store = get_store()

    stat = articles.collect_articles(store, now=NOW)["tf"]
    assert stat.unreadable == 1 and stat.ingested == 0
    assert calls == [], "a page we could not parse must never be extracted"
    fails = [d for d in store.documents(entity="TRENDFORCE", ok_only=False) if not d["ok"]]
    assert any(ref.slug in (d["document_id"] or "") for d in fails)


def test_an_unreadable_article_is_not_re_probed_every_week(wire):
    """`has_observations_for_document` only turns true on a SUCCESSFUL extraction, and a
    failure lands under a different key — so dedup keyed on it alone would re-fetch a
    permanently-gated article forever. The negative cache is what stops that."""
    ref = _ref("news-hbm-pricing-gated")
    adapter = _Adapter([ref], bodies={ref.url: ""})
    wire(_source(), adapter)
    store = get_store()

    articles.collect_articles(store, now=NOW)
    adapter.fetched.clear()
    second = articles.collect_articles(store, now=NOW)

    assert adapter.fetched == [], "a known-unreadable article must stay skipped"
    assert second["tf"].unreadable == 0 and second["tf"].code == 0


def test_a_short_body_is_treated_as_unreadable_not_as_a_thin_article(wire):
    """Truncation failures do not always yield "" — a marker that fires above the body
    returns a title fragment, which looks like success. The length floor is the backstop
    for exactly that, so it is measured on the EXTRACTED body, never the page."""
    ref = _ref("news-packaging-truncated")
    adapter = _Adapter([ref], bodies={ref.url: "[News] Some Headline 2026-08-13 editor"})
    calls = wire(_source(), adapter)
    store = get_store()

    stat = articles.collect_articles(store, now=NOW)["tf"]
    assert stat.unreadable == 1 and calls == []


def test_the_slug_names_the_document_but_never_the_period(wire):
    """`observer.extract` uses `period` as a per-ROW fallback, so passing the slug would
    stamp it onto every observation that did not name its own period — a slug is not a
    fiscal period. It belongs in the document id and the cache path only."""
    ref = _ref("news-cowos-capacity-up")
    adapter = _Adapter([ref])
    calls = wire(_source(), adapter)
    store = get_store()

    articles.collect_articles(store, now=NOW)
    assert len(calls) == 1
    assert calls[0]["period"] == ""
    assert ref.slug in calls[0]["document_id"]
    assert calls[0]["symbol"] == "TRENDFORCE"        # the PUBLISHER is the speaker
    assert calls[0]["source_url"] == ref.url


def test_the_model_call_is_capped_however_much_matched(wire):
    """`max_per_run` is a cost ceiling on extraction, not on discovery — a backlog after
    an outage must not turn into an unbounded bill."""
    refs = [_ref(f"news-cowos-item-{i}") for i in range(10)]
    adapter = _Adapter(refs)
    calls = wire(_source(max_per_run=3), adapter)
    store = get_store()

    stat = articles.collect_articles(store, now=NOW)["tf"]
    assert stat.ingested == 3 and len(calls) == 3
    assert stat.scanned == 10


def test_an_extraction_failure_still_counts_as_paid_for(wire):
    """The document row is written BEFORE the model runs, so a doc that extracts to
    nothing is not re-read next week. The body is on disk, so a human can always re-run
    it by hand — nothing is lost, it just stops costing money on a schedule."""
    ref = _ref("news-hbm-supply-tight")
    adapter = _Adapter([ref])
    wire(_source(), adapter,
         observe_result={"symbol": "TRENDFORCE", "saved": 0, "new": 0,
                         "failure": "未抽出任何带原文佐证的观测"})
    store = get_store()

    articles.collect_articles(store, now=NOW)
    adapter.fetched.clear()
    articles.collect_articles(store, now=NOW)
    assert adapter.fetched == []


def test_collect_articles_is_wired_into_the_weekly_job():
    """A configured source nobody fetches is worse than none: it looks live in the config
    and is frozen in the ledger. It must also run BEFORE the report renders, or the
    report shows last week's articles beside this week's filings."""
    import inspect

    from ats.runtime import scheduler

    src = inspect.getsource(scheduler._cross_section_weekly)
    assert "collect_articles" in src
    assert src.index("collect_articles(") < src.index("chain_report.write")


def test_an_article_source_outage_does_not_break_the_weekly_job():
    """A publisher being down is a recorded gap, never an exception that costs the whole
    sector job its report — the same rule the series sources follow."""
    import inspect

    from ats.runtime import scheduler

    src = inspect.getsource(scheduler._cross_section_weekly)
    after = src[src.index("chain_articles.collect_articles("):]
    assert "except Exception" in after.split("chain_report")[0]


# --- the TrendForce adapter's own parsing, against fixtures ---------------------

_PAGE = """<html><body>
  <article class='col-md-8 col-sm-12 presscenter'>
    <p>[News] Headline</p>
    <script>
      var highlightArea = $('.article_highlight-area-BG_wrap');
    </script>
    <p>{body}</p>
    <div class="highlight-frame article_highlight-area-BG_wrap">Please note that this
    article cites information from <a href="http://x">MoneyDJ</a>.</div>
  </article></body></html>"""


def test_the_body_survives_a_stop_marker_that_appears_above_it():
    """A jQuery snippet references `.article_highlight-area-BG_wrap` ABOVE the body. Cut
    on the marker before stripping scripts and you get a ~198-char title fragment
    instead of nothing — a failure that LOOKS like success and sails past the
    container-presence check, leaving only the length floor between it and the ledger."""
    from ats.data.articles import trendforce

    body = "TSMC said its back end is in shortage mode and the gap is bigger. " * 20
    got = trendforce.extract_body(_PAGE.format(body=body))

    assert "shortage mode" in got and len(got) > 800
    assert "highlightArea" not in got and "jQuery" not in got
    assert "Please note that this" not in got, "the citation box is not article text"


def test_a_page_without_the_article_container_yields_nothing():
    """The paywalled product pages and the index itself have no article container. That
    is the whole test for "can we read this" — every cheaper signal was measured and
    failed (see data/articles/__init__.py)."""
    from ats.data.articles import trendforce

    paywalled = ("<html><body><div class='container content'>"
                 "<h1>DRAM Research Report</h1>"
                 "<p>Subscribe to read. Member Center. " + "padding " * 4000 +
                 "</p></div></body></html>")
    assert trendforce.extract_body(paywalled) == ""
    assert len(paywalled) > 30_000, "长度不是判据——被墙的页面比正常文章更长"


def test_discovery_reads_the_date_and_slug_out_of_the_url(monkeypatch):
    """Both the keyword filter and the seen-check run before any body is fetched, which
    only works because TrendForce puts the date and the headline in the URL."""
    from ats.data.articles import trendforce

    index = """<a href="https://www.trendforce.com/news/2026/08/13/news-cowos-up/">x</a>
               <a href="https://www.trendforce.com/news/2026/08/12/news-hbm-tight/">y</a>
               <a href="https://www.trendforce.com/news/2026/08/13/news-cowos-up/">dup</a>"""
    monkeypatch.setattr(trendforce, "_get", lambda url, **k: index)

    refs = trendforce.discover(pages=1)
    assert [r.slug for r in refs] == ["news-cowos-up", "news-hbm-tight"]  # deduped, newest first
    assert refs[0].published_at == date(2026, 8, 13)
    assert refs[0].url.endswith("/news/2026/08/13/news-cowos-up/")
