"""TrendForce News — the packaging and memory beat, as an outsider reports it.

The claims about advanced packaging rest almost entirely on TSMC describing its own
capacity, and a foundry saying its back end is tight is an interested party. TrendForce
covers the same quarter from outside every side of it: it reports TSMC's CoWoS capacity
and Intel's EMIB backlog and ASE's price hikes in the same week, and it names customers
considering a switch — which no supplier's call ever does. That is the one vantage the
first-party corpus cannot produce.

Distinct from `data/sources/trendforce.py`, which scrapes their DRAM contract-price
TABLE. Same publisher, different shape: that one is a series and becomes an Observation
by formula, this one is prose and has to be read.

Discovery is by paging the news index, NOT by RSS. Their feed at /news/feed/ exists and
would be better — it carries full article text and structured tags — but measured on
2026-08-13 its contents were frozen at 2026-07-01 while the site kept publishing daily.
It is re-served with a fresh Last-Modified each time, so the staleness is invisible from
the response headers. If they ever fix it, `discover()` is the only thing that changes.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from ...schemas.chain import ArticleRef

log = logging.getLogger("ats.data.articles.trendforce")

INDEX = "https://www.trendforce.com/news/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

# Article URLs carry the date AND the headline slug, so both the keyword filter and the
# already-seen check run before anything is fetched:
#   /news/2026/08/13/news-samsung-sk-hynixs-hbm4-push-puts-hbm-general-memory-pricing/
_ARTICLE = re.compile(
    r"https://www\.trendforce\.com/news/(\d{4})/(\d{2})/(\d{2})/([a-z0-9][a-z0-9\-]*)/?")

# The body lives in this container on every readable template — [News], [Insights] and
# the press-center research write-ups alike. Absent on the paywalled /research/ product
# pages and on index pages, which is exactly what makes it usable as the test.
_BODY = re.compile(
    r"<article[^>]*class=['\"][^'\"]*presscenter[^'\"]*['\"][^>]*>(.*?)</article>", re.S)
# The citation box that closes an article. NOTE: strip <script> BEFORE looking for this
# — a jQuery snippet references `.article_highlight-area-BG_wrap` ABOVE the body, and
# cutting there yields a ~198-char title fragment instead of nothing. That is the worst
# possible failure: it looks like success and sails past a container-presence check.
_STOPS = ("article_highlight-area-BG_wrap", "Read more")
_ENTITIES = [("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"),
             ("&#8217;", "'"), ("&#8216;", "'"), ("&#8220;", '"'), ("&#8221;", '"'),
             ("&#8211;", "-"), ("&#8212;", "—"), ("&#8230;", "…"),
             ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')]


def _get(url: str, *, timeout: int = 25) -> str:
    import httpx

    return httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True).text


def _unescape(text: str) -> str:
    for ent, ch in _ENTITIES:
        text = text.replace(ent, ch)
    return text


def _slug_title(slug: str) -> str:
    """Best-effort headline from the slug — the real one needs the page, and the whole
    point of discovery is not to fetch it yet."""
    words = slug.replace("-", " ")
    for prefix in ("news ", "insights "):
        if words.startswith(prefix):
            words = f"[{prefix.strip().title()}] {words[len(prefix):]}"
            break
    return words


def discover(*, pages: int = 3, **_) -> list[ArticleRef]:
    """Recent articles from the news index, newest first, deduped across pages.

    One request per page and no article bodies. Overlap between runs is intentional:
    the caller drops anything already in the ledger before it costs a fetch.
    """
    seen: dict[str, ArticleRef] = {}
    for page in range(1, max(1, pages) + 1):
        url = INDEX if page == 1 else f"{INDEX}page/{page}/"
        try:
            html = _get(url)
        except Exception as exc:  # noqa: BLE001 - one bad page must not lose the others
            log.warning("trendforce: index page %d unreachable: %s", page, exc)
            continue
        found = 0
        for y, m, d, slug in _ARTICLE.findall(html):
            if slug in seen:
                continue
            try:
                published = date(int(y), int(m), int(d))
            except ValueError:
                continue
            seen[slug] = ArticleRef(
                url=f"{INDEX}{y}/{m}/{d}/{slug}/", slug=slug,
                title=_slug_title(slug), published_at=published)
            found += 1
        log.info("trendforce: index page %d -> %d new links", page, found)
        if not found and page > 1:
            break                                  # ran off the end of the archive
    return sorted(seen.values(), key=lambda a: (a.published_at or date.min), reverse=True)


def extract_body(html: str) -> str:
    """The article text inside a fetched page, or "" when it is not there.

    Kept separate from `fetch_body` so the parsing can be tested against a fixture
    without a network stub — the paywall and truncation regressions both live here.
    """
    m = _BODY.search(html)
    if not m:
        return ""                                  # paywalled, or not an article page
    seg = re.sub(r"<script.*?</script>|<style.*?</style>", " ", m.group(1), flags=re.S)
    for stop in _STOPS:                            # only AFTER scripts are gone
        cut = seg.find(stop)
        if cut > 0:
            seg = seg[:cut]
    paras = re.findall(r"<p[^>]*>(.*?)</p>", seg, re.S)
    text = " ".join(paras) if paras else seg
    text = _unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def fetch_body(url: str) -> str:
    """One article's text. "" means the body could not be located — see the package
    docstring for why that is a recorded gap and never a fallback to page text."""
    body = extract_body(_get(url))
    if not body:
        log.info("trendforce: no article container at %s (paywalled or not an article)", url)
    return body
