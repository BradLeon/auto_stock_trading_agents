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
from dataclasses import dataclass, field
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ...schemas.chain import ArticleRef
from .entity_association import normalised_title, title_mentions_entity

log = logging.getLogger("ats.data.articles.ibkr_news")

# Distinct from the broker's `base + pid%80 + 1`: a colliding clientId silently boots
# the other session, and the other session is the one that places orders.
CLIENT_ID = int(os.getenv("ATS_IBKR_NEWS_CLIENT_ID", "190"))
URL_PREFIX = "ibkr-news"
_SLICE_DAYS = 3                 # short enough that totalResults never truncates a slice
_PER_SLICE = 100
_OVERLAP_MINUTES = 10           # replay boundary rows; stable ids make this idempotent
_HISTORICAL_NEWS_TIMEOUT_SECONDS = 12
_PROVIDER_LOOKUP_ATTEMPTS = 3
_PROVIDER_LOOKUP_RETRY_SECONDS = 1
_ARTICLE_FETCH_ATTEMPTS = 2
_ARTICLE_FETCH_RETRY_SECONDS = 0.25

_ib = None                      # one connection per process, reused across the run


@dataclass
class _NewsRecord:
    """One admitted-or-rejected IBKR headline with all discovery lineage retained."""

    provider: str
    article_id: str
    title: str
    published_at: datetime | None
    published_at_exact: str
    published_at_timezone: str
    provider_article_ids: set[str] = field(default_factory=set)
    queried_entities: set[str] = field(default_factory=set)
    title_verified_entities: set[str] = field(default_factory=set)
    association_rejected_entities: set[str] = field(default_factory=set)


_records: dict[str, _NewsRecord] = {}


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


def _headline_time(value: Any) -> tuple[datetime | None, str, str]:
    """Retain TWS' exact timestamp without inventing an absent timezone.

    ``ib_async`` currently returns a naive ``datetime`` for HistoricalNews on this
    TWS session.  Re-labelling that value UTC would create false freshness evidence,
    so provenance names it as a TWS-session-local timestamp until the API exposes a
    timezone.  A timezone-aware response is normalized to UTC for stable ordering.
    """
    if not isinstance(value, datetime):
        return None, str(value or ""), "unknown"
    if value.tzinfo is None:
        return value, value.isoformat(), "tws_session_timezone_unreported"
    normalized = value.astimezone(timezone.utc)
    return normalized, normalized.isoformat(), "UTC"


def _publisher_from_headline(title: str, provider: str) -> str:
    """Expose the named publication when the IBKR headline carries one."""
    # Headlines can contain more than one ``--`` (for example a section label before
    # ``-- MarketWatch``).  The final suffix is the publisher, not the first one.
    match = re.search(r".*--\s*([^–—]+)$", title or "")
    if match:
        return match.group(1).strip()
    return provider or "IBKR News"


def to_text(article_text: str) -> str:
    """Dow Jones bodies are HTML fragments with numeric entities. Plain text out.

    Kept module-level and named without an underscore so a test can exercise the
    unescape/strip order directly: unescaping first would let `&lt;p&gt;` in quoted text
    turn into a tag that the stripper then eats.
    """
    txt = re.sub(r"<[^>]+>", " ", article_text or "")
    txt = html.unescape(txt)
    return re.sub(r"[ \t ]+", " ", txt).strip()


def _historical_wire_time(value: datetime | str) -> str:
    """Use the same TWS wire representation as ib_async's public API.

    The extended-timeout path calls the low-level client directly.  It therefore must
    not pass a Python ``datetime`` through to the socket serializer; public
    ``reqHistoricalNewsAsync`` normally performs this conversion for us.
    """
    from ib_async import util

    return util.formatIBDatetime(value)


def _sleep_without_blocking(ib, seconds: float) -> None:
    """Yield to ib_async while a retry delay elapses; test doubles need no delay."""
    sleep = getattr(ib, "sleep", None)
    if callable(sleep):
        sleep(seconds)


def _request_failure_status(errors: list[dict[str, object]]) -> str:
    """Classify a TWS request rejection without confusing it with an empty feed."""
    for error in errors:
        message = str(error.get("message") or "").lower()
        if "not subscribed" in message and "provider" in message:
            return "provider_not_subscribed"
    return "request_rejected"


