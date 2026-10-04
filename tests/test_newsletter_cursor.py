from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ats.data import research
from ats.memory import get_store


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
SENDER = "semianalysis@substack.com"


def _message(uid: int, *, marker: str = "", long: bool = True) -> bytes:
    body = (f"Deep research paragraph {uid}. " * (120 if long else 5)) + marker
    return (
        f"From: SemiAnalysis <{SENDER}>\r\n"
        "To: reader@example.com\r\n"
        f"Subject: Research {uid}\r\n"
        f"Date: {(NOW - timedelta(days=max(0, 12 - uid))).strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n"
        f"Message-ID: <msg-{uid}@semi.test>\r\n"
        "MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        f'<html><body><article>{body}</article><a href="https://semianalysis.com/p/post-{uid}?utm=mail">View</a></body></html>'
    ).encode()


class FakeIMAP:
    def __init__(self, messages, *, uidvalidity="100", fail_uid=None):
        self.messages = dict(messages)
        self.uidvalidity = uidvalidity
        self.fail_uid = fail_uid
        self.searches = []

    def login(self, *_):
        return "OK", []

    def select(self, *_args, **_kwargs):
        return "OK", [str(len(self.messages)).encode()]

    def response(self, name):
        assert name == "UIDVALIDITY"
        return "UIDVALIDITY", [self.uidvalidity.encode()]

    def uid(self, command, uid, query=None):
        if command == "SEARCH":
            self.searches.append(query)
            return "OK", [b" ".join(str(item).encode() for item in sorted(self.messages))]
        key = int(uid)
        if key == self.fail_uid:
            return "NO", []
        return "OK", [(b"RFC822", self.messages[key])]

    def logout(self):
        return "OK", []


def _configure(monkeypatch, conn):
    secrets = SimpleNamespace(
        gmail_address="reader@example.com", gmail_app_password="secret",
        gmail_imap_host="imap.example.test",
    )
    monkeypatch.setattr("ats.config.get_config", lambda: SimpleNamespace(secrets=secrets))
    monkeypatch.setattr(research, "_imap_connect", lambda _host: conn)
    return {"folder": "INBOX", "overlap_uids": 2,
            "senders": [{"name": "SemiAnalysis", "email": SENDER}]}


def test_first_backfill_persists_all_messages_before_advancing_cursor(monkeypatch):
    store = get_store()
    conn = FakeIMAP({uid: _message(uid) for uid in range(1, 11)})
    cfg = _configure(monkeypatch, conn)
    batch = research._imap_batch(NOW - timedelta(days=30), cfg, store=store)
    monkeypatch.setattr(research, "fetch_batch", lambda *a, **k: batch)

    articles = research.ingest(NOW - timedelta(days=30), store=store)

    assert len(articles) == 10
    assert "SINCE" in conn.searches[0]
    assert len(store.documents(doc_type="research_article")) == 10
    cursor = store.newsletter_cursor("reader@example.com", "INBOX", SENDER)
    assert cursor["last_uid"] == 10 and cursor["uidvalidity"] == "100"


def test_incremental_overlap_is_idempotent_and_collects_every_new_asset(monkeypatch):
    store = get_store()
    store.save_newsletter_cursor(
        mailbox="reader@example.com", folder="INBOX", sender=SENDER,
        uidvalidity="100", last_uid=10, last_message_id="<msg-10@semi.test>",
        watermark=NOW.isoformat(),
    )
    # Two overlap rows plus two new messages. Acquisition is not capped by the agent's
    # max_articles_per_run; immutable storage deduplicates the overlap by Message-ID.
    conn = FakeIMAP({uid: _message(uid) for uid in (9, 10, 11, 12)})
    cfg = _configure(monkeypatch, conn)
    batch = research._imap_batch(NOW - timedelta(days=30), cfg, store=store)
    monkeypatch.setattr(research, "fetch_batch", lambda *a, **k: batch)

    research.ingest(NOW - timedelta(days=30), store=store)

    assert "UID 8:*" in conn.searches[0]
    assert len(store.documents(doc_type="research_article")) == 4
    assert store.newsletter_cursor("reader@example.com", "INBOX", SENDER)["last_uid"] == 12


def test_uidvalidity_change_triggers_controlled_date_backfill(monkeypatch):
    store = get_store()
    store.save_newsletter_cursor(
        mailbox="reader@example.com", folder="INBOX", sender=SENDER,
        uidvalidity="old", last_uid=99, last_message_id="old", watermark=NOW.isoformat())
    conn = FakeIMAP({1: _message(1)}, uidvalidity="new")
    cfg = _configure(monkeypatch, conn)

    batch = research._imap_batch(NOW - timedelta(days=30), cfg, store=store)

    assert "SINCE" in conn.searches[0]
    assert batch.cursor_updates[0].uidvalidity == "new"


def test_fetch_failure_does_not_advance_success_watermark(monkeypatch):
    store = get_store()
    store.save_newsletter_cursor(
        mailbox="reader@example.com", folder="INBOX", sender=SENDER,
        uidvalidity="100", last_uid=10, last_message_id="old", watermark=NOW.isoformat())
    conn = FakeIMAP({11: _message(11), 12: _message(12)}, fail_uid=12)
    cfg = _configure(monkeypatch, conn)
    batch = research._imap_batch(NOW - timedelta(days=30), cfg, store=store)
    monkeypatch.setattr(research, "fetch_batch", lambda *a, **k: batch)

    research.ingest(NOW - timedelta(days=30), store=store)

    assert batch.complete is False
    assert store.newsletter_cursor("reader@example.com", "INBOX", SENDER)["last_uid"] == 10


def test_full_partial_and_teaser_are_explainable_and_incomplete_is_hidden_by_default():
    assert research.classify_completeness("complete analysis " * 200) == ("full", "")
    partial = research.classify_completeness(
        "substantial analysis " * 200 + " Subscribe to unlock the rest")
    teaser = research.classify_completeness("preview only. Subscribe to unlock the rest")
    assert partial == ("partial", "subscribe to unlock")
    assert teaser == ("teaser", "subscribe to unlock")


def test_generic_paid_upgrade_footer_does_not_imply_truncation():
    body = "complete analysis with a proper conclusion " * 200
    body += " Upgrade to paid. A subscription gets you access to the archive."
    assert research.classify_completeness(body) == ("full", "")


def test_source_named_unlock_marker_is_a_strong_truncation_signal():
    body = "substantial analysis " * 200
    body += " Subscribe to SemiAnalysis to unlock the rest. Upgrade to paid."
    assert research.classify_completeness(body) == (
        "partial", "subscribe to unlock the rest")
