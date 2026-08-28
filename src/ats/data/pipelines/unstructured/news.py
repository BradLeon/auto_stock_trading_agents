"""Scheduled news-ingestion pipeline compatibility surface."""

from ats.data.yahoo_news import YahooNewsBatch


def backfill(*args, **kwargs):
    """Forward at call time while Yahoo's persistence implementation is retained."""
    from ats.data import yahoo_news

    return yahoo_news.backfill(*args, **kwargs)


__all__ = ["YahooNewsBatch", "backfill"]
