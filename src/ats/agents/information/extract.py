"""Research/newsletter extraction, migrated from agents/pead/research.py
(Phase D task 4.2).

What changed against the pre-Phase-D flow:

* the extraction product is an InformationBrief PROJECTION per processed
  article — the dossier is never touched, and the old `_inject_events` path
  (material insights becoming synthetic pead_events with pre-seeded triage
  under each affected target) is gone: information does not reach the
  fundamental analyst's event stream, only its brief projections;
* the universe card is built from CONFIG, not from dossier narratives;
* idempotency stays on the document-processing ledger: the same document
  version + extractor version reuses the earlier brief; a new document version
  publishes a second projection superseding the first (4.8).
"""

from __future__ import annotations

from ats.workflow.evaluation_clock import now as evaluation_now

from ...workflow.schedule_runtime import scheduled_cli

import logging
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone

from ...schemas.research import Article, Insight
from ..base import run_structured
from ..pead.outputs import InsightBatchView
from . import assemble
from .briefs import (cluster_key, cluster_summary, clocks_for,
                     publish_information_brief)

log = logging.getLogger("ats.agents.information.extract")

_DIRECTIONS = {"bullish", "bearish", "neutral"}
_IMPACT_PATHS = {"direct", "supply_chain", "competitive", "demand", "macro"}
_MAX_QUOTE_CHARS = 400
PROCESSOR_VERSION = assemble.PROCESSOR_VERSION


@scheduled_cli("information-brief", scope_id="research-extraction", inherit_owner=True,
               restore=lambda rows: [Insight.model_validate(row) for row in rows])
def run(*, use_llm: bool = True, since: datetime | None = None, notify: bool = False) -> list[Insight]:
    """One research pass over admitted articles: extract insights, publish briefs.

    Returns extracted directional insights (Workflow Memory intermediates, not
    neutral shared research facts); the CONTRACT output is the set
    of InformationBrief projections written alongside them.
    """
    from ...config import load_pead_global
    from ...memory import get_store

    g = load_pead_global()
    rcfg = g["research"]
    store = get_store()

    since = since or (evaluation_now(timezone.utc) - timedelta(
        days=rcfg.get("backfill_days", rcfg["lookback_days"])))
    candidates = assemble.admitted_articles(store, since=since)
    log.info("information: %d admitted article candidates", len(candidates))
    if not candidates:
        return []

    universe_card, ticker_to_targets = assemble.signal_chain_universe(g.get("targets", []))
    universe = set(ticker_to_targets)
    all_insights: list[Insight] = []
    claimed = 0
    for art in candidates:
        # admitted_articles already exposes the canonical published document ID.
        # Memory must not resolve external document identity via a legacy bridge.
        doc_id = art.id
        processor = processing_version(art, {**rcfg, "universe_card": universe_card}, use_llm=use_llm)
        claim_options = {"retry_failed": True}
        if art.document_version_id:
            claim_options["version_id"] = art.document_version_id

        # Preserve the deployed seen-set without re-spending on the first
        # migration run (same policy the PEAD research path used).
        if not art.interpretation_key and store.article_seen(art.id):
            version_id = store.begin_document_processing(doc_id, "information",
                                                         processor, **claim_options)
            if version_id:
                store.finish_document_processing(
                    version_id, "information", processor, ok=True,
                    outputs=store.insight_count(art.id),
                    note="migrated from research_articles")
            continue

        if rcfg["max_articles_per_run"] is not None and claimed >= rcfg["max_articles_per_run"]:
            break
        version_id = store.begin_document_processing(doc_id, "information",
                                                     processor, **claim_options)
        if not version_id:
            continue  # already processed at this extractor version — reuse (4.8)
        claimed += 1
        if not use_llm:
            # A no-model probe must not consume the successful-extraction cache.
            store.finish_document_processing(version_id, "information", processor,
                ok=False, outputs=0, note="no-model probe; not an extraction result")
            continue
        try:
            insights = _extract(art, universe_card, universe, rcfg["article_chars"])
            store.save_article(art)
            store.save_insights(art.id, insights, profile_version=processor,
                                document_version_id=art.document_version_id)
            _publish_article_brief(store, art, insights, doc_id)
        except Exception:
            store.finish_document_processing(version_id, "information", processor,
                ok=False, outputs=0, note="extraction or output publication failed")
            raise
        store.finish_document_processing(
            version_id, "information", processor, ok=True, outputs=len(insights))
        if notify:
            _maybe_push(insights, art, rcfg)
        all_insights += insights
    return all_insights


