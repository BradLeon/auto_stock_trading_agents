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
        from ..data.persistent_queue import require_queue_worker

        require_queue_worker(source.id)
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


# --- extraction over already-ingested articles (article-witness-observations, 2026-11-13) --------------------- #
# `collect_articles` above discovers and fetches by itself. After the platform cutover the ingestion half moved to
# `ats data article-ingest` (shared catalog, versions, dedup) and nothing called this module any more, so the three
# publisher witnesses stopped on 2026-08-18/08-30 while their articles kept being ingested. The entry below reads
# ONLY admitted article versions: one network fetch per article (by ingestion), a replayable extraction, and "not
# collected" kept apart from "not extracted".

PREFILTER_CONSUMER = "chain_prefilter"
PREFILTER_VERSION = "witness-scope-v1"
HEAD_CHARS = 3_000
DEFAULT_MAX_REQUESTS = 20


def witness_scope(source: ArticleSourceDef, sector: str = "ai_hardware") -> dict[str, set[str]]:
    """What an article must touch to be worth a model call, derived from the witness declarations.

    The publisher's claims are every claim, in any layer, that names it as a witness. An article is relevant
    when it names one of those claims' companies (layer members, declared entities, expected speakers) or hits
    the source's curated keyword list. Nothing is pinned to a layer: TrendForce witnesses L4–L8 and its scope
    follows; registering a publisher on another layer widens it automatically.
    """
    from ..config import load_sector_config

    publisher = source.entity.upper()
    entities: set[str] = set()
    claims: set[str] = set()
    try:
        cfg = load_sector_config(sector)
    except Exception as exc:  # noqa: BLE001 - without the config there is no scope; read nothing rather than all
        log.warning("articles: sector config unavailable (%s) — %s", sector, exc)
        return {"claims": set(), "entities": set(), "keywords": set(source.match or [])}
    publishers = {s.entity.upper() for s in load_article_sources()}
    for layer in cfg.layers:
        members = {t.symbol.upper() for t in layer.tickers}
        for claim in layer.claims:
            if publisher not in {w.entity.upper() for w in claim.witnesses}:
                continue
            claims.add(claim.id)
            entities |= members | {e.upper() for e in claim.entities} | claim.expected_witnesses()
    entities -= publishers          # other publishers are speakers, never the subject an article is about
    return {"claims": claims, "entities": entities, "keywords": set(source.match or [])}


def scope_fingerprint(scope: dict[str, set[str]]) -> str:
    import hashlib
    import json

    blob = json.dumps({k: sorted(v) for k, v in scope.items()}, sort_keys=True)
    return f"{PREFILTER_VERSION}:{hashlib.sha256(blob.encode()).hexdigest()[:12]}"


def _keyword_hit(text: str, keywords: set[str]) -> bool:
    hay = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    # A plain English plural counts ("chips", "orders"): the curated lists are singular nouns.
    return any(re.search(rf"\b{re.escape(re.sub(r'[^a-z0-9]+', ' ', kw.lower()))}(?:s|es)?\b", hay)
               for kw in keywords)


def is_relevant(title: str, body: str, scope: dict[str, set[str]], *, read_all: bool) -> bool:
    """Deterministic pre-filter. `read_all` (an empty keyword list in config) means every article is read.

    Relevant when the HEADLINE hits a keyword, or when the headline names a company in the witness scope AND the
    opening of the body hits a keyword. Measured 2026-11-13 on IBKR (Dow Jones), 160 articles in one week:
      * a company named anywhere in the text: 160/160 — ingestion already picks issuer-linked stories, so it
        filters nothing;
      * keyword anywhere in the opening: 153/160 — words like "order" and "supply" are everywhere;
      * keyword in the headline only (the August `_wanted` rule): 14/160 — misses "Jensen Huang Affair Revives
        Push for Chip Restrictions", exactly the story the witness exists for;
      * this rule: 115/160 (~16 a day), inside the 20-request cap of a daily run.
    """
    if read_all:
        return True
    from ..config import entity_meta
    from ..data import admission

    if _keyword_hit(title, scope["keywords"]):
        return True
    names_member = False
    for symbol in scope["entities"]:
        try:
            company = str(entity_meta(symbol).get("name") or "")
        except Exception:  # noqa: BLE001 - an unregistered witness is matched by ticker only
            company = ""
        if admission.locator_names_entity(title, symbol, company):
            names_member = True
            break
    return names_member and _keyword_hit(f"{title}\n{(body or '')[:HEAD_CHARS]}", scope["keywords"])


# Extraction "failures" come in two kinds. A call that errored or an empty body is transient and is retried. A
# model that read the article and found nothing a claim can use ("market wrap, no operating figures") has done
# its job: retrying it spends the same request for the same answer. Measured on the first backfill batch
# (2026-11-13): 16 of 40 Dow Jones articles were market wraps answered that way.
_TRANSIENT_FAILURE_MARKERS = ("调用失败", "文档为空", "未取到")


def _transient(note: str) -> bool:
    return any(marker in (note or "") for marker in _TRANSIENT_FAILURE_MARKERS)


