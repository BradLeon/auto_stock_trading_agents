"""News provider access under the unified namespace."""

from ats.data.news import fetch_news
from ats.data.yahoo_news import YahooNewsBatch, fetch, fetch_many

__all__ = ["YahooNewsBatch", "fetch", "fetch_many", "fetch_news"]