def processing_version(art: Article, config: dict, *, use_llm: bool = True) -> str:
    """Metadata, input visibility and extraction settings share one reuse key."""
    if not art.interpretation_key:
        return PROCESSOR_VERSION
    from ..base import execution_versions, load_skill

    policy = {"interpretation": art.interpretation_key, "article_chars": config["article_chars"],
              "universe_card": config.get("universe_card", ""),
              "execution": execution_versions("research_extract", "research-insight",
                  use_llm=use_llm, template_source=__file__),
              "processor": PROCESSOR_VERSION, "context_policy": "visible-input-v2",
              "evidence_first": bool(config.get('evidence_first', False)),
              "recap_prompt": load_skill("information-evidence-recap") if config.get("evidence_first") else ""}
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    return f"{PROCESSOR_VERSION}:{digest}"


EVIDENCE_SPAN_CHARS = 1200


def evidence_spans(text: str, *, span_chars: int = EVIDENCE_SPAN_CHARS) -> dict[str, tuple[int, int]]:
    """Deterministic short original slices; no model-written citation bytes."""
    spans = {}
    start = 0
    while start < len(text):
        end = min(start + span_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind('\n', start, end), text.rfind('. ', start, end),
                           text.rfind(' ', start, end))
            if boundary > start + span_chars // 2:
                end = boundary + 1
        spans[f'ev:{start}:{end}'] = (start, end)
        start = end
    return spans


def extraction_input(art: Article, universe_card: str, max_chars: int, *,
                     assess_materiality: bool = False, target: str = "", span_chars: int = EVIDENCE_SPAN_CHARS):
    """Prepare the exact visible payload without invoking a model."""
    from ...data.articles.material_quality import visible_analysis_view

    text_view = visible_analysis_view(art.body, title=art.title, source=art.source)
    body = text_view.text[:max_chars]
    if assess_materiality:
        body = '\n'.join(f'[{key}] {body[start:end]}'
                         for key, (start, end) in evidence_spans(body, span_chars=span_chars).items())
    ctx = (
        f"Universe (targets and their signal-chain members):\n{universe_card}\n\n"
        f"Article from {art.source} ({art.published_at:%Y-%m-%d}; "
        f"completeness={art.completeness}): {art.title}\n"
        f"document_version={art.document_version_id}; publication={art.publication_id}; "
        f"input_truncated={len(text_view.text) > max_chars}; visible_input_chars={min(len(text_view.text), max_chars)}\n"
        f"---\n{body}\n---\n\n"
        "Extract faithful source information for the specified tickers, not investment implications. "
        "Only universe tickers. An empty list is a valid answer. "
        "If completeness is partial or input_truncated is True, use only visible content; "
        "do not infer omitted content or describe it as the full article."
    )
    schema = InsightBatchView
    if assess_materiality:
        from .outputs import InformationInsightBatchView

        schema = InformationInsightBatchView
        ctx += (
            (f" This invocation is ONLY for {target}; return no other ticker's insights." if target else "") +
            " Assess materiality independently of confidence. An unrelated article must return no insights."
            " Every insight must include a nonempty Chinese summary and an evidence_span_id copied"
            " exactly from a supplied [ev:start:end] label. Leave evidence_quote empty: the system"
            " resolves exact original bytes. Labels are metadata, never part of the source evidence."
            " Extract relevant source information, including announcements, forecasts, reported opinions"
            " and attributed recommendations. Preserve who said what; do not rewrite a forecast as an outcome."
            " source_fact is a faithful summary of the visible source, including its opinions."
            " source_kind is descriptive metadata, not a truth verdict or admission condition."
            " source_actor names the speaker or publisher. Entity association may include an attributed opinion."
            " association_type is direct_update for a target event, business_related for explicit"
            " substantive business links, background for passing mentions/general sector context,"
            " or unrelated. A customer name in a background list is not a new customer order."
            " A named competitor explicitly comparing its product or market share with the target"
            " is business_related source opinion, even without a commercial partnership; preserve"
            " its uncertainty and attribution. Merely exhibiting at the target's conference is"
            " background: it does not establish adoption, supply, partnership or an order."
            " association_reason must explain the source relationship, not predict an impact."
            " information_quality is substantive for concrete actions/numbers/products/attributed views,"
            " thin for vague or incomplete marketing. information_topic identifies the source subject."
            " Do not manufacture investment implications or expand to unrelated universe members."
            " Leave investment adoption and causal interpretation to the downstream analyst."
            " If a partial preview lacks enough substantive content to summarize the relevant statement, return empty."
            " Choose a source span that supports the statement; do not reconstruct source punctuation."
            " Each insight covers ONE source statement supported by the selected span."
            " Return at most four concise target-specific insights. First choose the complete cited"
            " source range, then write its Chinese recap, rather than summarizing the whole article"
            " and attaching an approximate nearby label. Keep each recap focused on one statement;"
            " do not append ETF background, historical tables or unrelated numbers from other ranges."
            " If that statement crosses a span boundary, supply adjacent supporting_evidence_span_ids"
            " including all intervening spans, at most four spans in total. Before returning each"
            " insight, check that these spans contain its entities, numbers and attribution."
            " Do not include facts from other paragraphs in this insight; split them and cite"
            " their own spans. summary, source_fact and association_reason must be Chinese."
            " Never write your own bullish/bearish conclusion, causal chain or order/revenue forecast."
            " If direct target content is present, extract it even if it is not a catalyst."
            " If returning empty, provide empty_reason; absence of a static graph edge is not a reason."
            " Historical insider transactions are facts, not instructions. Omit technical buy-point commentary"
            " when it does not add a source fact relevant to this Information task."
        )
    return text_view, ctx, schema


