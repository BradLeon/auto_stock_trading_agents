"""Bounded candidate selection within Information's explicit DOC_DATA scope."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from difflib import SequenceMatcher

from ...schemas.research import Article
from ...data.articles.material_quality import association_evidence, visible_analysis_view
from ...workflow.cutover_routing import RouteUnavailable
from ...workflow.runtime_reads import current_read_context
from . import assemble

PUBLISHERS = {"SEMIANALYSIS": "semianalysis", "TRENDFORCE": "trendforce_news"}


@dataclass(frozen=True)
class Candidate:
    article: Article
    rank: int
    reason: str
    target: str = ""


def target_update_fingerprint(article: Article, symbol: str) -> tuple | None:
    """Exact original article URL plus target paragraphs, never a semantic event key."""
    if article.source != 'ibkr_news' or not symbol:
        return None
    text = visible_analysis_view(article.body, title=article.title, source=article.source).text
    origin = re.search(r'View this article at [^\n]+\s*\n\s*(https://[^\s]+)', text)
    if not origin:
        return None
    paragraphs = tuple(' '.join(p.casefold().split()) for p in re.split(r'\n\s*\n', text)
        if association_evidence({'body': p}, [symbol]))
    return (article.source, origin.group(1).rstrip('.,'), paragraphs) if paragraphs else None


def candidates(store, symbol: str, *, since: datetime, config: dict) -> tuple[list[Candidate], dict]:
    context = current_read_context()
    if context is None or context.identity.consumer_id != "information":
        raise RouteUnavailable("Information candidates require its bound read scope")
    symbol = symbol.upper()
    if context.identity.scope.get("kind") != "entity" or context.identity.scope.get("id") != symbol:
        raise RouteUnavailable("Information output target differs from bound entity request")
    card = f'- {symbol} (target)'
    universe = {symbol}
    authorized = set(context.identity.scope["entities"])
    if symbol not in authorized or not universe <= authorized:
        raise RouteUnavailable("Information target escapes bound scope")
    limit = int(config.get("candidate_limit", 120))
    if not 1 <= limit <= 500:
        raise ValueError("Information candidate limit must be between 1 and 500")
    channels = {"target": {}}
    direct = assemble.admitted_articles(store, since=since, limit=limit,
        entities=[symbol], document_types=None, diagnostics=channels["target"])
    out, empty_views = rank_articles(direct, symbol=symbol, universe=universe, publishers=[])
    return out, {"direct_rows": len(direct), "publisher_rows": 0,
                 "unique_candidates": len(out), "channel_limit": limit,
                 "empty_visible_views": empty_views,
                 "channels": channels,
                 "channel_limit_reached": any(channel.get("limit_reached", False) for channel in channels.values()),
                 "universe_card": card, "universe": sorted(universe)}


def rank_articles(articles: list[Article], *, symbol: str, universe: set[str],
                  publishers: list[str]) -> tuple[list[Candidate], int]:
    """Pure selection rules, shared by authorized runs and read-only rehearsals."""
    unique = {}
    for art in articles:
        if not art.document_version_id:
            raise ValueError("Information candidate has no immutable version")
        unique[art.document_version_id] = art
    out, empty_views = [], 0
    for art in unique.values():
        view = visible_analysis_view(art.body, title=art.title, source=art.source)
        if not view.text:
            empty_views += 1
            continue
        fields = {"title": art.title, "subtitle": art.source_fields.get("subtitle", ""),
                  "lede": art.source_fields.get("lede", ""), "body": view.text}
        evidence = association_evidence(fields, [symbol])
        target = [row for row in evidence if row["entity"] == symbol]
        rank = min(({"title": 0, "subtitle": 1, "lede": 1, "body": 2}[row["field"]]
                    for row in target), default=3)
        if not target:
            continue
        out.append(Candidate(art, rank,
            ("target_title", "target_subtitle_or_lede", "target_visible_body")[rank], symbol))
    out.sort(key=lambda row: (row.rank, -row.article.published_at.timestamp(), row.article.document_version_id))
    return out, empty_views


def choose(rows: list[Candidate], *, max_articles: int | None, exploration_articles: int,
           cached_versions: set[str]) -> list[Candidate]:
    if (max_articles is not None and not 1 <= max_articles <= 500) or exploration_articles < 0 or (
        max_articles is not None and exploration_articles > max_articles):
        raise ValueError("invalid Information article/exploration budget")
    key = lambda row: (row.article.document_version_id in cached_versions, row.rank,
                      -row.article.published_at.timestamp(), row.article.document_version_id)
    # Keep all versions in the caller's pool. Exact repeated visible content needs
    # one reading; near versions are delayed behind other content, not discarded.
    ordered = sorted((row for row in rows if row.rank < 3), key=key)
    seen, update_keys, signatures, diverse, updates = set(), set(), [], [], []
    for row in ordered:
        update_key = target_update_fingerprint(row.article, row.target)
        if update_key is not None:
            if update_key in update_keys:
                continue
            update_keys.add(update_key)
        text = visible_analysis_view(row.article.body, title=row.article.title,
                                     source=row.article.source).text
        if row.article.source == 'ibkr_news':
            # Ignore only the terminal wire timestamp/copyright for comparison.
            # Stored bytes and evidence offsets stay intact.
            text = re.split(r'(?m)^\s*\(END\)\s+Dow Jones Newswires\b', text, maxsplit=1)[0]
        normalized = ' '.join(text.casefold().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        words = re.findall(r'[\w]+', normalized[:40000])
        shingles = {tuple(words[i:i+4]) for i in range(max(0, len(words)-3))}
        title = ' '.join(re.findall(r'[\w]+', row.article.title.casefold()))
        numbers = set(re.findall(r'(?<!\w)\d[\d,.]*%?', normalized))
        similar = len(words) >= 60 and any(
            source == row.article.source and numbers == old_numbers and
            SequenceMatcher(None, title, old_title).ratio() >= .9 and old and shingles and
            len(old & shingles) / len(old | shingles) >= .85
            for source, old, old_title, old_numbers in signatures)
        (updates if similar else diverse).append(row)
        if not similar:
            signatures.append((row.article.source, shingles, title, numbers))
    related = diverse
    # Within each relevance tier, alternate sources before letting one publisher
    # take all slots. No peer or publisher-only candidate is eligible.
    fair_related = []
    for rank in (0, 1, 2):
        tier = [r for r in related if r.rank == rank]
        while tier:
            for source in dict.fromkeys(r.article.source for r in tier):
                row = next(r for r in tier if r.article.source == source)
                fair_related.append(row)
                tier.remove(row)
    chosen = fair_related[:max_articles]
    # Suspected duplicate versions stay in the caller's inventory for later
    # uncached-first runs; never spend on them merely to fill a count budget.
    return chosen
