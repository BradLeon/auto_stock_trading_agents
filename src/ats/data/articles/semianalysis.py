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

## Discovery carries the body

Unlike a web publisher, there is no URL to re-fetch — IMAP messages are pulled once and
the body arrives with them. So `discover` stashes bodies for the `fetch_body` calls that
follow in the same run. That is a deliberate departure from the "discovery must be
cheap" rule in `data/articles/__init__.py`: here the expensive part (IMAP round trip)
cannot be split from discovery, and paying it twice would be strictly worse.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from ...schemas.chain import ArticleRef

log = logging.getLogger("ats.data.articles.semianalysis")

URL_PREFIX = "semianalysis"
_BODIES: dict[str, str] = {}         # slug -> body, populated by discover(), one run


def _slug(article_id: str) -> str:
    """Message-Ids are full of `<>@.` — flatten to something a filename can hold."""
    return re.sub(r"[^A-Za-z0-9]+", "-", article_id or "").strip("-")[:120]


def discover(*, pages: int = 3, lookback_days: int = 30, source_match: str = "",
             **_) -> list[ArticleRef]:
    """Newsletter articles in the window. Bodies are stashed for `fetch_body`.

    `source_match` narrows to one publisher when the newsletter config carries several
    (`Article.source` is `newsletter:<name>`); empty means take everything declared.
    """
    from ..research import fetch_articles

    since = datetime.now(timezone.utc) - timedelta(days=max(lookback_days, pages * 10))
    try:
        arts = fetch_articles(since)
    except Exception as exc:  # noqa: BLE001 - caller records the gap
        log.warning("semianalysis: fetch failed — %s", exc)
        return []

    _BODIES.clear()
    out = []
    for a in arts:
        if source_match and source_match.lower() not in (a.source or "").lower():
            continue
        if not (a.body or "").strip():
            continue                  # header-only mail: a gap, not an article
        slug = _slug(a.id)
        _BODIES[slug] = a.body
        out.append(ArticleRef(url=f"{URL_PREFIX}://{slug}", slug=slug,
                              title=a.title or slug,
                              published_at=a.published_at.date()))
    log.info("semianalysis: %d article(s) with bodies since %s", len(out), since.date())
    return out


def fetch_body(url: str) -> str:
    """The body `discover` already pulled. "" when it is not there — recorded as a gap."""
    slug = (url or "").split("://", 1)[-1]
    body = _BODIES.get(slug, "")
    if not body:
        log.warning("semianalysis: no body cached for %s (discover must run first)", url)
    return body