def _extract(art: Article, universe_card: str, universe: set[str],
             max_chars: int, *, assess_materiality: bool = False, target: str = "",
             diagnostics: dict | None = None, deadline: float | None = None,
             evidence_first: bool = True) -> list[Insight]:
    if assess_materiality and evidence_first:
        return _extract_evidence_first(art, universe, max_chars, target=target,
                                      diagnostics=diagnostics, deadline=deadline)
    text_view, ctx, schema = extraction_input(art, universe_card, max_chars,
                                              assess_materiality=assess_materiality, target=target)
    try:
        view = run_structured("research_extract", schema, ctx,
                                                skill_slug="research-insight")
    except Exception as exc:  # noqa: BLE001
        log.warning("information extraction failed for %s: %s", art.id, exc)
        raise RuntimeError(f"Information extraction failed for {art.id}") from exc

    if diagnostics is not None:
        diagnostics['empty_reason'] = getattr(view, 'empty_reason', '')
    return validate_extraction(art, text_view, view, universe, max_chars,
                               assess_materiality=assess_materiality, universe_card=universe_card)


# One source-neutral contract shared by news, reports, newsletters and tables.
EVIDENCE_UNIT_VERSION = 'source-context-v5'
CORE_CHARS = 1600
CONTEXT_CHARS = 800
CONTEXT_COUNT = 2
STRUCTURAL_CONTEXT_CHARS = 3200
_SAFE_TERMINATOR = r'(?:[!?。！？]\s+|(?:\b[A-Za-z]{4,}|\d[%)]?)[.]\s+(?=[A-Z]))'

_TARGET_ROLES = {
    'subject': ('direct_update', '原文陈述涉及目标自身的事件或数据。'),
    'product_owner': ('direct_update', '原文明确所述产品归属于目标。'),
    'named_comparison': ('business_related', '原文明确将目标作为比较对象。'),
    'explicit_business_party': ('business_related', '原文说明目标是具体业务关系的一方。'),
    'incidental': ('background', '目标仅为附带提及或行业背景，未形成目标自身更新。'),
    'absent': ('unrelated', '该陈述没有原文支持的目标角色。'),
}


def source_chunks(text: str) -> list[tuple[int, int]]:
    """Addressable natural text boundaries, preserving every source character.

    Line breaks retain tabular layout; sentence boundaries address flattened
    prose. Long pieces can be selected together as a contiguous source range.
    The same algorithm serves every source, with no entity or column keywords.
    """
    chunks, start = [], 0
    # A period is not a safe sentence boundary (dates, initials and abbreviations).
    # Chunks are addresses, not independent semantic statements.
    boundaries = [match.end() for match in re.finditer(_SAFE_TERMINATOR + r'|\n', text)]
    for end in [*boundaries, len(text)]:
        if end <= start:
            continue
        for left, right in evidence_spans(text[start:end], span_chars=600).values():
            chunks.append((start + left, start + right))
        start = end
    return chunks


