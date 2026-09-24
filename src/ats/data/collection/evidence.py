"""Evidence source acquisition: keyed transcripts, releases, gathered documents.

Moved verbatim from `agents/evidence/observer.py` (Phase D 8.1) — same behaviour,
same failure modes, same guards. Only the home changed: acquisition is a data-layer
concern, extraction stays with the observer.

The two guards that keep an evidence ledger honest are preserved exactly:

  * `fiscal_label` must reach the search. Without it transcript.fetch falls back
    to a bare "<SYM> latest earnings call transcript" query, which that module
    already measured as returning the wrong quarter — and for thin-coverage
    tickers it can return an entirely different COMPANY (observed: SKHY ->
    Sherwin-Williams, 005930.KS -> Teradyne).
  * the fetched text must then be period-verified. PEAD refuses to score on a
    confirmed mismatch; the observer refuses to extract and falls back to
    filings, because an observation carrying the wrong quarter's numbers is
    worse than none — it is indistinguishable from a real one once in the table.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.data.collection.evidence")


def _mentions_company(text: str, symbol: str, company_name: str = "") -> bool:
    """Does this document actually name the company it is supposed to be from?

    Only the head is searched: an earnings call identifies the issuer in its opening
    lines, whereas a passing mention deep in an unrelated transcript would be a false
    pass. Aliases are stripped to a distinctive token ("SK Hynix" -> "hynix") so that
    "SK hynix Inc." and "SK Hynix" both match.
    """
    from ..admission import mentions_entity

    return mentions_entity(text, symbol, company_name)


# Source hierarchy. A cached document may only pre-empt a fetch when it came from a
# tier we would not improve on; otherwise the cache silently freezes whatever was
# fetched first, which is how web-scraped material outlived the keyed dataset.
#
# Public on purpose (not `_`-prefixed): this classification used to only ever decide
# a caching question, but the viz bundle (agents/sector/viz.py) now surfaces it as a
# tier badge on every source document — "defeatbeta 直取" and "tavily 搜索拼接" carry
# two tiers of trust and the reader has a right to see which one backs a quote. A
# second real consumer means it stops being an implementation detail of this module.
RANK_MANUAL = 3        # a person put this file in 信息源/<SYM>/ — authoritative
RANK_KEYED = 2         # defeatbeta: keyed by ticker, cannot return another company
RANK_PUBLIC = 1        # documents.gather: SEC/IR material, but assembled by search
RANK_SEARCH = 0        # tavily/web: the tier that returned Sherwin-Williams for SKHY


def source_rank(source: str) -> int:
    s = (source or "").lower()
    if not s or s in ("manual", "cache") or s.startswith("file:") or s.startswith("doc:"):
        return RANK_MANUAL
    # Keyed-by-ticker sources, whatever the vendor: the property that matters is that
    # you ask for a symbol and cannot be handed a different company.
    if s.startswith("defeatbeta") or s.startswith("fmp"):
        return RANK_KEYED
    if s.startswith("documents") or s.startswith("sec"):
        return RANK_PUBLIC
    return RANK_SEARCH


def fetch_release(symbol: str, *, report_date: str = "", label: str = "",
                  store=None) -> tuple[str, str, str]:
    """The earnings RELEASE, as a document separate from the call. (text, src, note).

    Separate on purpose, not appended to the transcript: they answer different
    questions and must stay individually traceable. The call is where management
    narrates and takes questions; the release is where the quarter is tabulated. Every
    `reported_actual` observation in the ledger on 2026-08-07 came from a transcript,
    which meant the most checkable facts were being read off spoken, rounded figures
    ("ASP rose by mid 40%") when the exact ones were a filing away.

    Keeping them as two documents also keeps their observation ids distinct and lets
    one fail without taking the other down.
    """
    from .. import document_assets, sec, source_cache

    cached = source_cache.load(symbol, label, "release")
    if cached and source_rank(cached.source) >= RANK_KEYED:
        return cached.text, cached.source, f"缓存命中：{cached.path.name}"

    text, url, note = sec.earnings_release(symbol, near=report_date)
    if not text:
        return "", "", note

    company = ""
    try:
        from ...config import entity_meta, load_pead_config

        company = entity_meta(symbol).get("name", "")
        company = getattr(load_pead_config(symbol), "company_name", "") or company
    except Exception:  # noqa: BLE001
        pass
    # The same identity guard as the transcript path. Deterministic sourcing makes a
    # wrong company far less likely here, but "less likely" is not a reason to remove
    # a check that costs nothing.
    if not _mentions_company(text, symbol, company):
        log.warning("evidence %s: release does not name the company (%s)", symbol, url)
        if store is not None:
            try:
                store.save_document_failure(symbol, label, "release", source="sec",
                                            source_url=url, note="财报稿未提及本公司")
            except Exception:  # noqa: BLE001
                pass
        return "", "", "财报稿未提及本公司 → 记为缺口"

    try:
        document_assets.ingest(
            entity=symbol, key=label, doc_type="release", text=text, source="sec",
            source_url=url, external_id=url, title=f"{symbol} {label} earnings release",
            related_entities=(symbol,), note=note, store=store,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("evidence %s: could not record release (%s)", symbol, exc)
    return text, "sec", note


def fetch_document(symbol: str, *, print_=None, store=None) -> tuple[str, str, str]:
    """Get this company's latest filing text. Returns (text, source, note).

    Cache first (data.source_cache): a hit costs no network at all. This is not only a
    cost measure — re-fetching is not idempotent (the same SKHY query returned
    Sherwin-Williams on one run and the correct call on the next), so without a fixed
    corpus, results measured before and after a prompt change are not comparable.

    A miss fetches, then runs both guards BEFORE writing: a document that fails is never
    cached, and is recorded as an ok=0 row carrying the URL. Retry on a later run is
    still allowed on purpose — a transcript that did not exist yesterday may exist
    today — so what the failure row buys is visibility, not suppression.

    Mirrors the PEAD score path (graph/pead.py) on purpose, because the two failure
    modes it guards are exactly the ones that poison an evidence ledger; see the
    module docstring.
    """
    from ...config import canonical_entity, entity_meta, load_pead_config
    from .. import document_assets, documents, fiscal, period, source_cache, transcript

    symbol = canonical_entity(symbol)
    config_label, company = "", entity_meta(symbol).get("name", "")
    try:
        cfg = load_pead_config(symbol)
        config_label = getattr(cfg, "fiscal_label", "") or ""
        # `or company`, not a bare assignment: observe-list names have no per-ticker
        # config, and the registry name is the only thing the identity guard can use.
        company = getattr(cfg, "company_name", "") or company
    except Exception:  # noqa: BLE001 - a missing per-ticker file must not block
        pass
    # Derive the label from the ACTUAL print rather than trusting a per-ticker file.
    # Observe-list names have no per-ticker config at all, and even a target's can be
    # malformed (SKHY's said "Q FY2026" — no quarter number, so it neither steers the
    # search nor lets the period guard parse a target). Both cases silently degrade to
    # a bare "<SYM> latest earnings call transcript" query, which is how we ended up
    # extracting Sherwin-Williams for SKHY and last quarter's call for MU.
    label = config_label
    try:
        label = period.resolve_fiscal_label(
            symbol, print_, config_label=config_label, store=store)[0] or config_label
    except Exception as exc:  # noqa: BLE001
        log.info("evidence %s: fiscal label unresolved (%s)", symbol, exc)

    # Cache — but the tier order applies to it too. Checking the cache first and
    # returning on any hit inverted the whole source hierarchy: documents scraped off
    # investing.com before the keyed dataset was wired kept winning forever, because a
    # cached copy short-circuits before the dataset is ever asked. Nine of thirteen
    # witnesses were still on web-scraped material for exactly that reason, including
    # SKHY and MU, which the dataset carries.
    #
    # So a cached copy short-circuits only when it came from a source we would not
    # improve on — a person's hand-dropped file, or the keyed dataset. Anything weaker
    # is kept as a fallback and used only if the better tier has nothing.
    # Both doc types, in preference order: a name that never yields a transcript falls
    # back to `release` every run, and checking only "transcript" would mean it never
    # hits cache at all.
    stale_hit = source_cache.load(symbol, label, "release")

    text, src = "", ""
    try:
        text, src = transcript.fetch(symbol, label, company_name=company, store=store)
    except Exception as exc:  # noqa: BLE001
        log.info("evidence %s: transcript unavailable (%s)", symbol, exc)

    def _screen(body: str, source: str, kind: str) -> tuple[str, str]:
        """Both guards. Returns (rejection reason, detail) — ("", why) when it passes.

        Applied to EVERY path, not just the transcript. When the fallback ran unguarded,
        NVDA — which had not yet reported Q2 FY2027 — was served a September 2025
        investor deck and 17 observations from year-old material entered the ledger as
        current testimony. That is worse than the wrong-company failure it sits next to:
        an old NVIDIA deck does name NVIDIA, so the identity guard passes it, and the
        only thing wrong is the date. **Absent a current document the right answer is a
        gap, not the most recent thing findable.**
        """
        # Identity first: it is the failure that actually happened twice (SKHY ->
        # Sherwin-Williams, 005930.KS -> Teradyne), the period guard cannot catch it (a
        # wrong company's filing can carry the right quarter), and for names whose
        # fiscal label will not resolve the period guard never even runs.
        if not _mentions_company(body, symbol, company):
            return f"取回的文档未提及本公司（来源 {source}）", ""
        ok, why = fiscal.verify_transcript(label, body, source)
        return ("" if ok else f"报告期核对未通过（{why}）"), why

    note, rejected, attempted_url = "", "", src
    if text:
        rejected, why = _screen(text, src, "transcript")
        if rejected:
            log.warning("evidence %s: transcript rejected — %s", symbol, rejected)
            text, src, note = "", "", rejected + " → 改用公开文档"
        else:
            note = ("结构化纪要通过统一准入" if src.startswith("defeatbeta") else why)
    if rejected and store is not None:
        # Record the rejection with its URL. It does not block a later retry (the right
        # transcript may simply not be published yet) — it makes "we tried and got the
        # wrong document" visible instead of indistinguishable from "nobody looked".
        try:
            store.save_document_failure(symbol, label, "transcript",
                                        source=attempted_url or "search",
                                        source_url=attempted_url, note=rejected)
        except Exception as exc:  # noqa: BLE001 - bookkeeping must not break the fetch
            log.info("evidence %s: could not record document failure (%s)", symbol, exc)

    doc_type = "transcript"
    if not text:
        # Screen each gathered piece SEPARATELY. `gather` concatenates a web-searched
        # deck with whatever the user hand-dropped, and screening the join let one bad
        # piece condemn the good ones: for Samsung a 2020 Newmont deck sat at the head,
        # so the two authoritative PDFs a person had just placed in 信息源/005930.KS/
        # were thrown away with it. Hand-dropped files are the ONLY source for names the
        # dataset does not carry — they must not be hostage to a search result.
        kept, dropped = [], []
        report_date = str(getattr(print_, "date", "") or "")
        for lab, piece in documents.gather(
                symbol, period=label, report_date=report_date, store=store):
            if not (piece or "").strip():
                continue
            why, _d = _screen(piece, f"documents:{lab}", "release")
            (dropped if why else kept).append((lab, why))
            if not why:
                kept[-1] = (lab, piece)
        for lab, why in dropped:
            log.warning("evidence %s: dropped gathered piece %s — %s", symbol, lab, why)
        src, doc_type = "documents", "release"
        if kept:
            text = "\n\n".join(p for _, p in kept)
            if dropped:
                note = (f"{note + ' / ' if note else ''}"
                        f"已丢弃 {len(dropped)} 份不合格来源，保留 {len(kept)} 份")
        else:
            why = dropped[0][1] if dropped else "无公开文档"
            log.warning("evidence %s: no usable fallback document — %s", symbol, why)
            note = f"{note + ' / ' if note else ''}公开文档亦不合格（{why}）→ 记为缺口"
            if store is not None:
                try:
                    store.save_document_failure(symbol, label, "release",
                                                source="documents", note=why)
                except Exception as exc:  # noqa: BLE001
                    log.info("evidence %s: could not record fallback failure (%s)", symbol, exc)

    if not text.strip() and stale_hit is not None:
        # Everything better came up empty, so the weaker cached copy is what we have.
        # Kept rather than discarded: demoting it below the dataset must not turn a
        # witness we can still read into a gap.
        log.info("evidence %s: falling back to the cached %s copy", symbol, stale_hit.source)
        return (stale_hit.text, stale_hit.source or "cache",
                f"{note + ' / ' if note else ''}回落到旧缓存（来源 {stale_hit.source}）")

    if text.strip() and doc_type != "transcript":
        try:
            document_assets.ingest(
                entity=symbol, key=label, doc_type=doc_type, text=text, source=src,
                source_url=src, external_id=src, title=f"{symbol} {label} {doc_type}",
                related_entities=(symbol,), note=note, store=store,
            )
        except Exception as exc:  # noqa: BLE001
            log.info("evidence %s: could not record document (%s)", symbol, exc)
    return text, src, note


def identify_asset(text: str, *, entity: str, store=None) -> dict | None:
    """Canonical raw-asset catalog entry for this exact body, if one exists.

    Compatibility callers historically invented ids such as ``MU:20260805``. Once the
    exact body is in the shared catalog, facts must point to that immutable asset
    instead so lineage opens the source version rather than a synthetic key.
    """
    from .. import document_assets

    return document_assets.identify(text, entity=entity, store=store)
