"""IBKR news adapter — the parsing rules, hermetically.

The connection itself is not mocked: `discover`/`fetch_body` failing without TWS is
already the designed behaviour (`chain/articles.discover` catches and records the gap),
and a mock of ib_async would only assert that the mock was written correctly. What is
worth pinning down is the text handling, because every one of these was a live defect.
"""

import pytest

from ats.data.articles import ibkr_news


def test_control_prefix_is_stripped_before_the_keyword_filter_sees_it():
    """Headlines arrive as `{A:800015:L:en}Nvidia, Wall Street Firms Set Financing Pact`.

    `chain/articles._wanted` matches on WORD BOUNDARIES over slug+title, so leaving the
    prefix on would not merely look untidy — it would sit between the start of the
    string and the first real word and defeat the match for a source whose whole point
    is being keyword-filtered.
    """
    assert ibkr_news._clean_headline(
        "{A:800015:L:en}Nvidia, Wall Street Firms Set Financing Pact -- WSJ"
    ) == "Nvidia, Wall Street Firms Set Financing Pact -- WSJ"


def test_wire_markers_are_stripped_and_junk_rows_are_droppable():
    """Dow Jones emits continuation lines prefixed `*` and pure-marker rows of `!`."""
    assert ibkr_news._clean_headline("{A:1:L:en}* Etched Raising Funding -- WSJ") \
        == "Etched Raising Funding -- WSJ"
    # Left as an empty string so `discover`'s length guard drops it before it can cost
    # a body request; a bare marker carries nothing a claim could ever use.
    assert ibkr_news._clean_headline("{A:800015,800015:L:en,en}!") == ""


def test_article_ids_become_filesystem_and_document_id_safe_slugs():
    assert ibkr_news._slug("DJ-N$1f249a57") == "DJ-N-1f249a57"
    assert ibkr_news._slug("") == ""


def test_body_is_stripped_of_tags_before_entities_are_unescaped():
    """Order matters. Unescaping first would turn an `&lt;p&gt;` that appears INSIDE the
    reporting into a real tag, which the stripper would then eat along with the words
    between it and the next bracket — silently deleting quoted text."""
    raw = ("<p>&#10;  Apollo, Blackstone and BlackRock committed to the deal, "
           "which Huang called &apos;a new investable asset class&apos;. </p>")
    out = ibkr_news.to_text(raw)
    assert "Apollo, Blackstone and BlackRock committed" in out
    assert "'a new investable asset class'" in out
    assert "<" not in out and "&#10;" not in out


def test_url_round_trips_provider_and_article_id():
    """`fetch_body` gets only the url, so provider+articleId have to survive in it —
    IBKR articles have no web URL to fall back on."""
    ref_url = f"{ibkr_news.URL_PREFIX}://DJ-N/DJ-N$1f249a57"
    import re
    m = re.match(rf"^{ibkr_news.URL_PREFIX}://([^/]+)/(.+)$", ref_url)
    assert m.group(1) == "DJ-N"
    assert m.group(2) == "DJ-N$1f249a57"


def test_unparseable_url_is_a_gap_not_an_exception():
    """A malformed url must return "" so the caller records an unreadable article,
    rather than raising and taking the whole source down with it."""
    assert ibkr_news.fetch_body("https://example.com/whatever") == ""


def test_discover_without_symbols_does_not_open_a_connection():
    """The sweep is per-conId, so an empty symbol list has nothing to ask for. It must
    short-circuit BEFORE connecting — otherwise a misconfigured source opens a TWS
    session (and burns a clientId) to do nothing."""
    assert ibkr_news.discover(symbols=[]) == []
    assert ibkr_news._ib is None


def test_declared_source_is_wired_to_the_adapter_and_a_third_party_stance():
    """The publisher is the SPEAKER, never the subject; `regulator` is the enum's slot
    for "independent of every party" (same reading as TRENDFORCE — there is no `media`).
    """
    from ats.chain.articles import load_article_sources

    src = next((s for s in load_article_sources() if s.id == "ibkr_news"), None)
    assert src is not None, "config/sources.yaml should declare ibkr_news"
    assert src.adapter == "ibkr_news"
    assert src.entity == "DOWJONES"
    assert src.stance == "regulator"
    assert src.params.get("symbols"), "the sweep is per-symbol; an empty list is a no-op"


def test_publisher_is_a_declared_witness_or_everything_lands_unmapped():
    """`observer.concept_menu` is keyed on the SPEAKER, and it only offers dimensions
    from claims that name it. A source that is collected but never declared produces
    rows with an empty `concept` — the exact failure that left ASML at 100% unmapped
    before L6 got claims. Assert the wiring, not the yield.
    """
    from ats.agents.evidence.observer import concept_menu

    _, keys = concept_menu("DOWJONES")
    assert keys, "DOWJONES must be a witness on at least one claim"
    assert "xpu_order_funding_source" in keys
