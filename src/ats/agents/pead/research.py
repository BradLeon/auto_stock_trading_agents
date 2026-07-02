"""Research-insight extraction: read each newsletter article in full and map it
to the PEAD universe (targets + signal-chain members), including second-order
read-throughs (e.g. a hyperscaler compute-rental story -> memory/foundry names).

Material insights become synthetic pead_events under each affected target, so
the existing monitor -> _llm_update -> _apply path folds them into the dossier —
the dossier has exactly one write path. High-confidence insights also push a
Feishu info card immediately.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ...schemas.news import NewsItem
from ...schemas.research import Article, Insight
from ..base import run_structured
from .outputs import InsightBatchView

log = logging.getLogger("ats.agents.pead.research")

_DIRECTIONS = {"bullish", "bearish", "neutral"}
_IMPACT_PATHS = {"direct", "supply_chain", "competitive", "demand", "macro"}
_MAX_QUOTE_CHARS = 400


def run(*, use_llm: bool = True) -> list[Insight]:
    """One research pass: ingest new articles, extract insights, inject events."""
    from ...config import load_pead_global
    from ...data import research as research_src
    from ...memory import get_store

    g = load_pead_global()
    rcfg = g["research"]
    store = get_store()

    since = datetime.now(timezone.utc) - timedelta(days=rcfg["lookback_days"])
    arts = [a for a in research_src.fetch_articles(since) if not store.article_seen(a.id)]
    arts = arts[:rcfg["max_articles_per_run"]]
    log.info("research: %d new articles", len(arts))
    if not arts:
        return []

    universe_card, ticker_to_targets = _build_universe(g.get("targets", []))
    all_insights: list[Insight] = []
    for art in arts:
        insights = _extract(art, universe_card, set(ticker_to_targets),
                            rcfg["article_chars"]) if use_llm else []
        store.save_article(art)
        store.save_insights(art.id, insights)
        _inject_events(store, insights, art, ticker_to_targets, rcfg)
        _maybe_push(insights, art, rcfg)
        all_insights += insights
    return all_insights


def _extract(art: Article, universe_card: str, universe: set[str],
             max_chars: int) -> list[Insight]:
    ctx = (
        f"Universe (targets and their signal-chain members):\n{universe_card}\n\n"
        f"Article from {art.source} ({art.published_at:%Y-%m-%d}): {art.title}\n"
        f"---\n{art.body[:max_chars]}\n---\n\n"
        "Extract per-ticker insights (direct AND second-order read-throughs). "
        "Only universe tickers. An empty list is a valid answer."
    )
    try:
        view: InsightBatchView = run_structured("research_extract", InsightBatchView, ctx,
                                                skill_slug="research-insight")
    except Exception as exc:  # noqa: BLE001
        log.warning("research extraction failed for %s: %s", art.id, exc)
        return []

    out = []
    for r in view.insights:
        ticker = r.ticker.strip().upper()
        if ticker not in universe:
            log.info("research: dropped non-universe ticker %s from %s", ticker, art.id)
            continue
        out.append(Insight(
            article_id=art.id, ticker=ticker,
            direction=r.direction if r.direction in _DIRECTIONS else "neutral",
            impact_path=r.impact_path if r.impact_path in _IMPACT_PATHS else "direct",
            summary=r.summary.strip(),
            evidence_quote=r.evidence_quote.strip()[:_MAX_QUOTE_CHARS],
            confidence=max(0.0, min(1.0, float(r.confidence)))))
    return out


def _build_universe(targets: list[str]) -> tuple[str, dict[str, list[str]]]:
    """Universe card text + {ticker: [target symbols it maps to]}."""
    from ...config import load_pead_config
    from ...memory import get_store

    store = get_store()
    lines: list[str] = []
    ticker_to_targets: dict[str, list[str]] = {}
    for sym in targets:
        sym = sym.upper()
        try:
            cfg = load_pead_config(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("research: no pead config for %s: %s", sym, exc)
            continue
        dossier = store.get_dossier(sym, cfg.fiscal_label)
        thesis = (dossier.expectation_set.narrative
                  if dossier and dossier.expectation_set else cfg.narrative_seed) or ""
        thesis = " ".join(thesis.split())[:160]
        lines.append(f"- {sym} (target): {thesis}")
        ticker_to_targets.setdefault(sym, []).append(sym)
        for sc in cfg.signal_chain:
            member = sc.symbol.upper()
            lines.append(f"- {member} ({sc.role} of {sym})")
            ticker_to_targets.setdefault(member, []).append(sym)
    return "\n".join(lines), ticker_to_targets


def _inject_events(store, insights: list[Insight], art: Article,
                   ticker_to_targets: dict[str, list[str]], rcfg: dict) -> None:
    """Material insights -> synthetic pead_events under each mapped target."""
    for ins in insights:
        if ins.confidence < rcfg["min_confidence_event"]:
            continue
        for target in ticker_to_targets.get(ins.ticker, []):
            item = NewsItem(
                id=f"insight:{art.id}:{ins.ticker}",
                source=art.source,
                headline=f"[{ins.direction}/{ins.impact_path}] {ins.ticker}: {ins.summary}",
                summary=ins.evidence_quote, url=art.url,
                published_at=art.published_at, tickers=[ins.ticker])
            fresh = store.append_events(target, [item])
            if fresh:
                # Pre-seed triage so the monitor doesn't re-score a vetted insight.
                store.set_triage({item.id: (ins.confidence, "research")})


def _maybe_push(insights: list[Insight], art: Article, rcfg: dict) -> None:
    hot = [i for i in insights if i.confidence >= rcfg["push_threshold"]]
    if not hot:
        return
    try:
        from ...channel import get_channel
        from ...schemas.channel import Notification

        body = "\n".join(f"[{i.direction}/{i.impact_path}] {i.ticker}: {i.summary}" for i in hot)
        get_channel("feishu").push(Notification(
            kind="info", title=f"Research insight — {art.title[:60]}", body=body))
        log.info("research: pushed %d insights to Feishu", len(hot))
    except Exception as exc:  # noqa: BLE001 - push is best-effort
        log.info("research: Feishu push skipped: %s", exc)