def _done_for_latest(store, document_id: str, consumer: str, version: str) -> bool:
    latest = store.latest_document_version(document_id)
    if not latest:
        return True     # nothing admitted to read
    for row in store.document_processing(document_id, consumer=consumer, processor_version=version, limit=20):
        if row.get("version_id") != latest.get("version_id"):
            continue
        if row.get("status") == "succeeded":
            return True
        if row.get("status") == "failed" and not _transient(str(row.get("note") or "")):
            return True     # read, and nothing extractable: not worth a second request
    return False


def observe_ingested_articles(store, *, source_id: str, since: str = "",
                              max_requests: int = DEFAULT_MAX_REQUESTS, dry_run: bool = False,
                              now: datetime | None = None) -> dict:
    """Extract chain observations from admitted articles of one declared publisher witness.

    Newest first. Each admitted version is read at most once per processor version; an article outside the
    witness scope is recorded once as checked-and-irrelevant (per scope fingerprint) and never costs a call.
    At most `max_requests` extraction windows run; the rest is left for the next run and the packet is a
    `partial` naming how many remain. No network access.
    """
    from ..agents.evidence import observer
    from ..data import document_assets

    now = now or datetime.now(timezone.utc)
    source = next((s for s in load_article_sources() if s.id == source_id), None)
    if source is None:
        return {"status": "failed", "reason": "unknown_article_source", "source_id": source_id}
    scope = witness_scope(source)
    if not scope["claims"]:
        return {"status": "no_change", "reason": "publisher_witnesses_no_claim", "source_id": source_id,
                "entity": source.entity}
    fingerprint = scope_fingerprint(scope)
    read_all = not source.match
    rows = store.documents(entity=source.entity, ok_only=True, published_since=since or None, limit=5000)
    rows = [r for r in rows if r.get("doc_type") in {"article", "research_article", source.doc_type}]
    rows.sort(key=lambda r: str(r.get("published_at") or r.get("fetched_at") or ""), reverse=True)

    packet = {"source_id": source_id, "entity": source.entity, "since": since, "dry_run": dry_run,
              "claims": sorted(scope["claims"]), "scope_fingerprint": fingerprint,
              "candidates": len(rows), "already_extracted": 0, "irrelevant": 0, "extracted": 0,
              "observations": 0, "requests": 0, "deferred": 0, "failures": 0, "nothing_extractable": 0,
              "titles": []}
    for row in rows:
        document_id = row["document_id"]
        if _done_for_latest(store, document_id, "chain", PROCESSOR_VERSION):
            packet["already_extracted"] += 1
            continue
        if _done_for_latest(store, document_id, PREFILTER_CONSUMER, fingerprint):
            packet["irrelevant"] += 1
            continue
        body = document_assets.read_document(document_id, store=store)
        title = str(row.get("title") or "")
        if not is_relevant(title, body, scope, read_all=read_all):
            packet["irrelevant"] += 1
            if not dry_run:
                version_id = store.begin_document_processing(document_id, PREFILTER_CONSUMER, fingerprint)
                if version_id:
                    store.finish_document_processing(version_id, PREFILTER_CONSUMER, fingerprint, ok=True,
                                                     outputs=0, note="irrelevant to the witnessed claims")
            continue
        windows = len(observer._windows(body)) if body.strip() else 0
        if not windows:
            packet["failures"] += 1
            continue
        if packet["requests"] and packet["requests"] + windows > max_requests:
            packet["deferred"] += 1
            continue
        if dry_run:
            packet["requests"] += windows
            packet["extracted"] += 1
            packet["titles"].append(title[:120])
            continue
        version_id = store.begin_document_processing(document_id, "chain", PROCESSOR_VERSION,
                                                     retry_failed=True)
        if not version_id:
            packet["already_extracted"] += 1
            continue
        packet["requests"] += windows
        res = observer.observe_document(source.entity, document_id, body,
                                        source_url=str(row.get("source_url") or ""), period="",
                                        store=store, now=now)
        failure = str(res.get("failure") or "")
        failed = bool(failure) and _transient(failure)
        nothing = bool(failure) and not failed
        store.finish_document_processing(version_id, "chain", PROCESSOR_VERSION, ok=not failed,
                                         outputs=res.get("new", 0),
                                         note=(f"no_observations: {failure}" if nothing else failure))
        packet["failures" if failed else "nothing_extractable" if nothing else "extracted"] += 1
        packet["observations"] += res.get("new", 0)
        packet["titles"].append(title[:120])
    if packet["deferred"]:
        packet["status"], packet["reason"] = "partial", (
            f"partial_budget_deferred: {packet['deferred']} relevant articles left for the next run "
            f"(request cap {max_requests})")
    elif packet["failures"] and not (packet["extracted"] or packet["nothing_extractable"]):
        packet["status"], packet["reason"] = "failed", "extraction_failed"
    elif packet["extracted"] or packet["nothing_extractable"]:
        packet["status"], packet["reason"] = "succeeded", ""
    else:
        packet["status"], packet["reason"] = "no_change", "nothing_new_in_scope"
    log.info("articles: %s observe -> %s", source_id, {k: v for k, v in packet.items() if k != "titles"})
    return packet