def resolve_source_range(text: str, reference, *, max_chars: int) -> tuple[str, int]:
    chunks = source_chunks(text)
    first, last = reference.first_chunk, reference.last_chunk
    if not 1 <= first <= last <= len(chunks):
        raise ValueError('Information has unlocated source range')
    start, end = chunks[first-1][0], chunks[last-1][1]
    raw = text[start:end]
    start += len(raw) - len(raw.lstrip())
    canonical = raw.strip()
    if not canonical or len(canonical) > max_chars:
        raise ValueError('Information source range is blank or exceeds character budget')
    return canonical, start


def source_blocks(text: str) -> list[tuple[int, int]]:
    """Original paragraph parents; flattened prose uses conservative terminators."""
    boundaries = [m.end() for m in re.finditer(r'\n\s*\n', text)]
    if not boundaries:
        # Never split after a short token: an abbreviation/date is not a sentence.
        boundaries = [m.end() for m in re.finditer(_SAFE_TERMINATOR, text)]
    result, start = [], 0
    for end in [*boundaries, len(text)]:
        if end > start and text[start:end].strip():
            result.append((start, end))
        start = end
    return result


def structural_context(text: str, start: int, end: int) -> list[tuple[str, int]]:
    """Bind complete parents, including preceding layout context for multiline data.

    No column, date, company or publisher dictionary is used. If the complete
    parent cannot fit, fail closed rather than give the recap an ambiguous slice.
    """
    blocks = source_blocks(text)
    indices = [i for i, (left, right) in enumerate(blocks) if left < end and right > start]
    wanted = set(indices)
    for i in indices:
        left, right = blocks[i]
        if len([line for line in text[left:right].splitlines() if line.strip()]) > 1 and i:
            wanted.add(i - 1)
    result = []
    for i in sorted(wanted):
        left, right = blocks[i]
        raw = text[left:right]
        left += len(raw) - len(raw.lstrip())
        quote = raw.strip()
        if quote:
            result.append((quote, left))
    if sum(len(quote) for quote, _ in result) > STRUCTURAL_CONTEXT_CHARS:
        raise ValueError('Information complete source parent exceeds context budget')
    return result


def evidence_selection_input(art: Article, target: str, max_chars: int):
    from ...data.articles.material_quality import visible_analysis_view
    from .outputs import InformationEvidenceBatchView

    visible = visible_analysis_view(art.body, title=art.title, source=art.source)
    body = visible.text[:max_chars]
    labelled = '\n'.join(f'<source-chunk id="{i}">{body[start:end]}</source-chunk><!-- end {i} -->'
        for i, (start,end) in enumerate(source_chunks(body),1))
    context = (
        f'ARTICLE-LEVEL EVIDENCE UNITS. Target: {target}. Publisher: {art.source}. '
        f'completeness={art.completeness}; input_truncated={len(visible.text) > max_chars}.\n'
        'Read the ENTIRE visible article. Select up to four distinct substantive source '
        'statements relevant to the target. Prioritize concrete target events/data/products, '
        'explicit business relationships and named comparisons over general market lists. '
        'Never omit a concrete target update in favor of only background. Do not repeat the same statement. '
        'For each unit, select a core source range (first_chunk and last_chunk, inclusive; '
        'at most 1600 original characters). NEVER reproduce or reconstruct source text. '
        'Select up to two context_ranges (800 original characters each) needed to understand '
        'its subject, attribution, entity/product ownership, time, units, column meanings or qualifications. '
        'A context excerpt may be elsewhere in the SAME visible article, but cannot introduce '
        'an inferred relationship or unrelated story. Include original column headings when copying '
        'a data row; include attribution or identity context when the core uses a pronoun/product name. '
        'Choose supplied chunk numbers. A range contains every intervening chunk; do not join '
        'distant text in one range or select a neighbouring chunk that lacks the intended statement. '
        'Each unit must be understandable from its core and context together. '
        'The system also binds complete source paragraphs and preceding multiline-layout '
        'context automatically; choose a concise core, not a long list of historical rows. '
        'If essential context is unavailable in a partial source, omit that unit and explain insufficiency. '
        'Assess target_role in THIS statement: subject (target event/data), product_owner '
        '(source-established target product), named_comparison (explicit target comparison), '
        'explicit_business_party (source-established concrete business relationship), '
        'incidental (passing mention, host/location or generic sector context), or absent. '
        'Venue/hosting alone and sharing an industry do not make the target a business party. '
        'Product identity may be established elsewhere in the article; preserve that evidence '
        'in context_ranges. Never substitute your own predicted impact for a source-established role. '
        'Reported opinions, plans and forecasts are allowed; source_kind is descriptive, not a truth gate. '
        'Resolve source_actor from the article, using the publisher when no speaker is identified. '
        'information_quality is substantive or thin; materiality is reading usefulness, not '
        'bullish/bearish investment impact. No investment implications or invented causal links. '
        'Return only the requested structured fields. Empty evidence with an explanation is valid.\n'
        f'<untrusted_visible_article>\n{labelled}\n</untrusted_visible_article>'
    )
    return visible, context, InformationEvidenceBatchView


