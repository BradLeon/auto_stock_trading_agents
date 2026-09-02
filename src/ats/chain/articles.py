"""Third-party evidence sources — published articles turned into observations.

The prose sibling of `sources.py`, and the split is the point. A statistical series
becomes an Observation by formula, with no model in the loop; an article has to be read
by one. Merging the two would cost `sources.py` the property its own docstring sells.

What an outside publisher adds that no filing can: it covers every side of the same
quarter. The advanced-packaging claims rested almost entirely on TSMC describing its own
back end — an interested party — until four TrendForce articles were fed in by hand and
`advanced_packaging_supply_gap` moved from `mixed (single stance)` to `supportive`. This
module is that hand-feeding, automated.

## The three states a run reports

Same discipline as `sources.collect`, for the same reason:
  * ``N``  — N articles newly read this round.
  * ``0``  — the index was reached and everything on it was already in the ledger. The
    steady state for a weekly job against a daily publisher, and NOT a failure.
  * ``-1`` — discovery itself failed. A gap, recorded, never "the publisher said
    nothing".

## Two things that cost real money to get wrong

**A page whose body cannot be located is a gap, never a fallback to page text.** Feeding
navigation and footer to the extraction model manufactures evidence out of site
furniture. The adapters own this test; see `data/articles/__init__.py` for why every
cheaper way of detecting a paywall was measured and rejected.

**A document is marked as read BEFORE the model runs, not after.**
`store.has_observations_for_document` only becomes true on a successful extraction, and
a failure lands in `evidence_failures` under a different key — so keying dedup on it
alone would re-fetch and re-read a permanently-unextractable article every single week.
Writing `save_document` first makes the marker mean "we already paid for this one",
matching the rule the document cache already follows. Nothing is lost: the body is on
disk, so a re-read is `ats evidence observe <ENTITY> --file <cached path>`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..schemas.chain import ArticleRef, ArticleSourceDef

log = logging.getLogger("ats.chain.articles")
PROCESSOR_VERSION = "chain-observer-v1"


@dataclass
class ArticleRunStat:
    """What one source did this round. A bare count cannot distinguish "nothing new" from
    "the paywall moved", and those need different responses from a human."""

    source_id: str
    scanned: int = 0            # discovered on the index
    matched: int = 0            # survived the keyword filter and the seen-set
    ingested: int = 0           # bodies read and extracted
    observations: int = 0       # new rows in the ledger
    unreadable: int = 0         # body could not be located — paywalled or template drift
    unreachable: bool = False   # discovery itself failed
    titles: list[str] = field(default_factory=list)

    @property
    def code(self) -> int:
        """The -1 / 0 / N sentinel, for callers that only want the headline."""
        return -1 if self.unreachable else self.ingested


def load_article_sources() -> list[ArticleSourceDef]:
    """Read config/data/sources.yaml `article_sources:`. Missing key is fine."""
    from ..config import _config_dir, _load_yaml

    raw = _load_yaml(_config_dir() / "data" / "sources.yaml").get("article_sources", {}) or {}
    out = []
    for sid, body in raw.items():
        try:
            out.append(ArticleSourceDef(id=sid, **(body or {})))
        except Exception as exc:  # noqa: BLE001 - one bad entry must not hide the rest
            log.warning("articles: skipping %r — %s", sid, exc)
    return out


def _adapter(source: ArticleSourceDef):
    import importlib

    return importlib.import_module(f"..data.articles.{source.adapter}", __package__)


def discover(source: ArticleSourceDef) -> list[ArticleRef]:
    """Run the source's discoverer. Returns [] when unavailable — never raises."""
    try:
        mod = _adapter(source)
    except ImportError as exc:
        log.warning("articles: no adapter %r for %s (%s)", source.adapter, source.id, exc)
        return []
    try:
        return list(mod.discover(pages=source.pages, **source.params))
    except Exception as exc:  # noqa: BLE001 - a publisher outage must not break the job
        log.warning("articles: %s discovery failed — %s", source.id, exc)
        return []


