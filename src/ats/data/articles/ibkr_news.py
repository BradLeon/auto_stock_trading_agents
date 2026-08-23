"""IBKR news feed — Dow Jones (WSJ / Barron's / MarketWatch / IBD) + Briefing.com.

The only source on this chain that covers **structural transactions**. Filings describe
the quarter that closed; a financing platform, an equity stake in a customer, a
guarantee, a supply pact — those are announced, and they never appear in a 10-Q until
long after they have changed the thing a claim is asking about.

Measured, and the reason this adapter exists: `xpu_revenue_funded_by_customers` asks
whether XPU revenue is funded by customers or by the vendor's own balance sheet. With
only first-party filings it resolved to `unknown` — one cluster, below the floor. The
answer was sitting in this feed the whole time:

    [2026-08-11 DJ-N] "Nvidia, Wall Street Firms Set Financing Pact -- WSJ"
      Apollo, Blackstone, BlackRock, Brookfield, Goldman Sachs and KKR ... "aim to
      deploy over $500 billion of outside capital" in "compute financing platforms"
      ... "Apollo and Blackstone earlier this year struck a deal with Broadcom to set
      up a similar financing platform for the chip maker's customers."

That last sentence is worth more than the headline: it is **independent corroboration of
Broadcom's own SPV disclosure**, and Broadcom had zero non-self-reported readings in the
ledger. This feed is how a cross-section escapes being four companies grading themselves.

## Access

No subscription. The Dow Jones and Briefing.com feeds listed here are the ones
pre-enabled on a funded IBKR account (TWS ▸ 配置 ▸ API ▸ 新闻配置). Benzinga and The Fly
appear in the same dialog but are paid add-ons and are simply absent from
`reqNewsProviders()` until subscribed — no special handling needed, they never appear.

## Two API behaviours that are easy to get backwards

**`reqHistoricalNews` walks FORWARD from `startDateTime`, and `totalResults` truncates
the *early* end of the window.** Asking for the last 30 days with 60 results returns the
60 OLDEST headlines in that span — a month-stale batch that looks superficially fine.
Passing an empty `startDateTime` returns nothing at all. So the window is walked in
short slices, newest slice first.

**Headlines carry a control-code prefix** (`{A:800015:L:en}`) and a few rows are pure
junk (`!`). Both are stripped/dropped in `discover`, before the keyword filter runs —
otherwise the prefix defeats the word-boundary match in `chain/articles._wanted`.

## Never return the page when the article is not there

Same rule as every adapter here (see `data/articles/__init__.py`): `reqNewsArticle` can
return an empty or stub body for a headline-only wire item. That returns "", which
`chain/articles` records as a gap. A headline alone is not evidence — the span it would
produce could not be re-checked against anything.
"""

from __future__ import annotations

import atexit
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from ...schemas.chain import ArticleRef

log = logging.getLogger("ats.data.articles.ibkr_news")

# Distinct from the broker's `base + pid%80 + 1`: a colliding clientId silently boots
# the other session, and the other session is the one that places orders.
CLIENT_ID = int(os.getenv("ATS_IBKR_NEWS_CLIENT_ID", "190"))
URL_PREFIX = "ibkr-news"
_SLICE_DAYS = 3                 # short enough that totalResults never truncates a slice
_PER_SLICE = 100
_OVERLAP_MINUTES = 10           # replay boundary rows; stable ids make this idempotent

_ib = None                      # one connection per process, reused across the run


def _client():
    """Lazily connect, read-only. Raises on failure — the caller records the gap."""
    global _ib
    if _ib is not None and _ib.isConnected():
        return _ib
    from ib_async import IB

    from ...config import get_config

    # Same resolution order as broker/ibkr.py: settings.yaml [broker] overrides .env.
    cfg = get_config()
    host = cfg.secrets.ibkr_host or "127.0.0.1"
    port = cfg.app.broker.port or cfg.secrets.ibkr_port or 7496
    ib = IB()
    # readonly=True is not decoration: this process must never be able to place an
    # order, and the news path has no business holding a writable session.
    ib.connect(host, int(port), clientId=CLIENT_ID, timeout=20, readonly=True)
    _ib = ib
    atexit.register(close)
    return _ib


def close() -> None:
    global _ib
    if _ib is not None:
        try:
            _ib.disconnect()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
        _ib = None


def _clean_headline(h: str) -> str:
    """Strip the `{A:800015:L:en}` control prefix and the `*`/`!` wire markers."""
    return re.sub(r"^\{[^}]*\}", "", h or "").lstrip("*! ").strip()


def _slug(article_id: str) -> str:
    """Stable per-article id, safe for a document_id and a filename."""
    return re.sub(r"[^A-Za-z0-9]+", "-", article_id or "").strip("-")


