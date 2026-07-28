"""Stage B3 — weekly invalidation-trigger check for OPEN, decision-gradeable episodes.

For every open position that has a real pre-registered plan (`decision_gradeable`),
ask one question: has the plan's own `invalidation` text — a condition written down
BEFORE the outcome existed — been confirmed by what actually happened since? This is
the one review output in the whole system that is currently actionable: "thesis
already failed, position still open." Everything else in the journal is a historical
lesson; this is a today problem.

## Why this MUST go through EpisodeCard.blind()

Show the LLM the P&L and it will reason backward from the outcome to a self-consistent
story, even over pure noise — that is exactly the hindsight bias this check exists to
avoid. So the card handed to the model has every outcome field nulled by construction
(`EpisodeCard.blind()`), and `_assert_blind()` re-checks that right before the call as a
second, static line of defense — not because `blind()` is expected to fail, but so a
future field added to TradeEpisode without also being added to `blind()`'s strip list
fails loudly here instead of silently leaking into the prompt.

Going one step further than the P&L fields alone: the context builder below never
includes price levels or price changes either, even though those aren't literally
"P&L" — an LLM told "GOOG is at $180 now" can trivially infer direction and lands right
back in the same bias. The only inputs are the plan's own text, the legs' own
rationale, and period news/research — the same kind of evidence a human would have had
in hand at each point in time, none of it P&L-shaped.

## The `basis_source` / `decision_gradeable` gate

Same reasoning as marks.py: a `pre_tracking` episode's `opened_at` is a fabricated
timestamp, so "how long have we held this / what happened since we opened" is
meaningless for it. And an episode with no `primary_entry_id` has no `invalidation`
text to check in the first place (manual orders bypass `persist_decision` too — see
`TradeEpisode.decision_gradeable`). Both are skipped, not guessed.

`horizon_overdue_days` is computed here (not in marks.py) because, per schema, it is
one of the two "open position only" fields alongside `invalidation_triggered` — the
real-time form of drift, computed deterministically without any LLM call.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from ..agents.base import run_structured
from ..schemas.journal import EpisodeCard, JournalEntry, TradeEpisode
from .outputs import InvalidationView

log = logging.getLogger("ats.journal")

_OUTCOME_FIELDS = (
    "realized_pnl", "unrealized_pnl", "r_multiple", "r_multiple_mtm",
    "mae_pct", "mfe_pct", "excess_vs_sector_pct", "avg_exit",
    "exit_reason", "exit_as_planned",
)


def _assert_blind(card: EpisodeCard) -> None:
    leaked = [f for f in _OUTCOME_FIELDS if getattr(card.episode, f) is not None]
    if leaked:
        raise AssertionError(f"invalidation check received an unblinded card: {leaked}")


def _build_card(store, episode: TradeEpisode) -> EpisodeCard:
    plan = store.get_journal_entry(episode.primary_entry_id) if episode.primary_entry_id else None
    seen: set[str] = set()
    legs: list[JournalEntry] = []
    for leg in store.legs_for_episode(episode.episode_id):
        entry_id = leg.get("entry_id")
        if not entry_id or entry_id in seen:
            continue
        seen.add(entry_id)
        entry = store.get_journal_entry(entry_id)
        if entry is not None:
            legs.append(entry)
    return EpisodeCard(episode=episode, plan=plan, legs=legs)


def _to_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _period_events(store, symbol: str, since: date, limit: int = 20) -> list[dict]:
    rows = store.recent_events(symbol, limit=60)
    picked = [r for r in rows if (_to_date(r.get("published_at")) or date.min) >= since]
    picked.sort(key=lambda r: r["published_at"])
    return picked[-limit:]


def _period_insights(store, symbol: str, since: date, limit: int = 20) -> list[dict]:
    rows = store.recent_insights(ticker=symbol, limit=60)
    picked = [r for r in rows if (_to_date(r.get("created_at")) or date.min) >= since]
    picked.sort(key=lambda r: r["created_at"])
    return picked[-limit:]


def _context(entry: JournalEntry, card: EpisodeCard, events: list[dict],
            insights: list[dict]) -> str:
    ep = card.episode
    legs_block = "\n".join(
        f"  - {leg.as_of.date()} {leg.action}: {leg.rationale or '(无说明)'}"
        for leg in card.legs) or "  (无加减仓记录)"
    events_block = "\n".join(
        f"  - {(e.get('published_at') or '')[:10]} [{e.get('source', '')}] "
        f"{e.get('headline', '')}"
        for e in events) or "  (期间无相关新闻)"
    insights_block = "\n".join(
        f"  - {(i.get('created_at') or '')[:10]} ({i.get('direction', '')}) "
        f"{i.get('summary', '')}"
        + (f"：{i['evidence_quote']}" if i.get("evidence_quote") else "")
        for i in insights) or "  (期间无相关研报要点)"
    horizon_note = (f"计划持有 {entry.planned_horizon_days} 个交易日"
                    if entry.planned_horizon_days else "未设定计划持有期")
    return (
        f"标的 {ep.symbol}，{ep.direction} 仓位，开仓于 {ep.opened_at.date()}。{horizon_note}。\n\n"
        f"开仓时预登记的失效条件（原文，是你判断的唯一依据）：\n  {entry.invalidation}\n\n"
        f"开仓理由：{entry.rationale or '(无)'}\n\n"
        f"持有期内的加减仓记录：\n{legs_block}\n\n"
        f"持有期内的相关新闻：\n{events_block}\n\n"
        f"持有期内的相关研报要点：\n{insights_block}\n\n"
        "上面的失效条件是否已有确凿证据显示发生？"
    )


def check_all(*, store=None, use_llm: bool = True, as_of: date | None = None) -> dict:
    """Run the weekly check over every open, decision-gradeable episode.

    Returns a summary dict; does not raise on a single episode's LLM failure — that
    episode's `invalidation_triggered` is simply left as it was (never guessed).
    """
    from ..memory import get_store

    store = store or get_store()
    today = as_of or datetime.now(timezone.utc).date()
    summary = {"checked": 0, "triggered": 0, "skipped": 0, "llm_failed": 0}

    for ep in store.list_episodes(status="open", limit=100_000):
        if not ep.decision_gradeable or ep.basis_source != "observed_fills":
            summary["skipped"] += 1
            continue
        opening_entry = store.get_journal_entry(ep.primary_entry_id)
        if opening_entry is None:
            summary["skipped"] += 1
            continue

        updates: dict = {}
        if opening_entry.planned_horizon_days:
            holding_so_far = (today - ep.opened_at.date()).days
            updates["horizon_overdue_days"] = max(
                0, holding_so_far - opening_entry.planned_horizon_days)

        if not opening_entry.invalidation.strip():
            summary["skipped"] += 1
            if updates:
                store.save_episode(ep.model_copy(update=updates))
            continue

        if not use_llm:
            if updates:
                store.save_episode(ep.model_copy(update=updates))
            continue

        card = _build_card(store, ep).blind()
        _assert_blind(card)
        events = _period_events(store, ep.symbol, ep.opened_at.date())
        insights = _period_insights(store, ep.symbol, ep.opened_at.date())
        ctx = _context(opening_entry, card, events, insights)
        try:
            view: InvalidationView = run_structured(
                "invalidation_check", InvalidationView, ctx, skill_slug="journal-invalidation")
        except Exception as exc:  # noqa: BLE001
            log.warning("invalidation check failed for %s (%s): %s",
                       ep.symbol, ep.episode_id, exc)
            summary["llm_failed"] += 1
            if updates:
                store.save_episode(ep.model_copy(update=updates))
            continue

        updates["invalidation_triggered"] = view.triggered
        summary["checked"] += 1
        if view.triggered:
            summary["triggered"] += 1
            log.info("invalidation TRIGGERED for %s (%s): %s",
                     ep.symbol, ep.episode_id, view.evidence)
        store.save_episode(ep.model_copy(update=updates))

    return summary


def run(*, use_llm: bool = True) -> int:
    s = check_all(use_llm=use_llm)
    print(f"失效判定完成：检查 {s['checked']}，触发 {s['triggered']}，"
          f"跳过 {s['skipped']}，LLM 失败 {s['llm_failed']}")
    return 0
