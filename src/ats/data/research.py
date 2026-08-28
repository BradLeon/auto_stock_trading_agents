"""Newsletter/research article ingestion — Gmail IMAP + Substack RSS, full text.

High-signal subscribed sources (config/news_sources.yaml `newsletters:`) are read
in full — no ticker-keyword filter. Paid newsletter posts are only complete in
email, hence the IMAP path; the RSS path covers free posts. Each adapter degrades
independently (no creds / dead feed -> skipped, never raises).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..schemas.research import Article
from .base import safe_fetch
from .web import fetch_article_text, strip_html

log = logging.getLogger("ats.data.research")

name = "research"

_MIN_RSS_BODY = 1500     # below this, an RSS body is a paid-post teaser -> fetch the page
_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class CursorUpdate:
    mailbox: str
    folder: str
    sender: str
    uidvalidity: str
    last_uid: int
    last_message_id: str
    watermark: str


@dataclass(frozen=True)
class AcquisitionBatch:
    articles: tuple[Article, ...]
    cursor_updates: tuple[CursorUpdate, ...] = ()
    complete: bool = True


def article_slug(article_id: str) -> str:
    """Stable filesystem identity shared by every consumer of a research article."""
    return re.sub(r"[^A-Za-z0-9]+", "-", article_id or "").strip("-")[:120]


def publisher_entity(source: str) -> str:
    """Canonical publisher id for the shared document catalog."""
    name = (source or "research").split(":", 1)[-1]
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper() or "RESEARCH"


def ingest(since: datetime, *, store=None) -> list[Article]:
    """Fetch once, persist accepted bodies, and return the discovered articles.

    This is the only newsletter function allowed to touch IMAP/RSS. PEAD and chain
    consumers read `stored_articles` instead, so adding a consumer never adds another
    source-specific fetch path.
    """
    from ..memory import get_store
    from . import document_assets

    store = store or get_store()
    batch = fetch_batch(since, store=store)
    articles = list(batch.articles)
    persisted = True
    for art in articles:
        document = document_assets.ingest(
            entity=publisher_entity(art.source), key=article_slug(art.id),
            doc_type="research_article", text=art.body,
            source=art.source, source_url=art.url, external_id=art.id, title=art.title,
            published_at=art.published_at.isoformat(), min_chars=1, store=store,
            completeness=art.completeness, truncation_reason=art.truncation_reason,
            carrier_format="email" if art.mime_source else "html",
            mime_source=art.mime_source)
        persisted = persisted and document is not None
    if batch.complete and persisted:
        for cursor in batch.cursor_updates:
            store.save_newsletter_cursor(**cursor.__dict__)
    return articles


def ingest_configured(*, store=None) -> list[Article]:
    """Run the single configured newsletter acquisition window."""
    from ..config import load_pead_global

    cfg = load_pead_global().get("research", {}) or {}
    since = datetime.now(timezone.utc) - timedelta(
        days=int(cfg.get("backfill_days", cfg.get("lookback_days", 30))))
    return ingest(since, store=store)


def stored_articles(since: datetime, *, source_match: str = "", store=None,
                    limit: int = 500, allow_incomplete: bool = False,
                    consumer: str = "pead_research") -> list[Article]:
    """Read research bodies from the shared document asset store; never uses network."""
    from ..memory import get_store
    from .source_cache import _split_frontmatter

    store = store or get_store()
    # Reading accepted document history is data-layer work.  The PEAD workflow still
    # owns its processing lease and insight/event writes in memory, so route only this
    # immutable input and leave those write contracts untouched until retirement.
    from .products import get_unstructured_read_router

    reader = get_unstructured_read_router(consumer=consumer, legacy_repository=store)
    try:
        rows = reader.documents(doc_type="article", published_since=since.isoformat(),
                                source_contains=source_match or None, limit=limit)
    finally:
        reader.close()
    out: list[Article] = []
    for row in rows:
        completeness = row.get("completeness") or "full"
        if completeness != "full" and not allow_incomplete:
            continue
        external_id = row.get("external_id") or ""
        path = Path(row.get("local_path") or "")
        if not external_id or not path.is_file():
            continue
        try:
            _, body = _split_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
            published = datetime.fromisoformat(row.get("published_at") or "")
        except (OSError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        out.append(Article(id=external_id, source=row.get("source") or "research",
                           title=row.get("title") or "", url=row.get("source_url") or "",
                           body=body, published_at=published,
                           completeness=completeness,
                           truncation_reason=row.get("truncation_reason") or "",
                           mime_source=row.get("mime_source") or ""))
    out.sort(key=lambda a: a.published_at, reverse=True)
    return out


def fetch_batch(since: datetime, *, store=None) -> AcquisitionBatch:
    """Acquire every new asset; downstream processing limits do not apply here."""
    """All newsletter articles since `since`, deduped by id, newest first."""
    from ..config import load_news_sources

    cfg = (load_news_sources() or {}).get("newsletters", {}) or {}
    imap_batch = safe_fetch(
        lambda: _imap_batch(since, cfg.get("imap", {}) or {}, store=store),
        source="research:imap",
    )
    if imap_batch is None:
        imap_batch = AcquisitionBatch((), (), False)
    items: list[Article] = list(imap_batch.articles)
    items += safe_fetch(lambda: _substack_rss(since, cfg.get("research_feeds", []) or []),
                        source="research:rss") or []

    items.sort(key=lambda a: a.published_at, reverse=True)
    seen: set[str] = set()
    out = []
    for a in items:
        if a.id in seen:
            continue
        seen.add(a.id)
        out.append(a)
    return AcquisitionBatch(tuple(out), imap_batch.cursor_updates, imap_batch.complete)


def fetch_articles(since: datetime, *, store=None) -> list[Article]:
    """Compatibility list API around the cursor-aware acquisition batch."""
    return list(fetch_batch(since, store=store).articles)


# --------------------------------------------------------------------------- #
# Gmail IMAP (app password) — paid newsletters arrive complete only in email
# --------------------------------------------------------------------------- #
def _proxy_url() -> str:
    """GMAIL_PROXY secret, else the standard proxy env vars (imaplib ignores them)."""
    import os

    from ..config import get_config

    return (get_config().secrets.gmail_proxy
            or os.environ.get("all_proxy") or os.environ.get("ALL_PROXY")
            or os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or "")


def _imap_connect(host: str):
    """IMAP4_SSL, routed through a local socks5/http proxy when one is configured
    (direct connections to imap.gmail.com:993 are blocked on some networks)."""
    import imaplib

    proxy = _proxy_url()
    if not proxy:
        return imaplib.IMAP4_SSL(host)

    from urllib.parse import urlparse

    import socks  # PySocks

    u = urlparse(proxy)
    ptype = socks.SOCKS5 if u.scheme.startswith("socks") else socks.HTTP
    log.info("research imap: connecting via %s proxy %s:%s", u.scheme, u.hostname, u.port)

    class _ProxyIMAP4SSL(imaplib.IMAP4_SSL):
        def _create_socket(self, timeout):
            sock = socks.create_connection(
                (self.host, self.port), timeout=timeout if timeout else None,
                proxy_type=ptype, proxy_addr=u.hostname, proxy_port=u.port,
                proxy_rdns=True)   # resolve gmail's IP on the proxy side (local DNS may be poisoned)
            return self.ssl_context.wrap_socket(sock, server_hostname=self.host)

    return _ProxyIMAP4SSL(host)


def _imap(since: datetime, cfg: dict, *, store=None) -> list[Article]:
    return list(_imap_batch(since, cfg, store=store).articles)


def _imap_batch(since: datetime, cfg: dict, *, store=None) -> AcquisitionBatch:
    import email
    import email.utils

    from ..config import get_config

    import os

    secrets = get_config().secrets
    senders = list(cfg.get("senders", []) or [])
    test_sender = os.environ.get("ATS_TEST_SENDER")   # verification override (any From)
    if test_sender:
        senders.append({"name": "test-override", "email": test_sender})
    if not (secrets.gmail_address and secrets.gmail_app_password and senders):
        log.info("research imap: no creds or senders configured — skipping")
        return AcquisitionBatch(())

    # IMAP SINCE is date-only (server internal date): search one extra day back
    # and re-filter on the Date header client-side.
    d = since - timedelta(days=1)
    imap_date = f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"

    out: list[Article] = []
    updates: list[CursorUpdate] = []
    complete = True
    conn = _imap_connect(secrets.gmail_imap_host)
    try:
        conn.login(secrets.gmail_address, secrets.gmail_app_password)
        folder = cfg.get("folder", "INBOX")
        conn.select(folder, readonly=True)
        validity_response = conn.response("UIDVALIDITY")
        validity_values = validity_response[1] if validity_response else []
        uidvalidity = str((validity_values or [b""])[0].decode() if isinstance(
            (validity_values or [b""])[0], bytes) else (validity_values or [""])[0])
        mailbox = secrets.gmail_address
        for sender in senders:
            sname, semail = sender.get("name", "?"), sender.get("email", "")
            if not semail:
                continue
            cursor = store.newsletter_cursor(mailbox, folder, semail) if store else None
            if cursor and cursor.get("uidvalidity") == uidvalidity:
                overlap = int(cfg.get("overlap_uids", 20))
                start_uid = max(1, int(cursor.get("last_uid") or 0) - overlap)
                criteria = f'(UID {start_uid}:* FROM "{semail}")'
            else:
                criteria = f'(SINCE "{imap_date}" FROM "{semail}")'
            status, data = conn.uid("SEARCH", None, criteria)
            if status != "OK":
                complete = False
                continue
            uids = (data[0] or b"").split()
            log.info("research imap: %s (%s) -> %d messages", sname, semail, len(uids))
            last_uid, last_mid, watermark = 0, "", ""
            for uid in uids:
                status, msg_data = conn.uid("FETCH", uid, "(RFC822)")
                if status != "OK":
                    complete = False
                    continue
                if not msg_data or not msg_data[0]:
                    complete = False
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                pub = _msg_date(msg)
                if pub is None or pub < since:
                    continue
                subject = _decode_header(msg.get("Subject", ""))
                body, html, mime_source = _extract_body_details(msg)
                if not body:
                    complete = False
                    continue
                mid = (msg.get("Message-ID") or "").strip()
                if not mid:
                    mid = hashlib.sha1(f"{subject}{pub.isoformat()}".encode()).hexdigest()
                completeness, truncation = classify_completeness(body, html)
                uid_int = int(uid.decode() if isinstance(uid, bytes) else uid)
                out.append(Article(
                    id=f"imap:{mid}", source=f"newsletter:{sname}", title=subject,
                    url=_web_link(html), body=body, published_at=pub,
                    completeness=completeness, truncation_reason=truncation,
                    mime_source=mime_source, mailbox=mailbox, folder=folder,
                    sender=semail, uidvalidity=uidvalidity, uid=uid_int,
                    message_id=mid))
                if uid_int >= last_uid:
                    last_uid, last_mid, watermark = uid_int, mid, pub.isoformat()
            if last_uid:
                updates.append(CursorUpdate(
                    mailbox, folder, semail, uidvalidity, last_uid, last_mid, watermark))
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    return AcquisitionBatch(tuple(out), tuple(updates), complete)


def _msg_date(msg) -> datetime | None:
    import email.utils

    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date", ""))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _decode_header(raw: str) -> str:
    import email.header

    parts = []
    for chunk, charset in email.header.decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return " ".join("".join(parts).split())   # collapse header folding whitespace


def _extract_body_details(msg) -> tuple[str, str, str]:
    """Walk MIME parts; prefer HTML and retain the chosen MIME provenance."""
    html, plain = "", ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/html" and not html:
            html = text
        elif ctype == "text/plain" and not plain:
            plain = text
    if html:
        return strip_html(html), html, "text/html"
    return re.sub(r"\s+", " ", plain).strip(), "", "text/plain" if plain else ""


def _extract_body(msg) -> tuple[str, str]:
    """Compatibility wrapper returning (text, raw_html)."""
    text, html, _mime = _extract_body_details(msg)
    return text, html


_STRONG_TRUNCATION_PATTERNS = (
    (re.compile(r"subscribe to [^.\n]{0,80}?\s+to unlock the rest", re.I),
     "subscribe to unlock the rest"),
    (re.compile(r"subscribe to unlock(?: the rest)?", re.I), "subscribe to unlock"),
    (re.compile(r"continue reading by subscribing", re.I),
     "continue reading by subscribing"),
    (re.compile(r"this post is for paid subscribers", re.I),
     "this post is for paid subscribers"),
    (re.compile(r"read the full post", re.I), "read the full post"),
)


def classify_completeness(body: str, raw_html: str = "") -> tuple[str, str]:
    """Explain whether a newsletter body is full, partial, or only a teaser."""
    text = re.sub(r"\s+", " ", body or "").strip()
    searchable = f"{text}\n{strip_html(raw_html) if raw_html else ''}"
    reason = next(
        (reason for pattern, reason in _STRONG_TRUNCATION_PATTERNS
         if pattern.search(searchable)),
        "",
    )
    if not reason:
        return "full", ""
    status = "teaser" if len(text) < 2000 else "partial"
    return status, reason


def _web_link(html: str) -> str:
    """Best-effort canonical post URL from the email ('View in browser' link)."""
    if not html:
        return ""
    m = re.search(r'href="(https://[^"]+/p/[^"?]+)', html)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- #
# Substack RSS — free posts embed the full body; teasers get a page fetch
# --------------------------------------------------------------------------- #
def _substack_rss(since: datetime, feeds: list[dict]) -> list[Article]:
    import feedparser

    out: list[Article] = []
    for feed in feeds:
        fname, furl = feed.get("name", "?"), feed.get("url", "")
        if not furl:
            continue
        parsed = feedparser.parse(furl)
        for e in parsed.entries:
            pub = _entry_dt(e)
            if pub and pub < since:
                continue
            body = ""
            content = e.get("content") or []
            if content:
                body = strip_html(content[0].get("value", ""))
            if len(body) < _MIN_RSS_BODY and e.get("link"):
                fetched = fetch_article_text(e["link"])
                if len(fetched) > len(body):
                    body = fetched
            if not body:
                continue
            completeness, truncation = classify_completeness(body)
            out.append(Article(
                id=f"substack:{e.get('id') or e.get('link', e.get('title', ''))}",
                source=f"substack:{fname}", title=e.get("title", ""),
                url=e.get("link", ""), body=body,
                published_at=pub or datetime.now(timezone.utc),
                completeness=completeness, truncation_reason=truncation))
    return out


def _entry_dt(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None