def _news_providers(ib, *, attempts: int = _PROVIDER_LOOKUP_ATTEMPTS,
                    retry_seconds: float = _PROVIDER_LOOKUP_RETRY_SECONDS) -> tuple[list[str], dict]:
    """Read the current API provider list with bounded, event-loop-safe retries.

    An empty response can be a transient TWS session state immediately after login;
    it is not evidence that the account has no entitlement.  A genuine request error
    remains observable in the returned diagnostic.
    """
    errors: list[str] = []
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            rows = ib.reqNewsProviders() or []
        except Exception as exc:  # noqa: BLE001 - report after bounded retry
            errors.append(f"{type(exc).__name__}:{exc}")
            rows = []
        codes = [str(row.code) for row in rows if getattr(row, "code", "")]
        if codes:
            return codes, {"status": "succeeded", "attempts": attempt,
                           "errors": errors}
        if attempt < attempts:
            _sleep_without_blocking(ib, retry_seconds)
    return [], {"status": "empty_after_retries", "attempts": attempts,
                "errors": errors}


def _historical_news(ib, con_id: int, provider_codes: str, start: datetime | str,
                     end: datetime | str, limit: int, *, diagnostic: dict | None = None):
    """Request a slice with a source-owned timeout rather than ib_async's fixed 4s.

    TWS can take longer than four seconds to return a legitimate historical-news
    response.  A timeout remains a failed slice, but it should not be manufactured by
    a library-default deadline.  The public fake used by unit tests has no low-level
    client, so it continues through the normal public method.
    """
    if not all(hasattr(ib, attr) for attr in ("client", "wrapper", "_run")):
        return ib.reqHistoricalNews(con_id, provider_codes, start, end, limit)
    import asyncio

    request_id = ib.client.getReqId()
    if diagnostic is not None:
        diagnostic["request_id"] = request_id
        diagnostic["historical_news_end_received"] = False
        diagnostic["wire_start"] = _historical_wire_time(start)
        diagnostic["wire_end"] = _historical_wire_time(end)
    future = ib.wrapper.startReq(request_id)
    request_errors: list[dict[str, object]] = []
    error_event = getattr(ib, "errorEvent", None)

    def on_error(error_request_id, code, message, contract):
        if error_request_id != request_id:
            return
        error = {
            "request_id": error_request_id, "code": code, "message": str(message),
            "contract": str(contract) if contract else "",
        }
        request_errors.append(error)
        # ib_async intentionally treats 321 as a warning, so its wrapper does not
        # resolve the future.  For historical news it is terminal; end this one
        # request immediately instead of manufacturing a timeout 12 seconds later.
        if not future.done():
            future.set_result(None)

    if error_event is not None:
        error_event += on_error
    ib.client.reqHistoricalNews(
        request_id, con_id, provider_codes, _historical_wire_time(start),
        _historical_wire_time(end), limit, [])

    async def wait_for_response():
        try:
            await asyncio.wait_for(future, _HISTORICAL_NEWS_TIMEOUT_SECONDS)
            if request_errors:
                if diagnostic is not None:
                    diagnostic["request_errors"] = request_errors
                    diagnostic["request_failure"] = _request_failure_status(request_errors)
                return None
            if diagnostic is not None:
                diagnostic["historical_news_end_received"] = True
            return future.result()
        except asyncio.TimeoutError:
            if diagnostic is not None:
                diagnostic["timeout_seconds"] = _HISTORICAL_NEWS_TIMEOUT_SECONDS
            log.warning("ibkr_news: historical news request timed out after %ss",
                        _HISTORICAL_NEWS_TIMEOUT_SECONDS)
            return None

    try:
        return ib._run(wait_for_response())
    finally:
        if error_event is not None:
            try:
                error_event -= on_error
            except Exception:  # noqa: BLE001 - a diagnostic cleanup must not mask data status
                pass