def evidence_recap_input(art: Article, target: str, quote: str, *, source_actor: str, source_kind: str,
                         context_quotes: list[str] | None = None):
    from .outputs import InformationEvidenceRecapView

    # No title, full article, analyst opinion or article-stage interpretation is
    # passed. Every factual source block below has already been located.
    context = (
        f'EVIDENCE UNIT RECAP. Target: {target}. Publisher: {art.source}. '
        f'completeness={art.completeness}; attributed_actor={source_actor}; source_kind={source_kind}.\n'
        'Write ONE concise Chinese summary of the core statement. Use the supporting source '
        'context ONLY to interpret attribution, identities, time, units, column meanings and '
        'qualifications. Do not summarize separate facts from the context as additional updates. '
        'Preserve entities, numbers, units, source attribution and uncertainty. Never infer '
        'omitted content or use your own knowledge to add facts. Opinions/forecasts stay attributed. '
        'Target relevance is already assessed; do not reclassify it or append commentary about '
        'relevance, missing ticker names, evidence sufficiency or competitive ownership. '
        'No investment implications. The system owns all citations and creates source_fact '
        'from this same summary; return only summary and factual-summary confidence. '
        'Keep it concise: prioritize the core event and avoid listing every historical row. '
        'If the source does not establish a value\'s meaning, preserve it as unlabeled or '
        'omit that detail; never invent a transaction ID, unit, date or ownership.\n'
        f'<untrusted_core_statement>\n{quote}\n</untrusted_core_statement>'
    )
    for index, part in enumerate(context_quotes or [], 1):
        context += f'\n<untrusted_supporting_context_{index}>\n{part}\n</untrusted_supporting_context_{index}>'
    return context, InformationEvidenceRecapView


def _extract_evidence_first(art: Article, universe: set[str], max_chars: int, *,
                           target: str, diagnostics: dict | None, deadline: float | None):
    """Locate verbatim excerpts, then independently recap each isolated excerpt."""
    if not target or target not in universe:
        raise ValueError('Evidence-first extraction requires one authorized target')
    visible, context, schema = evidence_selection_input(art, target, max_chars)
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics['model_invocations'] = 0

    def invoke(contract, prompt, *, skill_slug='research-insight'):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Information excerpt dispatch deadline reached')
        diagnostics['model_invocations'] += 1
        try:
            return run_structured('research_extract', contract, prompt, skill_slug=skill_slug)
        except Exception as exc:
            raise RuntimeError(f'Information extraction failed for {art.id}') from exc

    selection = invoke(schema, context)
    diagnostics['empty_reason'] = selection.empty_reason
    excerpts = []
    for choice in selection.evidence:
        quote, position = resolve_source_range(visible.text[:max_chars], choice.core, max_chars=CORE_CHARS)
        if len(quote) > CORE_CHARS:
            raise ValueError('Information canonical excerpt exceeds its character budget')
        support = []
        for canonical, start in structural_context(visible.text[:max_chars], position, position + len(quote)):
            if canonical != quote:
                support.append({'quote': canonical, 'locator': visible.locator(start, start + len(canonical)),
                                'binding': 'complete_source_parent'})
        for part in choice.context_ranges:
            canonical, start = resolve_source_range(visible.text[:max_chars], part, max_chars=CONTEXT_CHARS)
            if len(canonical) > CONTEXT_CHARS:
                raise ValueError('Information supporting context exceeds its character budget')
            if canonical != quote and not any(canonical in row['quote'] for row in support):
                support.append({'quote': canonical, 'locator': visible.locator(start, start + len(canonical))})
        if quote not in [item[0] for item in excerpts]:
            excerpts.append((quote, position, choice, support))
    # Validate ALL excerpts before any recap/caching; a bad selection fails the
    # article instead of presenting a partially processed article as complete.
    out = []
    for quote, position, choice, support in excerpts:
        context, schema = evidence_recap_input(art, target, quote,
            source_actor=choice.source_actor, source_kind=choice.source_kind,
            context_quotes=[part['quote'] for part in support])
        recap = invoke(schema, context, skill_slug='information-evidence-recap')
        out.append(Insight(article_id=art.id, ticker=target, direction='neutral',
            impact_path='direct', evidence_quote=quote,
            evidence_locator=visible.locator(position, position + len(quote)),
            input_truncated=len(visible.text) > max_chars,
            visible_input_chars=min(len(visible.text), max_chars), relation_path=[],
            evidence_context=support, source_fact=recap.summary,
            association_type=_TARGET_ROLES[choice.target_role][0],
            association_reason=_TARGET_ROLES[choice.target_role][1],
            **choice.model_dump(exclude={'core', 'context_ranges'}), **recap.model_dump()))
    return out


