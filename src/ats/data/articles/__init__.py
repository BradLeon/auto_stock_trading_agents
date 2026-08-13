"""Adapters for third-party sources that publish PROSE rather than a series.

The sibling of `data/sources/`, and the split is on purpose: a statistical series
becomes an Observation by formula, while an article has to be read by a model. Keeping
them apart is what lets `chain/sources.py` keep claiming its numbers never passed
through a model's judgement.

One module per publisher, each exposing two functions:

    discover(*, pages: int = 3, **params) -> list[ArticleRef]
        What has this publisher put out lately? Cheap — must not fetch article bodies.
        The point is to decide what is worth fetching before paying for it, so return
        enough (url, slug, date, title) for the caller to filter on.

    fetch_body(url: str) -> str
        One article's text. Returns "" when the body cannot be located — see below,
        this is the load-bearing case, not an edge case.

Both may raise; `chain/articles.py` catches and records the gap.

Why body extraction lives HERE and not in the orchestrator: locating the article inside
a page is the only thing that genuinely differs between publishers, so it is exactly the
thing that must not be abstracted. A shared extractor would just grow a branch per site.

## The rule that matters: never return the page when you cannot find the article

Returning "" for a paywalled or unparseable page is not a degraded outcome, it is the
correct one. Feeding a page's navigation and footer to the extraction model as if it
were an article manufactures evidence out of site furniture — far worse than missing
the article, because it is invisible downstream.

Measured on TrendForce (2026-08-13), three plausible ways to detect this all fail:
  * keyword-sniffing for "Subscribe" / "Member Center" — every page matches; the words
    live in the nav bar. 6 of 6 ordinary articles were flagged.
  * thresholding total page length — INVERSELY correlated: the paywalled research page
    is the longest document on the site (32,216 chars of text) and contains no article.
  * using stripped whole-page text — the body is only ~47% of it on a good page and 0%
    on a gated one.

So the test is positive location of the body container, and anything else is a gap. A
new gating scheme the adapter has never seen then fails safe (recorded as unreachable)
instead of failing silently (nav bar ingested as evidence).
"""