def _article_slug(provider: str, article_id: str) -> str:
    """Provider-scoped stable id without duplicating prefixes embedded by IBKR."""
    raw = article_id if article_id.upper().startswith(provider.upper()) else \
        f"{provider}-{article_id}"
    return _slug(raw)


def to_text(article_text: str) -> str:
    """Dow Jones bodies are HTML fragments with numeric entities. Plain text out.

    Kept module-level and named without an underscore so a test can exercise the
    unescape/strip order directly: unescaping first would let `&lt;p&gt;` in quoted text
    turn into a tag that the stripper then eats.
    """
    txt = re.sub(r"<[^>]+>", " ", article_text or "")
    txt = html.unescape(txt)
    return re.sub(r"[ \t ]+", " ", txt).strip()


def discover(*, pages: int = 3, symbols: list[str] | None = None,
             lookback_days: int = 7, now: datetime | None = None,
             **_) -> list[ArticleRef]:
    """Headlines for the declared symbols, newest first. Bodies are NOT fetched here.

    `pages` is honoured as "how many lookback slices", so the shared config field keeps
    meaning "how far back to sweep" rather than becoming adapter-specific vocabulary.
    """
    symbols = [s.upper() for s in (symbols or [])]
    if not symbols:
        log.warning("ibkr_news: no symbols declared — nothing to discover")
        return []

    from ib_async import Stock

    ib = _client()
    try:
        provider_rows = ib.reqNewsProviders() or []
    except Exception as exc:  # noqa: BLE001
        log.warning("ibkr_news: provider lookup failed — %s", exc)
        return []
    providers = "+".join(str(p.code) for p in provider_rows if getattr(p, "code", ""))
    if not providers:
        log.warning("ibkr_news: account exposes no news providers")
        return []

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    span = max(lookback_days, pages * _SLICE_DAYS)
    # IBKR Historical News accepts UTC as ``yyyyMMdd-HH:mm:ss``. The former ISO-like
    # value timed out and sometimes surfaced as None, which looked like zero news.
    fmt = "%Y%m%d-%H:%M:%S"
    out: dict[str, ArticleRef] = {}

    for sym in symbols:
        try:
            c = Stock(sym, "SMART", "USD")
            ib.qualifyContracts(c)
            if not c.conId:
                log.warning("ibkr_news: %s did not qualify", sym)
                continue
        except Exception as exc:  # noqa: BLE001 - one bad symbol, not the whole sweep
            log.warning("ibkr_news: %s qualify failed — %s", sym, exc)
            continue

        # Newest slice first: if max_per_run bites, it should bite on the OLD end.
        edge = now
        while edge > now - timedelta(days=span):
            start = max(now - timedelta(days=span), edge - timedelta(days=_SLICE_DAYS))
            query_start = start - timedelta(minutes=_OVERLAP_MINUTES)
            try:
                heads = ib.reqHistoricalNews(c.conId, providers, query_start.strftime(fmt),
                                             edge.strftime(fmt), _PER_SLICE)
            except Exception as exc:  # noqa: BLE001
                log.warning("ibkr_news: %s slice %s failed — %s", sym, start.date(), exc)
                heads = []
            for h in heads or ():
                title = _clean_headline(h.headline)
                provider = str(getattr(h, "providerCode", "") or "")
                article_id = str(getattr(h, "articleId", "") or "")
                slug = _article_slug(provider, article_id)
                # A headline that is only a control code or a wire marker carries no
                # claim-relevant content and would just burn a body request.
                if not slug or len(title) < 12 or slug in out:
                    continue
                when = h.time
                out[slug] = ArticleRef(
                    url=f"{URL_PREFIX}://{provider}/{article_id}",
                    slug=slug, title=title,
                    published_at=when.date() if hasattr(when, "date") else None)
            edge = start

    log.info("ibkr_news: %d distinct headlines across %d symbols over %dd",
             len(out), len(symbols), span)
    return sorted(out.values(), key=lambda r: (r.published_at or datetime.min.date()),
                  reverse=True)


def fetch_body(url: str) -> str:
    """Full article text for one headline. "" when the body is not there (a gap)."""
    m = re.match(rf"^{URL_PREFIX}://([^/]+)/(.+)$", url or "")
    if not m:
        log.warning("ibkr_news: unrecognised url %r", url)
        return ""
    provider, article_id = m.group(1), m.group(2)
    try:
        art = _client().reqNewsArticle(provider, article_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("ibkr_news: article %s/%s failed — %s", provider, article_id, exc)
        return ""
    # articleType 1 is a binary/PDF payload — there is no text to read, and decoding it
    # is not worth a branch. Treat as a gap, same as an empty body.
    if getattr(art, "articleType", 0) == 1:
        return ""
    return to_text(getattr(art, "articleText", "") or "")