def validate_extraction(art: Article, text_view, view, universe: set[str],
                        max_chars: int, *, assess_materiality: bool = False,
                        universe_card: str = "", span_chars: int = EVIDENCE_SPAN_CHARS) -> list[Insight]:
    """Resolve model evidence identically for live extraction and isolated capture."""
    out = []
    for r in view.insights:
        ticker = r.ticker.strip().upper()
        if ticker not in universe:
            log.info("information: dropped non-universe ticker %s from %s",
                     ticker, art.id)
            continue
        if assess_materiality and not r.summary.strip():
            raise ValueError(f"Information target extraction has empty summary: {art.id}")
        quote = r.evidence_quote.strip()[:_MAX_QUOTE_CHARS]
        if assess_materiality and hasattr(r, 'evidence_span_id'):
            available = evidence_spans(text_view.text[:max_chars], span_chars=span_chars)
            ids = [r.evidence_span_id, *getattr(r, 'supporting_evidence_span_ids', [])]
            if len(ids) > 4 or len(set(ids)) != len(ids) or any(key not in available for key in ids):
                raise ValueError(f'Information has unlocated evidence span: {art.id}')
            pieces = sorted(available[key] for key in ids)
            if any(left[1] != right[0] for left, right in zip(pieces, pieces[1:])):
                raise ValueError(f'Information evidence spans must be adjacent: {art.id}')
            span = (pieces[0][0], pieces[-1][1])
            canonical = text_view.text[span[0]:span[1]]
            if quote and quote != canonical.strip():
                raise ValueError(f'Information has unlocated/mismatched quote for evidence span: {art.id}')
            quote = canonical
        position = span[0] if assess_materiality and hasattr(r, 'evidence_span_id') else (
            text_view.text[:max_chars].find(quote) if quote else -1)
        if position < 0:
            if assess_materiality:
                raise ValueError(f"Information target extraction has unlocated evidence: {art.id}")
            log.info("information: dropped unlocated evidence from %s", art.id)
            continue
        out.append(Insight(
            article_id=art.id, ticker=ticker,
            direction="neutral" if assess_materiality else (r.direction if r.direction in _DIRECTIONS else "neutral"),
            impact_path="direct" if assess_materiality else (r.impact_path if r.impact_path in _IMPACT_PATHS else "direct"),
            summary=r.summary.strip(),
            evidence_quote=quote,
            evidence_locator=text_view.locator(position, position+len(quote)),
            materiality=getattr(r, "materiality", None),
            source_fact=getattr(r, 'source_fact', ''),
            source_kind=getattr(r, 'source_kind', 'reported_fact'),
            source_actor=getattr(r, 'source_actor', ''), relation_path=[],
            association_type=getattr(r, 'association_type', 'direct_update'),
            association_reason=getattr(r, 'association_reason', ''),
            information_topic=getattr(r, 'information_topic', ''),
            information_quality=getattr(r, 'information_quality', 'substantive'),
            input_truncated=len(text_view.text) > max_chars,
            visible_input_chars=min(len(text_view.text), max_chars),
            confidence=max(0.0, min(1.0, float(r.confidence)))))
    return out


