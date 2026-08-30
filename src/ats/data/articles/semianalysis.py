"""SemiAnalysis — the subscribed newsletter, bridged onto the evidence chain.

## Why a bridge and not a new fetcher

The articles were already arriving. `data/research.fetch_articles` has been pulling them
(Gmail IMAP + Substack RSS, declared in `config/news_sources.yaml`) and mining them into
`research_insights` — 47 rows across 23 tickers. But `research_insights` has a different
shape entirely: ticker, direction, summary, quote. No `concept`, no `stance`, no
`source_entity`. So no claim could ever reach any of it.

That was a real loss, not a theoretical one. Sitting in that table:

    AMD | "Meta is requesting a custom cut-down version of AMD's MI450X with half the
           compute and HBM, which could significantly reduce AMD's volume..."

That is a `xpu_account_and_customer_mix` reading of exactly the kind the cross-section
starves for — a NAMED customer changing a NAMED program's configuration — and it is
**not self-reported**, which is the property four companies grading themselves can never
supply. `config/sources.yaml` had flagged this connection as a deferred decision; this
module is the decision.

## The two-pipelines question

The same article is now read twice, by two models, for two purposes, and that is the
intended design rather than waste:

  * `research_insights` answers "what does this mean for a ticker I hold" and feeds the
    PEAD monitor's per-ticker context.
  * `evidence_observations` answers "which claim dimension does this fact belong to" and
    feeds corroboration.

They are different questions, and collapsing them would force one output to serve both
badly. Cost is bounded by `max_per_run` like every other source.

Double-counting is not a risk: gate 1 clusters by SPEAKER, so everything SemiAnalysis
publishes about one fact is one evidence cluster no matter how many articles or how many
pipelines carry it.

## Shared document asset

IMAP/RSS acquisition now belongs to `data.research.ingest`. This adapter is a consumer:
discovery reads the catalog and `fetch_body` reads the immutable local asset. It never
touches the mailbox, so PEAD and chain processing can run independently without creating
a second source-specific ingestion path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ...schemas.chain import ArticleRef

log = logging.getLogger("ats.data.articles.semianalysis")

URL_PREFIX = "semianalysis"


def _slug(article_id: str) -> str:
    """Compatibility wrapper around the shared article identity function."""
    from ..research import article_slug

    return article_slug(article_id)


def _stored(*, pages: int, lookback_days: int, source_match: str):
    """Read the shared asset catalog and retain native-mail/RSS provenance."""
    from ..research import stored_articles

    since = datetime.now(timezone.utc) - timedelta(days=max(lookback_days, pages * 10))
    arts = stored_articles(since, source_match=source_match, allow_incomplete=True,
                           consumer="unstructured_source_acceptance")
    if source_match:
        arts = [a for a in arts if source_match.lower() in (a.source or "").lower()]
    return since, arts


def discover_with_status(*, pages: int = 3, lookback_days: int = 30, source_match: str = "",
                         **_) -> tuple[list[ArticleRef], dict]:
    """Newsletter articles already present in the shared document catalog.

    `source_match` narrows to one publisher when the newsletter config carries several
    (`Article.source` is `newsletter:<name>`); empty means take everything declared.
    """
    try:
        since, arts = _stored(pages=pages, lookback_days=lookback_days, source_match=source_match)
    except Exception as exc:  # noqa: BLE001 - caller records the gap
        log.warning("semianalysis: fetch failed — %s", exc)
        return [], {"status": "unreachable", "error": f"{type(exc).__name__}:{exc}",
                    "acquisition": "shared_research_asset_catalog"}

    out = []
    for a in arts:
        if source_match and source_match.lower() not in (a.source or "").lower():
            continue
        slug = _slug(a.id)
        out.append(ArticleRef(url=f"{URL_PREFIX}://{slug}", slug=slug,
                              title=a.title or slug,
                              published_at=a.published_at.date()))
    log.info("semianalysis: %d article candidate(s) since %s", len(out), since.date())
    return out, {"status": "succeeded", "acquisition": "shared_research_asset_catalog",
                 "candidates_in_catalog": len(out), "since": since.isoformat()}


def discover(*, pages: int = 3, lookback_days: int = 30, source_match: str = "",
             **kwargs) -> list[ArticleRef]:
    """Compatibility list API; bodies remain validated by the calling pipeline."""
    return discover_with_status(pages=pages, lookback_days=lookback_days,
                                source_match=source_match, **kwargs)[0]


def provenance(ref: ArticleRef) -> dict[str, str]:
    """Recover native mail/RSS id and canonical post URL from the shared asset."""
    try:
        _since, articles = _stored(pages=1, lookback_days=3650, source_match="SemiAnalysis")
    except Exception:  # noqa: BLE001 - acceptance will record the body/candidate gap
        return {"native_id": ref.slug, "canonical_url": ref.url}
    for article in articles:
        if _slug(article.id) == ref.slug:
            return {
                "native_id": article.id,
                "canonical_url": article.url or ref.url,
                "source_carrier": "imap" if article.id.startswith("imap:") else "rss",
                "message_id": article.message_id or "",
                "uid": str(article.uid or ""),
                "completeness": article.completeness,
            }
    return {"native_id": ref.slug, "canonical_url": ref.url}


def fetch_body(url: str) -> str:
    """Read the shared local body. "" means the catalog/file is inconsistent."""
    from .. import source_cache

    slug = (url or "").split("://", 1)[-1]
    doc = (source_cache.load("SEMIANALYSIS", slug, "research_article", min_chars=1)
           or source_cache.load("SEMIANALYSIS", slug, "article", min_chars=1))
    if doc is None:
        log.warning("semianalysis: shared body missing for %s", url)
        return ""
    return doc.text
