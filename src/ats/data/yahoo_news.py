"""Day-level Yahoo Finance news backfill from defeatbeta's structured mirror.

The mirror carries full paragraph arrays, unlike search snippets.  This adapter keeps
the provider UUID and original paragraph boundaries, and exposes snapshot age so a
successful query against a stale mirror cannot look healthy.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..schemas.news import NewsItem, NewsParagraph
from .defeatbeta import DefeatBetaConfig, _connect, _uri, dataset_snapshot, load_config

log = logging.getLogger("ats.data.yahoo_news")


@dataclass(frozen=True)
class YahooNewsBatch:
    items: tuple[NewsItem, ...]
    status: str
    snapshot_updated_at: str = ""
    snapshot_lag_hours: float | None = None
    error: str = ""
    discovered: int = 0
    quarantined: int = 0
    reason_codes: tuple[str, ...] = ()


def _status_for_error(exc: Exception) -> str:
    text = str(exc).lower()
    if any(token in text for token in ("401", "403", "unauthorized", "forbidden")):
        return "unauthorized"
    return "unreachable"


def fetch(symbol: str, since: datetime, until: datetime | None = None, *,
          config: DefeatBetaConfig | None = None,
          now: datetime | None = None,
          stale_after_hours: float = 72.0) -> YahooNewsBatch:
    """Return structured rows for one symbol and date range; never raise."""
    from ..config import canonical_entity

    symbol = canonical_entity(symbol).upper()
    return fetch_many(
        [symbol], since, until, config=config, now=now,
        stale_after_hours=stale_after_hours,
    )[symbol]


def _batch_from_rows(rows: list, symbol: str, snapshot,
                     stale_after_hours: float) -> YahooNewsBatch:
    items: list[NewsItem] = []
    reason_codes: list[str] = []
    for uuid, row_symbol, title, publisher, report_date, kind, link, raw_news in rows:
        try:
            published = datetime.fromisoformat(str(report_date)).replace(tzinfo=timezone.utc)
        except ValueError:
            reason_codes.append("report_date_invalid")
            continue
        paragraphs = [
            NewsParagraph(
                paragraph_number=int(row.get("paragraph_number", index)),
                highlight=str(row.get("highlight") or "").strip(),
                paragraph=str(row.get("paragraph") or "").strip(),
            )
            for index, row in enumerate(raw_news or (), start=1)
            if str(row.get("paragraph") or "").strip()
        ]
        if not uuid or not title or not paragraphs:
            if not uuid:
                reason_codes.append("uuid_missing")
            if not title:
                reason_codes.append("title_missing")
            if not paragraphs:
                reason_codes.append("paragraphs_missing")
            continue
        body = "\n\n".join(row.paragraph for row in paragraphs)
        items.append(NewsItem(
            id=f"yahoo:{uuid}", source="yahoo:defeatbeta", headline=str(title),
            summary=body[:800], url=str(link or ""), published_at=published,
            tickers=[str(row_symbol or symbol).upper()], publisher=str(publisher or ""),
            report_date=str(report_date), article_type=str(kind or ""),
            paragraphs=paragraphs, snapshot_updated_at=snapshot.updated_at,
            snapshot_lag_hours=snapshot.lag_hours,
        ))
    status = "zero_matches" if not items else (
        "stale" if snapshot.lag_hours is not None and snapshot.lag_hours > stale_after_hours
        else "succeeded")
    return YahooNewsBatch(
        tuple(items), status, snapshot.updated_at, snapshot.lag_hours,
        discovered=len(rows), quarantined=len(rows) - len(items),
        reason_codes=tuple(reason_codes),
    )


def fetch_many(symbols: list[str], since: datetime,
               until: datetime | None = None, *,
               config: DefeatBetaConfig | None = None,
               now: datetime | None = None,
               stale_after_hours: float = 72.0) -> dict[str, YahooNewsBatch]:
    """One predicate-pushed Parquet query for a whole coverage universe."""
    from ..config import canonical_entity

    config = config or load_config()
    until = (until or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = since.astimezone(timezone.utc)
    requested = list(dict.fromkeys(canonical_entity(symbol).upper() for symbol in symbols))
    if not requested:
        return {}
    placeholders = ",".join("?" for _ in requested)
    sql = (
        "SELECT uuid,symbol,title,publisher,report_date,type,link,news "
        f"FROM read_parquet('{_uri(config.news_uri)}') "
        f"WHERE symbol IN ({placeholders}) "
        "AND try_cast(report_date AS DATE) BETWEEN ? AND ? "
        "ORDER BY try_cast(report_date AS DATE) DESC,uuid"
    )
    try:
        rows = _connect(config.news_uri).execute(
            sql, [*requested, since.date().isoformat(), until.date().isoformat()]).fetchall()
    except Exception as exc:  # noqa: BLE001 - source failure is an explicit batch state
        log.warning("defeatbeta Yahoo News failed for %s: %s", requested, exc)
        failed = YahooNewsBatch((), _status_for_error(exc), error=str(exc))
        return {symbol: failed for symbol in requested}

    snapshot = dataset_snapshot(config, now=now, dataset_file="stock_news.parquet")
    grouped = {symbol: [] for symbol in requested}
    for row in rows:
        grouped.setdefault(str(row[1]).upper(), []).append(row)
    return {symbol: _batch_from_rows(grouped.get(symbol, []), symbol, snapshot,
                                     stale_after_hours)
            for symbol in requested}


def structure_payload(item: NewsItem) -> dict:
    return {
        "uuid": item.id.removeprefix("yahoo:"),
        "publisher": item.publisher,
        "report_date": item.report_date,
        "type": item.article_type,
        "snapshot_updated_at": item.snapshot_updated_at,
        "snapshot_lag_hours": item.snapshot_lag_hours,
        "paragraphs": [row.model_dump() for row in item.paragraphs],
    }


def save_structure(item: NewsItem, document) -> Path | None:
    version_path = getattr(document, "version_path", None)
    if version_path is None:
        return None
    folder = Path(version_path).parent / ".structured"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{Path(version_path).stem}.json"
    if not path.exists():
        path.write_text(json.dumps(structure_payload(item), ensure_ascii=False,
                                   indent=2, sort_keys=True), encoding="utf-8")
    return path


def stored(symbol: str, since: datetime, *, store=None,
           limit: int = 2000) -> list[NewsItem]:
    """Read already-backfilled Yahoo stories; consumers never query Parquet directly."""
    from ..memory import get_store
    from . import document_assets

    store = store or get_store()
    cutoff = since.astimezone(timezone.utc).isoformat()
    rows = store.documents(
        entity=symbol, doc_type="news_item", source_contains="yahoo:defeatbeta",
        published_since=cutoff, limit=limit)
    aliased = store.documents_by_alias_source(
        "yahoo:defeatbeta", entity=symbol, published_since=cutoff, limit=limit)
    by_id = {row["document_id"]: row for row in [*rows, *aliased]}
    out = []
    for row in by_id.values():
        raw = (row.get("published_at") or "").replace("Z", "+00:00")
        try:
            published = datetime.fromisoformat(raw)
        except ValueError:
            continue
        published = published.replace(tzinfo=published.tzinfo or timezone.utc)
        body = document_assets.read_document(row["document_id"], store=store)
        out.append(NewsItem(
            id=f"yahoo-cache:{row.get('external_id') or row['document_id']}",
            source="yahoo:defeatbeta", headline=row.get("title") or "",
            summary=body[:800], url=row.get("source_url") or "",
            published_at=published, tickers=[symbol.upper()],
            report_date=raw[:10],
        ))
    return sorted(out, key=lambda item: item.published_at, reverse=True)


def backfill(symbols: list[str], since: datetime, until: datetime | None = None, *,
             store=None, config: DefeatBetaConfig | None = None,
             now: datetime | None = None,
             stale_after_hours: float = 72.0) -> YahooNewsBatch:
    """Persist one date-bounded daily backfill for a symbol universe."""
    from ..memory import get_store
    from . import news as news_store

    store = store or get_store()
    from ..config import canonical_entity

    unique_symbols = list(dict.fromkeys(canonical_entity(symbol).upper()
                                        for symbol in symbols))
    by_symbol = fetch_many(
        unique_symbols, since, until, config=config, now=now,
        stale_after_hours=stale_after_hours)
    batches = []
    for symbol in unique_symbols:
        source = type("YahooNewsSource", (), {
            "id": f"defeatbeta_yahoo_news:{symbol}", "label": "Yahoo Finance News",
            "adapter": "defeatbeta.stock_news", "cadence": "daily", "entity": symbol,
        })()
        store.register_data_source(source, kind="unstructured")
        run_id = store.begin_ingestion(source.id, kind="unstructured")
        batch = by_symbol[symbol]
        counts = Counter(batch.reason_codes)
        store.finish_ingestion(
            run_id, status=batch.status, discovered=batch.discovered,
            accepted=len(batch.items), quarantined=batch.quarantined,
            reason_codes=dict(counts), snapshot_updated_at=batch.snapshot_updated_at,
            snapshot_lag_hours=batch.snapshot_lag_hours, note=batch.error)
        batches.append(batch)
    items: list[NewsItem] = []
    by_identity: dict[str, NewsItem] = {}
    for batch in batches:
        for item in batch.items:
            identity = news_store.external_id(item)
            if identity in by_identity:
                prior = by_identity[identity]
                by_identity[identity] = prior.model_copy(update={
                    "tickers": list(dict.fromkeys([*prior.tickers, *item.tickers]))})
            else:
                by_identity[identity] = item
    items = list(by_identity.values())
    news_store._catalog(items, store=store)
    statuses = {batch.status for batch in batches}
    status = (
        "unauthorized" if "unauthorized" in statuses else
        "unreachable" if "unreachable" in statuses else
        "stale" if "stale" in statuses else
        "zero_matches" if not items else "succeeded"
    )
    snapshot = next((batch for batch in batches if batch.snapshot_updated_at), None)
    error = "; ".join(batch.error for batch in batches if batch.error)
    return YahooNewsBatch(
        tuple(sorted(items, key=lambda item: item.published_at, reverse=True)), status,
        snapshot.snapshot_updated_at if snapshot else "",
        snapshot.snapshot_lag_hours if snapshot else None,
        error,
        discovered=sum(batch.discovered for batch in batches),
        quarantined=sum(batch.quarantined for batch in batches),
        reason_codes=tuple(code for batch in batches for code in batch.reason_codes),
    )