def _publish_article_brief(store, art: Article, insights: list[Insight],
                           doc_id: str) -> str | None:
    """One InformationBrief projection per processed article (best-effort)."""
    from ...agent.task_projection import ProjectionScope

    if not insights:
        return None
    clocks = clocks_for({"published_at": art.published_at.isoformat()
                         if hasattr(art.published_at, "isoformat")
                         else str(art.published_at)})
    ckey = cluster_key(art.title)
    stats = cluster_summary([
        {"source": art.source, "confidence": i.confidence} for i in insights])
    previous = store.reusable_task_projection(
        agent_role="information_brief",
        scope=ProjectionScope(kind="entity", id=doc_id),
        schema_name="InformationBrief") if hasattr(store, "reusable_task_projection") else None
    fact_changes = [
        f"{i.ticker}: {i.summary}（方向 {i.direction}，影响路径 {i.impact_path}，"
        f"证据「{i.evidence_quote[:120]}」）" for i in insights]
    unverified = [
        f"{i.ticker}: 单一来源陈述，置信 {i.confidence:.2f}，待核验"
        for i in insights if i.confidence < 0.7] or [
        "可见材料中的陈述仍需独立来源核验" ]
    if art.completeness != "full":
        unverified.append(f"来源完整性为 {art.completeness}，未取得隐藏内容")
    payload = {
        "entity": _publisher_entity(art.source),
        "headline": art.title,
        "summary": (insights[0].summary if insights else art.title)[:300],
        "relevance": "high" if stats["confidence"] and stats["confidence"] >= 0.7
        else ("medium" if stats["confidence"] and stats["confidence"] >= 0.4 else "low"),
        "sources": [art.source],
        "fact_changes": fact_changes,
        "source_evidence": [{"fact_index": index,
            "document_ref": art.document_version_id or art.id,
            "publication_id": art.publication_id, "locator": item.evidence_locator,
            "published_at": clocks["published_at"], "event_time": clocks["event_time"],
            "event_time_basis": "publication_proxy_not_independently_verified",
            "extracted_at": clocks["extracted_at"], "confidence": item.confidence,
            "completeness": art.completeness}
            for index, item in enumerate(insights)],
        "impact_candidates": sorted({i.impact_path for i in insights}),
        "entities": sorted({i.ticker for i in insights}),
        "confidence": stats["confidence"],
        "freshness": f"published {clocks['published_at'][:10]}",
        "unverified": unverified,
        "event_time": clocks["event_time"],
        "published_at": clocks["published_at"],
        "extracted_at": clocks["extracted_at"],
        "cluster_key": ckey,
        "source_count": stats["document_count"],
        "independent_sources": stats["independent_sources"],
    }
    try:
        from ..base import execution_versions

        versions = execution_versions("research_extract", "research-insight", template_source=__file__)
        return publish_information_brief(
            store, payload,
            scope=ProjectionScope(kind="entity", id=doc_id),
            as_of=clocks["extracted_at"],
            input_refs=[ref for ref in (art.document_version_id or art.id, art.publication_id) if ref],
            supersedes_projection_id=previous.projection_id if previous else "", **versions)
    except Exception as exc:  # noqa: BLE001 - brief publication is contract-checked
        log.warning("information brief publication failed for %s: %s", art.id, exc)
        raise RuntimeError(f"Information brief publication failed for {art.id}") from exc


def _publisher_entity(source: str) -> str:
    return (source or "").split(":", 1)[-1] or source or "unknown"


def _article_slug(article_id: str) -> str:
    """Stable filesystem identity shared by every consumer of a research article
    (inlined from the data layer so agents never import a provider module)."""
    import re as _re

    return _re.sub(r"[^A-Za-z0-9]+", "-", article_id or "").strip("-")[:120]


def _maybe_push(insights: list[Insight], art: Article, rcfg: dict) -> None:
    """Hot-insight Feishu card — a human notification, not an analysis output."""
    hot = [i for i in insights if i.confidence >= rcfg["push_threshold"]]
    if not hot:
        return
    try:
        from ...channel import get_channel
        from ...schemas.channel import Notification

        body = "\n".join(f"[{i.direction}/{i.impact_path}] {i.ticker}: {i.summary}" for i in hot)
        get_channel("feishu").push(Notification(
            kind="info", title=f"Research insight — {art.title[:60]}", body=body))
        log.info("information: pushed %d insights to Feishu", len(hot))
    except Exception as exc:  # noqa: BLE001 - push is best-effort
        log.info("information: Feishu push skipped: %s", exc)
