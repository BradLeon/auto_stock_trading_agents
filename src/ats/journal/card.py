"""Build an EpisodeCard: the read-only projection joining a TradeEpisode with its
opening plan, its legs' own intents, and any predictions tied to those intents.

Shared by both consumers named in the schema's module docstring:
  - the human-readable review card (Stage C, episode_report.py) — wants predictions
  - the invalidation check (Stage B3, invalidation.py) — calls .blind() on the result
    and skips predictions entirely (it only needs the plan text + legs' rationale)
"""

from __future__ import annotations

from ..schemas.journal import EpisodeCard, JournalEntry, TradeEpisode


def _legs(store, episode: TradeEpisode) -> list[JournalEntry]:
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
    return legs


def build_card(store, episode: TradeEpisode, *, with_predictions: bool = True) -> EpisodeCard:
    plan = (store.get_journal_entry(episode.primary_entry_id)
            if episode.primary_entry_id else None)
    legs = _legs(store, episode)
    predictions: list = []
    if with_predictions:
        entry_ids = {leg.entry_id for leg in legs}
        if plan is not None:
            entry_ids.add(plan.entry_id)
        preds = store.predictions_for_entries(sorted(entry_ids))
        predictions = [(p, store.prediction_outcomes(p.prediction_id)) for p in preds]
    return EpisodeCard(episode=episode, plan=plan, legs=legs, predictions=predictions)
