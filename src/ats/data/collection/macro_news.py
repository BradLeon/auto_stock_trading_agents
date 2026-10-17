"""Macro theme news, collected by the data platform and read back as admitted documents.

Until 2026-10-16 the Macro review ran Tavily searches *during analysis* (`macro_inputs.
search_news`), which breaks TARGET §12.1 (an Agent must not call a Provider) and left no
record of what the review had actually been shown. The same theme queries (`config/macro.yaml`
-> `themes[].queries`) now run here, on the refresh controller's schedule; every result that
passes the gates below becomes an immutable document version under the entity
`MACRO_<THEME>` and Macro reads only those (`products.macro_inputs.admitted_news`).

Gates (a result that fails one is counted and dropped, never stored):
  * a URL — it is the document identity, and the dedup key across days and across themes;
  * a body of at least `MIN_CHARS` (a headline or a paywall stub is not evidence);
  * recency — the search is asked for the configured window and an older stamped result is dropped.

What this does NOT claim: the body is the search provider's text extract, not the publisher's
full article, so documents are stored with `completeness="partial"` and the reader shows that
marker. Admission is automatic (no human title/URL review) — the same footing as the search
results the review used to read live, now with versions and lineage.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger("ats.data.collection.macro_news")

SOURCE_ID = "macro_theme_news"
DOC_TYPE = "news"            # legacy catalog value of the `news_item` semantic
MIN_CHARS = 200
STORED_CHARS = 6000


def theme_entity(theme_key: str) -> str:
    """Catalog entity for a theme. No ':' — the entity becomes a folder name in the document cache."""
    return "MACRO_" + "".join(ch if ch.isalnum() else "_" for ch in theme_key).upper()


def _published(value: str, now: datetime) -> tuple[str, bool]:
    """(ISO timestamp, estimated). A result without a usable date is stamped with the collection time."""
    text = (value or "").strip()
    if text:
        for parse in (parsedate_to_datetime, lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))):
            try:
                moment = parse(text)
            except (TypeError, ValueError):
                continue
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return moment.astimezone(timezone.utc).isoformat(timespec="seconds"), False
    return now.isoformat(timespec="seconds"), True


def collect(*, theme_keys=None, dry_run: bool = False, now: datetime | None = None,
            store=None, search=None, config=None) -> dict:
    """Run every theme query once and admit what passes the gates. Returns the result packet."""
    from ...config import load_macro_config
    from .. import document_assets

    now = now or datetime.now(timezone.utc)
    cfg = config or load_macro_config()
    search_cfg = dict(cfg.search or {})
    days = int(search_cfg.get("recency_days", 21))
    per_query = int(search_cfg.get("max_results_per_query", 4))
    if search is None:
        from .. import websearch

        search = websearch.search_news
    if store is None:
        from ..stores.unstructured import get_data_ingestion_store

        store = get_data_ingestion_store()

    cutoff = now - timedelta(days=days)
    themes: dict[str, dict] = {}
    requests = hits = 0
    for theme in cfg.themes:
        if theme_keys and theme.key not in theme_keys:
            continue
        counts = themes.setdefault(theme.key, {"queries": 0, "hits": 0, "new": 0, "seen": 0, "dropped": 0})
        for query in theme.queries:
            counts["queries"] += 1
            requests += 1
            results = search(query, max_results=per_query, days=days, max_chars=STORED_CHARS)
            counts["hits"] += len(results)
            hits += len(results)
            for res in results:
                url = (res.get("url") or "").strip()
                body = (res.get("content") or "").strip()
                title = (res.get("title") or "").strip()
                published, estimated = _published(res.get("published") or "", now)
                if not url or len(body) < MIN_CHARS or (
                        not estimated and datetime.fromisoformat(published) < cutoff):
                    counts["dropped"] += 1
                    continue
                entity = theme_entity(theme.key)
                if store is not None and store.document_by_external_id(url):
                    counts["seen"] += 1
                    continue
                if dry_run:
                    counts["new"] += 1
                    continue
                doc = document_assets.ingest(
                    entity=entity, key=document_assets.stable_key(url, prefix="macro"),
                    doc_type=DOC_TYPE, text=f"{title}\n\n{body}" if title else body,
                    source=SOURCE_ID, source_url=url, external_id=url, title=title,
                    published_at=published, completeness="partial",
                    truncation_reason="search_provider_text_extract", min_chars=MIN_CHARS,
                    note="published date missing; stamped with collection time" if estimated else "",
                    now=now, store=store)
                counts["new" if doc is not None else "dropped"] += 1
    new = sum(c["new"] for c in themes.values())
    packet = {"requests": requests, "hits": hits, "new": new, "dry_run": dry_run, "themes": themes}
    if requests and not hits:
        # Every query came back empty: with a 21-day window that is an outage or a missing
        # key, not "no news". Failing keeps it visible and retried.
        return {**packet, "status": "failed", "reason": "no_search_results"}
    if not requests:
        return {**packet, "status": "no_change", "reason": "no_queries_configured"}
    return {**packet, "status": "succeeded" if new else "no_change",
            "reason": "" if new else "nothing_new"}