def _wanted(ref: ArticleRef, source: ArticleSourceDef) -> bool:
    """Keyword pre-filter on the slug, which carries the headline.

    Cost control, not correctness: `observer.concept_menu` is a closed menu, so an
    off-topic article that slips through lands wholly in the unmapped pool where no
    claim can reach it. An empty `match` therefore means "read everything", not
    "read nothing" — the safe reading, since a miss here loses real evidence.

    Matching is on WORD boundaries, not substrings. Measured on the first live run:
    plain `in` let `ase` (the OSAT) match "b-ase-d" and "b-ase", pulling in a Huawei
    phone piece and a gallium-arsenide solar piece. Three wasted model calls would be
    tolerable on their own — but `max_per_run` had already been reached, so they
    displaced articles that belonged. A loose filter does not just cost money, it
    silently evicts real evidence.
    """
    if not source.match:
        return True
    # Slugs are hyphen-delimited, so normalise both sides to spaces and let \b do the
    # work. Multi-word keys ("sk-hynix") still match; short ones ("ase", "2nm") stop
    # matching inside longer words.
    hay = re.sub(r"[^a-z0-9]+", " ", f"{ref.slug} {ref.title}".lower())
    return any(re.search(rf"\b{re.escape(re.sub(r'[^a-z0-9]+', ' ', kw))}\b", hay)
               for kw in source.match)


def _seen_document_ids(store, entity: str) -> set[str]:
    """Documents this consumer spent work on, plus known-unreadable source failures.

    One query per run rather than one per article, and `ok_only=False` on purpose: a
    known-unreadable article must stay skipped, or the paywall gets re-probed weekly.
    """
    try:
        seen = store.processed_document_ids("chain", PROCESSOR_VERSION, entity=entity)
        rows = store.documents(entity=entity, ok_only=False, limit=5000)
    except Exception as exc:  # noqa: BLE001
        log.warning("articles: could not read document inventory for %s — %s", entity, exc)
        return set()
    # A failed body fetch has no accepted content version, so it cannot have a normal
    # processing row. Keep those ids in the negative cache exactly as before.
    seen |= {r["document_id"] for r in rows if r.get("document_id") and not r.get("ok")}
    return seen


def _related_entities(mod, ref: ArticleRef, source: ArticleSourceDef) -> tuple[str, ...]:
    """Keep a publisher witness while indexing only verified ticker associations.

    Article sources such as IBKR News are configured with a publisher entity
    (``DOWJONES``) because Chain uses it as an independent witness.  That must not
    erase the ticker(s) for which the API returned and title-verified the article:
    PEAD's monitor reads by ticker, whereas Chain reads by witness.  Adapter
    provenance is the only source of such associations; a ticker merely present in
    an API recommendation is deliberately not linked.
    """
    entities = {source.entity.upper()}
    provenance = getattr(mod, "provenance", None)
    if not callable(provenance):
        return tuple(sorted(entities))
    try:
        raw = provenance(ref) or {}
    except Exception as exc:  # noqa: BLE001 - association enrichment is best effort
        log.warning("articles: provenance lookup failed for %s/%s: %s",
                    source.id, ref.slug, exc)
        return tuple(sorted(entities))
    if str(raw.get("entity_association") or "") != "title_verified":
        return tuple(sorted(entities))
    for value in str(raw.get("title_verified_entities") or "").split(","):
        value = value.strip().upper()
        if value:
            entities.add(value)
    return tuple(sorted(entities))