def diagnose(*, symbol: str = "NVDA", providers: list[str] | None = None,
             lookback_days: int = 1, now: datetime | None = None,
             provider_lookup_attempts: int = _PROVIDER_LOOKUP_ATTEMPTS,
             provider_lookup_retry_seconds: float = _PROVIDER_LOOKUP_RETRY_SECONDS) -> dict:
    """Probe historical-news delivery without persisting documents or changing TWS.

    A provider being returned by ``reqNewsProviders`` proves that TWS exposes its
    code, but not that the server will complete ``reqHistoricalNews``.  This helper
    records the exact contract and callback outcome needed to escalate that gap to
    IBKR or verify an API-specific subscription.  It stays read-only and limits the
    default probe to one provider and one short time window.
    """
    requested_symbol = symbol.upper().strip()
    probe_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    errors: list[dict[str, object]] = []
    ib = _client()

    def on_error(request_id, code, message, contract):
        errors.append({
            "request_id": request_id, "code": code, "message": str(message),
            "contract": str(contract) if contract else "",
        })

    error_event = getattr(ib, "errorEvent", None)
    if error_event is not None:
        error_event += on_error
    result: dict[str, object] = {
        "access": "read_only_tws",
        "symbol": requested_symbol,
        "connected": bool(getattr(ib, "isConnected", lambda: False)()),
        "server_version": getattr(getattr(ib, "client", None), "serverVersion", lambda: None)(),
        "lookback_days": lookback_days,
        "probes": [],
        "api_errors": errors,
        "persistence": 0,
    }
    try:
        available, provider_lookup = _news_providers(
            ib, attempts=provider_lookup_attempts,
            retry_seconds=provider_lookup_retry_seconds)
        requested = [str(code) for code in (providers or available)]
        # Explicit diagnostics are allowed to probe a provider that was verified in
        # the same TWS session moments ago, even if a *new* enumeration is briefly
        # empty.  This is diagnostic-only: production discovery still requires the
        # fresh dynamic list, so it cannot silently publish stale provider coverage.
        selected = requested if providers else available
        result["available_providers"] = available
        result["provider_lookup"] = provider_lookup
        result["requested_providers"] = requested
        result["selected_providers"] = selected
        result["selected_but_not_currently_enumerated"] = [
            code for code in selected if code not in available]
        if not selected:
            result["status"] = "provider_not_available_after_retries"
            return result

        from ib_async import Stock

        contract = Stock(requested_symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        con_id = int(getattr(contract, "conId", 0) or 0)
        result["con_id"] = con_id
        if not con_id:
            result["status"] = "contract_not_qualified"
            return result
        start = probe_at - timedelta(days=max(1, int(lookback_days)))
        for provider in selected:
            before_errors = len(errors)
            detail: dict[str, object] = {
                "provider": provider, "start": start.isoformat(), "end": probe_at.isoformat(),
                "total_results": 10,
            }
            try:
                headlines = _historical_news(
                    ib, con_id, provider, start, probe_at, 10, diagnostic=detail)
                detail["headlines"] = None if headlines is None else len(headlines)
                if headlines is None:
                    detail["status"] = str(detail.get("request_failure") or "timeout_without_response")
                elif headlines:
                    detail["status"] = "headlines_received"
                else:
                    detail["status"] = "zero_headlines_completed"
            except Exception as exc:  # noqa: BLE001 - return the diagnostic, not a CLI traceback
                detail["status"] = "request_exception"
                detail["exception"] = f"{type(exc).__name__}:{exc}"
            detail["api_errors"] = errors[before_errors:]
            result["probes"].append(detail)
        statuses = {probe["status"] for probe in result["probes"]}
        result["status"] = (
            "historical_news_available" if statuses & {"headlines_received", "zero_headlines_completed"}
            else "historical_news_provider_not_subscribed"
            if statuses == {"provider_not_subscribed"}
            else "historical_news_no_callback" if statuses == {"timeout_without_response"}
            else "historical_news_failed"
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result["status"] = "diagnostic_failed"
        result["exception"] = f"{type(exc).__name__}:{exc}"
        return result
    finally:
        if error_event is not None:
            try:
                error_event -= on_error
            except Exception:  # noqa: BLE001 - diagnostic cleanup must not mask results
                pass


def discover_with_status(*, pages: int = 3, symbols: list[str] | None = None,
                         providers: list[str] | None = None,
                         lookback_days: int = 7, now: datetime | None = None,
                         provider_lookup_attempts: int = _PROVIDER_LOOKUP_ATTEMPTS,
                         provider_lookup_retry_seconds: float = _PROVIDER_LOOKUP_RETRY_SECONDS,
                         **_) -> tuple[list[ArticleRef], dict]:
    """Headlines for the declared symbols, newest first. Bodies are NOT fetched here.

    `pages` is honoured as "how many lookback slices", so the shared config field keeps
    meaning "how far back to sweep" rather than becoming adapter-specific vocabulary.
    """
    symbols = [s.upper() for s in (symbols or [])]
    if not symbols:
        log.warning("ibkr_news: no symbols declared — nothing to discover")
        return [], {"status": "validation_failed", "error": "symbols_missing"}

    from ib_async import Stock

    ib = _client()
    available_providers, provider_lookup = _news_providers(
        ib, attempts=provider_lookup_attempts, retry_seconds=provider_lookup_retry_seconds)
    requested = [str(code) for code in (providers or available_providers)]
    selected = [code for code in requested if code in available_providers]
    provider_codes = "+".join(selected)
    if not provider_codes:
        log.warning("ibkr_news: no news providers after %s lookup attempt(s)",
                    provider_lookup["attempts"])
        return [], {"status": "provider_unavailable",
                    "error": "news_provider_lookup_empty_after_retries",
                    "requested_providers": requested, "available_providers": available_providers,
                    "provider_lookup": provider_lookup}

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    span = max(lookback_days, pages * _SLICE_DAYS)
    # Pass timezone-aware datetimes to ib_async.  It formats them as the TWS API's
    # documented ``yyyyMMdd HH:mm:ss UTC`` form; a hand-built hyphenated string can
    # be accepted by some gateways but time out silently on others.
    _records.clear()
    by_provider_article: dict[tuple[str, str], str] = {}
    by_title_time: dict[tuple[str, str], str] = {}
    failed_slices: list[dict[str, str]] = []

    for sym in symbols:
        try:
            c = Stock(sym, "SMART", "USD")
            ib.qualifyContracts(c)
            if not c.conId:
                log.warning("ibkr_news: %s did not qualify", sym)
                continue
        except Exception as exc:  # noqa: BLE001 - one bad symbol, not the whole sweep
            log.warning("ibkr_news: %s qualify failed — %s", sym, exc)
            failed_slices.append({"symbol": sym, "stage": "qualify",
                                  "error": f"{type(exc).__name__}:{exc}"})
            continue

        # Newest slice first: if max_per_run bites, it should bite on the OLD end.
        edge = now
        while edge > now - timedelta(days=span):
            start = max(now - timedelta(days=span), edge - timedelta(days=_SLICE_DAYS))
            query_start = start - timedelta(minutes=_OVERLAP_MINUTES)
            request_detail: dict[str, object] = {}
            try:
                heads = _historical_news(
                    ib, c.conId, provider_codes, query_start, edge, _PER_SLICE,
                    diagnostic=request_detail)
            except Exception as exc:  # noqa: BLE001
                log.warning("ibkr_news: %s slice %s failed — %s", sym, start.date(), exc)
                failed_slices.append({"symbol": sym, "stage": "historical_news",
                                      "slice_start": start.isoformat(),
                                      "error": f"{type(exc).__name__}:{exc}"})
                heads = []
            # ib_async translates a Historical News timeout to ``None`` after logging
            # it, whereas a provider with no matching headlines returns an empty list.
            # Treating both as [] made an entitlement/network timeout look like a clean
            # "zero news" result and could publish an unobserved time slice.
            if heads is None:
                failed_slices.append({"symbol": sym, "stage": "historical_news",
                                      "slice_start": start.isoformat(),
                                      "error": str(request_detail.get(
                                          "request_failure",
                                          "empty_response_timeout_or_provider_failure"))})
                heads = []
            for h in heads or ():
                title = _clean_headline(h.headline)
                provider = str(getattr(h, "providerCode", "") or "")
                article_id = str(getattr(h, "articleId", "") or "")
                provider_article = (provider, article_id)
                candidate_slug = _article_slug(provider, article_id)
                # A headline that is only a control code or a wire marker carries no
                # claim-relevant content and would just burn a body request.
                if not candidate_slug or len(title) < 12:
                    continue
                when, exact_time, time_zone = _headline_time(getattr(h, "time", None))
                # TWS can repeat one article across overlapping slices.  It can also
                # surface the same wire story through a second provider/article ID;
                # title+exact-time collapses that case without collapsing a later
                # update that happens to reuse the same title.
                slug = by_provider_article.get(provider_article) or by_title_time.get(
                    (normalised_title(title), exact_time)) or candidate_slug
                record = _records.get(slug)
                if record is None:
                    record = _records[slug] = _NewsRecord(
                        provider=provider, article_id=article_id, title=title,
                        published_at=when, published_at_exact=exact_time,
                        published_at_timezone=time_zone,
                    )
                record.provider_article_ids.add(f"{provider}:{article_id}")
                record.queried_entities.add(sym)
                if title_mentions_entity(title, sym):
                    record.title_verified_entities.add(sym)
                else:
                    record.association_rejected_entities.add(sym)
                by_provider_article[provider_article] = slug
                by_title_time[(normalised_title(title), exact_time)] = slug
            edge = start

    refs = [ArticleRef(
        url=f"{URL_PREFIX}://{record.provider}/{record.article_id}", slug=slug,
        title=record.title,
        published_at=record.published_at.date() if record.published_at else None,
    ) for slug, record in _records.items()]
    refs.sort(key=lambda ref: (
        _records[ref.slug].published_at_exact, ref.slug), reverse=True)
    log.info("ibkr_news: %d distinct headlines across %d symbols over %dd",
             len(refs), len(symbols), span)
    return (refs,
            {"status": "succeeded", "providers": selected,
             "available_providers": available_providers,
             "provider_lookup": provider_lookup,
             "symbols": symbols, "failed_slices": failed_slices,
             "lookback_days": span, "slice_days": _SLICE_DAYS,
             "access": "read_only_tws"})


def discover(*, pages: int = 3, symbols: list[str] | None = None,
             providers: list[str] | None = None, lookback_days: int = 7, now: datetime | None = None,
             **kwargs) -> list[ArticleRef]:
    """Compatibility list API; acceptance callers use detailed state above."""
    return discover_with_status(pages=pages, symbols=symbols, providers=providers, lookback_days=lookback_days,
                                now=now, **kwargs)[0]


def provenance(ref: ArticleRef) -> dict[str, str]:
    record = _records.get(ref.slug)
    if record is not None:
        association = "title_verified" if record.title_verified_entities \
            else "association_rejected"
        return {
            "native_id": f"{record.provider}:{record.article_id}",
            "provider_article_ids": ", ".join(sorted(record.provider_article_ids)),
            "canonical_url": ref.url,
            "provider": record.provider,
            "publisher": _publisher_from_headline(record.title, record.provider),
            "article_id": record.article_id,
            "published_at_exact": record.published_at_exact,
            "published_at_timezone": record.published_at_timezone,
            "queried_entities": ", ".join(sorted(record.queried_entities)),
            "title_verified_entities": ", ".join(sorted(record.title_verified_entities)),
            "association_rejected_entities": ", ".join(
                sorted(record.association_rejected_entities)),
            "entity_association": association,
            "dedup_title": normalised_title(record.title),
            "dedup_time": record.published_at_exact,
        }
    match = re.match(rf"^{URL_PREFIX}://([^/]+)/(.+)$", ref.url or "")
    if not match:
        return {"native_id": ref.slug, "canonical_url": ref.url}
    provider, article_id = match.groups()
    return {"native_id": f"{provider}:{article_id}", "canonical_url": ref.url,
            "provider": provider, "article_id": article_id}


def fetch_body(url: str) -> str:
    """Full article text for one headline. "" when the body is not there (a gap)."""
    m = re.match(rf"^{URL_PREFIX}://([^/]+)/(.+)$", url or "")
    if not m:
        log.warning("ibkr_news: unrecognised url %r", url)
        return ""
    provider, article_id = m.group(1), m.group(2)
    ib = _client()
    for attempt in range(1, _ARTICLE_FETCH_ATTEMPTS + 1):
        try:
            # The provider and article ID come verbatim from HistoricalNews.  Do not
            # infer either from title, URL or ticker: reqNewsArticle requires this pair.
            art = ib.reqNewsArticle(provider, article_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("ibkr_news: article %s/%s attempt %s failed — %s",
                        provider, article_id, attempt, exc)
            art = None
        if getattr(art, "articleType", 0) == 1:
            return ""  # binary/PDF payload: a retry cannot turn it into text.
        text = to_text(getattr(art, "articleText", "") or "")
        if text:
            return text
        if attempt < _ARTICLE_FETCH_ATTEMPTS:
            _sleep_without_blocking(ib, _ARTICLE_FETCH_RETRY_SECONDS)
    return ""
