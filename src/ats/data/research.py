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
from datetime import datetime, timedelta, timezone

from ..schemas.research import Article
from .base import safe_fetch
from .web import fetch_article_text, strip_html

log = logging.getLogger("ats.data.research")

name = "research"

_MIN_RSS_BODY = 1500     # below this, an RSS body is a paid-post teaser -> fetch the page
_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fetch_articles(since: datetime) -> list[Article]:
    """All newsletter articles since `since`, deduped by id, newest first."""
    from ..config import load_news_sources

    cfg = (load_news_sources() or {}).get("newsletters", {}) or {}
    items: list[Article] = []
    items += safe_fetch(lambda: _imap(since, cfg.get("imap", {}) or {}),
                        source="research:imap") or []
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
    return out


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


def _imap(since: datetime, cfg: dict) -> list[Article]:
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
        return []

    # IMAP SINCE is date-only (server internal date): search one extra day back
    # and re-filter on the Date header client-side.
    d = since - timedelta(days=1)
    imap_date = f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"

    out: list[Article] = []
    conn = _imap_connect(secrets.gmail_imap_host)
    try:
        conn.login(secrets.gmail_address, secrets.gmail_app_password)
        conn.select(cfg.get("folder", "INBOX"), readonly=True)
        for sender in senders:
            sname, semail = sender.get("name", "?"), sender.get("email", "")
            if not semail:
                continue
            _, data = conn.uid("SEARCH", None, f'(SINCE "{imap_date}" FROM "{semail}")')
            uids = (data[0] or b"").split()
            log.info("research imap: %s (%s) -> %d messages", sname, semail, len(uids))
            for uid in uids:
                _, msg_data = conn.uid("FETCH", uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                pub = _msg_date(msg)
                if pub is None or pub < since:
                    continue
                subject = _decode_header(msg.get("Subject", ""))
                body, html = _extract_body(msg)
                if not body:
                    continue
                mid = (msg.get("Message-ID") or "").strip()
                if not mid:
                    mid = hashlib.sha1(f"{subject}{pub.isoformat()}".encode()).hexdigest()
                out.append(Article(
                    id=f"imap:{mid}", source=f"newsletter:{sname}", title=subject,
                    url=_web_link(html), body=body, published_at=pub))
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


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


def _extract_body(msg) -> tuple[str, str]:
    """Walk MIME parts; prefer text/html (stripped). Returns (text, raw_html)."""
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
        return strip_html(html), html
    return re.sub(r"\s+", " ", plain).strip(), ""


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
            out.append(Article(
                id=f"substack:{e.get('id') or e.get('link', e.get('title', ''))}",
                source=f"substack:{fname}", title=e.get("title", ""),
                url=e.get("link", ""), body=body,
                published_at=pub or datetime.now(timezone.utc)))
    return out


def _entry_dt(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None