def collect_articles(store, *, source_ids: set[str] | None = None,
                     now: datetime | None = None) -> dict[str, ArticleRunStat]:
    """Discover, filter, fetch and read every declared article source."""
    from ..agents.evidence import observer
    from ..data import document_assets

    now = now or datetime.now(timezone.utc)
    out: dict[str, ArticleRunStat] = {}

    for source in load_article_sources():
        if source_ids and source.id not in source_ids:
            continue
        stat = ArticleRunStat(source_id=source.id)
        out[source.id] = stat

        refs = discover(source)
        if not refs:
            stat.unreachable = True
            try:
                store.save_document_failure(source.entity, "", source.doc_type,
                                            source=source.adapter,
                                            note=f"{source.id} 本轮取不到文章列表")
            except Exception:  # noqa: BLE001
                pass
            log.warning("articles: %s unreachable this round", source.id)
            continue

        stat.scanned = len(refs)
        seen = _seen_document_ids(store, source.entity)
        mod = _adapter(source)

        for ref in refs:
            if stat.ingested >= source.max_per_run:
                break
            if not _wanted(ref, source):
                continue
            document_id = f"{source.entity}:{ref.slug}:{source.doc_type}"
            related_entities = _related_entities(mod, ref, source)
            if document_id in seen:
                # Older IBKR runs retained the publisher witness but predated ticker
                # association provenance.  A fresh, title-verified rediscovery can
                # safely add that missing association without fetching a body or
                # re-running Chain extraction.
                store.link_document_entities(document_id, related_entities)
                continue
            stat.matched += 1

            published = ref.published_at.isoformat() if ref.published_at else ""
            shared = store.document_by_story(ref.title, published)
            if shared is not None and int(shared.get("chars") or 0) >= source.min_body_chars:
                # Yahoo/Finnhub may already hold the same story. Reuse its exact bytes,
                # but retain the IBKR/publisher identity and witness association.
                store.save_document_alias(
                    shared["document_id"], source=source.adapter, source_url=ref.url,
                    external_id=ref.url, title=ref.title, published_at=published)
                store.link_document_entities(shared["document_id"], related_entities)
                body = document_assets.read_document(shared["document_id"], store=store)
                document_id = shared["document_id"]
            else:
                body = ""

            if not body:
                try:
                    body = mod.fetch_body(ref.url)
                except Exception as exc:  # noqa: BLE001 - one bad page, not the whole source
                    log.warning("articles: %s body fetch failed for %s — %s",
                                source.id, ref.slug, exc)
                    body = ""
            if len(body) < source.min_body_chars:
                # Paywalled, or the template moved. Either way it is a GAP: recorded so
                # a widening paywall shows up as missing evidence rather than as the
                # publisher having gone quiet.
                stat.unreadable += 1
                store.save_document_failure(
                    source.entity, ref.slug, source.doc_type, source=source.adapter,
                    source_url=ref.url,
                    note=f"取不到正文（{len(body)} 字符，阈值 {source.min_body_chars}）——付费或模板变更",
                    at=now.isoformat())
                continue

            # Managed sources such as subscribed research were persisted by the shared
            # ingestion stage before this consumer ran. Do not rewrite their catalog
            # metadata; web-only sources still enter the store here.
            if store.latest_document_version(document_id) is None:
                doc = document_assets.ingest(
                    entity=source.entity, key=ref.slug, doc_type=source.doc_type, text=body,
                    source=source.adapter, source_url=ref.url, external_id=ref.url,
                    title=ref.title, published_at=(ref.published_at.isoformat()
                                                   if ref.published_at else ""),
                    now=now, min_chars=1, related_entities=related_entities,
                    store=store,
                )  # source-specific guard already ran above
                if doc is not None:
                    document_id = doc.document_id

            version_id = store.begin_document_processing(
                document_id, "chain", PROCESSOR_VERSION)
            if not version_id:
                # Another run/version already paid for this exact processing step.
                continue

            # period="" on purpose. In observer.extract it is a per-ROW fallback
            # (`period=(v.period or period or "")`), so passing the slug would stamp it
            # onto every observation that did not name its own period.
            res = observer.observe_document(source.entity, document_id, body,
                                            source_url=ref.url, period="", store=store,
                                            now=now)
            stat.ingested += 1
            stat.observations += res.get("new", 0)
            stat.titles.append(ref.title or ref.slug)
            store.finish_document_processing(
                version_id, "chain", PROCESSOR_VERSION,
                ok=not bool(res.get("failure")), outputs=res.get("new", 0),
                note=res.get("failure", ""))
            if res.get("failure"):
                log.info("articles: %s extraction failed for %s — %s",
                         source.id, ref.slug, res["failure"])

        log.info("articles: %s -> scanned %d, matched %d, ingested %d (%d new obs, "
                 "%d unreadable)", source.id, stat.scanned, stat.matched, stat.ingested,
                 stat.observations, stat.unreadable)
    return out


def article_source_entities() -> set[str]:
    """Publisher entities, for callers assembling `rows_by_entity` themselves.

    Unlike `sources.source_entities_for(claim)`, this cannot be narrowed by concept: an
    article source is bound to claims through the sector config's witness tables (which
    is what `observer.concept_menu` reads), not through a `concepts:` list of its own.
    """
    return {s.entity for s in load_article_sources()}
